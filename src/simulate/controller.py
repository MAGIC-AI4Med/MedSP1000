from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .agents import EnvironmentAgent, EvaluatorAgent, ExamineeAgent, SPAgent
from .api import LLMClient
from .console import ConsoleLogger, preview_text
from .prompts import (
    build_environment_system_prompt,
    build_evaluator_system_prompt,
    build_evaluator_system_prompt_frozen_rubric,
    build_examinee_system_prompt,
    build_sp_system_prompt,
)
from .sim_types import CaseDefinition, SimulationState, TurnRecord
from .test_time import parse_test_time_config, run_examinee_test_time, select_expert_domains

DEFAULT_OUTPUT_ROOT = Path(
    "/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi/a-simulate/runs"
)


def _format_speak_for_prompt(speak: Any) -> str:
    if isinstance(speak, list):
        return "\n".join(str(item).strip() for item in speak if str(item).strip())
    if speak is None:
        return ""
    return str(speak).strip()


def _count_evaluation_items(node: Any) -> tuple[int, int]:
    if isinstance(node, bool):
        return (1 if node else 0, 1)
    if isinstance(node, dict):
        completed = 0
        total = 0
        for value in node.values():
            item_completed, item_total = _count_evaluation_items(value)
            completed += item_completed
            total += item_total
        return completed, total
    return 0, 0


def _build_evaluation_summary_markdown(evaluation: dict[str, Any]) -> str:
    completed_items, total_items = _count_evaluation_items(evaluation)
    lines = [
        "## 分类评分概览",
        "",
        f"- 评分项总数：{total_items}",
        f"- 已完成项数：{completed_items}",
        f"- 未完成项数：{max(total_items - completed_items, 0)}",
        "",
        "## 分类评分结果",
        "",
        "```json",
        json.dumps(evaluation, ensure_ascii=False, indent=2),
        "```",
    ]
    return "\n".join(lines)


def _safe_relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


_FROZEN_RUBRIC_DIMS = ("PC", "MK", "SBP", "ICS", "PBLI", "PROF")
FROZEN_RUBRIC_OUTPUT_NAME = "final_evaluation_frozen_rubric.json"

# 冻结 rubric 默认目录（与 runner.py 的 --rubric-dir 默认一致）：test_multi/z-rubric/rubrics
DEFAULT_RUBRIC_DIR = Path(__file__).resolve().parents[2] / "z-rubric" / "rubrics"


def _frozen_result_payload(
    evaluation: dict[str, Any],
    raw_output: str,
    rubric: dict[str, Any],
    *,
    eval_model: str,
    original_evaluator_model: str | None,
    rubric_file: str | Path | None,
    source_run_dir: str | Path,
    source_user_prompt_from: str,
    system_prompt: str,
) -> tuple[dict[str, Any], int, int]:
    """构造 final_evaluation_frozen_rubric.json 的内容（含 `_` 审计字段）。

    离线重打分（evaluate_run_dir_with_frozen_rubric）与 in-run 冻结评测共用，保证两条
    路径产出的 schema 完全一致，dashboard / 下游统计无需区分来源。
    """
    scores_only = {key: evaluation.get(key) for key in _FROZEN_RUBRIC_DIMS}
    completed_items, total_items = _count_evaluation_items(scores_only)
    result: dict[str, Any] = {"reasoning": evaluation.get("reasoning", [])}
    result.update({dim: evaluation.get(dim, {}) for dim in _FROZEN_RUBRIC_DIMS})
    result.update(
        {
            "_rescore_kind": "frozen_rubric",
            "_judge_model": eval_model,
            "_original_evaluator_model": original_evaluator_model,
            "_rubric_version": rubric.get("rubric_version"),
            "_rubric_case_id": rubric.get("case_id"),
            "_rubric_scenario": rubric.get("scenario"),
            "_rubric_file": str(rubric_file) if rubric_file else None,
            "_frozen_rubric": {dim: list(rubric.get(dim) or []) for dim in _FROZEN_RUBRIC_DIMS},
            "_source_run_dir": str(source_run_dir),
            "_source_user_prompt_from": source_user_prompt_from,
            "_rescored_at": datetime.now().astimezone().isoformat(),
            "_score_summary": {
                "completed_items": completed_items,
                "total_items": total_items,
                "false_items": max(total_items - completed_items, 0),
                "completion_ratio": (completed_items / total_items) if total_items else 0.0,
            },
            "_evaluator_system_prompt": system_prompt,
            "_raw_output": raw_output,
        }
    )
    return result, completed_items, total_items


def _write_frozen_rubric_file(out_path: Path, result: dict[str, Any]) -> None:
    """原子写 final_evaluation_frozen_rubric.json（先 .tmp 再 os.replace）。"""
    _tmp_path = out_path.with_name(out_path.name + ".tmp")
    _tmp_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(_tmp_path, out_path)


