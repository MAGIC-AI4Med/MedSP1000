#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SIM_PACKAGE_ROOT = ROOT_DIR / "a-simulate"
if str(SIM_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_PACKAGE_ROOT))

from simulate.api import load_config


REASONING_KEY = "reasoning"
OTHER_CATEGORIES_KEY = "other_categories"


@dataclass
class EvaluationItem:
    category: str
    subcategory: str
    item: str
    value: int
    column_name: str


@dataclass
class RunRecord:
    run_name: str
    run_dir: Path
    run_timestamp: str
    case_id: str
    case_title: str
    source_case_dir: str
    source_case_path: str
    source_scenario_dir: str
    source_scenario_path: str
    total_items: int
    true_items: int
    false_items: int
    completion_ratio: float
    completion_score_100: float
    reasoning_text: str
    items: list[EvaluationItem]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract evaluator outputs from a-simulate runs into wide and long CSV files."
    )
    parser.add_argument(
        "--runs-root",
        default="a-simulate/runs",
        help="Directory containing simulation run folders.",
    )
    parser.add_argument(
        "--output-dir",
        default="z-process-v3/outputs",
        help="Directory to store generated CSV files.",
    )
    parser.add_argument(
        "--wide-name",
        default="evaluator_wide.csv",
        help="Filename for the wide-format CSV.",
    )
    parser.add_argument(
        "--long-name",
        default="evaluator_long.csv",
        help="Filename for the long-format CSV.",
    )
    parser.add_argument(
        "--simple-name",
        default="evaluator_simple_scores.csv",
        help="Filename for the simple score summary CSV.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_timestamp(run_name: str) -> str:
    parts = run_name.rsplit("_", 2)
    if len(parts) == 3:
        return f"{parts[-2]}_{parts[-1]}"
    return ""


def infer_source_paths(materials: dict[str, Any]) -> tuple[str, str, str, str]:
    roles = materials.get("roles", {})
    examinee = roles.get("examinee", {})
    role_dir_text = str(examinee.get("role_dir", "")).strip()
    if not role_dir_text:
        return "", "", "", ""

    role_dir = Path(role_dir_text)
    if role_dir.name:
        scenario_dir = role_dir.parent
        case_dir = scenario_dir.parent
        return case_dir.name, str(case_dir), scenario_dir.name, str(scenario_dir)
    return "", "", "", ""


def normalize_reasoning(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    lines = [str(item).strip() for item in value if str(item).strip()]
    return "\n".join(lines)


def flatten_evaluation(evaluation: dict[str, Any]) -> list[EvaluationItem]:
    items: list[EvaluationItem] = []

    for category, bucket in evaluation.items():
        if category == REASONING_KEY:
            continue

        if category == OTHER_CATEGORIES_KEY:
            if not isinstance(bucket, dict):
                continue
            for subcategory, sub_bucket in bucket.items():
                if not isinstance(sub_bucket, dict):
                    continue
                subcategory_text = str(subcategory).strip()
                if not subcategory_text:
                    continue
                for item, value in sub_bucket.items():
                    item_text = str(item).strip()
                    if not item_text:
                        continue
                    items.append(
                        EvaluationItem(
                            category=category,
                            subcategory=subcategory_text,
                            item=item_text,
                            value=1 if bool(value) else 0,
                            column_name=f"{category}__{subcategory_text}__{item_text}",
                        )
                    )
            continue

        if not isinstance(bucket, dict):
            continue
        category_text = str(category).strip()
        if not category_text:
            continue
        for item, value in bucket.items():
            item_text = str(item).strip()
            if not item_text:
                continue
            items.append(
                EvaluationItem(
                    category=category_text,
                    subcategory="",
                    item=item_text,
                    value=1 if bool(value) else 0,
                    column_name=f"{category_text}__{item_text}",
                )
            )

    items.sort(key=lambda item: (item.category, item.subcategory, item.item))
    return items


def load_run_record(run_dir: Path) -> RunRecord:
    evaluation = load_json(run_dir / "final_evaluation.json")
    materials = load_json(run_dir / "materials.json")
    manifest = load_json(run_dir / "run_manifest.json") if (run_dir / "run_manifest.json").exists() else {}

    if not isinstance(evaluation, dict):
        raise ValueError(f"final_evaluation.json is not a JSON object: {run_dir}")
    if not isinstance(materials, dict):
        raise ValueError(f"materials.json is not a JSON object: {run_dir}")

    items = flatten_evaluation(evaluation)
    reasoning_text = normalize_reasoning(evaluation.get(REASONING_KEY))
    true_items = sum(item.value for item in items)
    total_items = len(items)
    false_items = total_items - true_items
    completion_ratio = (true_items / total_items) if total_items else 0.0
    completion_score_100 = completion_ratio * 100.0
    source_case_dir, source_case_path, source_scenario_dir, source_scenario_path = infer_source_paths(
        materials
    )

    return RunRecord(
        run_name=run_dir.name,
        run_dir=run_dir,
        run_timestamp=str(manifest.get("run_timestamp", "")).strip() or infer_timestamp(run_dir.name),
        case_id=str(manifest.get("case_id", "")).strip() or str(materials.get("case_id", "")).strip(),
        case_title=str(manifest.get("case_title", "")).strip() or str(materials.get("case_title", "")).strip(),
        source_case_dir=source_case_dir,
        source_case_path=source_case_path,
        source_scenario_dir=source_scenario_dir,
        source_scenario_path=source_scenario_path,
        total_items=total_items,
        true_items=true_items,
        false_items=false_items,
        completion_ratio=completion_ratio,
        completion_score_100=completion_score_100,
        reasoning_text=reasoning_text,
        items=items,
    )


def discover_run_records(runs_root: Path) -> tuple[list[RunRecord], list[tuple[str, str]]]:
    records: list[RunRecord] = []
    skipped: list[tuple[str, str]] = []

    run_dirs = sorted({path.parent for path in runs_root.rglob("final_evaluation.json")})
    for run_dir in run_dirs:
        evaluation_path = run_dir / "final_evaluation.json"
        materials_path = run_dir / "materials.json"

        if not evaluation_path.exists():
            skipped.append((run_dir.name, "missing final_evaluation.json"))
            continue
        if not materials_path.exists():
            skipped.append((run_dir.name, "missing materials.json"))
            continue

        try:
            records.append(load_run_record(run_dir))
        except Exception as exc:  # pragma: no cover - defensive path
            skipped.append((run_dir.name, str(exc)))

    return records, skipped


def build_metadata_row(record: RunRecord) -> dict[str, Any]:
    return {
        "run_name": record.run_name,
        "run_dir": str(record.run_dir),
        "run_timestamp": record.run_timestamp,
        "case_id": record.case_id,
        "case_title": record.case_title,
        "source_case_dir": record.source_case_dir,
        "source_case_path": record.source_case_path,
        "source_scenario_dir": record.source_scenario_dir,
        "source_scenario_path": record.source_scenario_path,
        "total_items": record.total_items,
        "true_items": record.true_items,
        "false_items": record.false_items,
        "completion_ratio": f"{record.completion_ratio:.6f}",
        "completion_score_100": f"{record.completion_score_100:.2f}",
        "reasoning_text": record.reasoning_text,
    }


def write_wide_csv(path: Path, records: list[RunRecord]) -> None:
    metadata_columns = [
        "run_name",
        "run_dir",
        "run_timestamp",
        "case_id",
        "case_title",
        "source_case_dir",
        "source_case_path",
        "source_scenario_dir",
        "source_scenario_path",
        "total_items",
        "true_items",
        "false_items",
        "completion_ratio",
        "completion_score_100",
        "reasoning_text",
    ]
    item_columns = sorted({item.column_name for record in records for item in record.items})
    fieldnames = metadata_columns + item_columns

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = build_metadata_row(record)
            for item in record.items:
                row[item.column_name] = item.value
            writer.writerow(row)


def write_long_csv(path: Path, records: list[RunRecord]) -> None:
    fieldnames = [
        "run_name",
        "run_dir",
        "run_timestamp",
        "case_id",
        "case_title",
        "source_case_dir",
        "source_case_path",
        "source_scenario_dir",
        "source_scenario_path",
        "total_items",
        "true_items",
        "false_items",
        "completion_ratio",
        "completion_score_100",
        "reasoning_text",
        "category",
        "subcategory",
        "item",
        "column_name",
        "value",
    ]

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            metadata = build_metadata_row(record)
            for item in record.items:
                row = dict(metadata)
                row.update(
                    {
                        "category": item.category,
                        "subcategory": item.subcategory,
                        "item": item.item,
                        "column_name": item.column_name,
                        "value": item.value,
                    }
                )
                writer.writerow(row)


def infer_examinee_model(run_dir: Path, fallback_model: str) -> str:
    model_info_path = run_dir / "run_models.json"
    if model_info_path.exists():
        try:
            payload = load_json(model_info_path)
            if isinstance(payload, dict):
                models = payload.get("models", {})
                if isinstance(models, dict):
                    model = str(models.get("examinee", "")).strip() or str(models.get("default", "")).strip()
                    if model:
                        return model
        except Exception:
            pass
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.exists():
        try:
            payload = load_json(manifest_path)
            if isinstance(payload, dict):
                models = payload.get("models", {})
                if isinstance(models, dict):
                    model = str(models.get("examinee", "")).strip() or str(models.get("default", "")).strip()
                    if model:
                        return model
        except Exception:
            pass
    call_log_path = run_dir / "agent_logs" / "examinee_calls.json"
    if call_log_path.exists():
        try:
            payload = load_json(call_log_path)
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    model = str(item.get("model", "")).strip()
                    if model:
                        return model
        except Exception:
            pass
    return fallback_model


def write_simple_csv(path: Path, records: list[RunRecord], fallback_model: str) -> None:
    fieldnames = [
        "run_name",
        "source_case_dir",
        "source_case_path",
        "case_id",
        "case_title",
        "examinee_model",
        "true_items",
        "total_items",
        "false_items",
        "completion_ratio",
        "completion_score_100",
        "sum_case_scores_100",
        "overall_completion_ratio",
        "overall_completion_score_100",
    ]

    total_true_items = sum(record.true_items for record in records)
    total_items = sum(record.total_items for record in records)
    total_false_items = sum(record.false_items for record in records)
    sum_case_scores_100 = sum(record.completion_score_100 for record in records)
    overall_completion_ratio = (total_true_items / total_items) if total_items else 0.0
    overall_completion_score_100 = overall_completion_ratio * 100.0
    model_set = {infer_examinee_model(record.run_dir, fallback_model) for record in records}
    total_model = next(iter(model_set)) if len(model_set) == 1 else "mixed"

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "run_name": record.run_name,
                    "source_case_dir": record.source_case_dir,
                    "source_case_path": record.source_case_path,
                    "case_id": record.case_id,
                    "case_title": record.case_title,
                    "examinee_model": infer_examinee_model(record.run_dir, fallback_model),
                    "true_items": record.true_items,
                    "total_items": record.total_items,
                    "false_items": record.false_items,
                    "completion_ratio": f"{record.completion_ratio:.6f}",
                    "completion_score_100": f"{record.completion_score_100:.2f}",
                    "sum_case_scores_100": "",
                    "overall_completion_ratio": "",
                    "overall_completion_score_100": "",
                }
            )
        writer.writerow(
            {
                "run_name": "__TOTAL__",
                "source_case_dir": "",
                "source_case_path": "",
                "case_id": "",
                "case_title": "",
                "examinee_model": total_model,
                "true_items": total_true_items,
                "total_items": total_items,
                "false_items": total_false_items,
                "completion_ratio": "",
                "completion_score_100": "",
                "sum_case_scores_100": f"{sum_case_scores_100:.2f}",
                "overall_completion_ratio": f"{overall_completion_ratio:.6f}",
                "overall_completion_score_100": f"{overall_completion_score_100:.2f}",
            }
        )


