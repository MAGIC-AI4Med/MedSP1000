from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_case_paths(case_file: Path) -> list[Path]:
    data = json.loads(case_file.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"case file must be a JSON array: {case_file}")

    case_paths: list[Path] = []
    for item in data:
        if isinstance(item, str) and item.strip():
            case_paths.append(Path(item.strip()).resolve())
    return case_paths


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
        description="Generate a scenario-directory JSON from case directories and keep only validated scenarios."
    )
    parser.add_argument(
        "--case-file",
        default="z-process-v2/case_directories_full.json",
        help="Input JSON array of case directories.",
    )
    parser.add_argument(
        "--output",
        default="z-process-v2/scenario_directories_full.json",
        help="Output JSON array of validated scenario directories.",
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

    root_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root_dir / "a-simulate"))

    from simulate.case_loader import load_case  # pylint: disable=import-outside-toplevel

    case_file = Path(args.case_file).resolve()
    output_file = Path(args.output).resolve()

    if not case_file.is_file():
        raise SystemExit(f"case file not found: {case_file}")

    case_paths = load_case_paths(case_file)
    valid_scenarios: list[str] = []
    missing_cases: list[str] = []
    cases_without_scenarios: list[str] = []
    invalid_scenarios: list[dict[str, str]] = []

    for case_dir in case_paths:
        if not case_dir.is_dir():
            missing_cases.append(str(case_dir))
            continue

        scenario_dirs = iter_scenario_dirs(case_dir)
        if not scenario_dirs:
            cases_without_scenarios.append(str(case_dir))
            continue

        for scenario_dir in scenario_dirs:
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

    print(f"case_file={case_file}")
    print(f"output={output_file}")
    print(f"input_case_count={len(case_paths)}")
    print(f"missing_case_count={len(missing_cases)}")
    print(f"cases_without_scenarios_count={len(cases_without_scenarios)}")
    print(f"validated_scenario_count={len(valid_scenarios)}")
    print(f"invalid_scenario_count={len(invalid_scenarios)}")

    if missing_cases:
        print("missing_cases:")
        for item in missing_cases:
            print(f"  - {item}")

    if cases_without_scenarios:
        print("cases_without_scenarios:")
        for item in cases_without_scenarios:
            print(f"  - {item}")

    if invalid_scenarios:
        print("invalid_scenarios:")
        for item in invalid_scenarios:
            print(
                f"  - {item['scenario_dir']} :: {item['error_type']}: {item['error']}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
