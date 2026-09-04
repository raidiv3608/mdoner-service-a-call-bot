"""Deterministic, read-only question planning for Service A."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import random
from typing import Protocol


class QuestionCategory(str, Enum):
    ORIENTATION_DAY_TIME = "ORIENTATION_DAY_TIME"
    FAMILY_RECOGNITION = "FAMILY_RECOGNITION"
    RELATIONSHIP_RECOGNITION = "RELATIONSHIP_RECOGNITION"
    FAMILY_VOICE_RECOGNITION = "FAMILY_VOICE_RECOGNITION"
    GENERAL_AWARENESS = "GENERAL_AWARENESS"
    PERSONAL_MEMORY = "PERSONAL_MEMORY"
    MILESTONE = "MILESTONE"


@dataclass(frozen=True)
class FamilyMemory:
    memory_id: str
    category: QuestionCategory
    prompt_text: str
    accepted_answers: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    active: bool = True
    consented: bool = True
    difficulty: int = 2


class FamilyMemoryRepository(Protocol):
    """Read-only boundary for patient-specific FamilyMemory."""

    def list_memories(self, patient_id: str) -> tuple[FamilyMemory, ...]:
        """Return available FamilyMemory records without modifying them."""


class LocalFamilyMemoryRepository:
    """Deterministic fixture repository used until shared storage is available."""

    def __init__(self, memories: tuple[FamilyMemory, ...] = ()) -> None:
        self._memories = memories

    def list_memories(self, patient_id: str) -> tuple[FamilyMemory, ...]:
        return self._memories


@dataclass(frozen=True)
class PlannedQuestion:
    question_id: str
    category: QuestionCategory
    prompt_text: str
    accepted_answers: tuple[str, ...]
    aliases: tuple[str, ...]
    difficulty: int
    memory_id: str | None = None


@dataclass(frozen=True)
class QuestionPlan:
    seed: int
    questions: tuple[PlannedQuestion, ...]

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(question.question_id for question in self.questions)


class QuestionPlanner:
    """Build a 5-8 question plan with orientation fixed at position one."""

    def __init__(
        self,
        family_memory_repository: FamilyMemoryRepository | None = None,
        *,
        min_questions: int = 5,
        max_questions: int = 8,
    ) -> None:
        if not 5 <= min_questions <= max_questions <= 8:
            raise ValueError("Question plans must contain between five and eight questions.")
        self.family_memory_repository = family_memory_repository or LocalFamilyMemoryRepository()
        self.min_questions = min_questions
        self.max_questions = max_questions

    def plan(
        self,
        patient_id: str,
        *,
        seed: int,
        previous_question_ids: tuple[str, ...] = (),
    ) -> QuestionPlan:
        memories = tuple(
            memory
            for memory in self.family_memory_repository.list_memories(patient_id)
            if memory.active and memory.consented and self._valid_memory(memory)
        )
        orientation = PlannedQuestion(
            question_id="ORIENTATION_DAY_TIME",
            category=QuestionCategory.ORIENTATION_DAY_TIME,
            prompt_text="What day is it today?",
            accepted_answers=("monday",),
            aliases=(),
            difficulty=1,
        )
        generic = (
            PlannedQuestion(
                "GENERAL_MONTH",
                QuestionCategory.GENERAL_AWARENESS,
                "What month is it?",
                ("january",),
                (),
                2,
            ),
            PlannedQuestion(
                "GENERAL_CITY",
                QuestionCategory.GENERAL_AWARENESS,
                "What city are you in?",
                ("london",),
                (),
                3,
            ),
            PlannedQuestion(
                "PERSONAL_BREAKFAST",
                QuestionCategory.PERSONAL_MEMORY,
                "What did you have for breakfast?",
                ("toast",),
                (),
                2,
            ),
            PlannedQuestion(
                "PERSONAL_ENJOYMENT",
                QuestionCategory.PERSONAL_MEMORY,
                "What is one thing you enjoy?",
                ("music",),
                (),
                1,
            ),
        )
        candidates = list(generic) + [
            PlannedQuestion(
                question_id=memory.memory_id,
                category=memory.category,
                prompt_text=memory.prompt_text,
                accepted_answers=memory.accepted_answers,
                aliases=memory.aliases,
                difficulty=memory.difficulty,
                memory_id=memory.memory_id,
            )
            for memory in memories
        ]
        target_count = min(self.max_questions, max(self.min_questions, len(candidates) + 1))
        selected = candidates[:]
        randomizer = random.Random(seed)
        randomizer.shuffle(selected)
        selected = selected[: target_count - 1]
        question_ids = (orientation.question_id,) + tuple(question.question_id for question in selected)
        if previous_question_ids and question_ids == previous_question_ids and len(selected) > 1:
            selected[0], selected[1] = selected[1], selected[0]
        return QuestionPlan(seed, (orientation, *selected))

    @staticmethod
    def _valid_memory(memory: FamilyMemory) -> bool:
        return bool(
            memory.memory_id.strip()
            and memory.prompt_text.strip()
            and memory.accepted_answers
            and all(answer.strip() for answer in memory.accepted_answers)
        )
