"""Local SQLite persistence for deterministic Service A call sessions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import sqlite3
from typing import TYPE_CHECKING, Protocol, runtime_checkable
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
    accepted_answers_json: str
    aliases_json: str
    category: str
    memory_id: str | None
    plan_seed: int | None


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


@dataclass(frozen=True)
class ConversationRecord:
    session_id: str
    state_json: str


class PersistenceError(RuntimeError):
    """Raised when a call result cannot be safely committed."""


@runtime_checkable
class CallPersistenceRepository(Protocol):
    """Persistence boundary used by the conversation engine."""

    def get_result(self, session_id: str) -> MockCallResult | None:
        """Return a previously finalized result for idempotent events."""

    def create_or_update_session(self, session: CognitiveSession) -> CognitiveSession:
        """Create a session or preserve the existing session record."""

    def store_call_question(self, question: CallQuestion) -> CallQuestion:
        """Store one call question attempt idempotently."""

    def store_session_metric(self, metric: SessionMetric) -> SessionMetric:
        """Store one session metric idempotently."""

    def finalize_session(self, session_id: str) -> PersistedCall:
        """Load the finalized session aggregate."""

    def persist_call(
        self,
        result: MockCallResult,
        *,
        patient_id: str = "local-patient",
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> PersistedCall:
        """Persist a complete call result."""

    def get_conversation_state(self, session_id: str) -> ConversationRecord | None:
        """Load an in-progress conversation snapshot."""

    def get_event_response(self, session_id: str, event_key: str) -> str | None:
        """Return a previously generated webhook response for a duplicate event."""

    def save_conversation_state(
        self,
        record: ConversationRecord,
        event_key: str,
        response_body: str,
    ) -> None:
        """Atomically save state and the response for one webhook event."""

    def claim_event(self, session_id: str, event_key: str) -> bool:
        """Atomically claim an event before advancing conversation state."""

    def release_event(self, session_id: str, event_key: str) -> None:
        """Release an event claim when processing cannot complete."""


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

    def create_or_update_session(self, session: CognitiveSession) -> CognitiveSession:
        """Create a session while preserving an existing terminal record."""

        existing = self.connection.execute(
            "SELECT * FROM cognitive_sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()
        if existing is not None:
            return CognitiveSession(**dict(existing))
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT OR IGNORE INTO cognitive_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(session.__dict__.values()),
                )
        except sqlite3.Error as error:
            raise PersistenceError("Session was not persisted") from error
        return session

    def store_call_question(self, question: CallQuestion) -> CallQuestion:
        """Store one question attempt without duplicating it."""

        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO call_questions
                    (call_question_id, session_id, question_number, attempt_number,
                     prompt, response, classification, difficulty,
                     accepted_answers_json, aliases_json, category, memory_id, plan_seed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(question.__dict__.values()),
                )
        except sqlite3.Error as error:
            raise PersistenceError("Call question was not persisted") from error
        return question

    def store_session_metric(self, metric: SessionMetric) -> SessionMetric:
        """Store one metric without duplicating it."""

        try:
            with self.connection:
                self.connection.execute(
                    "INSERT OR IGNORE INTO session_metrics VALUES (?, ?, ?, ?)",
                    tuple(metric.__dict__.values()),
                )
        except sqlite3.Error as error:
            raise PersistenceError("Session metric was not persisted") from error
        return metric

    def finalize_session(self, session_id: str) -> PersistedCall:
        """Load the stored session aggregate or fail explicitly."""

        try:
            persisted = self._load_persisted_call(session_id)
        except sqlite3.Error as error:
            raise PersistenceError("Session could not be finalized") from error
        if persisted is None:
            raise PersistenceError("Session does not exist")
        return persisted

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
                accepted_answers_json TEXT NOT NULL DEFAULT '[]',
                aliases_json TEXT NOT NULL DEFAULT '[]',
                category TEXT NOT NULL DEFAULT 'GENERAL_AWARENESS',
                memory_id TEXT,
                plan_seed INTEGER,
                UNIQUE (session_id, question_number, attempt_number)
            );
            CREATE TABLE IF NOT EXISTS session_metrics (
                metric_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES cognitive_sessions(session_id),
                call_question_id TEXT NOT NULL UNIQUE REFERENCES call_questions(call_question_id),
                accuracy REAL
            );
            CREATE TABLE IF NOT EXISTS conversation_states (
                session_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_events (
                session_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                response_body TEXT NOT NULL,
                PRIMARY KEY (session_id, event_key)
            );
            CREATE TABLE IF NOT EXISTS conversation_event_claims (
                session_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                PRIMARY KEY (session_id, event_key)
            );
            """
        )
        columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(call_questions)")
        }
        migrations = {
            "accepted_answers_json": "TEXT NOT NULL DEFAULT '[]'",
            "aliases_json": "TEXT NOT NULL DEFAULT '[]'",
            "category": "TEXT NOT NULL DEFAULT 'GENERAL_AWARENESS'",
            "memory_id": "TEXT",
            "plan_seed": "INTEGER",
        }
        for column, definition in migrations.items():
            if column not in columns:
                self.connection.execute(
                    f"ALTER TABLE call_questions ADD COLUMN {column} {definition}"
                )
        self.connection.commit()

    def claim_event(self, session_id: str, event_key: str) -> bool:
        try:
            with self.connection:
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO conversation_event_claims VALUES (?, ?)",
                    (session_id, event_key),
                )
            return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise PersistenceError("Conversation event was not claimed") from error

    def release_event(self, session_id: str, event_key: str) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    "DELETE FROM conversation_event_claims WHERE session_id = ? AND event_key = ?",
                    (session_id, event_key),
                )
        except sqlite3.Error as error:
            raise PersistenceError("Conversation event claim was not released") from error

    def get_conversation_state(self, session_id: str) -> ConversationRecord | None:
        row = self.connection.execute(
            "SELECT session_id, state_json FROM conversation_states WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return ConversationRecord(**dict(row)) if row is not None else None

    def get_event_response(self, session_id: str, event_key: str) -> str | None:
        row = self.connection.execute(
            "SELECT response_body FROM conversation_events WHERE session_id = ? AND event_key = ?",
            (session_id, event_key),
        ).fetchone()
        return row[0] if row is not None else None

    def save_conversation_state(
        self,
        record: ConversationRecord,
        event_key: str,
        response_body: str,
    ) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT OR REPLACE INTO conversation_states VALUES (?, ?)",
                    (record.session_id, record.state_json),
                )
                self.connection.execute(
                    "INSERT OR IGNORE INTO conversation_events VALUES (?, ?, ?)",
                    (record.session_id, event_key, response_body),
                )
        except sqlite3.Error as error:
            raise PersistenceError("Conversation state was not persisted") from error

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
            result.questions_completed / result.planned_question_count
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
            review_classification="REVIEW" if result.status == "EARLY_TERMINATED" else "NONE",
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
                accepted_answers_json=json.dumps(attempt.accepted_answers),
                aliases_json=json.dumps(attempt.aliases),
                category=attempt.category,
                memory_id=attempt.memory_id,
                plan_seed=attempt.plan_seed,
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
                    (call_question_id, session_id, question_number, attempt_number,
                     prompt, response, classification, difficulty,
                     accepted_answers_json, aliases_json, category, memory_id, plan_seed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        if table_name not in {
            "cognitive_sessions",
            "call_questions",
            "session_metrics",
            "conversation_states",
            "conversation_events",
            "conversation_event_claims",
        }:
            raise ValueError("Unsupported persistence table")
        return self.connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]

    def purge_expired_records(self, older_than: datetime) -> dict[str, int]:
        """Delete session records and transient state older than specified datetime."""

        cutoff_iso = older_than.isoformat()
        try:
            with self.connection:
                session_rows = self.connection.execute(
                    "SELECT session_id FROM cognitive_sessions WHERE started_at < ?",
                    (cutoff_iso,),
                ).fetchall()
                expired_session_ids = [row[0] for row in session_rows]

                deleted_metrics = 0
                deleted_questions = 0
                deleted_sessions = 0

                if expired_session_ids:
                    placeholders = ",".join("?" for _ in expired_session_ids)
                    deleted_metrics = self.connection.execute(
                        f"DELETE FROM session_metrics WHERE session_id IN ({placeholders})",
                        tuple(expired_session_ids),
                    ).rowcount
                    deleted_questions = self.connection.execute(
                        f"DELETE FROM call_questions WHERE session_id IN ({placeholders})",
                        tuple(expired_session_ids),
                    ).rowcount
                    deleted_sessions = self.connection.execute(
                        f"DELETE FROM cognitive_sessions WHERE session_id IN ({placeholders})",
                        tuple(expired_session_ids),
                    ).rowcount
                    self.connection.execute(
                        f"DELETE FROM conversation_states WHERE session_id IN ({placeholders})",
                        tuple(expired_session_ids),
                    )
                    self.connection.execute(
                        f"DELETE FROM conversation_events WHERE session_id IN ({placeholders})",
                        tuple(expired_session_ids),
                    )
                    self.connection.execute(
                        f"DELETE FROM conversation_event_claims WHERE session_id IN ({placeholders})",
                        tuple(expired_session_ids),
                    )

                for session_id in expired_session_ids:
                    self._persisted_cache.pop(session_id, None)
                    self._result_cache.pop(session_id, None)

                return {
                    "cognitive_sessions": deleted_sessions,
                    "call_questions": deleted_questions,
                    "session_metrics": deleted_metrics,
                }
        except sqlite3.Error as error:
            raise PersistenceError("Data retention purge failed") from error


def create_local_repository(database_path: str = ":memory:") -> CallPersistenceRepository:
    """Create the current development repository implementation."""

    return LocalCallStore(database_path)


def _termination_reason(result: MockCallResult) -> str:
    if result.status == "COMPLETED":
        return "COMPLETED"
    if result.status == "RESCHEDULED":
        return "NOT_READY"
    if result.status == "FAILED":
        return "PROVIDER_FAILURE"
    if result.readiness.value != "READY":
        return "NOT_READY"
    if result.classifications and result.classifications[-1].value == "STOP":
        return "PATIENT_STOP"
    if result.consecutive_incorrect >= 3:
        return "CONSECUTIVE_INCORRECT"
    return "EARLY_TERMINATED"
