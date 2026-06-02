"""
Aggregate + quality-control the per-scenario frozen rubrics.

Inputs:
  - ../scenario_list.json     (authoritative scenario list = 1639 dirs;
                                a copy of z-process-v2/scenario_directories_full.json)
  - ../rubrics/*.json         (per-scenario rubric files; schema:
      {
        case_id, scenario, scenario_dir, rubric_version,
        PC:   [ "<scoring item text>", .. ],
        MK: [], SBP: [], ICS: [], PBLI: [], PROF: []
      })

Output:
  - ../rubric_quality_report.json

Per-rubric validation (any hard violation -> entry goes to `malformed`):
  - top-level: case_id (str), scenario (str)
  - each of the 6 ACGME keys present and is a JSON array (list)
  - inside each dimension list: every element is a non-empty string

Soft quality flags (rubric still counted, but listed under `flagged`):
  - empty            : all 6 dimensions are empty
  - dup_text_in_dim  : an item text appears twice within the same dimension
  - dup_text_cross   : the same item text appears in >1 dimension
  - long_item        : an item's text exceeds LONG_ITEM_CHARS
  - short_item       : an item is so short / few-worded it is likely a
                       context-stripped fragment (<= SHORT_ITEM_WORDS words
                       or < SHORT_ITEM_CHARS chars after normalisation)
  - unverified_text  : the item text (whitespace-normalised) is not found in
                       the scenario's evaluator/ materials -> possible
                       paraphrase/hallucination. Best-effort: skipped if the
                       evaluator/ dir is unreadable or --no-quote-check.

This script never modifies a-simulate, existing runs, or scenario materials;
it only READS evaluator/ for the text check and writes one report JSON.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parent
DEFAULT_INPUT_FILE = PIPELINE_DIR / "scenario_list.json"
RUBRICS_DIR = PIPELINE_DIR / "rubrics"
DEFAULT_OUTPUT_FILE = PIPELINE_DIR / "rubric_quality_report.json"

DIMENSIONS = ["PC", "MK", "SBP", "ICS", "PBLI", "PROF"]
SHORT_ITEM_CHARS = 15
SHORT_ITEM_WORDS = 2
LONG_ITEM_CHARS = 600
TEXT_EXT = {".md", ".txt"}


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _scenario_input_id(path: str) -> str:
    p = Path(path)
    return f"{p.parent.name}_{p.name}"


def _read_evaluator_text(scenario_dir: str) -> str | None:
    """Concatenate all readable evaluator/ text, whitespace-normalised.

    Returns None when the directory cannot be read (text check then skipped).
    """
    try:
        ev = Path(scenario_dir) / "evaluator"
        if not ev.is_dir():
            return None
        chunks: list[str] = []
        for f in sorted(ev.rglob("*")):
            if f.is_file() and f.suffix.lower() in TEXT_EXT:
                try:
                    chunks.append(f.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
        if not chunks:
            return None
        return _norm_ws("\n".join(chunks))
    except OSError:
        return None


def _validate_rubric(data: object) -> str | None:
    """Return an error string for a hard violation, else None."""
    if not isinstance(data, dict):
        return "not a JSON object"
    if not isinstance(data.get("case_id"), str) or not data["case_id"].strip():
        return "case_id missing or not a non-empty string"
    if not isinstance(data.get("scenario"), str) or not data["scenario"].strip():
        return "scenario missing or not a non-empty string"
    for dim in DIMENSIONS:
        block = data.get(dim)
        if not isinstance(block, list):
            return f"dimension {dim!r} missing or not a JSON array"
        for el in block:
            if not isinstance(el, str) or not el.strip():
                return f"dimension {dim!r} has a non-string / empty item"
    return None


def _iter_items(data: dict):
    for dim in DIMENSIONS:
        for text in data[dim]:
            yield dim, text


def _quality_flags(data: dict, evaluator_text: str | None) -> list[str]:
    flags: list[str] = []
    all_items = list(_iter_items(data))

    if not all_items:
        flags.append("empty")
        return flags

    # duplicate item text within a single dimension list
    for dim in DIMENSIONS:
        norm = [_norm_ws(t) for t in data[dim]]
        if len(set(norm)) != len(norm):
            flags.append("dup_text_in_dim")
            break

    # same item text appearing in more than one dimension
    seen_dims: dict[str, set[str]] = {}
    for dim, text in all_items:
        seen_dims.setdefault(_norm_ws(text), set()).add(dim)
    if any(len(d) > 1 for d in seen_dims.values()):
        flags.append("dup_text_cross")

    if any(len(text) > LONG_ITEM_CHARS for _, text in all_items):
        flags.append("long_item")

    n_short = sum(
        1 for _, text in all_items
        if len(_norm_ws(text)) < SHORT_ITEM_CHARS
        or len(_norm_ws(text).split()) <= SHORT_ITEM_WORDS
    )
    if n_short:
        flags.append(f"short_item:{n_short}/{len(all_items)}")

    if evaluator_text is not None:
        unverified = 0
        for _, text in all_items:
            q = _norm_ws(text)
            if not q:
                unverified += 1
                continue
            probe = q if len(q) <= 80 else q[:80]
            if probe not in evaluator_text:
                unverified += 1
        if unverified:
            flags.append(f"unverified_text:{unverified}/{len(all_items)}")

    return flags


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate + QC the per-scenario frozen rubrics.",
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT_FILE,
        help=f"Authoritative scenario list JSON (default: {DEFAULT_INPUT_FILE.name})",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_FILE,
        help=f"Quality report output JSON (default: {DEFAULT_OUTPUT_FILE.name})",
    )
    parser.add_argument(
        "--no-quote-check", action="store_true",
        help="Skip the item-text-vs-evaluator-materials verification (faster).",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"scenario list not found: {args.input}")
    input_dirs = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(input_dirs, list):
        raise SystemExit(f"{args.input.name} must be a JSON array of strings")
    input_ids = [_scenario_input_id(p) for p in input_dirs if isinstance(p, str)]
    input_set = set(input_ids)

    scenarios: dict[str, dict] = {}
    malformed: list[dict] = []
    flagged: list[dict] = []
    extra_files: list[str] = []
    dim_item_counts: Counter[str] = Counter()
    items_per_scenario: list[int] = []

    if RUBRICS_DIR.exists():
        for jpath in sorted(RUBRICS_DIR.glob("*.json")):
            rid = jpath.stem
            try:
                data = json.loads(jpath.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                malformed.append({"id": rid, "error": f"invalid JSON: {e}"})
                continue

            err = _validate_rubric(data)
            if err is not None:
                malformed.append({"id": rid, "error": err})
                continue

            if rid not in input_set:
                extra_files.append(rid)

            ev_text = None
            if not args.no_quote_check:
                ev_text = _read_evaluator_text(str(data.get("scenario_dir", "")))
            flags = _quality_flags(data, ev_text)

            dim_counts = {d: len(data[d]) for d in DIMENSIONS}
            n_items = sum(dim_counts.values())
            items_per_scenario.append(n_items)
            for d in DIMENSIONS:
                dim_item_counts[d] += dim_counts[d]

            scenarios[rid] = {
                "id": rid,
                "case_id": data["case_id"],
                "scenario": data["scenario"],
                "n_items": n_items,
                "dim_counts": dim_counts,
                "flags": flags,
            }
            if flags:
                flagged.append({"id": rid, "n_items": n_items, "flags": flags})

    missing = sorted(input_set - set(scenarios.keys()))

    n = len(items_per_scenario)
    if n:
        srt = sorted(items_per_scenario)
        stats = {
            "min": srt[0],
            "median": srt[n // 2],
            "max": srt[-1],
            "mean": round(sum(srt) / n, 2),
            "total_items": sum(srt),
            "zero_item_scenarios": sum(1 for x in srt if x == 0),
        }
    else:
        stats = {"min": 0, "median": 0, "max": 0, "mean": 0,
                 "total_items": 0, "zero_item_scenarios": 0}

    flag_summary: Counter[str] = Counter()
    for f in flagged:
        for tag in f["flags"]:
            flag_summary[tag.split(":")[0]] += 1

    output = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dimensions": DIMENSIONS,
        "total_input_scenarios": len(input_ids),
        "n_rubrics": len(scenarios),
        "n_missing": len(missing),
        "n_malformed": len(malformed),
        "n_flagged": len(flagged),
        "items_per_scenario": stats,
        "dimension_item_counts": {d: dim_item_counts.get(d, 0) for d in DIMENSIONS},
        "flag_summary": dict(sorted(flag_summary.items())),
        "scenarios": scenarios,
        "missing": missing,
        "malformed": malformed,
        "flagged": flagged,
        "extra_files_not_in_inputs": sorted(extra_files),
    }

    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"inputs={len(input_ids)} rubrics={len(scenarios)} "
        f"missing={len(missing)} malformed={len(malformed)} flagged={len(flagged)}"
    )
    print(
        f"items/scenario: min={stats['min']} median={stats['median']} "
        f"max={stats['max']} mean={stats['mean']} total={stats['total_items']} "
        f"zero_item_scenarios={stats['zero_item_scenarios']}"
    )
    print("dimension_item_counts:")
    for d in DIMENSIONS:
        print(f"  {d:<5s} {dim_item_counts.get(d, 0)}")
    if flag_summary:
        print("flag_summary:")
        for k, v in sorted(flag_summary.items()):
            print(f"  {k:<18s} {v}")
    print(f"  -> {args.output}")


if __name__ == "__main__":
    main()
