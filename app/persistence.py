"""Local SQLite persistence for deterministic Service A call sessions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import sqlite3
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from app.mock_call import MockCallResult, MockQuestionAttempt


@dataclass(frozen=True)
class CognitiveSession:
    session_id: str
    patient_id: str
    source: str
    activity_type: str
    started_at: str
    ended_at: str
    duration_ms: int
    status: str
    termination_reason: str
    session_score: float | None
    summary_accuracy: float | None
    average_response_latency_ms: float | None
    hesitation_count: int
    review_classification: str
    metadata_json: str


@dataclass(frozen=True)
class CallQuestion:
    call_question_id: str
    session_id: str
    question_number: int
    attempt_number: int
    prompt: str
    response: str | None
    classification: str
    difficulty: int


@dataclass(frozen=True)
class SessionMetric:
    metric_id: str
    session_id: str
    call_question_id: str
    accuracy: float | None


@dataclass(frozen=True)
class PersistedCall:
    session: CognitiveSession
    questions: tuple[CallQuestion, ...]
    metrics: tuple[SessionMetric, ...]


class PersistenceError(RuntimeError):
    """Raised when a call result cannot be safely committed."""


class LocalCallStore:
    """Small SQLite store used by the local mock call flow and its tests."""

    def __init__(self, database_path: str = ":memory:") -> None:
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self._persisted_cache: dict[str, PersistedCall] = {}
        self._result_cache: dict[str, MockCallResult] = {}
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def get_result(self, session_id: str) -> MockCallResult | None:
        """Return the original result for a duplicate local event, if known."""

        return self._result_cache.get(session_id)

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cognitive_sessions (
                session_id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                source TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                termination_reason TEXT NOT NULL,
                session_score REAL,
                summary_accuracy REAL,
                average_response_latency_ms REAL,
                hesitation_count INTEGER NOT NULL,
                review_classification TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS call_questions (
                call_question_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES cognitive_sessions(session_id),
                question_number INTEGER NOT NULL,
                attempt_number INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                response TEXT,
                classification TEXT NOT NULL,
                difficulty INTEGER NOT NULL,
                UNIQUE (session_id, question_number, attempt_number)
            );
            CREATE TABLE IF NOT EXISTS session_metrics (
                metric_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES cognitive_sessions(session_id),
                call_question_id TEXT NOT NULL UNIQUE REFERENCES call_questions(call_question_id),
                accuracy REAL
            );
            """
        )
        self.connection.commit()

    def persist_call(
        self,
        result: MockCallResult,
        *,
        patient_id: str = "local-patient",
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> PersistedCall:
        """Persist a complete result idempotently and return its stored records."""

        try:
            existing = self._load_persisted_call(result.session_id)
        except sqlite3.Error as error:
            cached = self._persisted_cache.get(result.session_id)
            if cached is not None:
                return cached
            raise PersistenceError("Call finalization was not persisted") from error
        if existing is not None:
            self._persisted_cache[result.session_id] = existing
            return existing
        cached = self._persisted_cache.get(result.session_id)
        if cached is not None:
            return cached

        started = started_at or datetime.now(timezone.utc)
        ended = ended_at or datetime.now(timezone.utc)
        duration_ms = max(0, int((ended - started).total_seconds() * 1000))
        scored = [
            attempt
            for attempt in result.question_attempts
            if attempt.classification.value in {"CORRECT", "INCORRECT"}
        ]
        correct_count = sum(
            attempt.classification.value == "CORRECT" for attempt in scored
        )
        summary_accuracy = correct_count / len(scored) if scored else None
        session_score = (
            result.questions_completed / 5
            if result.readiness.value == "READY"
            else None
        )
        session = CognitiveSession(
            session_id=result.session_id,
            patient_id=patient_id,
            source="CALL",
            activity_type="DAILY_CALL",
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_ms=duration_ms,
            status=result.status,
            termination_reason=_termination_reason(result),
            session_score=session_score,
            summary_accuracy=summary_accuracy,
            average_response_latency_ms=None,
            hesitation_count=sum(
                attempt.classification.value == "UNSCORABLE"
                for attempt in result.question_attempts
            ),
            review_classification="REVIEW" if result.status == "STOPPED" else "NONE",
            metadata_json=json.dumps(
                {
                    "readiness": result.readiness.value,
                    "consecutive_incorrect": result.consecutive_incorrect,
                    "transcript_entries": len(result.transcript),
                },
                sort_keys=True,
            ),
        )

        questions = tuple(
            CallQuestion(
                call_question_id=f"{result.session_id}:{attempt.question_number}:{attempt.attempt_number}",
                session_id=result.session_id,
                question_number=attempt.question_number,
                attempt_number=attempt.attempt_number,
                prompt=attempt.prompt,
                response=attempt.response,
                classification=attempt.classification.value,
                difficulty=attempt.difficulty,
            )
            for attempt in result.question_attempts
        )
        metrics = tuple(
            SessionMetric(
                metric_id=f"metric:{question.call_question_id}",
                session_id=result.session_id,
                call_question_id=question.call_question_id,
                accuracy={"CORRECT": 1.0, "INCORRECT": 0.0, "UNSCORABLE": None}.get(
                    question.classification
                ),
            )
            for question in questions
            if question.classification != "SKIPPED"
        )

        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO cognitive_sessions
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(session.__dict__.values()),
                )
                self.connection.executemany(
                    """
                    INSERT OR IGNORE INTO call_questions
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [tuple(question.__dict__.values()) for question in questions],
                )
                self.connection.executemany(
                    """
                    INSERT OR IGNORE INTO session_metrics
                    VALUES (?, ?, ?, ?)
                    """,
                    [tuple(metric.__dict__.values()) for metric in metrics],
                )
        except sqlite3.Error as error:
            raise PersistenceError("Call finalization was not persisted") from error

        persisted = PersistedCall(session, questions, metrics)
        self._persisted_cache[result.session_id] = persisted
        self._result_cache[result.session_id] = replace(
            result,
            persisted_call=persisted,
        )
        return persisted

    def _load_persisted_call(self, session_id: str) -> PersistedCall | None:
        session_row = self.connection.execute(
            "SELECT * FROM cognitive_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session_row is None:
            return None
        session = CognitiveSession(**dict(session_row))
        question_rows = self.connection.execute(
            "SELECT * FROM call_questions WHERE session_id = ? ORDER BY question_number, attempt_number",
            (session_id,),
        ).fetchall()
        questions = tuple(CallQuestion(**dict(row)) for row in question_rows)
        metric_rows = self.connection.execute(
            "SELECT * FROM session_metrics WHERE session_id = ? ORDER BY metric_id",
            (session_id,),
        ).fetchall()
        metrics = tuple(SessionMetric(**dict(row)) for row in metric_rows)
        return PersistedCall(session, questions, metrics)

    def count(self, table_name: str) -> int:
        if table_name not in {"cognitive_sessions", "call_questions", "session_metrics"}:
            raise ValueError("Unsupported persistence table")
        return self.connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]


def _termination_reason(result: MockCallResult) -> str:
    if result.status == "COMPLETED":
        return "COMPLETED"
    if result.readiness.value != "READY":
        return f"READINESS_{result.readiness.value}"
    if result.classifications and result.classifications[-1].value == "STOP":
        return "PATIENT_STOP"
    if result.consecutive_incorrect >= 3:
        return "THREE_CONSECUTIVE_INCORRECT"
    return "CALL_STOPPED"
