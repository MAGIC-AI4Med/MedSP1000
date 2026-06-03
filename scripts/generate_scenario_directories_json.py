#!/usr/bin/env python
"""Build the scenario-directory manifest the simulation runner consumes.

It scans the downloaded MedSP1000 dataset (default: ``data/MedSP1000`` under the
repo root), keeps only scenarios that load cleanly through ``simulate.case_loader``,
and writes their absolute paths to ``scenario_directories_full.json`` at the repo
root. ``scripts/run_simulate_cases.sh`` reads that file by default.

Expected dataset layout (as released on the Hugging Face Hub):

    data/MedSP1000/
      <case_id>/
        scenario1/{examinee, sp_actor, environment_controller, evaluator}/*.md
        scenario2/...
      <case_id>/...

Usage:
    python scripts/generate_scenario_directories_json.py --pretty
    python scripts/generate_scenario_directories_json.py --data-dir /path/to/MedSP1000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def iter_case_dirs(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    return sorted(
        path.resolve()
        for path in data_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def load_subset_keys(subset_path: Path) -> set[str]:
    """Parse a subset spec into a set of '<case_id>/<scenarioN>' keys."""
    data = json.loads(subset_path.read_text(encoding="utf-8"))
    entries = data.get("scenarios") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise SystemExit(f"subset spec must be a list or have a 'scenarios' list: {subset_path}")

    keys: set[str] = set()
    for item in entries:
        if isinstance(item, str) and item.strip():
            keys.add(item.strip().strip("/"))
        elif isinstance(item, dict) and item.get("case_id") and item.get("scenario_id"):
            keys.add(f"{item['case_id']}/{item['scenario_id']}")
    return keys


def iter_scenario_dirs(case_dir: Path) -> list[Path]:
    if not case_dir.is_dir():
        return []
    return sorted(
        path.resolve()
        for path in case_dir.iterdir()
        if path.is_dir() and path.name.startswith("scenario")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan the MedSP1000 dataset and emit a JSON list of validated scenario directories."
    )
    parser.add_argument(
        "--data-dir",
        default=str(REPO_ROOT / "data" / "MedSP1000"),
        help="Root of the downloaded dataset (default: data/MedSP1000).",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "scenario_directories_full.json"),
        help="Output JSON array of validated scenario directories.",
    )
    parser.add_argument(
        "--subset",
        default=None,
        help=(
            "Optional subset spec (e.g. subset.json). Only scenarios listed there are "
            "emitted. Accepts a JSON array of '<case_id>/<scenarioN>' strings, or an "
            "object with a 'scenarios' list of such strings."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON with indentation.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Make the engine importable regardless of the caller's working directory.
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from simulate.case_loader import load_case  # pylint: disable=import-outside-toplevel

    data_dir = Path(args.data_dir).resolve()
    output_file = Path(args.output).resolve()

    if not data_dir.is_dir():
        raise SystemExit(
            f"dataset directory not found: {data_dir}\n"
            "Download it first: python scripts/download_data.py"
        )

    subset_keys: set[str] | None = None
    if args.subset:
        subset_path = Path(args.subset).resolve()
        if not subset_path.is_file():
            raise SystemExit(f"subset spec not found: {subset_path}")
        subset_keys = load_subset_keys(subset_path)

    case_dirs = iter_case_dirs(data_dir)
    valid_scenarios: list[str] = []
    cases_without_scenarios: list[str] = []
    invalid_scenarios: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    for case_dir in case_dirs:
        scenario_dirs = iter_scenario_dirs(case_dir)
        if not scenario_dirs:
            cases_without_scenarios.append(str(case_dir))
            continue

        for scenario_dir in scenario_dirs:
            key = f"{case_dir.name}/{scenario_dir.name}"
            if subset_keys is not None:
                if key not in subset_keys:
                    continue
                seen_keys.add(key)
            try:
                load_case(case_root=scenario_dir)
            except Exception as exc:  # pragma: no cover - surfaced to user output
                invalid_scenarios.append(
                    {
                        "scenario_dir": str(scenario_dir),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            valid_scenarios.append(str(scenario_dir))

    output_file.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(valid_scenarios, ensure_ascii=False, indent=2 if args.pretty else None)
    output_file.write_text(payload + ("\n" if args.pretty else ""), encoding="utf-8")

    print(f"data_dir={data_dir}")
    print(f"output={output_file}")
    print(f"case_count={len(case_dirs)}")
    print(f"cases_without_scenarios_count={len(cases_without_scenarios)}")
    print(f"validated_scenario_count={len(valid_scenarios)}")
    print(f"invalid_scenario_count={len(invalid_scenarios)}")

    if subset_keys is not None:
        missing_from_data = sorted(subset_keys - seen_keys)
        print(f"subset_requested_count={len(subset_keys)}")
        print(f"subset_missing_from_data_count={len(missing_from_data)}")
        if missing_from_data:
            print("subset_missing_from_data:")
            for item in missing_from_data:
                print(f"  - {item}")

    if cases_without_scenarios:
        print("cases_without_scenarios:")
        for item in cases_without_scenarios:
            print(f"  - {item}")

    if invalid_scenarios:
        print("invalid_scenarios:")
        for item in invalid_scenarios:
            print(f"  - {item['scenario_dir']} :: {item['error_type']}: {item['error']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
