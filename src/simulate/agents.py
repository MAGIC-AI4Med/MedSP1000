from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .api import LLMClient

EVALUATION_CATEGORIES = [
    "PC",
    "MK",
    "SBP",
    "ICS",
    "PBLI",
    "PROF",
]

EVALUATION_REASONING_KEY = "reasoning"

# vLLM guided_json schema for examinee output. 只在 examinee.model 走 vLLM 路由（当前 medgemma）时由
# LLMClient.chat 透传给 vLLM；其它供应商客户端不会收到该字段。
EXAMINEE_GUIDED_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "speak": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "string"}},
        "eos": {"type": "boolean"},
    },
    "required": ["speak", "actions", "eos"],
    "additionalProperties": False,
}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _coerce_evaluation_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _normalize_evaluation_item_map(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, bool] = {}
    for key, item_value in value.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        normalized[key_text] = _coerce_evaluation_flag(item_value)
    return normalized


def _normalize_evaluation_reasoning(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_evaluation_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}

    normalized: dict[str, Any] = {
        EVALUATION_REASONING_KEY: _normalize_evaluation_reasoning(payload.get(EVALUATION_REASONING_KEY))
    }
    normalized.update(
        {
            category: _normalize_evaluation_item_map(payload.get(category))
            for category in EVALUATION_CATEGORIES
        }
    )

    return normalized


