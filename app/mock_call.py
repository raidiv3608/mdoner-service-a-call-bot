"""Deterministic local call flow for Service A."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

from app.telephony.mock import MockTelephonyAdapter

if TYPE_CHECKING:
    from app.persistence import LocalCallStore, PersistedCall


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


@dataclass
class _ConversationProgress:
    state: ConversationState = ConversationState.GREETING
    question_position: int = 0
    questions_completed: int = 0
    consecutive_incorrect: int = 0
    repeated_unscorable: bool = False


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
    store: LocalCallStore | None = None,
    patient_id: str = "local-patient",
    session_id: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> MockCallResult:
    """Run one complete deterministic mock call and return its session result."""

    if len(questions) < 5:
        raise ValueError("The prototype requires at least five question fixtures.")
    question_set = questions[:5]

    if store is not None and session_id is not None:
        existing_result = store.get_result(session_id)
        if existing_result is not None:
            return existing_result

    adapter = MockTelephonyAdapter(responses)
    classifications: list[AnswerClassification] = []
    question_attempts: list[MockQuestionAttempt] = []
    attempt_counts: dict[int, int] = {}
    call_session_id = session_id or str(uuid4())
    progress = _ConversationProgress()

    def record_attempt(
        question_index: int,
        question: MockQuestion,
        response: str | None,
        classification: AnswerClassification,
    ) -> None:
        attempt_counts[question_index] = attempt_counts.get(question_index, 0) + 1
        question_attempts.append(
            MockQuestionAttempt(
                question_number=question_index + 1,
                attempt_number=attempt_counts[question_index],
                prompt=question.prompt,
                response=response,
                classification=classification,
                difficulty=question.difficulty,
            )
        )

    def finish(status: str, readiness_outcome: ReadinessOutcome) -> MockCallResult:
        result = MockCallResult(
            status,
            progress.state,
            readiness_outcome,
            tuple(classifications),
            progress.questions_completed,
            progress.consecutive_incorrect,
            tuple(adapter.transcript),
            call_session_id,
            tuple(question_attempts),
        )
        from app.persistence import LocalCallStore

        persisted = (store or LocalCallStore()).persist_call(
            result,
            patient_id=patient_id,
            started_at=started_at,
            ended_at=ended_at,
        )
        return replace(result, persisted_call=persisted)

    adapter.start_call()
    adapter.speak(adapter.greeting_response())
    progress.state = ConversationState.READINESS
    adapter.speak("Are you ready to begin? Please say yes or no.")
    readiness = adapter.listen()
    readiness_outcome = classify_readiness(readiness)
    if readiness_outcome in {ReadinessOutcome.UNKNOWN, ReadinessOutcome.NO_INPUT}:
        adapter.speak("I did not catch that. Please say ready or not ready.")
        readiness_outcome = classify_readiness(adapter.listen())
    if readiness_outcome is not ReadinessOutcome.READY:
        progress.state = ConversationState.STOPPED
        adapter.speak("That is okay. We can try again another time. Goodbye.")
        return finish("STOPPED", readiness_outcome)

    adapter.speak("Thank you. We will take this one question at a time.")
    progress.state = ConversationState.QUESTIONS
    question_order = list(range(5))

    while progress.question_position < len(question_order):
        question_index = question_order[progress.question_position]
        question = question_set[question_index]
        adapter.speak(question.prompt)
        raw_response = adapter.listen()
        if isinstance(raw_response, MockSpeechResult):
            response = raw_response.text
            confidence = raw_response.confidence
        else:
            response = raw_response
            confidence = None
        classification = classify_answer(response, question, confidence)
        record_attempt(question_index, question, response, classification)

        if classification is AnswerClassification.STOP:
            classifications.append(classification)
            progress.state = ConversationState.STOPPED
            adapter.speak("Understood. Thank you for your time. Goodbye.")
            return finish("STOPPED", readiness_outcome)
        if classification is AnswerClassification.UNSCORABLE:
            classifications.append(classification)
            if not progress.repeated_unscorable:
                progress.repeated_unscorable = True
                adapter.speak("I did not catch that. Please take your time and try once more.")
                continue
            progress.state = ConversationState.STOPPED
            adapter.speak("I am having trouble hearing you. We will end the call now. Goodbye.")
            return finish("STOPPED", readiness_outcome)
        if classification is AnswerClassification.SKIPPED:
            classifications.append(classification)
            adapter.speak("That is okay. We will move to the next question.")
            progress.question_position += 1
            progress.repeated_unscorable = False
            continue

        classifications.append(classification)
        progress.repeated_unscorable = False
        if classification is AnswerClassification.CORRECT:
            progress.questions_completed += 1
            progress.consecutive_incorrect = 0
            progress.question_position += 1
            continue

        progress.consecutive_incorrect += 1
        if progress.consecutive_incorrect == 1:
            adapter.speak("That is okay. We will keep going one step at a time.")
        if progress.consecutive_incorrect >= 3:
            progress.state = ConversationState.STOPPED
            adapter.speak("It seems like this is not a good time. We will end the call now. Goodbye.")
            return finish("STOPPED", readiness_outcome)
        if progress.consecutive_incorrect == 2:
            adapter.speak("That is okay. I will make the next question a little easier.")
            remaining = question_order[progress.question_position + 1 :]
            remaining.sort(key=lambda index: question_set[index].difficulty)
            question_order[progress.question_position + 1 :] = remaining
        progress.question_position += 1

    progress.state = ConversationState.COMPLETED
    adapter.speak("You have completed all five questions. Thank you for taking part. Goodbye.")
    return finish("COMPLETED", readiness_outcome)
