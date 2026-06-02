from __future__ import annotations

import difflib
import re

from .sim_types import ActionMatch, ActionSpec


def _normalize(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"\s+", "", lowered)
    return lowered


class ActionInterpreter:
    def __init__(self, action_catalog: dict[str, ActionSpec]):
        self.action_catalog = action_catalog

    def interpret(self, raw_actions: list[str]) -> list[ActionMatch]:
        return [self._match_single(action) for action in raw_actions]

    def _match_single(self, raw_action: str) -> ActionMatch:
        normalized_action = _normalize(raw_action)
        best_spec: ActionSpec | None = None
        best_synonym = ""
        best_score = 0.0

        for spec in self.action_catalog.values():
            for synonym in spec.synonyms:
                normalized_synonym = _normalize(synonym)
                score = 0.0
                if normalized_synonym and normalized_synonym in normalized_action:
                    score = 1.0
                elif normalized_action and normalized_action in normalized_synonym:
                    score = 0.92
                else:
                    score = difflib.SequenceMatcher(
                        None, normalized_action, normalized_synonym
                    ).ratio()
                if score > best_score:
                    best_score = score
                    best_spec = spec 
                    best_synonym = synonym

        if best_spec is None or best_score < 0.55:
            return ActionMatch(
                raw_text=raw_action,
                action_id=None,
                action_name=None,
                category=None,
                confidence=best_score,
            )

        return ActionMatch(
            raw_text=raw_action,
            action_id=best_spec.id,
            action_name=best_spec.name or best_synonym,
            category=best_spec.category,
            confidence=best_score,
        )
