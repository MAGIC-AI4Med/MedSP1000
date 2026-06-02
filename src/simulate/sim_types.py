from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Document:
    path: Path
    content: str


@dataclass
class RoleBundle:
    name: str
    visible_files: list[Path]
    documents: list[Document]

    @staticmethod
    def _strip_markdown_strikethrough(text: str) -> str:
        return strip_markdown_strikethrough(text)

    def compiled_text(self, *, strip_strikethrough: bool = False) -> str:
        chunks: list[str] = []
        for document in self.documents:
            content = document.content.strip()
            if strip_strikethrough:
                content = strip_markdown_strikethrough(content)
            chunks.append(f"## {document.path.name}\n\n{content}\n")
        return "\n".join(chunks).strip()


def strip_markdown_strikethrough(text: str) -> str:
        sanitized = re.sub(r"~~.*?~~", "", text, flags=re.DOTALL)
        lines: list[str] = []
        pending_blank = False
        for raw_line in sanitized.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                if lines:
                    pending_blank = True
                continue
            if re.fullmatch(r"#{1,6}", stripped):
                continue
            if re.fullmatch(r"[-*+]", stripped):
                continue
            if re.fullmatch(r"\d+\.", stripped):
                continue
            if pending_blank:
                lines.append("")
                pending_blank = False
            lines.append(raw_line.rstrip())
        return "\n".join(lines).strip()


@dataclass
class ActionSpec:
    id: str
    name: str
    category: str
    synonyms: list[str]
    time_cost_minutes: int = 0
    feedback: list[str] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultSpec:
    id: str
    name: str
    text: str
    requires_actions: list[str] = field(default_factory=list)
    min_minutes_since_ingestion: int = 0
    pending_feedback: str | None = None
    one_time: bool = True


@dataclass
class EventSpec:
    id: str
    text: str
    one_time: bool = True


@dataclass
class PhaseSpec:
    id: str
    title: str
    max_turns: int
    next_phase: str | None
    advance_when: dict[str, Any]
    entry_encounter_minutes_at_least: int | None = None
    entry_event_ids: list[str] = field(default_factory=list)
    entry_visible_feedback: list[str] = field(default_factory=list)


@dataclass
class ScenarioDefinition:
    scenario_path: Path
    scenario_root: Path
    meta: dict[str, Any]
    entry: dict[str, Any]
    runtime: dict[str, Any]
    role_bundles: dict[str, RoleBundle]
    action_catalog: dict[str, ActionSpec]
    result_catalog: dict[str, ResultSpec]
    event_catalog: dict[str, EventSpec]
    phase_sequence: list[str]
    phase_map: dict[str, PhaseSpec]
    completion_condition: dict[str, Any]
    evaluation: dict[str, Any]


@dataclass
class CaseDefinition:
    case_root: Path
    case_id: str
    case_title: str
    role_bundles: dict[str, RoleBundle]
    role_dirs: dict[str, Path]
    scenario_id: str = ""
    scenario_title: str = ""
    source_case_root: Path | None = None
    source_scenario_root: Path | None = None
    runtime_hints: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionMatch:
    raw_text: str
    action_id: str | None
    action_name: str | None
    category: str | None
    confidence: float


@dataclass
class RuntimeUpdate:
    feedback_candidates: list[str] = field(default_factory=list)
    event_texts: list[str] = field(default_factory=list)
    phase_changed: bool = False
    newly_entered_phase: str | None = None
    newly_released_result_ids: list[str] = field(default_factory=list)
    should_stop: bool = False
    completion_reason: str | None = None


@dataclass
class TurnRecord:
    turn_index: int
    phase_id: str
    progress_index: int
    minutes_since_encounter: int
    minutes_since_ingestion: int
    examinee_speak: str
    examinee_actions: list[str]
    canonical_actions: list[str]
    sp_speak: list[str]
    environment_feedback: list[str]
    environment_events: list[str]


@dataclass
class SimulationState:
    current_phase_id: str
    progress_index: int
    total_turns: int
    phase_turns: int
    minutes_since_encounter: int
    minutes_since_ingestion: int
    completed_actions: set[str] = field(default_factory=set)
    released_results: set[str] = field(default_factory=set)
    triggered_events: set[str] = field(default_factory=set)
    flags: dict[str, Any] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    turns: list[TurnRecord] = field(default_factory=list)
    phase_history: list[str] = field(default_factory=list)
    action_history: list[dict[str, Any]] = field(default_factory=list)
    latest_patient_speak: list[str] = field(default_factory=list)
    latest_environment_feedback: list[str] = field(default_factory=list)
    latest_environment_events: list[str] = field(default_factory=list)
    latest_state_changed: bool = False
    patient_status: str = ""
    completion_reason: str = ""
