from app.mock_call import (
    AnswerClassification,
    ConversationState,
    MockSpeechResult,
    QUESTIONS,
    ReadinessOutcome,
    classify_answer,
    classify_readiness,
    run_mock_call,
)


def test_normal_successful_call() -> None:
    result = run_mock_call(["yes", "monday", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
    assert result.state is ConversationState.COMPLETED
    assert result.readiness is ReadinessOutcome.READY
    assert result.questions_completed == 5
    assert result.classifications == (
        AnswerClassification.CORRECT,
        AnswerClassification.CORRECT,
        AnswerClassification.CORRECT,
        AnswerClassification.CORRECT,
        AnswerClassification.CORRECT,
    )


def test_incorrect_answer_continues() -> None:
    result = run_mock_call(["yes", "wrong", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
    assert result.classifications[0] is AnswerClassification.INCORRECT
    assert result.questions_completed == 4


def test_unscorable_answer_repeats_once_then_terminates() -> None:
    result = run_mock_call(["yes", "unclear", "", "january", "london", "toast", "music"])

    assert result.status == "STOPPED"
    assert result.classifications == (
        AnswerClassification.UNSCORABLE,
        AnswerClassification.UNSCORABLE,
    )


def test_stop_ends_immediately() -> None:
    result = run_mock_call(["yes", "stop"])

    assert result.status == "STOPPED"
    assert result.classifications == (AnswerClassification.STOP,)
    assert "Goodbye." in result.transcript[-1]


def test_three_consecutive_incorrect_answers_end_call() -> None:
    result = run_mock_call(["yes", "wrong", "wrong", "wrong"])

    assert result.status == "STOPPED"
    assert result.state is ConversationState.STOPPED
    assert result.consecutive_incorrect == 3
    assert result.classifications == (
        AnswerClassification.INCORRECT,
        AnswerClassification.INCORRECT,
        AnswerClassification.INCORRECT,
    )


def test_repeated_no_input_terminates_safely() -> None:
    result = run_mock_call(["yes", None, None, "january", "london", "toast", "music"])

    assert result.status == "STOPPED"
    assert result.classifications == (
        AnswerClassification.UNSCORABLE,
        AnswerClassification.UNSCORABLE,
    )
    assert any("try once more" in entry for entry in result.transcript)
    assert result.transcript[-1].endswith("end the call now. Goodbye.")


def test_successful_completion_has_normal_closing() -> None:
    result = run_mock_call(["ready", "monday", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
    assert result.transcript[-1].startswith("BOT: You have completed all 5 questions.")


def test_orientation_question_is_first_and_uses_day_fixture() -> None:
    result = run_mock_call(["yes", "monday", "january", "london", "toast", "music"])

    assert "BOT: What day is it today?" in result.transcript
    assert result.classifications[0] is AnswerClassification.CORRECT


def test_readiness_outcomes_are_explicit() -> None:
    assert classify_readiness("yes") is ReadinessOutcome.READY
    assert classify_readiness("no") is ReadinessOutcome.NOT_READY
    assert classify_readiness("stop") is ReadinessOutcome.STOP
    assert classify_readiness("maybe") is ReadinessOutcome.UNKNOWN
    assert classify_readiness(None) is ReadinessOutcome.NO_INPUT


def test_readiness_no_input_retries_once() -> None:
    result = run_mock_call([None, "ready", "monday", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
    assert result.readiness is ReadinessOutcome.READY


def test_two_consecutive_incorrect_answers_select_an_easier_remaining_question() -> None:
    result = run_mock_call(
        ["yes", "wrong", "wrong", "music", "toast", "london"]
    )

    assert result.status == "COMPLETED"
    assert result.questions_completed == 3
    assert result.transcript.index("BOT: What is one thing you enjoy?") < result.transcript.index(
        "BOT: What city are you in?"
    )


def test_only_the_five_prototype_questions_are_evaluated() -> None:
    result = run_mock_call(
        ["yes", "monday", "january", "london", "toast", "music", "unexpected"]
    )

    assert result.status == "COMPLETED"
    assert result.questions_completed == 5
    assert result.classifications == (AnswerClassification.CORRECT,) * 5


def test_empty_and_low_confidence_speech_are_unscorable() -> None:
    question = QUESTIONS[0]

    assert classify_answer("", question) is AnswerClassification.UNSCORABLE
    assert classify_answer(MockSpeechResult("monday", 0.49), question) is AnswerClassification.UNSCORABLE
    assert classify_answer(MockSpeechResult("wrong", 0.49), question) is AnswerClassification.UNSCORABLE
