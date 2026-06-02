"""
Compare one frozen offline rubric (z-rubric/rubrics/<id>.json) against the
per-run rubrics that the simulation's evaluator extracted on-the-fly inside
each model's run (final_evaluation.json).

Goal: quantify (a) how much the per-run rubrics drift ACROSS the models for
the same scenario (the original concern), and (b) how the new frozen rubric
lines up with them.

Run rubric is read from each model's marker:
  a-simulate/status/deepseek-v4-pro/<model>/<case>_<scenario>.txt -> run dir
  run dir/final_evaluation.json -> {reasoning, PC:{item:bool}, MK:{...}, ...}

We compare ITEM SETS per dimension (keys only; true/false ignored — that is
scoring, not the rubric). Matching uses whitespace-normalised lowercase text;
a looser substring match is also reported.

Usage:
  python compare_rubric_vs_runs.py <case>_<scenario>
  e.g.  python compare_rubric_vs_runs.py mededportal_10011_scenario2
"""
from __future__ import annotations

import json
import re
import sys
from itertools import combinations
from pathlib import Path

DIMS = ["PC", "MK", "SBP", "ICS", "PBLI", "PROF"]
SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parent
TEST_MULTI = PIPELINE_DIR.parent
STATUS_ROOT = TEST_MULTI / "a-simulate" / "status" / "deepseek-v4-pro"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def loose_eq(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    if a == b:
        return True
    if len(a) >= 12 and len(b) >= 12 and (a in b or b in a):
        return True
    return False


def set_overlap(xs: list[str], ys: list[str]) -> tuple[int, int, int]:
    """(exact-normalised overlap, loose overlap, |xs|) — overlap counted on xs side."""
    nys = [norm(y) for y in ys]
    exact = sum(1 for x in xs if norm(x) in nys)
    loose = sum(1 for x in xs if any(loose_eq(x, y) for y in ys))
    return exact, loose, len(xs)


def jaccard(xs: list[str], ys: list[str]) -> float:
    nx, ny = {norm(x) for x in xs}, {norm(y) for y in ys}
    if not nx and not ny:
        return 1.0
    return len(nx & ny) / len(nx | ny) if (nx | ny) else 1.0


def load_run_rubric(eval_path: Path) -> dict[str, list[str]]:
    d = json.loads(eval_path.read_text(encoding="utf-8"))
    return {k: list(d.get(k, {}).keys()) if isinstance(d.get(k), dict) else []
            for k in DIMS}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: compare_rubric_vs_runs.py <case>_<scenario>")
    rid = sys.argv[1]

    new_path = PIPELINE_DIR / "rubrics" / f"{rid}.json"
    nd = json.loads(new_path.read_text(encoding="utf-8"))
    new_rub = {k: list(nd.get(k, [])) for k in DIMS}
    new_all = [t for k in DIMS for t in new_rub[k]]

    # collect each model's per-run rubric
    runs: dict[str, dict[str, list[str]]] = {}
    for mdir in sorted(STATUS_ROOT.iterdir()):
        if not mdir.is_dir():
            continue
        marker = mdir / f"{rid}.txt"
        if not marker.is_file():
            continue
        run_dir = Path(marker.read_text(encoding="utf-8").strip())
        ev = run_dir / "final_evaluation.json"
        if ev.is_file():
            runs[mdir.name] = load_run_rubric(ev)

    print(f"=== Scenario: {rid} ===")
    print(f"models with a run: {len(runs)}  ({', '.join(runs)})\n")

    # 1) per-dimension item counts: new vs each model
    hdr = f"{'source':<24}" + "".join(f"{d:>6}" for d in DIMS) + f"{'TOT':>6}"
    print("【1】每维度 item 数(rubric 大小,不看 true/false)")
    print(hdr)
    print(f"{'NEW offline rubric':<24}" +
          "".join(f"{len(new_rub[d]):>6}" for d in DIMS) +
          f"{len(new_all):>6}")
    for m, rr in runs.items():
        tot = sum(len(rr[d]) for d in DIMS)
        print(f"{m:<24}" + "".join(f"{len(rr[d]):>6}" for d in DIMS) + f"{tot:>6}")

    # 2) cross-model drift among the 7 ORIGINAL per-run rubrics
    print("\n【2】原模拟里 7 个 run 之间的 rubric 漂移(归一化文本)")
    sizes = [sum(len(rr[d]) for d in DIMS) for rr in runs.values()]
    print(f"  各 run 总 item: min={min(sizes)} max={max(sizes)} "
          f"range={max(sizes)-min(sizes)}")
    all_norm = [{norm(t) for d in DIMS for t in rr[d]} for rr in runs.values()]
    inter = set.intersection(*all_norm) if all_norm else set()
    union = set.union(*all_norm) if all_norm else set()
    print(f"  7 个 run 的 item 并集={len(union)}  交集={len(inter)}  "
          f"(只有 {len(inter)}/{len(union)} 条所有模型都抽到 = "
          f"{len(inter)/len(union)*100:.0f}% 一致)" if union else "  (空)")
    js = [jaccard([t for d in DIMS for t in a[d]],
                  [t for d in DIMS for t in b[d]])
          for a, b in combinations(runs.values(), 2)]
    if js:
        print(f"  两两 Jaccard 相似度: min={min(js):.2f} "
              f"mean={sum(js)/len(js):.2f} max={max(js):.2f}  "
              f"(1.0=完全相同, 越低说明每个 run 抽的 rubric 越不一样)")

    # 3) new frozen rubric vs each model's per-run rubric
    print("\n【3】新冻结 rubric vs 各模型 run rubric(覆盖率)")
    print(f"{'model':<24}{'run总':>6}{'新∩run(精确)':>14}{'新∩run(宽松)':>14}"
          f"{'仅新有':>8}{'仅run有':>9}{'Jac':>6}")
    for m, rr in runs.items():
        run_all = [t for d in DIMS for t in rr[d]]
        ex, lo, _ = set_overlap(new_all, run_all)
        ronly_ex, ronly_lo, _ = set_overlap(run_all, new_all)
        only_new = len(new_all) - lo
        only_run = len(run_all) - ronly_lo
        print(f"{m:<24}{len(run_all):>6}{ex:>11}/{len(new_all):<2}"
              f"{lo:>11}/{len(new_all):<2}{only_new:>8}{only_run:>9}"
              f"{jaccard(new_all, run_all):>6.2f}")

    # 4) concrete item-level diff against the deepseek-v4-pro run (the model
    #    that also did sp/env/eval, i.e. closest to the offline extractor)
    ref = "deepseek-v4-pro"
    if ref in runs:
        print(f"\n【4】逐条对照:NEW vs {ref} run(宽松匹配)")
        for d in DIMS:
            nset, rset = new_rub[d], runs[ref][d]
            if not nset and not rset:
                continue
            print(f"  -- {d}: new={len(nset)} run={len(rset)}")
            for t in nset:
                if not any(loose_eq(t, r) for r in rset):
                    print(f"     [仅NEW] {t[:100]}")
            for r in rset:
                if not any(loose_eq(r, t) for t in nset):
                    print(f"     [仅RUN] {r[:100]}")


if __name__ == "__main__":
    main()
