"""Reading an AI answer back: its JSON, stripped of fences, validated against the task's schema."""

from __future__ import annotations

from pydantic import ValidationError
import json
import re

from .schemas import Located, Maintenance, Review, Spec


class AiResponseError(ValueError):
    pass


def parse(text: str, model):
    try:
        data = json.loads(_strip_fences(text))
        if model is Maintenance and isinstance(data, dict):
            return Maintenance.lenient(data)
        return model.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as error:
        raise AiResponseError(str(error)[:500]) from error


def parse_spec(text: str) -> Spec:
    return parse(text, Spec)


def parse_located(text: str) -> Located:
    return parse(text, Located)


def parse_review(text: str) -> Review:
    return parse(text, Review)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    return text
