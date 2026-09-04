"""Deterministic local call flow for Service A."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

from app.telephony.mock import MockTelephonyAdapter

if TYPE_CHECKING:
    from app.persistence import CallPersistenceRepository, PersistedCall


class AnswerClassification(str, Enum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    UNSCORABLE = "UNSCORABLE"
    STOP = "STOP"
    SKIPPED = "SKIPPED"


class ReadinessOutcome(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    STOP = "STOP"
    UNKNOWN = "UNKNOWN"
    NO_INPUT = "NO_INPUT"


class ConversationState(str, Enum):
    GREETING = "GREETING"
    READINESS = "READINESS"
    QUESTIONS = "QUESTIONS"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class MockQuestion:
    prompt: str
    accepted_answers: tuple[str, ...]
    difficulty: int


@dataclass(frozen=True)
class MockSpeechResult:
    text: str | None
    confidence: float | None = None


@dataclass(frozen=True)
class MockQuestionAttempt:
    question_number: int
    attempt_number: int
    prompt: str
    response: str | None
    classification: AnswerClassification
    difficulty: int


QUESTIONS = (
    MockQuestion("What day is it today?", ("monday",), 1),
    MockQuestion("What month is it?", ("january",), 2),
    MockQuestion("What city are you in?", ("london",), 3),
    MockQuestion("What did you have for breakfast?", ("toast",), 2),
    MockQuestion("What is one thing you enjoy?", ("music",), 1),
)


@dataclass(frozen=True)
class MockCallResult:
    status: str
    state: ConversationState
    readiness: ReadinessOutcome
    classifications: tuple[AnswerClassification, ...]
    questions_completed: int
    consecutive_incorrect: int
    transcript: tuple[str, ...]
    session_id: str
    question_attempts: tuple[MockQuestionAttempt, ...]
    persisted_call: PersistedCall | None = None


@dataclass(frozen=True)
class ConversationTurn:
    messages: tuple[str, ...]
    classification: AnswerClassification | None = None


@dataclass
class _ConversationProgress:
    state: ConversationState = ConversationState.GREETING
    question_position: int = 0
    questions_completed: int = 0
    consecutive_incorrect: int = 0
    repeated_unscorable: bool = False


class ConversationEngine:
    """Incremental deterministic state machine shared by mock and Twilio flows."""

    def __init__(
        self,
        session_id: str,
        questions: tuple[MockQuestion, ...] = QUESTIONS,
    ) -> None:
        if len(questions) < 5:
            raise ValueError("The prototype requires at least five question fixtures.")
        self.session_id = session_id
        self.question_set = questions[:5]
        self.progress = _ConversationProgress()
        self.question_order = list(range(5))
        self.classifications: list[AnswerClassification] = []
        self.question_attempts: list[MockQuestionAttempt] = []
        self.attempt_counts: dict[int, int] = {}
        self.readiness_attempts = 0
        self.readiness = ReadinessOutcome.NO_INPUT
        self.revision = 0

    @property
    def state(self) -> ConversationState:
        return self.progress.state

    @property
    def current_question(self) -> MockQuestion | None:
        if self.progress.question_position >= len(self.question_order):
            return None
        return self.question_set[self.question_order[self.progress.question_position]]

    def start(self) -> ConversationTurn:
        self.progress.state = ConversationState.READINESS
        self.revision += 1
        return ConversationTurn(("Are you ready to begin? Please say yes or no.",))

    def submit_readiness(self, answer: str | None) -> ConversationTurn:
        self.revision += 1
        self.readiness_attempts += 1
        self.readiness = classify_readiness(answer)
        if self.readiness in {ReadinessOutcome.UNKNOWN, ReadinessOutcome.NO_INPUT}:
            if self.readiness_attempts == 1:
                return ConversationTurn(("I did not catch that. Please say ready or not ready.",))
            return self._stop(("That is okay. We can try again another time. Goodbye.",))
        if self.readiness is not ReadinessOutcome.READY:
            return self._stop(("That is okay. We can try again another time. Goodbye.",))
        self.progress.state = ConversationState.QUESTIONS
        return ConversationTurn(
            (
                "Thank you. We will take this one question at a time.",
                self.current_question.prompt,
            )
        )

    def submit_answer(
        self,
        answer: str | None | MockSpeechResult,
    ) -> ConversationTurn:
        self.revision += 1
        question = self.current_question
        if self.progress.state is not ConversationState.QUESTIONS or question is None:
            return ConversationTurn(())
        if isinstance(answer, MockSpeechResult):
            response = answer.text
            confidence = answer.confidence
        else:
            response = answer
            confidence = None
        classification = classify_answer(response, question, confidence)
        question_index = self.question_order[self.progress.question_position]
        self.attempt_counts[question_index] = self.attempt_counts.get(question_index, 0) + 1
        self.question_attempts.append(
            MockQuestionAttempt(
                question_number=question_index + 1,
                attempt_number=self.attempt_counts[question_index],
                prompt=question.prompt,
                response=response,
                classification=classification,
                difficulty=question.difficulty,
            )
        )
        self.classifications.append(classification)

        if classification is AnswerClassification.STOP:
            return self._stop(("Understood. Thank you for your time. Goodbye.",), classification)
        if classification is AnswerClassification.UNSCORABLE:
            if not self.progress.repeated_unscorable:
                self.progress.repeated_unscorable = True
                return ConversationTurn(
                    ("I did not catch that. Please take your time and try once more.",),
                    classification,
                )
            return self._stop(
                ("I am having trouble hearing you. We will end the call now. Goodbye.",),
                classification,
            )
        if classification is AnswerClassification.SKIPPED:
            self.progress.question_position += 1
            self.progress.repeated_unscorable = False
            return self._next_question(("That is okay. We will move to the next question.",), classification)

        self.progress.repeated_unscorable = False
        if classification is AnswerClassification.CORRECT:
            self.progress.questions_completed += 1
            self.progress.consecutive_incorrect = 0
            self.progress.question_position += 1
            return self._next_question((), classification)

        self.progress.consecutive_incorrect += 1
        if self.progress.consecutive_incorrect >= 3:
            return self._stop(
                ("It seems like this is not a good time. We will end the call now. Goodbye.",),
                classification,
            )
        if self.progress.consecutive_incorrect == 2:
            remaining = self.question_order[self.progress.question_position + 1 :]
            remaining.sort(key=lambda index: self.question_set[index].difficulty)
            self.question_order[self.progress.question_position + 1 :] = remaining
            message = "That is okay. I will make the next question a little easier."
        else:
            message = "That is okay. We will keep going one step at a time."
        self.progress.question_position += 1
        return self._next_question((message,), classification)

    def build_result(self, transcript: tuple[str, ...]) -> MockCallResult:
        status = "COMPLETED" if self.state is ConversationState.COMPLETED else "STOPPED"
        return MockCallResult(
            status,
            self.state,
            self.readiness,
            tuple(self.classifications),
            self.progress.questions_completed,
            self.progress.consecutive_incorrect,
            transcript,
            self.session_id,
            tuple(self.question_attempts),
        )

    def to_state(self) -> dict[str, object]:
        """Return JSON-compatible state for server-side conversation storage."""

        return {
            "progress": {
                "state": self.progress.state.value,
                "question_position": self.progress.question_position,
                "questions_completed": self.progress.questions_completed,
                "consecutive_incorrect": self.progress.consecutive_incorrect,
                "repeated_unscorable": self.progress.repeated_unscorable,
            },
            "question_order": self.question_order,
            "classifications": [classification.value for classification in self.classifications],
            "question_attempts": [
                {
                    "question_number": attempt.question_number,
                    "attempt_number": attempt.attempt_number,
                    "prompt": attempt.prompt,
                    "response": attempt.response,
                    "classification": attempt.classification.value,
                    "difficulty": attempt.difficulty,
                }
                for attempt in self.question_attempts
            ],
            "attempt_counts": self.attempt_counts,
            "readiness_attempts": self.readiness_attempts,
            "readiness": self.readiness.value,
            "revision": self.revision,
        }

    @classmethod
    def from_state(
        cls,
        session_id: str,
        state: dict[str, object],
        questions: tuple[MockQuestion, ...] = QUESTIONS,
    ) -> "ConversationEngine":
        engine = cls(session_id, questions)
        progress = state["progress"]
        engine.progress = _ConversationProgress(
            state=ConversationState(progress["state"]),
            question_position=progress["question_position"],
            questions_completed=progress["questions_completed"],
            consecutive_incorrect=progress["consecutive_incorrect"],
            repeated_unscorable=progress["repeated_unscorable"],
        )
        engine.question_order = state["question_order"]
        engine.classifications = [
            AnswerClassification(value) for value in state["classifications"]
        ]
        engine.question_attempts = [
            MockQuestionAttempt(
                question_number=attempt["question_number"],
                attempt_number=attempt["attempt_number"],
                prompt=attempt["prompt"],
                response=attempt["response"],
                classification=AnswerClassification(attempt["classification"]),
                difficulty=attempt["difficulty"],
            )
            for attempt in state["question_attempts"]
        ]
        engine.attempt_counts = {int(key): value for key, value in state["attempt_counts"].items()}
        engine.readiness_attempts = state["readiness_attempts"]
        engine.readiness = ReadinessOutcome(state["readiness"])
        engine.revision = state["revision"]
        return engine

    def _next_question(
        self,
        messages: tuple[str, ...],
        classification: AnswerClassification,
    ) -> ConversationTurn:
        question = self.current_question
        if question is None:
            self.progress.state = ConversationState.COMPLETED
            return ConversationTurn(
                messages
                + ("You have completed all five questions. Thank you for taking part. Goodbye.",),
                classification,
            )
        return ConversationTurn(messages + (question.prompt,), classification)

    def _stop(
        self,
        messages: tuple[str, ...],
        classification: AnswerClassification | None = None,
    ) -> ConversationTurn:
        self.progress.state = ConversationState.STOPPED
        return ConversationTurn(messages, classification)


def classify_answer(
    answer: str | None | MockSpeechResult,
    question: MockQuestion,
    confidence: float | None = None,
) -> AnswerClassification:
    """Classify fixture answers with exact, normalized matching."""

    if isinstance(answer, MockSpeechResult):
        confidence = answer.confidence
        answer = answer.text
    normalized = (answer or "").strip().lower()
    if confidence is not None and confidence < 0.5:
        return AnswerClassification.UNSCORABLE
    if normalized in {"stop", "quit", "end call"}:
        return AnswerClassification.STOP
    if normalized in {"skip", "skipped"}:
        return AnswerClassification.SKIPPED
    if not normalized or normalized in {"unclear", "i don't know", "unknown"}:
        return AnswerClassification.UNSCORABLE
    if normalized in question.accepted_answers:
        return AnswerClassification.CORRECT
    return AnswerClassification.INCORRECT


def classify_readiness(answer: str | None) -> ReadinessOutcome:
    """Classify the patient's deterministic readiness response."""

    normalized = (answer or "").strip().lower()
    if normalized in {"stop", "quit", "end call"}:
        return ReadinessOutcome.STOP
    if not normalized:
        return ReadinessOutcome.NO_INPUT
    if normalized in {"yes", "ready"}:
        return ReadinessOutcome.READY
    if normalized in {"no", "not ready", "later"}:
        return ReadinessOutcome.NOT_READY
    return ReadinessOutcome.UNKNOWN