def evaluate_run_dir_with_frozen_rubric(
    run_dir: str | Path,
    rubric: dict[str, Any],
    *,
    config: dict[str, Any],
    eval_model: str = "deepseek-v4-pro",
    rubric_file: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Re-score one existing run against a pre-frozen rubric.

    Enters the pipeline at the evaluation step only: it does NOT re-run the
    simulation. The interaction context is taken verbatim from the run's
    recorded evaluator call (``agent_logs/evaluator_calls.json``), so the model
    sees exactly the same transcript / action log / state summary the original
    evaluator saw. The only changed variable is the evaluator system prompt
    (frozen rubric, judge-only true/false).

    Side effect: writes a single new file ``final_evaluation_frozen_rubric.json``
    into ``run_dir``. The original ``final_evaluation.json``, ``agent_logs/``
    and everything else are left untouched.
    """
    run_dir = Path(run_dir)
    out_path = run_dir / FROZEN_RUBRIC_OUTPUT_NAME

    if out_path.exists() and not force:
        return {"status": "skipped_exists", "run_dir": str(run_dir), "out_path": str(out_path)}

    calls_path = run_dir / "agent_logs" / "evaluator_calls.json"
    if not calls_path.exists():
        return {"status": "skipped_no_evaluator_calls", "run_dir": str(run_dir)}
    calls = json.loads(calls_path.read_text(encoding="utf-8"))
    if not calls:
        return {"status": "skipped_empty_evaluator_calls", "run_dir": str(run_dir)}

    original = calls[0]
    user_prompt = original.get("user_prompt")
    if not user_prompt:
        return {"status": "skipped_no_user_prompt", "run_dir": str(run_dir)}
    metadata = original.get("metadata", {})

    system_prompt = build_evaluator_system_prompt_frozen_rubric(rubric)
    client = LLMClient(config)
    evaluator = EvaluatorAgent(client, "evaluator", system_prompt, model=eval_model)
    evaluation = evaluator.evaluate(user_prompt, metadata=metadata)

    raw_output = (evaluator.call_logs or [{}])[0].get("raw_output", "")
    result, completed_items, total_items = _frozen_result_payload(
        evaluation,
        raw_output,
        rubric,
        eval_model=eval_model,
        original_evaluator_model=original.get("model"),
        rubric_file=rubric_file,
        source_run_dir=run_dir,
        source_user_prompt_from="agent_logs/evaluator_calls.json",
        system_prompt=system_prompt,
    )

    # 原子写：先写 .tmp 再 os.replace（同目录=同文件系统，rename 原子）。
    # 进程被 SIGKILL/OOM 杀掉也只会留 .tmp，绝不会在最终文件留半截损坏 JSON，
    # 配合脚本里的 JSON 合法性校验，保证续跑安全。
    _write_frozen_rubric_file(out_path, result)
    return {
        "status": "ok",
        "run_dir": str(run_dir),
        "out_path": str(out_path),
        "judge_model": eval_model,
        "completed_items": completed_items,
        "total_items": total_items,
        "rubric_file": str(rubric_file) if rubric_file else None,
    }


class SimulationController:
    def __init__(
        self,
        case: CaseDefinition,
        config: dict[str, Any],
        *,
        output_dir: str | Path | None = None,
    ):
        self.case = case
        self.config = config
        self.client = LLMClient(config)
        simulation_config = config.get("simulation", {})
        self.console = ConsoleLogger(
            enabled=bool(simulation_config.get("console_progress", True)),
            debug=bool(simulation_config.get("debug", False)),
        )
        self.output_dir = Path(output_dir) if output_dir else self._default_output_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.execution_log: list[dict[str, Any]] = []

        self.examinee = ExamineeAgent(
            self.client,
            "examinee",
            build_examinee_system_prompt(self.case, self.case.role_bundles["examinee"]),
            model=self._role_model("examinee"),
        )
        self.sp = SPAgent(
            self.client,
            "sp",
            build_sp_system_prompt(self.case, self.case.role_bundles["sp"]),
            model=self._role_model("sp"),
        )
        self.environment = EnvironmentAgent(
            self.client,
            "environment",
            build_environment_system_prompt(self.case, self.case.role_bundles["environment"]),
            model=self._role_model("environment"),
        )
        # 评测口径：默认所有新跑批 in-run 直接用冻结 rubric 打分（固定项集合，跨 run/策略可比）。
        # 缺该 scenario 的冻结 rubric → 本次 run 直接跳过（不跑模拟、不写 marker）。
        # 可用 config.evaluation.use_frozen_rubric=false 回退到旧的「evaluator 临场抽取 rubric」。
        eval_cfg = self.config.get("evaluation", {}) or {}
        self.use_frozen_rubric = bool(eval_cfg.get("use_frozen_rubric", True))
        rubric_dir = eval_cfg.get("rubric_dir") or DEFAULT_RUBRIC_DIR
        self.frozen_rubric, self.frozen_rubric_file = self._resolve_frozen_rubric(rubric_dir)
        self.skip_no_rubric = self.use_frozen_rubric and self.frozen_rubric is None
        if self.use_frozen_rubric and self.frozen_rubric is not None:
            evaluator_system_prompt = build_evaluator_system_prompt_frozen_rubric(self.frozen_rubric)
            self.frozen_eval = True
        else:
            evaluator_system_prompt = build_evaluator_system_prompt(
                self.case, self.case.role_bundles["evaluator"]
            )
            self.frozen_eval = False
        self.evaluator = EvaluatorAgent(
            self.client,
            "evaluator",
            evaluator_system_prompt,
            model=self._role_model("evaluator"),
        )

        # Test-time scaling（examinee 端，opt-in）。off 时下面一切都是惰性的，
        # examinee 走原 respond 路径，行为与历史评测字节级一致。
        self.test_time = parse_test_time_config(self.config.get("agents", {}))
        self.tts_logs: list[dict[str, Any]] = []
        self.tts_expert_domains: list[str] = []
        self.tts_domain_selection: dict[str, Any] | None = None
        if self.test_time.active:
            # 固定 SP/env/evaluator：未显式指定温度的调用统一用 fixed_temperature（默认 0）。
            self.client.default_temperature = self.test_time.fixed_temperature

        self.max_total_turns = int(simulation_config.get("max_total_turns", 12))
        runtime_hints = self.case.runtime_hints
        initial_encounter_minutes = int(
            simulation_config.get(
                "initial_minutes_since_encounter",
                runtime_hints.get("initial_minutes_since_encounter", 0),
            )
        )
        initial_ingestion_minutes = int(
            simulation_config.get(
                "initial_minutes_since_ingestion",
                runtime_hints.get("initial_minutes_since_ingestion", 0),
            )
        )
        initial_stage = str(
            simulation_config.get("initial_stage", runtime_hints.get("initial_stage", "initial_assessment"))
        )
        initial_patient_status = str(
            simulation_config.get(
                "initial_patient_status",
                runtime_hints.get("initial_patient_status", "Not assessed"),
            )
        )
        self.state = SimulationState(
            current_phase_id=initial_stage,
            progress_index=0,
            total_turns=0,
            phase_turns=0,
            minutes_since_encounter=initial_encounter_minutes,
            minutes_since_ingestion=initial_ingestion_minutes,
            patient_status=initial_patient_status,
            latest_environment_feedback=[
                "No environment feedback yet. Begin the encounter based on your visible materials."
            ],
        )
        self.state.phase_history.append(f"{self.state.progress_index}:{initial_stage}")
        self._log(
            "controller_initialized",
            {
                "scenario_id": self.case.scenario_id or self.case.case_root.name,
                "case_id": self.case.case_id,
                "case_title": self.case.case_title,
                "scenario_title": self.case.scenario_title or self.case.case_root.name,
                "source_case_path": str(self.case.source_case_root or self.case.case_root),
                "source_scenario_path": str(self.case.source_scenario_root or self.case.case_root),
                "output_dir": str(self.output_dir),
                "current_stage": initial_stage,
                "progress_index": self.state.progress_index,
                "initial_minutes_since_encounter": initial_encounter_minutes,
                "initial_minutes_since_ingestion": initial_ingestion_minutes,
                "max_total_turns": self.max_total_turns,
                "models": self._resolved_models(),
                "role_dirs": {name: str(path) for name, path in self.case.role_dirs.items()},
            },
        )
        self.console.info(
            "Simulation initialized "
            f"(case={self.case.case_id}, output_dir={self.output_dir}, max_total_turns={self.max_total_turns})"
        )

    def _progress_history_entry(self) -> str:
        return f"{self.state.progress_index}:{self.state.current_phase_id}"

    def _log(self, event_type: str, payload: dict[str, Any]) -> None:
        self.execution_log.append(
            {
                "index": len(self.execution_log) + 1,
                "event_type": event_type,
                "payload": payload,
            }
        )

    def _role_model(self, role_name: str) -> str | None:
        agents_cfg = self.config.get("agents", {})
        role_cfg = agents_cfg.get(role_name, {})
        return role_cfg.get("model") or agents_cfg.get("default_model") or self.client.default_model

    def _resolved_models(self) -> dict[str, str]:
        return {
            "default": str(self.client.default_model or "").strip(),
            "examinee": str(self.examinee.model or "").strip(),
            "sp": str(self.sp.model or "").strip(),
            "environment": str(self.environment.model or "").strip(),
            "evaluator": str(self.evaluator.model or "").strip(),
        }

    def _test_time_manifest(self) -> dict[str, Any]:
        """run_manifest 里的 test-time 元数据快照（off 时 strategy=off，便于下游区分）。"""
        tt = self.test_time
        info: dict[str, Any] = {"strategy": tt.strategy}
        if not tt.active:
            return info
        info.update(
            {
                "marker_tag": tt.marker_tag,
                "examinee_temperature": tt.examinee_temperature,
                "fixed_temperature": tt.fixed_temperature,
            }
        )
        if tt.strategy == "bon":
            info.update({"n": tt.n, "selector": tt.selector})
        elif tt.strategy == "medagents":
            info.update(
                {
                    "experts": tt.experts,
                    "consensus_rounds": tt.consensus_rounds,
                    "expert_domains": list(self.tts_expert_domains),
                }
            )
        return info

    def _resolve_frozen_rubric(self, rubric_dir: str | Path) -> tuple[dict[str, Any] | None, Path]:
        """按 <case_id>_<scenario>.json 在 rubric_dir 找该 scenario 的冻结 rubric。

        返回 (rubric_dict|None, rubric_file_path)。文件不存在或解析失败 → (None, path)。
        """
        scenario = self.case.scenario_id or self.case.case_root.name
        rubric_file = Path(rubric_dir) / f"{self.case.case_id}_{scenario}.json"
        if rubric_file.is_file():
            try:
                return json.loads(rubric_file.read_text(encoding="utf-8")), rubric_file
            except Exception as exc:  # noqa: BLE001
                self.console.warn(f"冻结 rubric 解析失败，按缺失处理 {rubric_file}: {exc}")
        return None, rubric_file

    def _write_frozen_rubric_output(self, evaluation: dict[str, Any]) -> None:
        """in-run 冻结评测后，落 final_evaluation_frozen_rubric.json（含 `_` 审计字段）。"""
        raw_output = (self.evaluator.call_logs or [{}])[-1].get("raw_output", "")
        result, _, _ = _frozen_result_payload(
            evaluation,
            raw_output,
            self.frozen_rubric or {},
            eval_model=str(self.evaluator.model or ""),
            original_evaluator_model=None,
            rubric_file=self.frozen_rubric_file,
            source_run_dir=self.output_dir,
            source_user_prompt_from="in_run_live_evaluator",
            system_prompt=self.evaluator.system_prompt,
        )
        _write_frozen_rubric_file(self.output_dir / FROZEN_RUBRIC_OUTPUT_NAME, result)

    def _default_output_dir(self) -> Path:
        simulation_config = self.config.get("simulation", {})
        configured_root = simulation_config.get("output_root")
        root = Path(configured_root) if configured_root else DEFAULT_OUTPUT_ROOT
        if not root.is_absolute():
            root = self.case.case_root / root
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        scenario_segment = self.case.scenario_id or self.case.case_root.name
        return root / self.case.case_id / scenario_segment / timestamp

    def _opening_context(self) -> str:
        return (
            f"Case `{self.case.case_title}` has started.\n"
            f"- Patient status: {self.state.patient_status or 'unknown'}\n"
            "The examinee can only see the materials in their own role directory, and must begin the encounter based on those materials."
        )

    def _recent_turns(self, limit: int = 4) -> str:
        turns = [asdict(turn) for turn in self.state.turns[-limit:]]
        return json.dumps(turns, ensure_ascii=False, indent=2)

    def _build_examinee_prompt(self) -> str:
        if self.state.total_turns == 0:
            return (
                f"Case: {self.case.case_title}\n"
                f"Patient status: {self.state.patient_status or 'unknown'}\n\n"
                "Please begin the encounter.\n"
                "Put only what you say to the patient into `speak`; put examination, monitoring, investigations, medication administration, and management actions into `actions`.\n"
                "When you believe everything that should be done at the current state has been completed, set `eos` to true in the JSON."
            )

        latest_patient = _format_speak_for_prompt(self.state.latest_patient_speak) or "No new utterance from the patient."
        latest_env = self.state.latest_environment_feedback or []
        latest_events = self.state.latest_environment_events or []

        parts: list[str] = []
        parts.append(f"Case: {self.case.case_title}")
        parts.append(f"Current patient status: {self.state.patient_status or 'unknown'}")
        parts.append(f"Current scenario label: {self.state.current_phase_id}")

        if latest_events or latest_env:
            title = "[State change] The patient's condition has changed:" if self.state.latest_state_changed else "[Previous environment feedback]"
            parts.append(f"\n{title}")
            if latest_events:
                parts.append("System events: " + "; ".join(latest_events))
            if latest_env:
                parts.append("Environment feedback: " + "; ".join(latest_env))
            parts.append("")

        parts.append(f"Patient's previous utterance: {latest_patient}")
        parts.append("\nPlease continue the encounter.")
        parts.append("Put only what you say to the patient into `speak`; put examination, monitoring, investigations, medication administration, and management actions into `actions`.")
        parts.append("When you believe everything that should be done at the current state has been completed, set `eos` to true in the JSON.")

        return "\n".join(parts)

    def _build_sp_prompt(self, doctor_speak: str) -> str:
        return (
            f"The physician just said to the patient:\n{doctor_speak or '(The physician did not speak to the patient this turn.)'}\n\n"
            f"Recent turns:\n{self._recent_turns()}\n\n"
            "Respond naturally, strictly in the patient-side role."
        )

    def _build_environment_prompt(
        self,
        doctor_speak: str,
        raw_actions: list[str],
        sp_response: dict[str, Any],
        eos: bool = False,
    ) -> str:
        parts = [
            f"Current patient status: {self.state.patient_status or 'Not assessed'}",
            f"Current scenario progress index: {self.state.progress_index}",
            f"Current scenario label: {self.state.current_phase_id}",
            f"What the physician said to the patient this turn:\n{doctor_speak or '(empty)'}",
            f"Patient response this turn:\n{_format_speak_for_prompt(sp_response.get('speak'))}",
            f"Physician's action list this turn:\n{json.dumps(raw_actions, ensure_ascii=False, indent=2)}",
        ]
        if eos:
            parts.insert(
                1,
                "\n[Important signal] The physician issued eos=true, indicating they believe the current state has been handled.\n"
                "Decide based on the reference materials:\n"
                "1. If the materials describe a next state after the current scenario label, advance to that state and return its feedback/events.\n"
                "2. Only one node may be advanced at a time; do not skip intermediate nodes.\n"
                "3. If the materials describe no subsequent state, set should_end=true to mark the case as complete.\n"
                "4. Do not fabricate content not present in the materials.",
            )
        else:
            parts.append("Please return this turn's clinical feedback / investigation results / events based on your materials.")
        return "\n\n".join(parts)

    def _build_evaluator_prompt(self) -> str:
        transcript = json.dumps(self.state.transcript, ensure_ascii=False, indent=2)
        action_history = json.dumps(self.state.action_history, ensure_ascii=False, indent=2)
        summary = {
            "case_id": self.case.case_id,
            "case_title": self.case.case_title,
            "phase_history": self.state.phase_history,
            "progress_index": self.state.progress_index,
            "current_state_label": self.state.current_phase_id,
            "patient_status": self.state.patient_status,
            "minutes_since_encounter": self.state.minutes_since_encounter,
            "minutes_since_ingestion": self.state.minutes_since_ingestion,
            "total_turns": self.state.total_turns,
            "completion_reason": self.state.completion_reason,
        }
        return (
            f"Full transcript:\n{transcript}\n\n"
            f"Action log:\n{action_history}\n\n"
            f"State summary:\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n\n"
            "Provide the final evaluation based on the above content."
        )

    def _record_action_history(
        self,
        raw_actions: list[str],
        env_response: dict[str, Any],
    ) -> None:
        assessments = env_response.get("action_assessments", [])
        assessment_by_raw: dict[str, dict[str, Any]] = {}
        remaining_by_order: list[dict[str, Any]] = []
        for item in assessments:
            raw = str(item.get("raw", "")).strip()
            if raw:
                assessment_by_raw[raw] = item
            else:
                remaining_by_order.append(item)

        for index, raw_action in enumerate(raw_actions):
            assessment = assessment_by_raw.get(raw_action)
            if assessment is None and index < len(remaining_by_order):
                assessment = remaining_by_order[index]
            if assessment is None:
                assessment = {
                    "interpreted_action": "",
                    "status": "unsupported",
                    "rationale": "",
                }
            self.state.action_history.append(
                {
                    "raw": raw_action,
                    "canonical_id": assessment.get("interpreted_action") or None,
                    "status": assessment.get("status", "unsupported"),
                    "rationale": assessment.get("rationale", ""),
                }
            )

    def _apply_environment_update(self, env_response: dict[str, Any]) -> None:
        patient_status = env_response.get("patient_status", "").strip()
        if patient_status:
            self.state.patient_status = patient_status

        previous_progress_index = self.state.progress_index
        previous_state_label = self.state.current_phase_id
        progress_index = env_response.get("progress_index")
        if isinstance(progress_index, int) and progress_index >= 0:
            self.state.progress_index = progress_index

        state_label = str(env_response.get("state_label", "")).strip()
        if state_label:
            self.state.current_phase_id = state_label

        if (
            self.state.current_phase_id
            and (
                self.state.current_phase_id != previous_state_label
                or self.state.progress_index != previous_progress_index
            )
        ):
            self.state.phase_history.append(self._progress_history_entry())

        # 当 env 返回了新的 events 时，视为状态发生了变化
        events = env_response.get("events", [])
        state_changed = (
            bool(events)
            or bool(env_response.get("feedback", []))
            or self.state.progress_index != previous_progress_index
            or self.state.current_phase_id != previous_state_label
        )
        self.state.latest_state_changed = state_changed

        if env_response.get("should_end"):
            self.state.completion_reason = env_response.get("completion_reason", "").strip()

        self._log(
            "environment_update_applied",
            {
                "patient_status": self.state.patient_status,
                "progress_index": self.state.progress_index,
                "state_label": self.state.current_phase_id,
                "state_changed": state_changed,
                "should_end": bool(env_response.get("should_end")),
                "completion_reason": self.state.completion_reason,
            },
        )

    def _record_turn(
        self,
        examinee_response: dict[str, Any],
        sp_response: dict[str, Any],
        env_response: dict[str, Any],
    ) -> None:
        turn = TurnRecord(
            turn_index=self.state.total_turns,
            phase_id=self.state.current_phase_id,
            progress_index=self.state.progress_index,
            minutes_since_encounter=self.state.minutes_since_encounter,
            minutes_since_ingestion=self.state.minutes_since_ingestion,
            examinee_speak=examinee_response["speak"],
            examinee_actions=examinee_response["actions"],
            canonical_actions=[
                item.get("interpreted_action", "")
                for item in env_response.get("action_assessments", [])
                if item.get("interpreted_action")
            ],
            sp_speak=sp_response["speak"],
            environment_feedback=env_response.get("feedback", []),
            environment_events=env_response.get("events", []),
        )
        self.state.turns.append(turn)
        self.state.transcript.extend(
            [
                {"role": "examinee", "content": examinee_response["speak"]},
                {"role": "examinee_actions", "content": examinee_response["actions"]},
                {"role": "sp", "content": sp_response["speak"]},
                {"role": "environment", "content": env_response.get("feedback", [])},
            ]
        )
        if env_response.get("events"):
            self.state.transcript.append(
                {"role": "system_event", "content": env_response["events"]}
            )
        self.state.latest_patient_speak = sp_response["speak"]
        self.state.latest_environment_feedback = env_response.get("feedback", [])
        self.state.latest_environment_events = env_response.get("events", [])
        self._log(
            "turn_recorded",
            {
                "turn_index": turn.turn_index,
                "phase_id": turn.phase_id,
                "progress_index": turn.progress_index,
                "minutes_since_encounter": turn.minutes_since_encounter,
                "minutes_since_ingestion": turn.minutes_since_ingestion,
                "examinee_speak": turn.examinee_speak,
                "examinee_actions": turn.examinee_actions,
                "sp_speak": turn.sp_speak,
                "environment_feedback": turn.environment_feedback,
                "environment_events": turn.environment_events,
            },
        )

    def _stop_reason(self, env_response: dict[str, Any]) -> str | None:
        if env_response.get("should_end"):
            return self.state.completion_reason or "environment_marked_complete"
        if self.state.total_turns >= self.max_total_turns:
            return "max_total_turns_reached"
        return None

    def run(self) -> dict[str, Any]:
        if self.skip_no_rubric:
            # 政策：缺该 scenario 的冻结 rubric → 直接跳过，不跑模拟、不写 marker。
            # __init__ 已建空 output_dir，跳过时清掉它避免留空目录（仅当为空时）。
            try:
                self.output_dir.rmdir()
            except OSError:
                pass
            self.console.info(
                "Scenario skipped: 缺冻结 rubric（evaluation.use_frozen_rubric 开启），"
                f"未找到 {self.frozen_rubric_file}"
            )
            return {
                "status": "skipped_no_rubric",
                "skipped": True,
                "case_id": self.case.case_id,
                "scenario_id": self.case.scenario_id or self.case.case_root.name,
                "rubric_file": str(self.frozen_rubric_file),
                "output_dir": str(self.output_dir),
            }
        self.console.info(
            f"Simulation run started for case={self.case.case_id} title={self.case.case_title}"
        )
        self.state.transcript.append(
            {
                "role": "system",
                "type": "opening_context",
                "content": self._opening_context(),
            }
        )

        # MedAgents 式审议：run 开始时一次性选定专科，整个 run 固定复用（不逐轮重选）。
        if self.test_time.strategy == "medagents":
            self.tts_expert_domains, self.tts_domain_selection = select_expert_domains(
                self.client, self.examinee, self.test_time, console=self.console
            )
            self._log("test_time_expert_domains", {"experts": self.tts_expert_domains})

        while self.state.total_turns < self.max_total_turns:
            self.state.total_turns += 1
            self.state.phase_turns += 1
            self._log(
                "turn_started",
                {
                    "turn_index": self.state.total_turns,
                    "phase_id": self.state.current_phase_id,
                    "progress_index": self.state.progress_index,
                    "patient_status": self.state.patient_status,
                },
            )

            turn_metadata = {
                "turn_index": self.state.total_turns,
                "phase_id": self.state.current_phase_id,
                "progress_index": self.state.progress_index,
            }

            if self.test_time.active:
                # test-time 策略：内部采样/审议多次，只把最终动作提交进 examinee 历史；
                # 中间候选/讨论回传到 tts_logs 落 agent_logs，不进模型上下文。
                examinee_response, tts_turn_log = run_examinee_test_time(
                    client=self.client,
                    examinee=self.examinee,
                    turn_prompt=self._build_examinee_prompt(),
                    tt=self.test_time,
                    expert_domains=self.tts_expert_domains,
                    turn_metadata=turn_metadata,
                )
                self.tts_logs.append(tts_turn_log)
            else:
                examinee_response = self.examinee.respond(
                    self._build_examinee_prompt(),
                    metadata=turn_metadata,
                )
            self._log("agent_output_examinee", examinee_response)
            self.console.info(
                f"Turn {self.state.total_turns} examinee response: "
                f"speak='{preview_text(examinee_response['speak'], limit=100)}', "
                f"actions={len(examinee_response['actions'])}, eos={examinee_response['eos']}"
            )

            sp_response = self.sp.respond(
                self._build_sp_prompt(examinee_response["speak"]),
                metadata=turn_metadata,
            )
            self._log("agent_output_sp", sp_response)

            env_response = self.environment.respond(
                self._build_environment_prompt(
                    examinee_response["speak"],
                    examinee_response["actions"],
                    sp_response,
                    eos=examinee_response["eos"],
                ),
                metadata={**turn_metadata, "eos": examinee_response["eos"]},
            )
            self._log("agent_output_environment", env_response)

            self._record_action_history(examinee_response["actions"], env_response)
            self._apply_environment_update(env_response)
            self._record_turn(examinee_response, sp_response, env_response)

            stop_reason = self._stop_reason(env_response)
            if stop_reason:
                self._log(
                    "simulation_stop_condition_met",
                    {
                        "reason": stop_reason,
                        "turn_index": self.state.total_turns,
                        "phase_id": self.state.current_phase_id,
                    },
                )
                self.console.info(
                    f"Simulation stop condition met at turn {self.state.total_turns}: {stop_reason}"
                )
                break

        evaluation = self.evaluator.evaluate(
            self._build_evaluator_prompt(),
            metadata={
                "turn_count": self.state.total_turns,
                "phase_history": list(self.state.phase_history),
            },
        )
        self._log("agent_output_evaluator", evaluation)
        self._write_outputs(evaluation)
        if self.frozen_eval and self.frozen_rubric is not None:
            # in-run 冻结评测：evaluation 本身已是按冻结 rubric 判的 6 维，额外落一份带
            # `_` 审计字段的 final_evaluation_frozen_rubric.json（与离线重打分 schema 一致），
            # dashboard / 下游统计直接可读，无需再单独重打分。
            self._write_frozen_rubric_output(evaluation)
        completed_items, total_items = _count_evaluation_items(evaluation)
        self.console.info(
            f"Simulation finished: total_turns={self.state.total_turns}, "
            f"evaluation_items={completed_items}/{total_items}, output_dir={self.output_dir}"
        )
        return {
            "output_dir": str(self.output_dir),
            "evaluation": evaluation,
            "total_turns": self.state.total_turns,
        }

    def _write_outputs(self, evaluation: dict[str, Any]) -> None:
        transcript_path = self.output_dir / "transcript.json"
        turns_path = self.output_dir / "turns.json"
        actions_path = self.output_dir / "action_history.json"
        evaluation_path = self.output_dir / "final_evaluation.json"
        summary_path = self.output_dir / "summary.md"
        execution_log_path = self.output_dir / "execution_log.json"
        execution_log_jsonl_path = self.output_dir / "execution_log.jsonl"
        materials_path = self.output_dir / "materials.json"
        manifest_path = self.output_dir / "run_manifest.json"
        model_info_path = self.output_dir / "run_models.json"
        agent_log_dir = self.output_dir / "agent_logs"
        agent_log_dir.mkdir(parents=True, exist_ok=True)
        completed_items, total_items = _count_evaluation_items(evaluation)
        success = bool(isinstance(evaluation, dict) and evaluation)
        models = self._resolved_models()

        transcript_path.write_text(
            json.dumps(self.state.transcript, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        turns_path.write_text(
            json.dumps([asdict(turn) for turn in self.state.turns], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        actions_path.write_text(
            json.dumps(self.state.action_history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        evaluation_path.write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        execution_log_path.write_text(
            json.dumps(self.execution_log, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        execution_log_jsonl_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in self.execution_log),
            encoding="utf-8",
        )
        materials_path.write_text(
            json.dumps(
                {
                    "case_id": self.case.case_id,
                    "case_title": self.case.case_title,
                    "scenario_id": self.case.scenario_id or self.case.case_root.name,
                    "scenario_title": self.case.scenario_title or self.case.case_root.name,
                    "source_case_path": str(self.case.source_case_root or self.case.case_root),
                    "source_scenario_path": str(self.case.source_scenario_root or self.case.case_root),
                    "roles": {
                        role_name: {
                            "role_name": role_name,
                            "role_dir": str(self.case.role_dirs[role_name]),
                            "documents": [
                                {
                                    "name": document.path.name,
                                    "path": str(document.path),
                                    "relative_path": _safe_relative_path(
                                        document.path,
                                        self.case.role_dirs[role_name],
                                    ),
                                    "content": document.content,
                                }
                                for document in bundle.documents
                            ],
                        }
                        for role_name, bundle in self.case.role_bundles.items()
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(
                {
                    "run_id": str(self.output_dir),
                    "run_dir": str(self.output_dir),
                    "run_name": self.output_dir.name,
                    "run_timestamp": self.output_dir.name,
                    "status": "completed",
                    "success": success,
                    "case_id": self.case.case_id,
                    "case_title": self.case.case_title,
                    "scenario_id": self.case.scenario_id or self.case.case_root.name,
                    "scenario_title": self.case.scenario_title or self.case.case_root.name,
                    "source_case_path": str(self.case.source_case_root or self.case.case_root),
                    "source_scenario_path": str(self.case.source_scenario_root or self.case.case_root),
                    "models": models,
                    "test_time": self._test_time_manifest(),
                    "score_summary": {
                        "completed_items": completed_items,
                        "total_items": total_items,
                        "false_items": max(total_items - completed_items, 0),
                        "completion_ratio": (completed_items / total_items) if total_items else 0.0,
                        "completion_score_100": ((completed_items / total_items) * 100.0) if total_items else 0.0,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        model_info_path.write_text(
            json.dumps(
                {
                    "case_id": self.case.case_id,
                    "scenario_id": self.case.scenario_id or self.case.case_root.name,
                    "run_timestamp": self.output_dir.name,
                    "models": models,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        agent_logs = {
            "examinee": self.examinee.call_logs,
            "sp": self.sp.call_logs,
            "environment": self.environment.call_logs,
            "evaluator": self.evaluator.call_logs,
        }
        for agent_name, logs in agent_logs.items():
            (agent_log_dir / f"{agent_name}_calls.json").write_text(
                json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (agent_log_dir / f"{agent_name}_calls.jsonl").write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in logs),
                encoding="utf-8",
            )

        # test-time 逐轮审议/候选明细（仅激活时写；不进模型上下文，纯审计用）。
        # 每个 turn 的 `calls` 含每次子调用（专家/合成/共识/候选/选择器）的完整发送消息 +
        # 原始输出，与 *_calls.json 同精度，确保所有讨论/交互都落地本地 JSON。
        if self.test_time.active:
            tts_payload = {
                "strategy": self.test_time.strategy,
                "config": self._test_time_manifest(),
                "domain_selection": self.tts_domain_selection,
                "turns": self.tts_logs,
            }
            (agent_log_dir / "examinee_tts_calls.json").write_text(
                json.dumps(tts_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (agent_log_dir / "examinee_tts_calls.jsonl").write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in self.tts_logs),
                encoding="utf-8",
            )

        lines = [
            f"# {self.case.case_title} 运行摘要",
            "",
            f"- 总轮次：{self.state.total_turns}",
            f"- 剧情轨迹：{', '.join(self.state.phase_history)}",
            f"- 当前推进索引：{self.state.progress_index}",
            f"- 患者状态：{self.state.patient_status or '未记录'}",
            f"- 结束原因：{self.state.completion_reason or '达到最大轮次或环境未声明结束'}",
            f"- 执行日志：{execution_log_path.name}",
            f"- Agent 日志目录：{agent_log_dir.name}/",
            f"- 材料快照：{materials_path.name}",
            f"- 运行清单：{manifest_path.name}",
            f"- 模型快照：{model_info_path.name}",
            "",
            _build_evaluation_summary_markdown(evaluation),
        ]
        summary_path.write_text("\n".join(lines), encoding="utf-8")
