"""Examinee 端的 test-time scaling 策略（opt-in，默认关闭）。

设计要点（与用户确认的两条铁律 + 工程约束）：

1. **单一入参选策略**：config `agents.examinee.test_time.strategy` 决定用哪种策略，
   取值 off | single | bon | medagents。run_simulate_cases.sh 用 `--examinee-tts` 注入。

2. **零影响历史评测**：strategy == off（或无 test_time 块）时 controller 走原 respond
   路径，本模块**不被调用**，行为与历史评测字节级一致；激活时产物落 `__tts-<tag>`
   隔离 marker 子目录（见 status_marker.get_status_dir），不碰任何历史 run / marker / CSV。

3. **提交纪律**：无论一回合内采样/审议多少次，**只有最终敲定的那一个动作**通过
   examinee.commit_turn 进入持久对话历史（一回合 = 1 条 user + 1 条 final action，结构与
   普通跑一致）。所有候选 / 专家讨论只回传给 controller 落 agent_logs，**绝不进模型上下文**
   —— 既省钱又保证下一回合上下文干净。

4. **公平红线**：专家 / 选择器只以 examinee 自己的 system prompt（可见材料）+ 当前回合
   prompt 为输入，**绝不**接触 evaluator 材料 / rubric。

5. **固定 SP/env**：激活时 controller 把 LLMClient.default_temperature 设为 fixed_temperature
   （默认 0.0），使 SP/environment/evaluator 确定化；examinee 候选采样显式用更高温度。

策略概览：
  - single   : 单次采样（temp=fixed），作为与 bon/medagents 同 SP/env 设置的对照基线。
  - bon       : 每回合采 n 个候选（temp=examinee），self-critic 选 1。
  - medagents : run 开始时一次性选定 experts 个专科；每回合各专家给建议 → 合成动作 →
                consensus_rounds 轮共识投票/修订（全程临时），只提交最终动作。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_DOMAIN = "General Medicine"
_VALID_STRATEGIES = {"off", "single", "bon", "medagents"}


@dataclass
class TestTimeConfig:
    strategy: str = "off"
    n: int = 5  # bon 候选数
    experts: int = 5  # medagents 专科数
    consensus_rounds: int = 1  # medagents 共识修订轮数
    selector: str = "self_critic"  # bon 选择器
    examinee_temperature: float = 0.8  # 候选 / 专家采样温度（制造多样性）
    fixed_temperature: float = 0.0  # SP/env/eval + single/合成/选择 的确定化温度

    @property
    def active(self) -> bool:
        return self.strategy in _VALID_STRATEGIES and self.strategy != "off"

    @property
    def marker_tag(self) -> str:
        return marker_tag_from_strategy(
            self.strategy, n=self.n, experts=self.experts,
            consensus_rounds=self.consensus_rounds, selector=self.selector,
        )


def marker_tag_from_strategy(
    strategy: str,
    *,
    n: int = 5,
    experts: int = 5,
    consensus_rounds: int = 1,
    selector: str = "self_critic",
) -> str:
    """根据策略 + 参数算出 marker 子目录后缀（off 返回空串）。

    纯函数，无重依赖：供 status_marker 直接调用做隔离，无需实例化 LLMClient。
    """
    strategy = (strategy or "off").strip()
    if strategy in ("", "off"):
        return ""
    if strategy == "single":
        return "single"
    if strategy == "bon":
        tag = f"bon{int(n)}"
        if selector and selector != "self_critic":
            tag += f"-{_slug(selector)}"
        return tag
    if strategy == "medagents":
        return f"medagents{int(experts)}c{int(consensus_rounds)}"
    return _slug(strategy)


def _slug(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "-", str(text).strip()).strip("-")


def parse_test_time_config(agents_cfg: dict[str, Any] | None) -> TestTimeConfig:
    """从 config 的 agents 块解析 test_time 配置；缺省 / 非法 → strategy=off。"""
    agents_cfg = agents_cfg or {}
    examinee_cfg = agents_cfg.get("examinee") or {}
    if not isinstance(examinee_cfg, dict):
        return TestTimeConfig()
    raw = examinee_cfg.get("test_time")
    if not isinstance(raw, dict):
        return TestTimeConfig()
    strategy = str(raw.get("strategy", "off")).strip() or "off"
    if strategy not in _VALID_STRATEGIES:
        strategy = "off"
    defaults = TestTimeConfig()
    return TestTimeConfig(
        strategy=strategy,
        n=int(raw.get("n", defaults.n)),
        experts=int(raw.get("experts", defaults.experts)),
        consensus_rounds=int(raw.get("consensus_rounds", defaults.consensus_rounds)),
        selector=str(raw.get("selector", defaults.selector)).strip() or defaults.selector,
        examinee_temperature=float(raw.get("examinee_temperature", defaults.examinee_temperature)),
        fixed_temperature=float(raw.get("fixed_temperature", defaults.fixed_temperature)),
    )


def marker_tag_from_agents(agents_cfg: dict[str, Any] | None) -> str:
    return parse_test_time_config(agents_cfg).marker_tag


# ---------------------------------------------------------------------------
# 运行时执行器
# ---------------------------------------------------------------------------

def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    if value is None:
        return default
    return bool(value)


def _parse_domains(text: str, num_experts: int) -> list[str]:
    """从领域选定模型输出里解析专科列表，去重并补齐到 num_experts 个。"""
    line = (text or "").strip()
    if ":" in line:
        line = line.split(":", 1)[1]
    # 兼容多种分隔符
    parts = re.split(r"\s*[|,;\n]\s*", line)
    domains: list[str] = []
    for part in parts:
        name = part.strip().strip("-*0123456789. ").strip()
        if name and name.lower() not in {d.lower() for d in domains}:
            domains.append(name)
        if len(domains) >= num_experts:
            break
    while len(domains) < num_experts:
        domains.append(DEFAULT_DOMAIN if not domains else f"{DEFAULT_DOMAIN} {len(domains) + 1}")
    return domains[:num_experts]


def _action_to_text(normalized: dict[str, Any]) -> str:
    actions = "; ".join(normalized.get("actions") or []) or "(none)"
    return (
        f"Says to patient: {normalized.get('speak') or '(nothing)'}\n"
        f"Actions: {actions}\n"
        f"eos: {normalized.get('eos')}"
    )


def _record(
    calls: list[dict[str, Any]],
    *,
    role: str,
    request_label: str,
    messages: list[dict[str, str]],
    raw_output: str,
    parsed: Any = None,
    normalized: Any = None,
) -> None:
    """把一次 test-time 子调用的完整交互（发送消息 + 原始输出）记进 calls。

    与 BaseAgent._record_call 同精度（input_messages + raw_output），保证审计时能逐字
    看到每个专家/合成/共识/候选/选择器收到什么、回了什么。
    """
    calls.append(
        {
            "role": role,
            "request_label": request_label,
            "input_messages": [dict(m) for m in messages],
            "raw_output": raw_output,
            "parsed_output": parsed,
            "normalized_output": normalized,
        }
    )


def select_expert_domains(
    client, examinee, tt: TestTimeConfig, console=None
) -> tuple[list[str], dict[str, Any]]:
    """run 开始时一次性选定专科领域（medagents 用），整个 run 固定复用。

    返回 (domains, call_record)；call_record 是这次领域选定的完整交互，供 run 级日志。
    """
    from .prompts import build_tts_domain_selection_prompt

    user = build_tts_domain_selection_prompt(tt.experts)
    messages = [
        {"role": "system", "content": examinee.system_prompt},
        {"role": "user", "content": user},
    ]
    calls: list[dict[str, Any]] = []
    raw_output = ""
    try:
        raw_output = client.chat(
            messages,
            model=examinee.model,
            request_label="tts:domain_selection",
            temperature=tt.fixed_temperature,
        )
        domains = _parse_domains(raw_output, tt.experts)
    except Exception as exc:  # noqa: BLE001
        if console:
            console.warn(f"[test-time] domain selection failed, fallback to General Medicine: {exc}")
        raw_output = f"(domain selection failed: {exc})"
        domains = [DEFAULT_DOMAIN] * tt.experts
    _record(
        calls,
        role="domain_selection",
        request_label="tts:domain_selection",
        messages=messages,
        raw_output=raw_output,
        normalized=domains,
    )
    if console:
        console.info(f"[test-time] expert domains fixed for this run: {domains}")
    return domains, calls[0]


def _self_critic_select(client, examinee, turn_prompt, candidates, tt, calls):
    """让 examinee 模型在 N 个候选里自评选最优；失败回退候选 0。返回 (index, rationale)。"""
    from .prompts import build_tts_selector_prompt

    user = build_tts_selector_prompt(turn_prompt, candidates)
    messages = [
        {"role": "system", "content": examinee.system_prompt},
        {"role": "user", "content": user},
    ]
    raw_output = ""
    parsed: Any = None
    try:
        parsed, raw_output = client.chat_json(
            messages,
            model=examinee.model,
            request_label="tts:selector",
            temperature=tt.fixed_temperature,
        )
        idx = int(parsed.get("choice"))
        rationale = str(parsed.get("rationale", "")).strip()
        if not (0 <= idx < len(candidates)):
            idx, rationale = 0, f"(out-of-range choice, fell back to 0) {rationale}"
    except Exception as exc:  # noqa: BLE001
        raw_output = raw_output or f"(selector failed: {exc})"
        idx, rationale = 0, f"(selector failed, fell back to candidate 0: {exc})"
    _record(
        calls,
        role="selector",
        request_label="tts:selector",
        messages=messages,
        raw_output=raw_output,
        parsed=parsed,
        normalized={"choice": idx, "rationale": rationale},
    )
    return idx, rationale


def _run_single(client, examinee, turn_prompt, tt: TestTimeConfig, turn_metadata):
    calls: list[dict[str, Any]] = []
    normalized, payload, raw, messages = examinee.preview_candidate(
        turn_prompt, temperature=tt.fixed_temperature, request_label="tts:single"
    )
    _record(
        calls, role="single", request_label="tts:single",
        messages=messages, raw_output=raw, parsed=payload, normalized=normalized,
    )
    examinee.commit_turn(
        turn_prompt, raw, parsed=payload, normalized=normalized, metadata=turn_metadata
    )
    turn_log = {
        "turn_index": (turn_metadata or {}).get("turn_index"),
        "strategy": "single",
        "final": normalized,
        "calls": calls,
    }
    return normalized, turn_log


def _run_bon(client, examinee, turn_prompt, tt: TestTimeConfig, turn_metadata):
    calls: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for i in range(max(1, tt.n)):
        norm, payload, raw, messages = examinee.preview_candidate(
            turn_prompt, temperature=tt.examinee_temperature, request_label=f"tts:candidate:{i}"
        )
        candidates.append({"index": i, "normalized": norm, "payload": payload, "raw": raw})
        _record(
            calls, role=f"candidate:{i}", request_label=f"tts:candidate:{i}",
            messages=messages, raw_output=raw, parsed=payload, normalized=norm,
        )

    choice_idx, rationale = _self_critic_select(
        client, examinee, turn_prompt, candidates, tt, calls
    )
    chosen = candidates[choice_idx]
    examinee.commit_turn(
        turn_prompt,
        chosen["raw"],
        parsed=chosen["payload"],
        normalized=chosen["normalized"],
        metadata=turn_metadata,
    )
    turn_log = {
        "turn_index": (turn_metadata or {}).get("turn_index"),
        "strategy": "bon",
        "n": tt.n,
        "selector": tt.selector,
        "chosen_index": choice_idx,
        "rationale": rationale,
        "candidates": [
            {"index": c["index"], "normalized": c["normalized"]} for c in candidates
        ],
        "final": chosen["normalized"],
        "calls": calls,
    }
    return chosen["normalized"], turn_log


def _run_medagents(client, examinee, turn_prompt, tt, expert_domains, turn_metadata):
    from .agents import EXAMINEE_GUIDED_JSON_SCHEMA, normalize_examinee_payload
    from .prompts import (
        build_tts_consensus_prompt,
        build_tts_expert_analysis_prompt,
        build_tts_revision_note,
        build_tts_synthesis_prompt,
    )

    domains = expert_domains or [DEFAULT_DOMAIN] * tt.experts
    calls: list[dict[str, Any]] = []

    # 1. 各专科专家给本步建议（临时调用，不进上下文）
    opinions: dict[str, str] = {}
    for domain in domains:
        overlay, user = build_tts_expert_analysis_prompt(domain, turn_prompt)
        messages = [
            {"role": "system", "content": examinee.system_prompt + "\n\n" + overlay},
            {"role": "user", "content": user},
        ]
        label = f"tts:expert:{_slug(domain)}"
        try:
            text = client.chat(
                messages, model=examinee.model, request_label=label,
                temperature=tt.examinee_temperature,
            )
        except Exception as exc:  # noqa: BLE001
            text = f"(no opinion: {exc})"
        opinions[domain] = text.strip()
        _record(
            calls, role=f"expert:{domain}", request_label=label,
            messages=messages, raw_output=text,
        )

    # 2. 合成 → 共识投票/修订（最多 consensus_rounds 轮），全程临时
    revision_note = ""
    consensus_log: list[dict[str, Any]] = []
    normalized = payload = raw = None
    for round_i in range(tt.consensus_rounds + 1):
        syn_user = build_tts_synthesis_prompt(turn_prompt, opinions, revision_note)
        payload_messages = examinee.messages + [{"role": "user", "content": syn_user}]
        label = f"tts:synthesis:r{round_i}"
        payload, raw = client.chat_json(
            payload_messages, model=examinee.model, request_label=label,
            guided_schema=EXAMINEE_GUIDED_JSON_SCHEMA, temperature=tt.fixed_temperature,
        )
        normalized = normalize_examinee_payload(payload)
        _record(
            calls, role=f"synthesis:r{round_i}", request_label=label,
            messages=payload_messages, raw_output=raw, parsed=payload, normalized=normalized,
        )
        if round_i >= tt.consensus_rounds:
            break
        # 共识投票
        proposed_text = _action_to_text(normalized)
        advice: dict[str, str] = {}
        votes: dict[str, bool] = {}
        for domain in domains:
            overlay, user = build_tts_consensus_prompt(domain, proposed_text)
            messages = [
                {"role": "system", "content": examinee.system_prompt + "\n\n" + overlay},
                {"role": "user", "content": user},
            ]
            label = f"tts:consensus:{_slug(domain)}:r{round_i}"
            vpayload: Any = None
            vraw = ""
            try:
                vpayload, vraw = client.chat_json(
                    messages, model=examinee.model, request_label=label,
                    temperature=tt.fixed_temperature,
                )
                agree = _as_bool(vpayload.get("agree", True))
                votes[domain] = agree
                if not agree:
                    adv = str(vpayload.get("advice", "")).strip()
                    if adv:
                        advice[domain] = adv
            except Exception as exc:  # noqa: BLE001
                votes[domain] = True  # 解析失败按同意处理，不无谓拖长循环
                vraw = vraw or f"(consensus parse failed: {exc})"
            _record(
                calls, role=f"consensus:{domain}:r{round_i}", request_label=label,
                messages=messages, raw_output=vraw, parsed=vpayload,
                normalized={"agree": votes[domain], "advice": advice.get(domain, "")},
            )
        consensus_log.append({"round": round_i, "votes": votes, "advice": advice})
        if not advice:
            break  # 全票通过，提前结束
        revision_note = build_tts_revision_note(advice)

    examinee.commit_turn(
        turn_prompt, raw, parsed=payload, normalized=normalized, metadata=turn_metadata
    )
    turn_log = {
        "turn_index": (turn_metadata or {}).get("turn_index"),
        "strategy": "medagents",
        "experts": list(domains),
        "consensus_rounds": tt.consensus_rounds,
        "opinions": opinions,
        "consensus": consensus_log,
        "final": normalized,
        "calls": calls,
    }
    return normalized, turn_log


def run_examinee_test_time(
    *,
    client,
    examinee,
    turn_prompt: str,
    tt: TestTimeConfig,
    expert_domains: list[str] | None = None,
    turn_metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """test-time 激活时的 examinee 单回合入口。返回 (normalized_action, turn_log)。"""
    if tt.strategy == "single":
        return _run_single(client, examinee, turn_prompt, tt, turn_metadata)
    if tt.strategy == "bon":
        return _run_bon(client, examinee, turn_prompt, tt, turn_metadata)
    if tt.strategy == "medagents":
        return _run_medagents(
            client, examinee, turn_prompt, tt, expert_domains, turn_metadata
        )
    # 不应到达（active 时已分派）；兜底走 single 语义。
    return _run_single(client, examinee, turn_prompt, tt, turn_metadata)
