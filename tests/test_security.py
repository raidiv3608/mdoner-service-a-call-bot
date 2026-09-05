from datetime import datetime, timedelta, timezone
import importlib
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.main import app, trigger_rate_limiter
from app.persistence import LocalCallStore
from app.telephony.twilio import TwilioAdapter

main_module = importlib.import_module("app.main")
client = TestClient(app)
AUTH_TOKEN = "test-auth-token"


def test_trigger_outbound_call_rate_limiting(monkeypatch) -> None:
    trigger_rate_limiter.reset()


def test_startup_rejects_missing_trigger_auth_token(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, trigger_auth_token="  "),
    )

    with pytest.raises(RuntimeError, match="SERVICE_A_TRIGGER_AUTH_TOKEN"):
        with TestClient(app):
            pass
    monkeypatch.setattr(
        main_module,
        "settings",
        main_module.settings.__class__(
            trigger_auth_token="test-trigger-token",
            twilio_public_base_url="https://service.example.com",
            twilio_account_sid="AC123",
            twilio_auth_token="token123",
            twilio_from_phone_number="+15550000000",
        ),
    )
    class DummyTwilioAdapter:
        def start_outbound_call(self, to, url):
            return "CA_RATE_TEST"
    monkeypatch.setattr(main_module, "twilio_adapter", DummyTwilioAdapter())

    headers = {"X-Service-A-Trigger-Token": "test-trigger-token"}
    payload = {"to_phone_number": "+15551234567"}

    # First 10 calls succeed
    for _ in range(10):
        response = client.post("/v1/calls/trigger", json=payload, headers=headers)
        assert response.status_code == 200

    # 11th call is rate limited
    response = client.post("/v1/calls/trigger", json=payload, headers=headers)
    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded for outbound calls"

    trigger_rate_limiter.reset()


def test_security_headers_present() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"


def test_purge_expired_records() -> None:
    store = LocalCallStore(":memory:")
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=60)
    new_time = now - timedelta(days=1)

    store.connection.execute(
        "INSERT INTO cognitive_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "session-old",
            "patient-1",
            "CALL",
            "DAILY_CALL",
            old_time.isoformat(),
            old_time.isoformat(),
            1000,
            "COMPLETED",
            "COMPLETED",
            1.0,
            1.0,
            None,
            0,
            "NONE",
            "{}",
        ),
    )
    store.connection.execute(
        "INSERT INTO cognitive_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "session-new",
            "patient-1",
            "CALL",
            "DAILY_CALL",
            new_time.isoformat(),
            new_time.isoformat(),
            1000,
            "COMPLETED",
            "COMPLETED",
            1.0,
            1.0,
            None,
            0,
            "NONE",
            "{}",
        ),
    )
    store.connection.commit()

    assert store.count("cognitive_sessions") == 2

    cutoff = now - timedelta(days=30)
    result = store.purge_expired_records(cutoff)

    assert result["cognitive_sessions"] == 1
    assert store.count("cognitive_sessions") == 1
    remaining = store.connection.execute("SELECT session_id FROM cognitive_sessions").fetchone()[0]
    assert remaining == "session-new"
