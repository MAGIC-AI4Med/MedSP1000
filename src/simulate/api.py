from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

import yaml

from .console import ConsoleLogger, preview_text


DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("config.yaml")


def _resolve_config_path(config_path: str | None = None) -> Path:
    if config_path is None:
        return DEFAULT_CONFIG_PATH

    candidate = Path(config_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    search_roots = [
        Path.cwd(),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
    ]
    for root in search_roots:
        resolved = (root / candidate).resolve()
        if resolved.exists():
            return resolved
    raise FileNotFoundError(f"配置文件不存在: {config_path}")


def load_config(config_path: str | None = None) -> dict[str, Any]:
    path = _resolve_config_path(config_path)
    with path.open("r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj) or {}


def _extract_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(item.get("text", ""))
            elif hasattr(item, "text"):
                chunks.append(getattr(item, "text", ""))
        return "".join(chunks)
    return str(content or "")


def extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型输出中未找到 JSON 对象")
    payload = text[start : end + 1]
    return json.loads(payload)


class LLMClient:
    def __init__(self, config: dict[str, Any]):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # noqa: BLE001
            raise ImportError(
                "未安装 openai 包，无法运行真实模拟。可以先使用 --validate-only 检查配置。"
            ) from exc

        self.config = config
        self._apply_proxy()
        api_config = config.get("api", {})
        simulation_config = config.get("simulation", {})
        self.default_model = api_config.get("model")
        # 默认采样温度：None 表示完全不向后端传 temperature（与历史评测行为字节级一致）。
        # 仅当 test-time 策略激活时，controller 会把它设成固定值（如 0.0）以确定化 SP/env/eval；
        # examinee 的候选采样再通过 chat(temperature=...) 显式覆盖为更高温度制造多样性。
        self.default_temperature: float | None = None
        self.debug = bool(simulation_config.get("debug", False))
        self.console = ConsoleLogger(
            enabled=bool(simulation_config.get("console_progress", True)),
            debug=self.debug,
        )
        timeout = float(api_config.get("timeout_seconds", 180))
        self.client = OpenAI(
            base_url=api_config.get("base_url"),
            api_key=api_config.get("api_key"),
            timeout=timeout,
        )
        baichuan_key = api_config.get("baichuan-api-key")
        baichuan_base_url = api_config.get("baichuan_base_url")
        if baichuan_key and baichuan_base_url:
            self._baichuan_client = OpenAI(
                base_url=baichuan_base_url,
                api_key=baichuan_key,
                timeout=timeout,
            )
        else:
            self._baichuan_client = None
        deepseek_key = api_config.get("deepseek-api-key")
        deepseek_base_url = api_config.get("deepseek_base_url")
        if deepseek_key and deepseek_base_url:
            self._deepseek_client = OpenAI(
                base_url=deepseek_base_url,
                api_key=deepseek_key,
                timeout=timeout,
            )
        else:
            self._deepseek_client = None
        qwen_key = api_config.get("qwen-api-key")
        qwen_base_url = api_config.get("qwen_base_url")
        if qwen_key and qwen_base_url:
            self._qwen_client = OpenAI(
                base_url=qwen_base_url,
                api_key=qwen_key,
                timeout=timeout,
            )
        else:
            self._qwen_client = None
        # MEDGEMMA_BASE_URL 环境变量覆盖 yaml（srun 分配的 vLLM 节点 hostname 是动态的）；
        # 支持逗号分隔多端点做 DP（数据并行），LLMClient round-robin 分发；
        # 每个 simulate.runner 子进程独立持有 LLMClient，起始位置用 PID hash 错开避免冷启都打 endpoint 0。
        medgemma_base_url = os.environ.get("MEDGEMMA_BASE_URL") or api_config.get("medgemma_base_url")
        medgemma_key = api_config.get("medgemma-api-key") or "EMPTY"
        self._medgemma_clients: list = []
        self._medgemma_hosts: list[str] = []
        self._medgemma_rr_counter = 0
        if medgemma_base_url:
            urls = [u.strip() for u in str(medgemma_base_url).split(",") if u.strip()]
            from urllib.parse import urlparse
            for u in urls:
                self._medgemma_clients.append(
                    OpenAI(base_url=u, api_key=medgemma_key, timeout=timeout)
                )
                host = urlparse(u).hostname
                if host:
                    self._medgemma_hosts.append(host)
            self._medgemma_client = self._medgemma_clients[0] if self._medgemma_clients else None
            self._medgemma_base_url = urls[0] if urls else None
            if self._medgemma_clients:
                self._medgemma_rr_counter = os.getpid() % len(self._medgemma_clients)
        else:
            self._medgemma_client = None
            self._medgemma_base_url = None
        self._add_medgemma_to_no_proxy()

    def _select_client(self, model: str | None):
        resolved = model or self.default_model or ""
        lowered = resolved.lower()
        if lowered.startswith("baichuan"):
            if self._baichuan_client is None:
                raise RuntimeError(
                    f"模型 {resolved!r} 需要百川端点，但 config.api 缺少 baichuan_base_url 或 baichuan-api-key"
                )
            return self._baichuan_client
        if lowered.startswith("deepseek"):
            if self._deepseek_client is None:
                raise RuntimeError(
                    f"模型 {resolved!r} 需要 DeepSeek 端点，但 config.api 缺少 deepseek_base_url 或 deepseek-api-key"
                )
            return self._deepseek_client
        if lowered.startswith("qwen"):
            if self._qwen_client is None:
                raise RuntimeError(
                    f"模型 {resolved!r} 需要 Qwen / DashScope 端点，但 config.api 缺少 qwen_base_url 或 qwen-api-key"
                )
            return self._qwen_client
        if lowered.startswith("medgemma"):
            if not self._medgemma_clients:
                raise RuntimeError(
                    f"模型 {resolved!r} 需要本地 vLLM 端点，但 config.api.medgemma_base_url 与环境变量 MEDGEMMA_BASE_URL 均为空"
                )
            if len(self._medgemma_clients) == 1:
                return self._medgemma_clients[0]
            idx = self._medgemma_rr_counter % len(self._medgemma_clients)
            self._medgemma_rr_counter += 1
            return self._medgemma_clients[idx]
        return self.client

    def _no_thinking_extra_body(self, model: str | None) -> dict[str, Any]:
        # 全局策略：能关 thinking 就关。按 model 前缀给出对应供应商的关闭参数。
        # - deepseek 官方 API（/v1）: thinking={"type":"disabled"} 关 v4-pro/v4-flash 的思考链
        # - qwen DashScope OpenAI 兼容: enable_thinking=False 关 Qwen3.x 混合思考
        # - 其他（gpt / claude / baichuan / gemini 等）默认就不思考，不需要透传
        lowered = (model or self.default_model or "").lower()
        if lowered.startswith("deepseek"):
            return {"thinking": {"type": "disabled"}}
        if lowered.startswith("qwen"):
            return {"enable_thinking": False}
        return {}

    def _apply_proxy(self) -> None:
        proxy_url = self.config.get("proxy", {}).get("url")
        if not proxy_url:
            return
        os.environ["http_proxy"] = proxy_url
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["https_proxy"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url

    def _add_medgemma_to_no_proxy(self) -> None:
        """把 medgemma vLLM 服务端 host 加进 NO_PROXY，防止集群代理拦截内网调用。

        支持多端点（DP）：把每个端点的 host 都加进 NO_PROXY。
        """
        if not self._medgemma_hosts:
            return
        entries = {"localhost", "127.0.0.1", *self._medgemma_hosts}
        existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
        for token in existing.split(","):
            token = token.strip()
            if token:
                entries.add(token)
        combined = ",".join(sorted(entries))
        os.environ["NO_PROXY"] = combined
        os.environ["no_proxy"] = combined

    def _request_label(self, request_label: str | None, model: str | None) -> str:
        resolved_model = model or self.default_model or "default-model"
        return request_label or f"chat:{resolved_model}"

    def chat(
        self,
        messages: Iterable[dict[str, str]],
        *,
        model: str | None = None,
        request_label: str | None = None,
        guided_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        payload_messages = list(messages)
        label = self._request_label(request_label, model)
        started_at = time.perf_counter()
        message_count = len(payload_messages)
        input_chars = sum(len(str(item.get("content", ""))) for item in payload_messages)
        self.console.info(
            f"{label} -> API request started "
            f"(model={model or self.default_model}, messages={message_count}, chars={input_chars})"
        )
        if self.debug and payload_messages:
            last_message = payload_messages[-1]
            self.console.debug(
                f"{label} last message preview: {preview_text(str(last_message.get('content', '')), limit=200)}"
            )
        try:
            active_client = self._select_client(model)
            create_kwargs: dict[str, Any] = {
                "model": model or self.default_model,
                "messages": payload_messages,
            }
            extra_body = self._no_thinking_extra_body(model)
            # guided_json 只对 vLLM 服务端有效（当前只 medgemma 走 vLLM）；其它供应商收到会报错
            resolved_model = (model or self.default_model or "").lower()
            if guided_schema and resolved_model.startswith("medgemma"):
                extra_body = {**extra_body, "guided_json": guided_schema}
            if extra_body:
                create_kwargs["extra_body"] = extra_body
            # 温度：显式入参优先，否则回退到 client.default_temperature；两者都为 None 时
            # 不传 temperature（保持历史评测的厂商默认行为，零影响）。
            resolved_temperature = (
                temperature if temperature is not None else self.default_temperature
            )
            if resolved_temperature is not None:
                create_kwargs["temperature"] = resolved_temperature
            completion = active_client.chat.completions.create(**create_kwargs)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - started_at
            self.console.error(
                f"{label} -> API request failed after {elapsed:.2f}s: {type(exc).__name__}: {exc}"
            )
            raise
        content = _extract_content(completion.choices[0].message.content)
        elapsed = time.perf_counter() - started_at
        self.console.info(
            f"{label} <- API response received after {elapsed:.2f}s (output_chars={len(content)})"
        )
        if self.debug:
            self.console.debug(f"{label} output preview: {preview_text(content, limit=200)}")
        return content

    def chat_json(
        self,
        messages: Iterable[dict[str, str]],
        *,
        model: str | None = None,
        retries: int = 2,
        request_label: str | None = None,
        guided_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> tuple[dict[str, Any], str]:
        last_error: Exception | None = None
        payload_messages = list(messages)
        label = self._request_label(request_label, model)
        for attempt in range(retries + 1):
            raw_text = self.chat(
                payload_messages,
                model=model,
                request_label=f"{label} [attempt {attempt + 1}/{retries + 1}]",
                guided_schema=guided_schema,
                temperature=temperature,
            )
            try:
                return extract_json_object(raw_text), raw_text
            except Exception as error:  # noqa: BLE001
                last_error = error
                self.console.warn(
                    f"{label} JSON parse failed on attempt {attempt + 1}/{retries + 1}: "
                    f"{type(error).__name__}: {error}"
                )
                # 必须先回放上一条 assistant 回复，再追加纠错 user 消息，
                # 否则 payload_messages 结尾会出现 user->user 连续同角色：
                # vLLM/Gemma 等严格交替的后端会直接 400 "roles must alternate"，
                # 重试链彻底失效（对宽松厂商也无害，且让模型看到自己的坏输出便于自纠）。
                payload_messages = payload_messages + [
                    {"role": "assistant", "content": raw_text},
                    {
                        "role": "user",
                        "content": (
                            "你的上一条回复不是合法 JSON。请只输出一个 JSON 对象，"
                            "不要加代码块、解释或多余文字。"
                        ),
                    },
                ]
        raise ValueError(f"无法解析模型 JSON 输出: {last_error}")


def query(input_messages: list[dict[str, str]], config_path: str | None = None) -> str:
    config = load_config(config_path)
    client = LLMClient(config)
    return client.chat(input_messages)
