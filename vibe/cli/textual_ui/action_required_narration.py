from __future__ import annotations

from pydantic import BaseModel

from vibe.cli.textual_ui.blocking_voice_answers import (
    ApprovalVoiceAction,
    MultiSelectQuestionVoiceAnswer,
    QuestionVoiceAnswer,
)
from vibe.core.tools.builtins.ask_user_question import Question
from vibe.core.tools.permissions import RequiredPermission


def format_approval_narration(
    tool_name: str,
    _tool_args: BaseModel,
    _required_permissions: list[RequiredPermission] | None,
) -> str:
    action = _describe_tool_action(tool_name)
    return f"I need your approval to {action}."


def format_active_question_narration(question: Question) -> str:
    parts = ["I need your input before I continue."]
    parts.append(question.question)

    if options_text := _format_question_options(question):
        parts.append(options_text)

    if not question.hide_other:
        parts.append("You can also enter a different answer.")

    return " ".join(parts)


def format_approval_voice_confirmation(action: ApprovalVoiceAction) -> str:
    match action:
        case ApprovalVoiceAction.APPROVE_ONCE:
            return "Got it, I'll allow this command."
        case ApprovalVoiceAction.APPROVE_ALWAYS:
            return "Understood, I'll always allow that for this session."
        case ApprovalVoiceAction.REJECT:
            return "Okay, I won't allow it."


def format_question_voice_confirmation(
    question: Question, answer: QuestionVoiceAnswer | MultiSelectQuestionVoiceAnswer
) -> str:
    match answer:
        case QuestionVoiceAnswer(is_other=True):
            return "Understood, I'll use your custom answer."
        case QuestionVoiceAnswer(answer=selected_label):
            return f"Okay, I'll use {selected_label}."
        case MultiSelectQuestionVoiceAnswer(other_text=other_text):
            if other_text:
                return "Understood, I'll use your custom answer."
            selected_labels = [
                question.options[idx].label for idx in answer.selected_indices
            ]
            joined_labels = _join_with_and(selected_labels)
            return f"Okay, I'll use {joined_labels}."


def _describe_tool_action(tool_name: str) -> str:
    match tool_name:
        case "bash":
            return "run this command"
        case "write_file" | "search_replace":
            return "edit this file"
        case _:
            return f"use the {tool_name.replace('_', ' ')} tool"


def _format_question_options(question: Question) -> str | None:
    labels = [choice.label for choice in question.options]
    if not labels:
        return None

    joined_labels = _join_with_conjunction(labels, conjunction="or")
    if question.multi_select:
        return f"Choose one or more options: {joined_labels}."
    return f"Choose {joined_labels}."


def _join_with_and(labels: list[str]) -> str:
    return _join_with_conjunction(labels, conjunction="and")


def _join_with_conjunction(labels: list[str], *, conjunction: str) -> str:
    match labels:
        case []:
            return ""
        case [only]:
            return only
        case [first, second]:
            return f"{first} {conjunction} {second}"
        case _:
            return f"{', '.join(labels[:-1])}, {conjunction} {labels[-1]}"
