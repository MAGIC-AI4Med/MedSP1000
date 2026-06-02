"""完成标记：记录 (case, scenario, 模型组合) 的成功结果落盘位置。

目录布局：

    a-simulate/status/<sp_env_eval_model>/<examinee_model>/<case>_<scenario>.txt

marker 文件内容 = 该次 run 的绝对输出目录（含 transcript / agent_logs / final_evaluation 等），
便于：
  - `run_simulate_cases.sh` 跨批次幂等：marker 存在则 SKIP 该 (case, scenario, 模型组合)
  - 人工排查：`cat status/<m1>/<m2>/<case>_<scenario>.txt` 一行得到 run 目录绝对路径

约束：sp / environment / evaluator 三个角色当前必须配同一模型；不一致时
`get_status_dir` 抛 ConfigInvalidForStatus，调用方决定是 abort 还是跳过写 marker。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_STATUS_ROOT = Path(__file__).resolve().parent.parent / "status"


class ConfigInvalidForStatus(ValueError):
    """Config 无法产出 status 目录（缺字段 / sp env eval 不一致等）。"""


def _model_of(agents: dict[str, Any], role: str) -> str:
    spec = agents.get(role) or {}
    if not isinstance(spec, dict):
        raise ConfigInvalidForStatus(f"agents.{role} 不是 dict：{spec!r}")
    model = spec.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ConfigInvalidForStatus(f"agents.{role}.model 缺失或非字符串：{model!r}")
    return model.strip()


def get_status_dir(config_path: str | Path) -> Path:
    """读 config，验证 sp==environment==evaluator，返回模型组合对应的 status 目录。

    不创建目录。Raises ConfigInvalidForStatus 当 config 无法产出合法路径时。
    """
    cfg_path = Path(config_path).resolve()
    if not cfg_path.is_file():
        raise ConfigInvalidForStatus(f"config 文件不存在：{cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    agents = config.get("agents") or {}
    if not isinstance(agents, dict):
        raise ConfigInvalidForStatus(f"agents 块不是 dict：{agents!r}")

    examinee = _model_of(agents, "examinee")
    sp = _model_of(agents, "sp")
    environment = _model_of(agents, "environment")
    evaluator = _model_of(agents, "evaluator")
    if not (sp == environment == evaluator):
        raise ConfigInvalidForStatus(
            "sp / environment / evaluator 模型必须相同才能产出 status marker，"
            f"当前 sp={sp!r}, environment={environment!r}, evaluator={evaluator!r}"
        )
    # test-time 策略激活时给 examinee 段加 `__tts-<tag>` 后缀做隔离：
    # 无 test_time 块（历史 config）→ tag 为空 → 路径与历史完全一致，绝不碰旧 marker。
    from .test_time import marker_tag_from_agents

    tag = marker_tag_from_agents(agents)
    if tag:
        examinee = f"{examinee}__tts-{tag}"
    return _STATUS_ROOT / sp / examinee


def marker_filename(scenario_dir: str | Path) -> str:
    """给定 scenario 绝对路径，返回 `<case>_<scenario>.txt`。"""
    p = Path(scenario_dir).resolve()
    return f"{p.parent.name}_{p.name}.txt"


def write_marker(
    config_path: str | Path,
    scenario_dir: str | Path,
    run_dir: str | Path,
) -> Path:
    """写完成 marker；内容 = run_dir 绝对路径（单行）。返回 marker 文件路径。"""
    status_dir = get_status_dir(config_path)
    status_dir.mkdir(parents=True, exist_ok=True)
    marker_path = status_dir / marker_filename(scenario_dir)
    run_dir_abs = Path(run_dir).resolve()
    marker_path.write_text(str(run_dir_abs) + "\n", encoding="utf-8")
    return marker_path


def _cli() -> None:
    """供 shell 脚本调用：

        python -m simulate.status_marker get-status-dir <config>
        python -m simulate.status_marker marker-filename <scenario_dir>
    """
    import sys

    if len(sys.argv) < 2:
        print(
            "usage: python -m simulate.status_marker "
            "get-status-dir <config> | marker-filename <scenario_dir>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    sub = sys.argv[1]
    if sub == "get-status-dir":
        if len(sys.argv) != 3:
            print("usage: get-status-dir <config>", file=sys.stderr)
            raise SystemExit(2)
        print(get_status_dir(sys.argv[2]))
    elif sub == "marker-filename":
        if len(sys.argv) != 3:
            print("usage: marker-filename <scenario_dir>", file=sys.stderr)
            raise SystemExit(2)
        print(marker_filename(sys.argv[2]))
    else:
        print(f"unknown subcommand: {sub}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    _cli()