def run_mock_call(
    responses: list[str | None | MockSpeechResult],
    questions: tuple[MockQuestion, ...] = QUESTIONS,
    *,
    store: CallPersistenceRepository | None = None,
    patient_id: str = "local-patient",
    session_id: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> MockCallResult:
    """Run one complete deterministic mock call and return its session result."""

    if store is not None and session_id is not None:
        existing_result = store.get_result(session_id)
        if existing_result is not None:
            return existing_result

    adapter = MockTelephonyAdapter(responses)
    call_session_id = session_id or str(uuid4())
    engine = ConversationEngine(call_session_id, questions)

    def finish() -> MockCallResult:
        result = engine.build_result(tuple(adapter.transcript))
        from app.persistence import create_local_repository

        persisted = (store or create_local_repository()).persist_call(
            result,
            patient_id=patient_id,
            started_at=started_at,
            ended_at=ended_at,
        )
        return replace(result, persisted_call=persisted)

    adapter.start_call()
    adapter.speak(adapter.greeting_response())
    for message in engine.start().messages:
        adapter.speak(message)
    while engine.state is ConversationState.READINESS:
        readiness = adapter.listen()
        for message in engine.submit_readiness(readiness).messages:
            adapter.speak(message)
    while engine.state is ConversationState.QUESTIONS:
        answer = adapter.listen()
        turn = engine.submit_answer(answer)
        for message in turn.messages:
            adapter.speak(message)
    return finish()
