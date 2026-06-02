#!/usr/bin/env python3
"""
Single-phase codex runner for the rubric-extraction pipeline.

Forked from z-stat-case-tags/code/run_codex_classify.py. Key differences:
  - No taxonomy substitution. Instead, the prompt's placeholders are filled
    from the scenario directory passed via --working-directory:
      {CASE_ID}          basename of the scenario directory's PARENT
                          (e.g. mededportal_10011)
      {SCENARIO}         basename of the scenario directory (e.g. scenario2)
      {SCENARIO_DIR}     absolute path of the scenario directory
      {OUTPUT_JSON_PATH} <pipeline>/rubrics/<CASE_ID>_<SCENARIO>.json
  - Codex still sees the scenario directory as its cwd so it can ls/cat the
    evaluator/ files; the prompt instructs it to read ONLY evaluator/ and to
    write its single JSON output to the absolute OUTPUT_JSON_PATH (never
    inside the scenario directory).

This script does NOT touch a-simulate, existing runs, or the scenario
materials — codex only reads evaluator/ and writes to z-rubric/rubrics/.

Usage:
  python run_codex_extract_rubric.py --working-directory <scenario_dir> \
      --prompt-file <yaml> --prompt-key judgment
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


def _ensure_local_sdk_path() -> None:
    try:
        import codex_app_server  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    candidates = [
        Path(__file__).resolve().parents[2] / "z-codex-agent-sdk" / "codex" / "sdk" / "python" / "src",
    ]
    for c in candidates:
        if c.exists():
            sys.path.insert(0, str(c))
            return

    raise ModuleNotFoundError(
        "Could not import codex_app_server. Install SDK with:\n"
        "  python -m pip install -e z-codex-agent-sdk/codex/sdk/python\n"
        "or ensure local path exists."
    )


_ensure_local_sdk_path()

from codex_app_server import AppServerConfig, AskForApproval, Codex, SandboxMode, TextInput


SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parent
RUBRICS_DIR = PIPELINE_DIR / "rubrics"

DEFAULT_PROMPT_FILE = str(SCRIPT_DIR / "extract_rubric_prompt.yaml")
DEFAULT_PROMPT_KEY = "judgment"
DEFAULT_SANDBOX_MODE = "danger-full-access"
DEFAULT_APPROVAL_POLICY = "never"

SANDBOX_MODE_ENV = os.getenv("CODEX_SANDBOX_MODE", DEFAULT_SANDBOX_MODE)
APPROVAL_POLICY_ENV = os.getenv("CODEX_APPROVAL_POLICY", DEFAULT_APPROVAL_POLICY)
CODEX_BIN_ENV = os.getenv("CODEX_BIN")


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


def scenario_identity(working_directory: str) -> tuple[str, str, str, Path]:
    """Return (case_id, scenario, scenario_dir_abs, output_json_path).

    case_id  = basename of the scenario directory's parent
    scenario = basename of the scenario directory
    output   = <pipeline>/rubrics/<case_id>_<scenario>.json
    """
    scenario_dir = Path(working_directory).expanduser().resolve()
    scenario = scenario_dir.name
    case_id = scenario_dir.parent.name
    output_path = RUBRICS_DIR / f"{case_id}_{scenario}.json"
    return case_id, scenario, str(scenario_dir), output_path


def load_prompt(file_path: str, prompt_key: str, working_directory: str) -> str:
    text = Path(file_path).read_text(encoding="utf-8")
    parsed: Any
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(text)
    except ModuleNotFoundError:
        parsed = parse_simple_yaml_map(text)

    if not isinstance(parsed, dict):
        raise ValueError(f"{file_path}: must be a key-value YAML object")

    value = parsed.get(prompt_key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{file_path}: missing or empty prompt key {prompt_key!r}. "
            f"Available keys: {sorted(parsed.keys())}"
        )

    case_id, scenario, scenario_dir, output_path = scenario_identity(working_directory)
    rendered = value.strip()
    rendered = rendered.replace("{OUTPUT_JSON_PATH}", str(output_path))
    rendered = rendered.replace("{SCENARIO_DIR}", scenario_dir)
    rendered = rendered.replace("{CASE_ID}", case_id)
    rendered = rendered.replace("{SCENARIO}", scenario)
    return rendered


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
    if CODEX_BIN_ENV:
        return CODEX_BIN_ENV

    base = Path(__file__).resolve().parents[2] / "z-codex-agent-sdk"
    vendor_bins = sorted(
        base.glob("node_modules/@openai/codex-*/vendor/*/codex/codex"),
    )
    candidates: list[Path] = list(vendor_bins)
    candidates.extend([
        base / "codex" / "target" / "release" / "codex",
        base / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
    ])

    from_path = shutil.which("codex")
    if from_path:
        candidates.append(Path(from_path))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        "Could not locate codex binary. Set CODEX_BIN explicitly, e.g.:\n"
        "  CODEX_BIN=/mnt/petrelfs/liangcheng/local/node/bin/codex"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a single-phase Codex rubric extraction for one scenario directory."
    )
    parser.add_argument(
        "-w", "--working-directory", required=True,
        help="Scenario directory to inspect (will be codex cwd).",
    )
    parser.add_argument(
        "-p", "--prompt-file", default=DEFAULT_PROMPT_FILE,
        help=f"YAML prompt file. Default: {DEFAULT_PROMPT_FILE}",
    )
    parser.add_argument(
        "-k", "--prompt-key", default=DEFAULT_PROMPT_KEY,
        help=f"Which key in the YAML to run. Default: {DEFAULT_PROMPT_KEY}",
    )
    parser.add_argument("--sandbox-mode", default=SANDBOX_MODE_ENV)
    parser.add_argument("--approval-policy", default=APPROVAL_POLICY_ENV)
    parser.add_argument("--codex-bin", default=CODEX_BIN_ENV)
    return parser.parse_args()


def run_single_phase(thread: Any, prompt_key: str, prompt: str) -> str:
    print(f"\n===== {prompt_key} START =====")
    turn = thread.turn(TextInput(prompt))
    stream = turn.stream()

    latest_usage: Any = None
    final_response = ""

    try:
        for event in stream:
            method = event.method
            payload = event.payload

            if method == "thread/started":
                thread_id = getattr(getattr(payload, "thread", None), "id", "unknown")
                print(f"[{prompt_key}] thread.started id={thread_id}")
                continue

            if method == "turn/started":
                print(f"[{prompt_key}] turn.started")
                continue

            if method == "item/started":
                item_raw = model_to_json(getattr(payload, "item", None)) or {}
                item_type = item_raw.get("type")
                if item_type == "commandExecution":
                    print(f"[{prompt_key}] command.start: {item_raw.get('command', '')}")
                elif item_type == "mcpToolCall":
                    server = item_raw.get("server", "")
                    tool = item_raw.get("tool", "")
                    print(f"[{prompt_key}] mcp.start: {server}/{tool}")
                else:
                    print(f"[{prompt_key}] item.start: {item_type}")
                continue

            if method == "item/commandExecution/outputDelta":
                delta = getattr(payload, "delta", "")
                if isinstance(delta, str) and delta.strip():
                    print(f"[{prompt_key}] command.output: {summarize_text(delta)}")
                continue

            if method == "item/completed":
                item_raw = model_to_json(getattr(payload, "item", None)) or {}
                item_type = item_raw.get("type")
                if item_type == "commandExecution":
                    status = item_raw.get("status", "unknown")
                    exit_code = item_raw.get("exitCode", item_raw.get("exit_code", "n/a"))
                    print(f"[{prompt_key}] command.end: status={status} exit={exit_code}")
                elif item_type == "fileChange":
                    changed = format_file_changes(item_raw)
                    print(f"[{prompt_key}] file_change: {summarize_text(changed, 320)}")
                elif item_type == "agentMessage":
                    final_response = str(item_raw.get("text", ""))
                    print(f"[{prompt_key}] agent_message: {summarize_text(final_response)}")
                elif item_type == "reasoning":
                    content = item_raw.get("content")
                    if isinstance(content, list):
                        reason_text = " ".join(str(x) for x in content if x).strip()
                    else:
                        reason_text = str(content or "")
                    print(f"[{prompt_key}] reasoning: {summarize_text(reason_text)}")
                else:
                    print(f"[{prompt_key}] item.end: {item_type}")
                continue

            if method == "thread/tokenUsage/updated":
                latest_usage = getattr(payload, "token_usage", None)
                continue

            if method == "turn/completed":
                status = enum_value(getattr(getattr(payload, "turn", None), "status", "unknown"))
                if latest_usage is not None and getattr(latest_usage, "last", None) is not None:
                    last = latest_usage.last
                    print(
                        f"[{prompt_key}] turn.completed usage in={getattr(last, 'input_tokens', 0)} "
                        f"out={getattr(last, 'output_tokens', 0)} "
                        f"cached={getattr(last, 'cached_input_tokens', 0)}"
                    )
                else:
                    print(f"[{prompt_key}] turn.completed status={status}")

                if status == "failed":
                    turn_error = getattr(getattr(payload, "turn", None), "error", None)
                    message = getattr(turn_error, "message", "turn failed")
                    raise RuntimeError(f"[{prompt_key}] turn.failed: {message}")
                continue

            if method == "error":
                error = getattr(payload, "error", None)
                message = getattr(error, "message", "unknown stream error")
                raise RuntimeError(f"[{prompt_key}] stream.error: {message}")
    finally:
        stream.close()

    print(f"\n[{prompt_key}] final response:\n{final_response}")
    print(f"===== {prompt_key} END =====")
    return final_response


def main() -> None:
    args = parse_args()
    working_directory = str(Path(args.working_directory).expanduser().resolve())
    prompt_file = str(Path(args.prompt_file).expanduser().resolve())
    prompt = load_prompt(prompt_file, args.prompt_key, working_directory)

    RUBRICS_DIR.mkdir(parents=True, exist_ok=True)

    sandbox_mode = SandboxMode(args.sandbox_mode)
    approval_policy = AskForApproval.model_validate(args.approval_policy)
    codex_bin = (
        str(Path(args.codex_bin).expanduser()) if args.codex_bin else detect_codex_bin()
    )

    case_id, scenario, scenario_dir, output_path = scenario_identity(working_directory)

    print("Config:")
    print(f"  working_directory={working_directory}")
    print(f"  case_id={case_id}  scenario={scenario}")
    print(f"  output_json={output_path}")
    print(f"  prompt_file={prompt_file}")
    print(f"  prompt_key={args.prompt_key}")
    print(f"  sandbox_mode={args.sandbox_mode}")
    print(f"  approval_policy={args.approval_policy}")
    print(f"  codex_bin={codex_bin}")

    with Codex(config=AppServerConfig(codex_bin=codex_bin)) as codex:
        thread = codex.thread_start(
            cwd=working_directory,
            sandbox=sandbox_mode,
            approval_policy=approval_policy,
        )
        run_single_phase(thread, args.prompt_key, prompt)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
