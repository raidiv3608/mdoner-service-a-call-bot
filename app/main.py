"""HTTP application entry point for MDoNER Service A."""

import json
import hashlib
import hmac

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import settings
from app.mock_call import ConversationEngine, ConversationState
from app.mock_call import MockSpeechResult
from app.persistence import ConversationRecord, LocalCallStore, PersistenceError
from app.question_planner import QuestionPlanner
from app.telephony.twilio import TwilioAdapter


app = FastAPI(title=settings.app_name)
TWILIO_VOICE_START_PATH = "/webhooks/twilio/voice/start"
TWILIO_READINESS_PATH = "/webhooks/twilio/voice/readiness"
TWILIO_ANSWER_PATH = "/webhooks/twilio/voice/answer"
E164_PATTERN = r"^\+[1-9]\d{7,14}$"


class CallTriggerRequest(BaseModel):
    to_phone_number: str = Field(pattern=E164_PATTERN)


twilio_adapter = TwilioAdapter(
    account_sid=settings.twilio_account_sid,
    auth_token=settings.twilio_auth_token,
    from_phone_number=settings.twilio_from_phone_number,
)
question_planner = QuestionPlanner()


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the application process is available."""

    return {"status": "ok", "service": settings.app_name}


@app.post("/v1/calls/trigger")
def trigger_outbound_call(
    request: Request,
    payload: CallTriggerRequest,
) -> dict[str, str]:
    """Start one developer-triggered outbound call."""

    configured_token = settings.trigger_auth_token
    provided_token = request.headers.get("X-Service-A-Trigger-Token", "")
    if not configured_token or not provided_token or not hmac.compare_digest(
        provided_token, configured_token
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not settings.twilio_public_base_url:
        raise HTTPException(status_code=500, detail="Twilio public base URL is not configured")
    voice_url = f"{settings.twilio_public_base_url.rstrip('/')}{TWILIO_VOICE_START_PATH}"
    try:
        call_sid = twilio_adapter.start_outbound_call(
            payload.to_phone_number,
            voice_url,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail="Telephony provider unavailable") from error
    return {"call_sid": call_sid}


def _repository() -> LocalCallStore:
    return LocalCallStore(settings.local_database_path)


def _plan_seed(session_id: str) -> int:
    return int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:8], "big")


def _public_url(request: Request, path: str, turn: int | None = None) -> str:
    base_url = settings.twilio_public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    url = f"{base_url}{path}"
    return f"{url}?turn={turn}" if turn is not None else url


async def _validated_params(request: Request, path: str) -> dict[str, str]:
    try:
        form = await request.form()
    except Exception as error:
        raise HTTPException(status_code=400, detail="Malformed webhook input") from error
    params = {key: str(value) for key, value in form.items()}
    if not params.get("CallSid"):
        raise HTTPException(status_code=400, detail="CallSid is required")
    signature = request.headers.get("X-Twilio-Signature")
    webhook_url = _public_url(request, path)
    if request.url.query:
        webhook_url = f"{webhook_url}?{request.url.query}"
    try:
        valid_signature = twilio_adapter.validate_webhook(webhook_url, params, signature)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Telephony provider unavailable") from error
    if not valid_signature:
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    return params


def _turn_value(request: Request) -> int:
    raw_turn = request.query_params.get("turn")
    if raw_turn is None:
        raise HTTPException(status_code=400, detail="Conversation turn is required")
    try:
        return int(raw_turn)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid conversation turn") from error


def _speech_input(params: dict[str, str]) -> str | MockSpeechResult | None:
    speech = params.get("SpeechResult")
    confidence = params.get("Confidence")
    if confidence is None or not confidence.strip():
        return speech
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return MockSpeechResult(speech, 0.0)
    if not 0.0 <= value <= 1.0:
        return MockSpeechResult(speech, 0.0)
    return MockSpeechResult(speech, value)


def _claim_event(repository: LocalCallStore, session_id: str, event_key: str) -> bool:
    try:
        if repository.get_event_response(session_id, event_key) is not None:
            return False
        if not repository.claim_event(session_id, event_key):
            return False
        return True
    except PersistenceError as error:
        raise HTTPException(status_code=503, detail="Conversation state unavailable") from error


def _release_event(repository: LocalCallStore, session_id: str, event_key: str) -> None:
    try:
        repository.release_event(session_id, event_key)
    except PersistenceError:
        pass


def _safe_engine_from_state(repository: LocalCallStore, session_id: str) -> ConversationEngine:
    record = repository.get_conversation_state(session_id)
    if record is None:
        raise HTTPException(status_code=409, detail="Conversation state not found")
    try:
        return ConversationEngine.from_state(session_id, json.loads(record.state_json))
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail="Conversation state unavailable") from error


def _save_turn(
    repository: LocalCallStore,
    engine: ConversationEngine,
    event_key: str,
    response_body: str,
) -> None:
    try:
        repository.save_conversation_state(
            ConversationRecord(engine.session_id, json.dumps(engine.to_state(), sort_keys=True)),
            event_key,
            response_body,
        )
    except PersistenceError as error:
        raise HTTPException(status_code=503, detail="Conversation state unavailable") from error


def _response_for_turn(
    request: Request,
    repository: LocalCallStore,
    engine: ConversationEngine,
    turn_messages: tuple[str, ...],
    event_key: str,
) -> Response:
    try:
        if engine.state is ConversationState.QUESTIONING:
            response_body = twilio_adapter.gather_response(
                turn_messages,
                _public_url(request, TWILIO_ANSWER_PATH, engine.revision),
            )
        else:
            result = engine.build_result(())
            persisted = repository.persist_call(result, patient_id="local-patient")
            response_body = twilio_adapter.terminal_response(turn_messages)
    except PersistenceError as error:
        raise HTTPException(status_code=503, detail="Conversation finalization unavailable") from error
    except Exception as error:
        engine.fail()
        try:
            repository.persist_call(engine.build_result(()), patient_id="local-patient")
        except PersistenceError:
            pass
        raise HTTPException(status_code=502, detail="Telephony provider unavailable") from error
    _save_turn(repository, engine, event_key, response_body)
    return Response(content=response_body, media_type="application/xml")


@app.post("/webhooks/twilio/voice/start", response_class=Response)
async def twilio_voice_start(request: Request) -> Response:
    """Start the deterministic call conversation with a readiness Gather."""

    params = await _validated_params(request, TWILIO_VOICE_START_PATH)
    session_id = params["CallSid"]
    repository = _repository()
    event_key = "start:1"
    existing = repository.get_event_response(session_id, event_key)
    if existing is not None:
        return Response(content=existing, media_type="application/xml")
    if not _claim_event(repository, session_id, event_key):
        existing = repository.get_event_response(session_id, event_key)
        if existing is not None:
            return Response(content=existing, media_type="application/xml")
        raise HTTPException(status_code=409, detail="Event is already being processed")
    plan = question_planner.plan(
        "local-patient",
        seed=_plan_seed(session_id),
    )
    engine = ConversationEngine(session_id, plan=plan)
    turn = engine.start()
    try:
        response_body = twilio_adapter.greeting_gather_response(
            turn.messages[0],
            _public_url(request, TWILIO_READINESS_PATH, engine.revision),
        )
    except Exception as error:
        engine.fail()
        try:
            repository.persist_call(engine.build_result(()), patient_id="local-patient")
        except PersistenceError:
            pass
        raise HTTPException(status_code=502, detail="Telephony provider unavailable") from error
    _save_turn(repository, engine, event_key, response_body)
    return Response(content=response_body, media_type="application/xml")


@app.post(TWILIO_READINESS_PATH, response_class=Response)
async def twilio_voice_readiness(request: Request) -> Response:
    params = await _validated_params(request, TWILIO_READINESS_PATH)
    turn_number = _turn_value(request)
    repository = _repository()
    event_key = f"readiness:{turn_number}:{params.get('SpeechResult', '')}:{params.get('Confidence', '')}"
    existing = repository.get_event_response(params["CallSid"], event_key)
    if existing is not None:
        return Response(content=existing, media_type="application/xml")
    if not _claim_event(repository, params["CallSid"], event_key):
        existing = repository.get_event_response(params["CallSid"], event_key)
        if existing is not None:
            return Response(content=existing, media_type="application/xml")
        raise HTTPException(status_code=409, detail="Event is already being processed")
    engine = _safe_engine_from_state(repository, params["CallSid"])
    if engine.revision != turn_number:
        raise HTTPException(status_code=409, detail="Stale conversation turn")
    if engine.state in {
        ConversationState.COMPLETED,
        ConversationState.EARLY_TERMINATED,
        ConversationState.RESCHEDULED,
        ConversationState.FAILED,
    }:
        raise HTTPException(status_code=409, detail="Conversation is already terminal")
    turn = engine.submit_readiness(_speech_input(params))
    return _response_for_turn(request, repository, engine, turn.messages, event_key)


@app.post(TWILIO_ANSWER_PATH, response_class=Response)
async def twilio_voice_answer(request: Request) -> Response:
    params = await _validated_params(request, TWILIO_ANSWER_PATH)
    turn_number = _turn_value(request)
    repository = _repository()
    event_key = f"answer:{turn_number}:{params.get('SpeechResult', '')}:{params.get('Confidence', '')}"
    existing = repository.get_event_response(params["CallSid"], event_key)
    if existing is not None:
        return Response(content=existing, media_type="application/xml")
    if not _claim_event(repository, params["CallSid"], event_key):
        existing = repository.get_event_response(params["CallSid"], event_key)
        if existing is not None:
            return Response(content=existing, media_type="application/xml")
        raise HTTPException(status_code=409, detail="Event is already being processed")
    engine = _safe_engine_from_state(repository, params["CallSid"])
    if engine.revision != turn_number:
        raise HTTPException(status_code=409, detail="Stale conversation turn")
    if engine.state in {
        ConversationState.COMPLETED,
        ConversationState.EARLY_TERMINATED,
        ConversationState.RESCHEDULED,
        ConversationState.FAILED,
    }:
        raise HTTPException(status_code=409, detail="Conversation is already terminal")
    turn = engine.submit_answer(_speech_input(params))
    return _response_for_turn(request, repository, engine, turn.messages, event_key)
