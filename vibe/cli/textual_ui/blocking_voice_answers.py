from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
import re

from vibe.core.tools.builtins.ask_user_question import Question

_PUNCTUATION_RE = re.compile(r"[^\w\s]+")
_WHITESPACE_RE = re.compile(r"\s+")


class ApprovalVoiceAction(StrEnum):
    APPROVE_ONCE = auto()
    APPROVE_ALWAYS = auto()
    REJECT = auto()


@dataclass(frozen=True, slots=True)
class QuestionVoiceAnswer:
    answer: str
    is_other: bool


@dataclass(frozen=True, slots=True)
class MultiSelectQuestionVoiceAnswer:
    selected_indices: tuple[int, ...]
    other_text: str | None = None


def normalize_voice_transcript(text: str) -> str:
    lowered = text.strip().lower()
    without_punctuation = _PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", without_punctuation).strip()


def collapse_voice_answer_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def resolve_approval_voice_action(text: str) -> ApprovalVoiceAction | None:
    normalized = normalize_voice_transcript(text)
    if not normalized:
        return None

    if normalized in {"always", "always allow", "allow always"}:
        return ApprovalVoiceAction.APPROVE_ALWAYS

    if normalized in {"yes", "approve", "allow"}:
        return ApprovalVoiceAction.APPROVE_ONCE

    if normalized in {"no", "reject", "deny", "cancel"}:
        return ApprovalVoiceAction.REJECT

    return None


def resolve_question_voice_answer(
    question: Question, text: str
) -> QuestionVoiceAnswer | None:
    if question.multi_select:
        return None

    normalized = normalize_voice_transcript(text)
    if not normalized:
        return None

    for option in question.options:
        if normalize_voice_transcript(option.label) == normalized:
            return QuestionVoiceAnswer(answer=option.label, is_other=False)

    if question.hide_other:
        return None

    other_text = collapse_voice_answer_text(text)
    if not other_text:
        return None
    return QuestionVoiceAnswer(answer=other_text, is_other=True)


def resolve_multi_select_question_voice_answer(
    question: Question, text: str
) -> MultiSelectQuestionVoiceAnswer | None:
    if not question.multi_select:
        return None

    normalized = normalize_voice_transcript(text)
    if not normalized:
        return None

    option_matches = _find_multi_select_option_matches(question, normalized)
    if not option_matches:
        if question.hide_other:
            return None
        return _resolve_multi_select_other_only(text)

    return _resolve_multi_select_option_matches(normalized, option_matches)


@dataclass(frozen=True, slots=True)
class _OptionMatch:
    option_idx: int
    start: int
    end: int


def _find_multi_select_option_matches(
    question: Question, normalized_text: str
) -> list[_OptionMatch]:
    matches: list[_OptionMatch] = []
    occupied_ranges: list[tuple[int, int]] = []
    normalized_options = [
        (idx, normalize_voice_transcript(option.label))
        for idx, option in enumerate(question.options)
    ]
    normalized_options = [(idx, label) for idx, label in normalized_options if label]
    normalized_options.sort(key=lambda item: (-len(item[1]), item[0]))

    for option_idx, label in normalized_options:
        pattern = re.compile(rf"(?<!\w){re.escape(label)}(?!\w)")
        for match in pattern.finditer(normalized_text):
            start, end = match.span()
            if _overlaps_existing_range(start, end, occupied_ranges):
                continue
            matches.append(_OptionMatch(option_idx=option_idx, start=start, end=end))
            occupied_ranges.append((start, end))

    matches.sort(key=lambda item: item.start)
    return matches


def _resolve_multi_select_other_only(
    text: str,
) -> MultiSelectQuestionVoiceAnswer | None:
    other_text = collapse_voice_answer_text(text)
    if not other_text:
        return None
    return MultiSelectQuestionVoiceAnswer(selected_indices=(), other_text=other_text)


def _resolve_multi_select_option_matches(
    normalized_text: str, option_matches: list[_OptionMatch]
) -> MultiSelectQuestionVoiceAnswer | None:
    remainder = _strip_multi_select_matches(normalized_text, option_matches)
    if remainder and not _is_safe_multi_select_remainder(remainder):
        return None

    selected_indices = tuple(
        dict.fromkeys(match.option_idx for match in option_matches)
    )
    if not selected_indices:
        return None
    return MultiSelectQuestionVoiceAnswer(selected_indices=selected_indices)


def _overlaps_existing_range(
    start: int, end: int, occupied_ranges: list[tuple[int, int]]
) -> bool:
    return any(
        start < existing_end and end > existing_start
        for existing_start, existing_end in occupied_ranges
    )


def _strip_multi_select_matches(
    normalized_text: str, matches: list[_OptionMatch]
) -> str:
    remainder_parts: list[str] = []
    cursor = 0

    for match in matches:
        remainder_parts.append(normalized_text[cursor : match.start])
        cursor = match.end

    remainder_parts.append(normalized_text[cursor:])
    return collapse_voice_answer_text(" ".join(remainder_parts))


def _is_safe_multi_select_remainder(remainder: str) -> bool:
    return all(token == "and" for token in remainder.split())
