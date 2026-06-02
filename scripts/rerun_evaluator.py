"""基于已有 run 的 transcript / action_history / 状态摘要，单独重跑 evaluator。

复用 `agent_logs/evaluator_calls.json` 里保存的原始 user_prompt 和 metadata，
唯一变量是 evaluator system prompt（来自 simulate.prompts.build_evaluator_system_prompt）。
新输出落到 <run_dir>/<out_name>/，不动原 final_evaluation.json。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

for _var in ("ALL_PROXY", "all_proxy"):
    os.environ.pop(_var, None)

from simulate.agents import EvaluatorAgent
from simulate.api import LLMClient, load_config
from simulate.case_loader import load_case
from simulate.controller import _count_evaluation_items
from simulate.prompts import build_evaluator_system_prompt


SHAREABLE_FILES = (
    "turns.json",
    "execution_log.json",
    "execution_log.jsonl",
    "transcript.json",
    "action_history.json",
    "materials.json",
    "run_models.json",
    "summary.md",
)


def register_as_run(
    orig_run_dir: Path,
    eval_rerun_dir: Path,
    parsed_output: dict,
    out_name: str,
) -> Path:
    """把回放结果伪装成一个兄弟 run，让 dashboard 自动发现。

    关键点：顶层 final_evaluation.json 用 parsed_output（即模型按 ACGME 6 维度的
    原始输出），绕过 simulate/agents.py 仍是旧 8 类的 _normalize_evaluation_payload。
    """
    parent = orig_run_dir.parent
    new_name = f"{orig_run_dir.name}__{out_name}"
    new_dir = parent / new_name
    new_dir.mkdir(parents=True, exist_ok=True)

    for fname in SHAREABLE_FILES:
        src = orig_run_dir / fname
        dst = new_dir / fname
        if src.exists() and not dst.exists():
            dst.symlink_to(src)
    agent_src = orig_run_dir / "agent_logs"
    agent_dst = new_dir / "agent_logs"
    if agent_src.exists() and not agent_dst.exists():
        agent_dst.symlink_to(agent_src)

    rerun_link = new_dir / "rerun_details"
    if not rerun_link.exists():
        rerun_link.symlink_to(eval_rerun_dir)

    (new_dir / "final_evaluation.json").write_text(
        json.dumps(parsed_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    completed, total = _count_evaluation_items(parsed_output)
    orig_manifest = json.loads((orig_run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    new_manifest = dict(orig_manifest)
    new_manifest["run_id"] = str(new_dir)
    new_manifest["run_dir"] = str(new_dir)
    new_manifest["run_name"] = new_name
    new_manifest["run_timestamp"] = f"{orig_manifest.get('run_timestamp', '')}__{out_name}"
    new_manifest["rerun_of"] = str(orig_run_dir)
    new_manifest["evaluator_prompt_variant"] = out_name
    new_manifest["score_summary"] = {
        "completed_items": completed,
        "total_items": total,
        "false_items": max(total - completed, 0),
        "completion_ratio": (completed / total) if total else 0.0,
        "completion_score_100": (completed / total * 100) if total else 0.0,
    }
    (new_dir / "run_manifest.json").write_text(
        json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return new_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run evaluator on an existing run.")
    parser.add_argument("--case-root", required=True, help="scenario 目录（含 4 角色子目录）")
    parser.add_argument("--run-dir", required=True, help="原 run 目录（含 agent_logs/evaluator_calls.json）")
    parser.add_argument(
        "--out-name",
        default="eval_rerun_acgme_v1",
        help="输出子目录名（落在 run-dir 之下）",
    )
    parser.add_argument("--config", default=None, help="config.yaml 路径，默认走 simulate/config.yaml")
    parser.add_argument(
        "--model",
        default=None,
        help="覆盖 evaluator 模型；默认沿用原 evaluator_calls.json 里的 model",
    )
    parser.add_argument(
        "--register-as-run",
        action="store_true",
        help="额外在 scenario 同级建一个兄弟 run 目录（symlink 原 run 共享文件 + 用 parsed_output 写新 final_evaluation.json），让 dashboard 自动发现",
    )
    args = parser.parse_args()

    case_root = Path(args.case_root).resolve()
    run_dir = Path(args.run_dir).resolve()
    out_dir = run_dir / args.out_name

    calls_path = run_dir / "agent_logs" / "evaluator_calls.json"
    calls = json.loads(calls_path.read_text(encoding="utf-8"))
    if not calls:
        raise SystemExit(f"evaluator_calls.json 为空: {calls_path}")
    original = calls[0]
    user_prompt = original["user_prompt"]
    metadata = original.get("metadata", {})
    model = args.model or original.get("model")

    config = load_config(args.config)
    hk_proxy = os.environ.get("HK_PROXY_URL")
    if hk_proxy:
        config.setdefault("proxy", {})["url"] = hk_proxy
    case = load_case(case_root=str(case_root))
    bundle = case.role_bundles["evaluator"]
    system_prompt = build_evaluator_system_prompt(case, bundle)

    client = LLMClient(config)
    evaluator = EvaluatorAgent(client, "evaluator", system_prompt, model=model)
    evaluation = evaluator.evaluate(user_prompt, metadata=metadata)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "system_prompt.md").write_text(system_prompt, encoding="utf-8")
    (out_dir / "user_prompt.md").write_text(user_prompt, encoding="utf-8")
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "final_evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if evaluator.call_logs:
        (out_dir / "evaluator_calls.json").write_text(
            json.dumps(evaluator.call_logs, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    parsed_output = (evaluator.call_logs or [{}])[0].get("parsed_output", {})

    sibling_run_dir = None
    if args.register_as_run and isinstance(parsed_output, dict) and parsed_output:
        sibling_run_dir = register_as_run(run_dir, out_dir, parsed_output, args.out_name)

    summary = {
        "case_root": str(case_root),
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "model": model,
        "system_prompt_chars": len(system_prompt),
        "user_prompt_chars": len(user_prompt),
        "top_level_keys": list(evaluation.keys()) if isinstance(evaluation, dict) else None,
        "parsed_top_level_keys": list(parsed_output.keys()) if isinstance(parsed_output, dict) else None,
        "sibling_run_dir": str(sibling_run_dir) if sibling_run_dir else None,
    }
    (out_dir / "rerun_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
