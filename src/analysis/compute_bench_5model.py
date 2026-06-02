#!/usr/bin/env python3
"""5-model benchmark metric on the 100-scenario subset.

走 status marker:
  status/<sp_env_eval>/<examinee>/<case>_<scenario>.txt
marker 文件内容 = 对应 run 输出目录的绝对路径。

每个 run 读 final_evaluation.json (顶层键: reasoning + ACGME 6 维 PC/MK/SBP/ICS/PBLI/PROF)。
每个维度下是 {item_text: bool} flat dict。

输出 long-format CSV: examinee × dimension。
缺失 scenario 直接忽略，不补 0。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from statistics import mean

ACGME_DIMS = ["PC", "MK", "SBP", "ICS", "PBLI", "PROF"]
DEFAULT_MODELS = [
    "claude-opus-4-7",
    "gpt-5.5",
    "Baichuan-M3",
    "deepseek-v4-pro",
    "gemini-3.1-pro-preview",
    "qwen3.5-397b-a17b",
    "medgemma-27b-it",
]
DEFAULT_STATUS_ROOT = (
    "/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi"
    "/a-simulate/status"
)
DEFAULT_OUT = (
    "/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi"
    "/z-stat-figure/tables/bench_5model.csv"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--status-root", default=DEFAULT_STATUS_ROOT)
    p.add_argument("--sp-env-eval", default="deepseek-v4-pro")
    p.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument(
        "--eval-filename",
        default="final_evaluation.json",
        help="run 目录下读哪个评测文件;冻结 rubric 重打分用 "
        "final_evaluation_frozen_rubric.json(顶层 ACGME 6 维同构,"
        "_ 前缀元数据被 score_run 天然忽略)。缺省=原始口径,行为不变。",
    )
    return p.parse_args()


def score_run(
    run_dir: Path, eval_filename: str = "final_evaluation.json"
) -> dict[str, tuple[int, int]]:
    """Read <eval_filename> -> {dim: (n_true, n_total)} incl. 'overall'."""
    fe = json.loads((run_dir / eval_filename).read_text(encoding="utf-8"))
    out: dict[str, tuple[int, int]] = {}
    tot_t = tot_n = 0
    for dim in ACGME_DIMS:
        bucket = fe.get(dim)
        if not isinstance(bucket, dict):
            out[dim] = (0, 0)
            continue
        t = sum(1 for v in bucket.values() if bool(v))
        n = len(bucket)
        out[dim] = (t, n)
        tot_t += t
        tot_n += n
    out["overall"] = (tot_t, tot_n)
    return out


def aggregate_model(
    examinee: str, marker_dir: Path, eval_filename: str = "final_evaluation.json"
) -> tuple[list[dict], list[str]]:
    """Returns (rows, warnings). One row per dimension (incl. overall)."""
    warnings: list[str] = []
    markers = sorted(marker_dir.glob("*.txt"))
    per_scenario: list[dict[str, tuple[int, int]]] = []
    for mk in markers:
        run_dir = Path(mk.read_text(encoding="utf-8").strip())
        if not run_dir.is_absolute():
            warnings.append(f"{examinee}/{mk.name}: non-absolute path in marker")
            continue
        fe_path = run_dir / eval_filename
        if not fe_path.exists():
            warnings.append(f"{examinee}/{mk.name}: missing {eval_filename} at {run_dir}")
            continue
        try:
            per_scenario.append(score_run(run_dir, eval_filename))
        except Exception as exc:
            warnings.append(f"{examinee}/{mk.name}: parse error: {exc}")

    n_scenarios = len(per_scenario)
    rows: list[dict] = []
    for dim in ["overall"] + ACGME_DIMS:
        n_true = sum(s[dim][0] for s in per_scenario)
        n_rubric = sum(s[dim][1] for s in per_scenario)
        micro = n_true / n_rubric if n_rubric else 0.0
        ratios = [s[dim][0] / s[dim][1] for s in per_scenario if s[dim][1] > 0]
        macro = mean(ratios) if ratios else 0.0
        rows.append({
            "examinee": examinee,
            "dimension": dim,
            "n_scenarios": n_scenarios,
            "n_scenarios_with_dim": len(ratios),
            "n_rubric": n_rubric,
            "n_true": n_true,
            "micro": round(micro, 4),
            "macro": round(macro, 4),
        })
    return rows, warnings


def print_wide_pivot(rows: list[dict], models: list[str]) -> None:
    by = {(r["examinee"], r["dimension"]): r for r in rows}
    dims = ["overall"] + ACGME_DIMS

    def show(metric: str) -> None:
        col_w = 9
        name_w = max(len(m) for m in models) + 2
        header = "model".ljust(name_w) + "n_sc".rjust(6) + "".join(d.rjust(col_w) for d in dims)
        print(header)
        print("-" * len(header))
        for m in models:
            r0 = by.get((m, "overall"))
            n_sc = r0["n_scenarios"] if r0 else 0
            line = m.ljust(name_w) + str(n_sc).rjust(6)
            for d in dims:
                r = by.get((m, d))
                line += (f"{r[metric]:.4f}".rjust(col_w)) if r else "--".rjust(col_w)
            print(line)

    print("\n=== Micro completion ratio (sum_true / sum_total) ===")
    show("micro")
    print("\n=== Macro completion ratio (mean of per-scenario ratios) ===")
    show("macro")


def main() -> None:
    args = parse_args()
    status_root = Path(args.status_root) / args.sp_env_eval
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not status_root.exists():
        sys.exit(f"status root not found: {status_root}")

    all_rows: list[dict] = []
    all_warnings: list[str] = []
    for examinee in args.models:
        marker_dir = status_root / examinee
        if not marker_dir.exists():
            print(f"WARN: marker dir not found: {marker_dir}", file=sys.stderr)
            continue
        rows, warnings = aggregate_model(examinee, marker_dir, args.eval_filename)
        all_rows.extend(rows)
        all_warnings.extend(warnings)

    cols = [
        "examinee", "dimension", "n_scenarios", "n_scenarios_with_dim",
        "n_rubric", "n_true", "micro", "macro",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_rows)

    print(f"wrote {len(all_rows)} rows -> {out_path}")
    if all_warnings:
        print(f"\n{len(all_warnings)} warnings:", file=sys.stderr)
        for w in all_warnings:
            print(f"  {w}", file=sys.stderr)

    print_wide_pivot(all_rows, args.models)


if __name__ == "__main__":
    main()