class BaseAgent:
    def __init__(self, client: LLMClient, name: str, system_prompt: str, model: str | None = None):
        self.client = client
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        self.call_logs: list[dict[str, Any]] = []

    def _request_label(self, metadata: dict[str, Any] | None = None) -> str:
        metadata = metadata or {}
        turn_index = metadata.get("turn_index")
        if turn_index is None:
            return f"agent:{self.name}"
        return f"agent:{self.name}:turn:{turn_index}"

    def _record_call(
        self,
        *,
        user_prompt: str,
        input_messages: list[dict[str, str]],
        raw_output: str,
        parsed_output: dict[str, Any],
        normalized_output: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.call_logs.append(
            {
                "call_index": len(self.call_logs) + 1,
                "agent": self.name,
                "model": self.model,
                "metadata": deepcopy(metadata or {}),
                "user_prompt": user_prompt,
                "input_messages": deepcopy(input_messages),
                "raw_output": raw_output,
                "parsed_output": deepcopy(parsed_output),
                "normalized_output": deepcopy(normalized_output),
            }
        )

    def _send_json(
        self,
        user_prompt: str,
        *,
        metadata: dict[str, Any] | None = None,
        guided_schema: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
        payload_messages = self.messages + [{"role": "user", "content": user_prompt}]
        payload, raw_text = self.client.chat_json(
            payload_messages,
            model=self.model,
            request_label=self._request_label(metadata),
            guided_schema=guided_schema,
        )
        self.messages.append({"role": "user", "content": user_prompt})
        self.messages.append({"role": "assistant", "content": raw_text})
        return payload, raw_text, payload_messages


def normalize_examinee_payload(payload: Any) -> dict[str, Any]:
    """把 examinee 原始 JSON 输出归一化为 {speak, actions, eos}。

    抽出为模块级函数，供 ExamineeAgent.respond 与 test_time 策略（合成/候选）共用，
    保证所有路径产出的动作结构完全一致。
    """
    if not isinstance(payload, dict):
        payload = {}
    speak = payload.get("speak", "")
    actions = payload.get("actions", [])
    if not isinstance(speak, str):
        speak = str(speak)
    if not isinstance(actions, list):
        actions = []
    return {
        "speak": speak.strip(),
        "actions": [str(item).strip() for item in actions if str(item).strip()],
        "eos": _coerce_bool(payload.get("eos", False)),
    }


class ExamineeAgent(BaseAgent):
    def respond(self, user_prompt: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload, raw_text, payload_messages = self._send_json(
            user_prompt,
            metadata=metadata,
            guided_schema=EXAMINEE_GUIDED_JSON_SCHEMA,
        )
        normalized = normalize_examinee_payload(payload)
        self._record_call(
            user_prompt=user_prompt,
            input_messages=payload_messages,
            raw_output=raw_text,
            parsed_output=payload,
            normalized_output=normalized,
            metadata=metadata,
        )
        return normalized

    def preview_candidate(
        self,
        user_prompt: str,
        *,
        temperature: float | None = None,
        request_label: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], str, list[dict[str, str]]]:
        """采样一个候选动作，但**不**改动 self.messages、**不**写 call_logs。

        供 test-time 策略（best-of-N 候选 / single 基线）使用：所有候选都以同一份
        干净历史为条件，互不影响；最终只有选中的那条通过 commit_turn 落入历史。
        返回 (normalized, payload, raw_text, payload_messages)；payload_messages 是本次
        实际发送给模型的完整消息列表，供 test_time 全量落日志用。
        """
        payload_messages = self.messages + [{"role": "user", "content": user_prompt}]
        payload, raw_text = self.client.chat_json(
            payload_messages,
            model=self.model,
            request_label=request_label or self._request_label({"tts": "candidate"}),
            guided_schema=EXAMINEE_GUIDED_JSON_SCHEMA,
            temperature=temperature,
        )
        return normalize_examinee_payload(payload), payload, raw_text, payload_messages

    def commit_turn(
        self,
        user_prompt: str,
        raw_text: str,
        *,
        parsed: dict[str, Any] | None = None,
        normalized: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """把一条已敲定的回合（干净 user_prompt + 最终动作）写入历史与 call_logs，
        **不**发起任何 API 调用。

        test-time 策略用它落地最终选中/合成出的动作：持久历史只增长「干净 prompt + 最终
        动作」一对，结构与普通跑完全一致，中间的候选/专家讨论一律不进上下文。
        """
        payload_messages = self.messages + [{"role": "user", "content": user_prompt}]
        self.messages.append({"role": "user", "content": user_prompt})
        self.messages.append({"role": "assistant", "content": raw_text})
        self._record_call(
            user_prompt=user_prompt,
            input_messages=payload_messages,
            raw_output=raw_text,
            parsed_output=parsed if parsed is not None else {},
            normalized_output=normalized if normalized is not None else {},
            metadata=metadata,
        )


_SP_ACTOR_PREFIX_RE = re.compile(r"^([^\s:：][^:：]{0,20})\s*[:：]\s*(.*)$", re.DOTALL)


class SPAgent(BaseAgent):
    def respond(self, user_prompt: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload, raw_text, payload_messages = self._send_json(user_prompt, metadata=metadata)

        raw_speak = payload.get("speak", [])
        if isinstance(raw_speak, str):
            speak_items = [raw_speak] if raw_speak.strip() else []
        elif isinstance(raw_speak, list):
            speak_items = [str(item) for item in raw_speak]
        else:
            speak_items = []
        speak = [item.strip() for item in speak_items if item and item.strip()]

        actors_present_raw = payload.get("actors_present", {})
        actors_present: dict[str, str] = {}
        if isinstance(actors_present_raw, dict):
            for key, value in actors_present_raw.items():
                key_text = str(key).strip()
                if not key_text:
                    continue
                value_text = "" if value is None else str(value).strip()
                actors_present[key_text] = value_text

        warnings: list[str] = []
        for entry in speak:
            match = _SP_ACTOR_PREFIX_RE.match(entry)
            if not match:
                continue
            actor = match.group(1).strip()
            if actor and actor not in actors_present:
                warnings.append(
                    f"speak entry uses actor prefix '{actor}' that is not in actors_present"
                )

        normalized: dict[str, Any] = {
            "speak": speak,
            "actors_present": actors_present,
        }
        if warnings:
            normalized["warnings"] = warnings
        self._record_call(
            user_prompt=user_prompt,
            input_messages=payload_messages,
            raw_output=raw_text,
            parsed_output=payload,
            normalized_output=normalized,
            metadata=metadata,
        )
        return normalized


class EnvironmentAgent(BaseAgent):
    def respond(self, user_prompt: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload, raw_text, payload_messages = self._send_json(user_prompt, metadata=metadata)

        feedback = payload.get("feedback", [])
        if not isinstance(feedback, list):
            feedback = []
        events = payload.get("events", [])
        if not isinstance(events, list):
            events = []

        assessments = payload.get("action_assessments", [])
        normalized_assessments: list[dict[str, Any]] = []
        if isinstance(assessments, list):
            for item in assessments:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "unsupported")).strip().lower()
                if status not in {"executed", "pending", "unsupported"}:
                    status = "unsupported"
                normalized_assessments.append(
                    {
                        "raw": str(item.get("raw", "")).strip(),
                        "interpreted_action": str(item.get("interpreted_action", "")).strip(),
                        "status": status,
                        "rationale": str(item.get("rationale", "")).strip(),
                    }
                )

        actors_present_raw = payload.get("actors_present", {})
        actors_present: dict[str, str] = {}
        if isinstance(actors_present_raw, dict):
            for key, value in actors_present_raw.items():
                key_text = str(key).strip()
                if not key_text:
                    continue
                value_text = "" if value is None else str(value).strip()
                actors_present[key_text] = value_text

        progress_index: int | None = None
        if "progress_index" in payload:
            try:
                progress_index = int(payload.get("progress_index"))
            except (TypeError, ValueError):
                progress_index = None
            if isinstance(progress_index, int) and progress_index < 0:
                progress_index = 0

        state_label = ""
        if "state_label" in payload:
            state_label = str(payload.get("state_label", "")).strip()

        normalized = {
            "feedback": [str(item).strip() for item in feedback if str(item).strip()],
            "events": [str(item).strip() for item in events if str(item).strip()],
            "actors_present": actors_present,
            "action_assessments": normalized_assessments,
            "patient_status": str(payload.get("patient_status", "")).strip(),
            "progress_index": progress_index,
            "state_label": state_label,
            "should_end": _coerce_bool(payload.get("should_end", False)),
            "completion_reason": str(payload.get("completion_reason", "")).strip(),
        }
        self._record_call(
            user_prompt=user_prompt,
            input_messages=payload_messages,
            raw_output=raw_text,
            parsed_output=payload,
            normalized_output=normalized,
            metadata=metadata,
        )
        return normalized


class SmartEnvironmentAgent(EnvironmentAgent):
    pass


class EvaluatorAgent(BaseAgent):
    def evaluate(self, user_prompt: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload, raw_text, payload_messages = self._send_json(user_prompt, metadata=metadata)
        normalized = _normalize_evaluation_payload(payload)
        self._record_call(
            user_prompt=user_prompt,
            input_messages=payload_messages,
            raw_output=raw_text,
            parsed_output=payload,
            normalized_output=normalized,
            metadata=metadata,
        )
        return normalized
