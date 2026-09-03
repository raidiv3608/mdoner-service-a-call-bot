from app.mock_call import AnswerClassification, run_mock_call


def test_normal_successful_call() -> None:
    result = run_mock_call(["yes", "alex", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
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
    result = run_mock_call(["ready", "alex", "january", "london", "toast", "music"])

    assert result.status == "COMPLETED"
    assert result.transcript[-1].startswith("BOT: You have completed all five questions.")
