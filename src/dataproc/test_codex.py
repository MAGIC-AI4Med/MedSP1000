#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import sys
import argparse
from pathlib import Path
from typing import Any


def _ensure_local_sdk_path() -> None:
    try:
        import codex_app_server  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    local_src = Path(__file__).resolve().parent / "codex" / "sdk" / "python" / "src"
    if local_src.exists():
        sys.path.insert(0, str(local_src))
        return

    raise ModuleNotFoundError(
        "Could not import codex_app_server. Install SDK with:\n"
        "  python -m pip install -e codex/sdk/python\n"
        "or ensure local path exists: codex/sdk/python/src"
    )


_ensure_local_sdk_path()

from codex_app_server import AppServerConfig, AskForApproval, Codex, SandboxMode, TextInput


DEFAULT_WORKING_DIRECTORY = (
    "/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi/mededportal_586"
)
DEFAULT_PROMPT_FILE = "prompt.yaml"
DEFAULT_SANDBOX_MODE = "danger-full-access"
DEFAULT_APPROVAL_POLICY = "never"
SCRIPT_DIR = Path(__file__).resolve().parent

# SDK 流连接抖动时会发出 "Reconnecting... N/5" 错误事件；这本身是 SDK 内部重试，
# 不应被当作致命错误。这里给一个安全阀：连续 reconnect 超过该次数才真正放弃。
MAX_CONSEC_RECONNECTS = 10

WORKING_DIRECTORY = os.getenv("CODEX_WORKING_DIRECTORY", DEFAULT_WORKING_DIRECTORY)
PROMPT_FILE = os.getenv("CODEX_PROMPT_FILE", DEFAULT_PROMPT_FILE)
SANDBOX_MODE = os.getenv("CODEX_SANDBOX_MODE", DEFAULT_SANDBOX_MODE)
APPROVAL_POLICY = os.getenv("CODEX_APPROVAL_POLICY", DEFAULT_APPROVAL_POLICY)
CODEX_BIN = os.getenv("CODEX_BIN")

PHASES = ("phase1", "phase2", "phase3")


def summarize_text(text: str, max_len: int = 220) -> str:
    clean = text.strip()
    if len(clean) <= max_len:
        return clean
    return f"{clean[:max_len]}..."


def enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def model_to_json(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_simple_yaml_map(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current_key: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current_key, buffer
        if current_key is None:
            return
        value = "\n".join(buffer).strip()
        result[current_key] = strip_quotes(value)
        current_key = None
        buffer = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not line.startswith((" ", "\t")) and ":" in line:
            flush()
            key, value = line.split(":", 1)
            current_key = key.strip()
            buffer = [value.strip()]
            continue

        if current_key is not None:
            buffer.append(stripped)

    flush()
    return result


def load_prompts(file_path: str) -> dict[str, str]:
    text = Path(file_path).read_text(encoding="utf-8")

    parsed: Any
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(text)
    except ModuleNotFoundError:
        parsed = parse_simple_yaml_map(text)

    if not isinstance(parsed, dict):
        raise ValueError("prompt.yaml must be a key-value YAML object")

    prompts: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        prompts[key] = value.strip()

    for phase in PHASES:
        if not prompts.get(phase):
            raise ValueError(f"missing prompt: {phase}")
    return prompts


def format_file_changes(item_raw: dict[str, Any]) -> str:
    parts: list[str] = []
    for change in item_raw.get("changes", []) or []:
        if not isinstance(change, dict):
            continue
        kind = change.get("kind")
        if isinstance(kind, dict):
            kind_text = str(kind.get("type", "unknown"))
        else:
            kind_text = str(kind)
        path = str(change.get("path", ""))
        parts.append(f"{kind_text}:{path}")
    return ", ".join(parts)


def detect_codex_bin() -> str:
    if CODEX_BIN:
        return CODEX_BIN

    base = Path(__file__).resolve().parent
    vendor_bins = sorted(
        base.glob("node_modules/@openai/codex-*/vendor/*/codex/codex"),
    )
    candidates = list(vendor_bins)
    candidates.extend(
        [
            base / "codex" / "target" / "release" / "codex",
            base / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
        ]
    )

    from_path = shutil.which("codex")
    if from_path:
        candidates.append(Path(from_path))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        "Could not locate codex binary. Set CODEX_BIN explicitly, for example:\n"
        "  CODEX_BIN=/mnt/petrelfs/liangcheng/local/node/bin/codex"
    )


def resolve_prompt_file(prompt_file: str) -> str:
    prompt_path = Path(prompt_file).expanduser()
    if prompt_path.is_absolute():
        return str(prompt_path)

    if prompt_path.exists():
        return str(prompt_path.resolve())

    script_relative = SCRIPT_DIR / prompt_path
    if script_relative.exists():
        return str(script_relative.resolve())

    return str(prompt_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex phases for a single case directory.")
    parser.add_argument(
        "-w",
        "--working-directory",
        default=WORKING_DIRECTORY,
        help="Case directory to process. Defaults to CODEX_WORKING_DIRECTORY or built-in default.",
    )
    parser.add_argument(
        "-p",
        "--prompt-file",
        default=PROMPT_FILE,
        help="Prompt YAML path. Defaults to CODEX_PROMPT_FILE or prompt.yaml.",
    )
    parser.add_argument(
        "--sandbox-mode",
        default=SANDBOX_MODE,
        help="Sandbox mode. Defaults to CODEX_SANDBOX_MODE or danger-full-access.",
    )
    parser.add_argument(
        "--approval-policy",
        default=APPROVAL_POLICY,
        help="Approval policy. Defaults to CODEX_APPROVAL_POLICY or never.",
    )
    parser.add_argument(
        "--codex-bin",
        default=CODEX_BIN,
        help="Optional explicit codex binary path. Overrides CODEX_BIN.",
    )
    return parser.parse_args()


def run_phase_with_progress(thread: Any, phase: str, prompt: str) -> None:
    print(f"\n===== {phase} START =====")
    turn = thread.turn(TextInput(prompt))
    stream = turn.stream()

    latest_usage: Any = None
    final_response = ""
    consec_reconnects = 0

    try:
        for event in stream:
            method = event.method
            payload = event.payload

            # 收到任何非 error 事件就清零 reconnect 计数（处理"累积型"而非"连续型"）
            if method != "error":
                consec_reconnects = 0

            if method == "thread/started":
                thread_id = getattr(getattr(payload, "thread", None), "id", "unknown")
                print(f"[{phase}] thread.started id={thread_id}")
                continue

            if method == "turn/started":
                print(f"[{phase}] turn.started")
                continue

            if method == "item/started":
                item_raw = model_to_json(getattr(payload, "item", None)) or {}
                item_type = item_raw.get("type")
                if item_type == "commandExecution":
                    print(f"[{phase}] command.start: {item_raw.get('command', '')}")
                elif item_type == "mcpToolCall":
                    server = item_raw.get("server", "")
                    tool = item_raw.get("tool", "")
                    print(f"[{phase}] mcp.start: {server}/{tool}")
                else:
                    print(f"[{phase}] item.start: {item_type}")
                continue

            if method == "item/commandExecution/outputDelta":
                delta = getattr(payload, "delta", "")
                if isinstance(delta, str) and delta.strip():
                    print(f"[{phase}] command.output: {summarize_text(delta)}")
                continue

            if method == "item/completed":
                item_raw = model_to_json(getattr(payload, "item", None)) or {}
                item_type = item_raw.get("type")
                if item_type == "commandExecution":
                    status = item_raw.get("status", "unknown")
                    exit_code = item_raw.get("exitCode", item_raw.get("exit_code", "n/a"))
                    print(f"[{phase}] command.end: status={status} exit={exit_code}")
                elif item_type == "fileChange":
                    changed = format_file_changes(item_raw)
                    print(f"[{phase}] file_change: {summarize_text(changed, 320)}")
                elif item_type == "agentMessage":
                    final_response = str(item_raw.get("text", ""))
                    print(f"[{phase}] agent_message: {summarize_text(final_response)}")
                elif item_type == "reasoning":
                    content = item_raw.get("content")
                    if isinstance(content, list):
                        reason_text = " ".join(str(x) for x in content if x).strip()
                    else:
                        reason_text = str(content or "")
                    print(f"[{phase}] reasoning: {summarize_text(reason_text)}")
                else:
                    print(f"[{phase}] item.end: {item_type}")
                continue

            if method == "thread/tokenUsage/updated":
                latest_usage = getattr(payload, "token_usage", None)
                continue

            if method == "turn/completed":
                status = enum_value(getattr(getattr(payload, "turn", None), "status", "unknown"))
                if latest_usage is not None and getattr(latest_usage, "last", None) is not None:
                    last = latest_usage.last
                    print(
                        f"[{phase}] turn.completed usage in={getattr(last, 'input_tokens', 0)} "
                        f"out={getattr(last, 'output_tokens', 0)} "
                        f"cached={getattr(last, 'cached_input_tokens', 0)}"
                    )
                else:
                    print(f"[{phase}] turn.completed status={status}")

                if status == "failed":
                    turn_error = getattr(getattr(payload, "turn", None), "error", None)
                    message = getattr(turn_error, "message", "turn failed")
                    raise RuntimeError(f"[{phase}] turn.failed: {message}")
                continue

            if method == "error":
                error = getattr(payload, "error", None)
                message = getattr(error, "message", "unknown stream error")
                lower = message.lower()
                if "reconnecting" in lower or "reconnect" in lower:
                    consec_reconnects += 1
                    print(
                        f"[{phase}] stream.warn (transient #{consec_reconnects}): {message}"
                    )
                    if consec_reconnects > MAX_CONSEC_RECONNECTS:
                        raise RuntimeError(
                            f"[{phase}] stream.error: gave up after "
                            f"{consec_reconnects} consecutive reconnect events"
                        )
                    continue
                raise RuntimeError(f"[{phase}] stream.error: {message}")
    finally:
        stream.close()

    print(f"\n[{phase}] final response:\n{final_response}")
    print(f"===== {phase} END =====")


def main() -> None:
    args = parse_args()
    working_directory = str(Path(args.working_directory).expanduser().resolve())
    prompt_file = resolve_prompt_file(args.prompt_file)
    sandbox_mode_name = args.sandbox_mode
    approval_policy_name = args.approval_policy

    prompts = load_prompts(prompt_file)
    sandbox_mode = SandboxMode(sandbox_mode_name)
    approval_policy = AskForApproval.model_validate(approval_policy_name)
    codex_bin = str(Path(args.codex_bin).expanduser()) if args.codex_bin else detect_codex_bin()

    print("Config:")
    print(f"  working_directory={working_directory}")
    print(f"  prompt_file={prompt_file}")
    print(f"  sandbox_mode={sandbox_mode_name}")
    print(f"  approval_policy={approval_policy_name}")
    print(f"  codex_bin={codex_bin}")

    with Codex(config=AppServerConfig(codex_bin=codex_bin)) as codex:
        thread = codex.thread_start(
            cwd=working_directory,
            sandbox=sandbox_mode,
            approval_policy=approval_policy,
        )

        for phase in PHASES:
            run_phase_with_progress(thread, phase, prompts[phase])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
