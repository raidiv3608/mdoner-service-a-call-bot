from app.mock_call import (
    AnswerClassification,
    ReadinessOutcome,
    classify_readiness,
    run_mock_call,
)


def test_normal_successful_call() -> None:
    result = run_mock_call(["yes", "monday", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
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


def test_unscorable_answer_repeats_once_then_skips() -> None:
    result = run_mock_call(["yes", "unclear", "", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
    assert result.classifications[:3] == (
        AnswerClassification.UNSCORABLE,
        AnswerClassification.UNSCORABLE,
        AnswerClassification.SKIPPED,
    )


def test_stop_ends_immediately() -> None:
    result = run_mock_call(["yes", "stop"])

    assert result.status == "STOPPED"
    assert result.classifications == (AnswerClassification.STOP,)
    assert "Goodbye." in result.transcript[-1]


def test_three_consecutive_incorrect_answers_end_call() -> None:
    result = run_mock_call(["yes", "wrong", "wrong", "wrong"])

    assert result.status == "STOPPED"
    assert result.consecutive_incorrect == 3
    assert result.classifications == (
        AnswerClassification.INCORRECT,
        AnswerClassification.INCORRECT,
        AnswerClassification.INCORRECT,
    )


def test_repeated_no_input_is_skipped_and_call_continues() -> None:
    result = run_mock_call(["yes", None, None, "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
    assert AnswerClassification.SKIPPED in result.classifications
    assert any("try once more" in entry for entry in result.transcript)


def test_successful_completion_has_normal_closing() -> None:
    result = run_mock_call(["ready", "monday", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
    assert result.transcript[-1].startswith("BOT: You have completed all five questions.")


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
