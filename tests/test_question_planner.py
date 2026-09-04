import json

import pytest

from app.mock_call import AnswerClassification, run_mock_call
from app.persistence import LocalCallStore
from app.question_planner import (
    FamilyMemory,
    LocalFamilyMemoryRepository,
    QuestionCategory,
    QuestionPlanner,
)


def memories(count: int = 4) -> tuple[FamilyMemory, ...]:
    return tuple(
        FamilyMemory(
            memory_id=f"memory-{index}",
            category=QuestionCategory.FAMILY_RECOGNITION,
            prompt_text=f"Who is family member {index}?",
            accepted_answers=(f"name{index}",),
            aliases=(f"alias{index}",),
        )
        for index in range(count)
    )


def test_default_plan_contains_five_questions() -> None:
    plan = QuestionPlanner().plan("patient-1", seed=11)

    assert len(plan.questions) == 5
    assert plan.questions[0].question_id == "ORIENTATION_DAY_TIME"


def test_memory_plan_can_contain_eight_questions() -> None:
    planner = QuestionPlanner(LocalFamilyMemoryRepository(memories(4)))

    plan = planner.plan("patient-1", seed=11)

    assert len(plan.questions) == 8
    assert plan.questions[0].category is QuestionCategory.ORIENTATION_DAY_TIME


def test_orientation_is_always_first_after_shuffling() -> None:
    planner = QuestionPlanner(LocalFamilyMemoryRepository(memories(4)))

    for seed in range(5):
        assert planner.plan("patient-1", seed=seed).questions[0].category is QuestionCategory.ORIENTATION_DAY_TIME


def test_only_active_consented_and_complete_memory_is_used() -> None:
    valid = memories(1)[0]
    repository = LocalFamilyMemoryRepository(
        (
            valid,
            FamilyMemory("inactive", valid.category, "Inactive", ("x",), active=False),
            FamilyMemory("not-consented", valid.category, "Private", ("x",), consented=False),
            FamilyMemory("missing-prompt", valid.category, "", ("x",)),
            FamilyMemory("missing-answer", valid.category, "No answer", ()),
        )
    )

    plan = QuestionPlanner(repository).plan("patient-1", seed=3)

    assert "memory-0" in plan.question_ids
    assert not {"inactive", "not-consented", "missing-prompt", "missing-answer"}.intersection(plan.question_ids)


def test_same_seed_is_deterministic_and_previous_sequence_changes_order() -> None:
    planner = QuestionPlanner(LocalFamilyMemoryRepository(memories(4)))

    first = planner.plan("patient-1", seed=27)
    same = planner.plan("patient-1", seed=27)
    next_plan = planner.plan(
        "patient-1",
        seed=27,
        previous_question_ids=first.question_ids,
    )

    assert first == same
    assert next_plan.question_ids != first.question_ids
    assert next_plan.question_ids[0] == "ORIENTATION_DAY_TIME"


def test_mock_call_uses_planned_questions_and_snapshots_answers() -> None:
    planner = QuestionPlanner(LocalFamilyMemoryRepository(memories(4)))
    plan = planner.plan("patient-1", seed=7)
    responses = ["yes", *(question.accepted_answers[0] for question in plan.questions)]
    store = LocalCallStore()

    result = run_mock_call(
        list(responses),
        store=store,
        patient_id="patient-1",
        session_id="planned-call",
        planner=planner,
        plan_seed=7,
    )

    assert result.status == "COMPLETED"
    assert len(result.question_attempts) == 8
    first_memory = next(question for question in plan.questions if question.memory_id)
    snapshot = next(
        question
        for question in result.persisted_call.questions
        if question.memory_id == first_memory.memory_id
    )
    assert snapshot.prompt == first_memory.prompt_text
    assert json.loads(snapshot.accepted_answers_json) == list(first_memory.accepted_answers)
    assert json.loads(snapshot.aliases_json) == list(first_memory.aliases)
    assert snapshot.plan_seed == 7


def test_planned_alias_is_evaluated_as_correct() -> None:
    planner = QuestionPlanner(LocalFamilyMemoryRepository(memories(1)))
    plan = planner.plan("patient-1", seed=4)
    memory = next(question for question in plan.questions if question.memory_id)
    responses = ["yes"]
    for question in plan.questions:
        responses.append(question.aliases[0] if question.memory_id == memory.memory_id else question.accepted_answers[0])

    result = run_mock_call(responses, planner=planner, plan_seed=4)

    memory_index = next(
        index for index, question in enumerate(result.question_attempts)
        if question.memory_id == memory.memory_id
    )
    assert result.classifications[memory_index] is AnswerClassification.CORRECT


@pytest.mark.parametrize("question_count", [5, 6, 7, 8])
def test_execution_and_aggregates_match_each_plan_length(question_count: int) -> None:
    planner = QuestionPlanner(
        LocalFamilyMemoryRepository(memories(max(0, question_count - 5)))
    )
    plan = planner.plan("patient-1", seed=question_count)
    responses = ["yes", "wrong"] + [
        question.accepted_answers[0] for question in plan.questions[1:]
    ]
    store = LocalCallStore()

    result = run_mock_call(
        responses,
        store=store,
        patient_id="patient-1",
        session_id=f"plan-{question_count}",
        planner=planner,
        plan_seed=question_count,
    )

    assert result.status == "COMPLETED"
    assert result.planned_question_count == question_count
    assert len(result.question_attempts) == question_count
    assert f"You have completed all {question_count} questions." in result.transcript[-1]
    assert result.persisted_call.session.session_score == pytest.approx(
        (question_count - 1) / question_count
    )
    assert result.persisted_call.session.summary_accuracy == pytest.approx(
        (question_count - 1) / question_count
    )