def main() -> None:
    args = parse_args()
    runs_root = Path(args.runs_root)
    output_dir = Path(args.output_dir)
    fallback_model = load_config(Path("a-simulate") / "simulate" / "config.yaml").get("api", {}).get("model", "")

    if not runs_root.exists():
        raise SystemExit(f"Runs root does not exist: {runs_root}")
    if not runs_root.is_dir():
        raise SystemExit(f"Runs root is not a directory: {runs_root}")

    output_dir.mkdir(parents=True, exist_ok=True)

    records, skipped = discover_run_records(runs_root)
    if not records:
        raise SystemExit(f"No valid run records found under: {runs_root}")

    wide_path = output_dir / args.wide_name
    long_path = output_dir / args.long_name
    simple_path = output_dir / args.simple_name

    write_wide_csv(wide_path, records)
    write_long_csv(long_path, records)
    write_simple_csv(simple_path, records, str(fallback_model).strip())

    print(f"Processed runs: {len(records)}")
    print(f"Wide CSV: {wide_path}")
    print(f"Long CSV: {long_path}")
    print(f"Simple CSV: {simple_path}")
    if skipped:
        print(f"Skipped runs: {len(skipped)}")
        for run_name, reason in skipped:
            print(f"- {run_name}: {reason}")


if __name__ == "__main__":
    main()
