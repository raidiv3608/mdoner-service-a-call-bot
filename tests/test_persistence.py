from datetime import datetime, timezone

import pytest

from app.mock_call import AnswerClassification, run_mock_call
from app.persistence import (
    CallPersistenceRepository,
    CallQuestion,
    CognitiveSession,
    LocalCallStore,
    PersistenceError,
    SessionMetric,
)


STARTED_AT = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
ENDED_AT = datetime(2026, 1, 1, 9, 1, tzinfo=timezone.utc)


class RepositorySpy:
    def __init__(self) -> None:
        self.backend = LocalCallStore()
        self.persist_calls = 0

    def get_result(self, session_id: str):
        return self.backend.get_result(session_id)

    def create_or_update_session(self, session: CognitiveSession) -> CognitiveSession:
        return self.backend.create_or_update_session(session)

    def store_call_question(self, question: CallQuestion) -> CallQuestion:
        return self.backend.store_call_question(question)

    def store_session_metric(self, metric: SessionMetric) -> SessionMetric:
        return self.backend.store_session_metric(metric)

    def finalize_session(self, session_id: str):
        return self.backend.finalize_session(session_id)

    def persist_call(self, *args, **kwargs):
        self.persist_calls += 1
        return self.backend.persist_call(*args, **kwargs)


def run_with_store(responses: list[str | None], session_id: str):
    store = LocalCallStore()
    result = run_mock_call(
        responses,
        store=store,
        session_id=session_id,
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
    )
    return store, result


def test_completed_session_persistence() -> None:
    store, result = run_with_store(
        ["yes", "monday", "january", "london", "toast", "music"],
        "session-complete",
    )

    session = result.persisted_call.session
    assert session.session_id == "session-complete"
    assert session.patient_id == "local-patient"
    assert session.source == "CALL"
    assert session.activity_type == "DAILY_CALL"
    assert session.status == "COMPLETED"
    assert session.termination_reason == "COMPLETED"
    assert store.count("cognitive_sessions") == 1
    assert store.count("call_questions") == 5


def test_mock_call_uses_repository_interface() -> None:
    repository = RepositorySpy()

    assert isinstance(repository, CallPersistenceRepository)
    result = run_mock_call(
        ["yes", "monday", "january", "london", "toast", "music"],
        store=repository,
        session_id="session-repository-interface",
    )

    assert result.status == "COMPLETED"
    assert repository.persist_calls == 1
    assert repository.backend.count("cognitive_sessions") == 1


def test_correct_answer_produces_full_accuracy_metric() -> None:
    _, result = run_with_store(
        ["yes", "monday", "january", "london", "toast", "music"],
        "session-correct",
    )

    assert len(result.persisted_call.metrics) == 5
    assert {metric.accuracy for metric in result.persisted_call.metrics} == {1.0}


def test_incorrect_answer_produces_zero_accuracy_metric() -> None:
    _, result = run_with_store(
        ["yes", "wrong", "january", "london", "toast", "music"],
        "session-incorrect",
    )

    assert 0.0 in {metric.accuracy for metric in result.persisted_call.metrics}


def test_unscorable_answer_produces_null_accuracy_metric() -> None:
    _, result = run_with_store(
        ["yes", "unclear", "monday", "january", "london", "toast", "music"],
        "session-unscorable",
    )

    unscorable = [
        question
        for question in result.persisted_call.questions
        if question.classification == AnswerClassification.UNSCORABLE.value
    ]
    assert len(unscorable) == 1
    assert any(
        metric.call_question_id.endswith(":1:1") and metric.accuracy is None
        for metric in result.persisted_call.metrics
    )


def test_skipped_answer_has_no_accuracy_metric() -> None:
    store, result = run_with_store(
        ["yes", "skip", "january", "london", "toast", "music"],
        "session-skipped",
    )

    skipped = [
        question
        for question in result.persisted_call.questions
        if question.classification == AnswerClassification.SKIPPED.value
    ]
    assert len(skipped) == 1
    assert all(metric.call_question_id != skipped[0].call_question_id for metric in result.persisted_call.metrics)
    assert store.count("session_metrics") == 4


def test_early_termination_is_persisted() -> None:
    store, result = run_with_store(
        ["yes", "wrong", "wrong", "wrong"],
        "session-stopped",
    )

    session = result.persisted_call.session
    assert session.status == "STOPPED"
    assert session.termination_reason == "THREE_CONSECUTIVE_INCORRECT"
    assert store.count("cognitive_sessions") == 1
    assert store.count("call_questions") == 3


def test_session_aggregation_is_deterministic() -> None:
    _, result = run_with_store(
        ["yes", "wrong", "january", "london", "toast", "music"],
        "session-aggregate",
    )

    session = result.persisted_call.session
    assert session.session_score == 0.8
    assert session.summary_accuracy == 0.8
    assert session.hesitation_count == 0
    assert session.duration_ms == 60_000


def test_persisting_the_same_result_does_not_duplicate_records() -> None:
    store = LocalCallStore()
    result = run_mock_call(
        ["yes", "monday", "january", "london", "toast", "music"],
        store=store,
        session_id="session-idempotent",
    )
    counts_before = {
        table: store.count(table)
        for table in ("cognitive_sessions", "call_questions", "session_metrics")
    }

    store.persist_call(result)

    assert counts_before == {
        table: store.count(table)
        for table in ("cognitive_sessions", "call_questions", "session_metrics")
    }


def test_duplicate_call_and_answer_event_returns_existing_result() -> None:
    store = LocalCallStore()
    responses = ["yes", "monday", "january", "london", "toast", "music"]

    first = run_mock_call(responses, store=store, session_id="session-duplicate")
    counts_before = tuple(
        store.count(table)
        for table in ("cognitive_sessions", "call_questions", "session_metrics")
    )
    second = run_mock_call(responses, store=store, session_id="session-duplicate")

    assert second == first
    assert tuple(
        store.count(table)
        for table in ("cognitive_sessions", "call_questions", "session_metrics")
    ) == counts_before


@pytest.mark.parametrize("terminal_status", ["COMPLETED", "EARLY_TERMINATED", "RESCHEDULED", "FAILED"])
def test_terminal_session_cannot_be_reopened(terminal_status: str) -> None:
    store = LocalCallStore()
    result = run_mock_call(
        ["yes", "monday", "january", "london", "toast", "music"],
        store=store,
        session_id=f"session-{terminal_status.lower()}",
    )
    store.connection.execute(
        "UPDATE cognitive_sessions SET status = ? WHERE session_id = ?",
        (terminal_status, result.session_id),
    )
    store.connection.commit()

    persisted = store.persist_call(result)

    assert persisted.session.status == terminal_status
    assert store.connection.execute(
        "SELECT status FROM cognitive_sessions WHERE session_id = ?",
        (result.session_id,),
    ).fetchone()[0] == terminal_status


def test_database_failure_does_not_report_successful_finalization() -> None:
    store = LocalCallStore()
    store.close()

    with pytest.raises(PersistenceError):
        run_mock_call(
            ["yes", "monday", "january", "london", "toast", "music"],
            store=store,
            session_id="session-db-failure",
        )
