from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from simulate.api import load_config
from simulate.case_loader import load_case
from simulate.console import ConsoleLogger
from simulate.controller import SimulationController, evaluate_run_dir_with_frozen_rubric
from simulate.status_marker import ConfigInvalidForStatus, write_marker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a folder-driven multi-agent medical simulation.")
    parser.add_argument(
        "--config",
        default=str(Path("simulate") / "config.yaml"),
        help="Path to runtime config YAML.",
    )
    parser.add_argument(
        "--case-root",
        default=None,
        help="Case root containing the four role folders.",
    )
    parser.add_argument("--examinee-dir", default=None, help="Folder visible to the examinee.")
    parser.add_argument("--sp-dir", default=None, help="Folder visible to the SP.")
    parser.add_argument(
        "--environment-dir",
        default=None,
        help="Folder visible to the environment controller.",
    )
    parser.add_argument(
        "--evaluator-dir",
        default=None,
        help="Folder visible to the evaluator.",
    )
    parser.add_argument("--output-dir", default=None, help="Optional output directory override.")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate role-folder loading without calling the model API.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose console debugging for controller and API calls.",
    )
    parser.add_argument(
        "--eval-from-run-dir",
        default=None,
        help=(
            "只跑评估：跳过模拟，直接读该 run 目录的日志，用冻结 rubric 重判 true/false，"
            "结果写 <run_dir>/final_evaluation_frozen_rubric.json（不动原文件）。"
        ),
    )
    parser.add_argument(
        "--rubric-file",
        default=None,
        help="冻结 rubric JSON 路径（配合 --eval-from-run-dir）；缺省则按 run 目录推导 <case>_<scenario>.json。",
    )
    parser.add_argument(
        "--rubric-dir",
        default=str(Path(__file__).resolve().parents[2] / "z-rubric" / "rubrics"),
        help="冻结 rubric 目录；--rubric-file 未给时从此处按 <case>_<scenario>.json 取。",
    )
    parser.add_argument(
        "--eval-model",
        default="deepseek-v4-pro",
        help="冻结 rubric 重判的判分模型（默认 deepseek-v4-pro，与原 eval 一致）。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的 final_evaluation_frozen_rubric.json。",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.debug:
        config.setdefault("simulation", {})
        config["simulation"]["debug"] = True
        config["simulation"]["console_progress"] = True

    console = ConsoleLogger(
        enabled=bool(config.get("simulation", {}).get("console_progress", True)),
        debug=bool(config.get("simulation", {}).get("debug", False)),
    )

    if args.eval_from_run_dir:
        # 从评估步骤起跑：不加载 case、不跑模拟、不写 status marker。
        for _proxy_var in ("ALL_PROXY", "all_proxy"):
            os.environ.pop(_proxy_var, None)
        run_dir = Path(args.eval_from_run_dir).resolve()
        if args.rubric_file:
            rubric_file = Path(args.rubric_file).resolve()
        else:
            # run 目录约定: .../runs/<case_id>/<scenario>/<timestamp>
            case_id = run_dir.parent.parent.name
            scenario = run_dir.parent.name
            rubric_file = Path(args.rubric_dir).resolve() / f"{case_id}_{scenario}.json"
        if not rubric_file.exists():
            # 单行 + 哨兵前缀：ConsoleLogger/evaluate 也会往 stdout 打字，
            # 批处理脚本只挑这一行解析，与其它 stdout 噪声隔离。
            print(
                "@@RESCORE_JSON@@ "
                + json.dumps(
                    {
                        "status": "skipped_no_rubric",
                        "run_dir": str(run_dir),
                        "rubric_file": str(rubric_file),
                    },
                    ensure_ascii=False,
                )
            )
            return
        rubric = json.loads(rubric_file.read_text(encoding="utf-8"))
        summary = evaluate_run_dir_with_frozen_rubric(
            run_dir,
            rubric,
            config=config,
            eval_model=args.eval_model,
            rubric_file=rubric_file,
            force=args.force,
        )
        # 单行 + 哨兵前缀（同上）：脚本按 @@RESCORE_JSON@@ 取末行解析。
        print("@@RESCORE_JSON@@ " + json.dumps(summary, ensure_ascii=False))
        return

    case_root = args.case_root or config.get("simulation", {}).get("default_case_root")
    console.info("Loading role folders for simulation")
    case = load_case(
        case_root=case_root,
        examinee_dir=args.examinee_dir,
        sp_dir=args.sp_dir,
        environment_dir=args.environment_dir,
        evaluator_dir=args.evaluator_dir,
    )
    console.info(
        f"Case loaded: id={case.case_id}, roles={list(case.role_bundles.keys())}, "
        f"root={case.case_root}"
    )

    if args.validate_only:
        payload = {
            "case_id": case.case_id,
            "case_title": case.case_title,
            "case_root": str(case.case_root),
            "role_dirs": {name: str(path) for name, path in case.role_dirs.items()},
            "role_files": {
                name: [str(path) for path in bundle.visible_files]
                for name, bundle in case.role_bundles.items()
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    console.info("Starting simulation controller")
    controller = SimulationController(case, config, output_dir=args.output_dir)
    result = controller.run()

    if result.get("skipped"):
        # 缺冻结 rubric 等原因跳过：不写 status marker（下游/批处理视为未完成）。
        console.info(f"Scenario skipped ({result.get('status')}); 不写 status marker。")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if case_root:
        try:
            marker_path = write_marker(args.config, case_root, controller.output_dir)
            console.info(f"Status marker written: {marker_path}")
        except ConfigInvalidForStatus as exc:
            console.info(f"Status marker skipped (config不满足约束): {exc}")
    else:
        console.info("Status marker skipped (无 case_root，可能用 --examinee-dir 等单角色入口启动)")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
