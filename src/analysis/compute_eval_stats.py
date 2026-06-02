"""Generate evaluation statistics report and figures for GPT-5.1 (and other models).

Inputs (relative to repository root /mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi):
- a-simulate/runs/<case>/<scenario>/<timestamp>/run_models.json
- a-simulate/runs/<case>/<scenario>/<timestamp>/final_evaluation.json
- z-process-v3/outputs/evaluator_simple_scores.csv
- z-process-v3/outputs/evaluator_long.csv
- mededportal_*/phase1_scenarios_meta.json

Outputs (under z-stat-figure/):
- eval_stats_report.md
- figures/*.png
- tables/*.csv

Run:
    /mnt/petrelfs/liangcheng/miniconda3/envs/mm/bin/python z-stat-figure/compute_eval_stats.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from glob import glob
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# Paths
ROOT = Path("/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi")
OUT_DIR = ROOT / "z-stat-figure"
FIG_DIR = OUT_DIR / "figures"
TBL_DIR = OUT_DIR / "tables"
REPORT_PATH = OUT_DIR / "eval_stats_report.md"

FIG_DIR.mkdir(parents=True, exist_ok=True)
TBL_DIR.mkdir(parents=True, exist_ok=True)

SIMPLE_CSV = ROOT / "z-process-v3/outputs/evaluator_simple_scores.csv"
LONG_CSV = ROOT / "z-process-v3/outputs/evaluator_long.csv"

# Use DejaVu Sans (always available) since the server has no CJK font installed.
plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# Chinese category -> English label, used only on figure axes (Markdown keeps Chinese).
CATEGORY_EN = {
    "病史采集与查体": "History & PE",
    "监护与状态识别": "Monitoring & status",
    "检查申请": "Order tests",
    "检查解读": "Interpret tests",
    "鉴别诊断更新": "Differential dx",
    "治疗或干预": "Treatment / intervention",
    "会诊与收治去向": "Consult & disposition",
    "禁忌或危险动作": "Contraindicated actions",
    "其他类别": "Other",
}


# ---------------------------------------------------------------------------
# Loading helpers
def load_run_models() -> dict[str, str]:
    """Return run_dir (path relative to ROOT, matching long CSV) -> examinee_model."""
    out: dict[str, str] = {}
    for mf in glob(str(ROOT / "a-simulate/runs/*/scenario*/*/run_models.json")):
        try:
            d = json.load(open(mf))
        except Exception:
            continue
        rel = os.path.relpath(os.path.dirname(mf), ROOT)
        out[rel] = d.get("models", {}).get("examinee") or "unknown"
    return out


def load_scenario_metadata() -> tuple[dict[tuple[str, str], dict], list[dict]]:
    """Return (case_id, scenario_id) -> meta, plus a flat list of scenarios."""
    idx: dict[tuple[str, str], dict] = {}
    flat: list[dict] = []
    for f in glob(str(ROOT / "mededportal_*/phase1_scenarios_meta.json")):
        cid = os.path.basename(os.path.dirname(f))
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for s in d.get("scenarios", []):
            s2 = dict(s)
            s2["case_id"] = cid
            idx[(cid, s2["scenario_id"])] = s2
            flat.append(s2)
    return idx, flat


def load_long_rows():
    with open(LONG_CSV) as f:
        for r in csv.DictReader(f):
            yield r


def load_simple_rows():
    with open(SIMPLE_CSV) as f:
        for r in csv.DictReader(f):
            yield r


# ---------------------------------------------------------------------------
# Normalization helpers (same convention used in the chat session)
def norm_gender(v):
    if not v:
        return None
    s = str(v).lower()
    if "female" in s or "女" in s or s.strip() == "f":
        return "female"
    if "male" in s or "男" in s or s.strip() == "m":
        return "male"
    if "either" in s or "any" in s or " or " in f" {s} ":
        return "either/any"
    return "other"


def norm_age(v):
    if not v:
        return None
    s = str(v)
    nums = [int(x) for x in re.findall(r"\d+", s) if 0 < int(x) < 130]
    if not nums:
        sl = s.lower()
        if any(w in sl for w in ["neonate", "newborn", "infant", "baby"]):
            return "0-1 (infant)"
        if any(w in sl for w in ["child", "pediatric", "toddler"]):
            return "2-12 (child)"
        if any(w in sl for w in ["teen", "adolescent"]):
            return "13-17 (teen)"
        if any(w in sl for w in ["adult", "young"]):
            return "18-39 (adult)"
        if "middle" in sl:
            return "40-64 (middle)"
        if any(w in sl for w in ["elder", "old", "senior", "geriatric"]):
            return "65+ (older)"
        return "other-text"
    a = sum(nums) / len(nums)
    if a < 2:
        return "0-1 (infant)"
    if a < 13:
        return "2-12 (child)"
    if a < 18:
        return "13-17 (teen)"
    if a < 40:
        return "18-39 (adult)"
    if a < 65:
        return "40-64 (middle)"
    return "65+ (older)"


_DEPT_PAIRS = [
    ("emergency", "Emergency"), ("急诊", "Emergency"),
    ("icu", "ICU"), ("intensive", "ICU"),
    ("pediat", "Pediatrics"), ("儿", "Pediatrics"),
    ("obstet", "OB/GYN"), ("gynec", "OB/GYN"), ("妇产", "OB/GYN"),
    ("surg", "Surgery"), ("外科", "Surgery"),
    ("anesth", "Anesthesiology"), ("麻醉", "Anesthesiology"),
    ("intern", "Internal Medicine"), ("内科", "Internal Medicine"),
    ("family", "Family Medicine"), ("全科", "Family Medicine"),
    ("psychi", "Psychiatry"), ("精神", "Psychiatry"),
    ("ortho", "Orthopedics"), ("骨", "Orthopedics"),
    ("cardio", "Cardiology"), ("心脏", "Cardiology"),
    ("neuro", "Neurology"), ("神经", "Neurology"),
    ("palliat", "Palliative"),
    ("outpat", "Outpatient"), ("clinic", "Outpatient"), ("门诊", "Outpatient"),
    ("hospit", "Inpatient"), ("ward", "Inpatient"), ("病房", "Inpatient"),
]


def norm_dept(v):
    if not v:
        return None
    s = str(v).lower()
    for kw, lbl in _DEPT_PAIRS:
        if kw in s:
            return lbl
    return "Other"


def norm_urg(v):
    if not v:
        return None
    s = str(v).lower()
    if any(w in s for w in ["high", "急", "urgent", "severe", "critical", "emergent", "life-thr"]):
        return "High/urgent"
    if any(w in s for w in ["mod", "medium", "中"]):
        return "Medium"
    if any(w in s for w in ["low", "mild", "轻", "non-urgent", "non-acute", "elective", "stable"]):
        return "Low/non-urgent"
    return "Other"


# ---------------------------------------------------------------------------
# Statistics
def overall_run_summary():
    """Return high-level counts across the runs/ tree."""
    runs = glob(str(ROOT / "a-simulate/runs/*/scenario*/*"))
    runs = [r for r in runs if os.path.isdir(r)]
    by_date = Counter(os.path.basename(r)[:8] for r in runs)
    cases = {os.path.basename(os.path.dirname(os.path.dirname(r))) for r in runs}
    cs = {(os.path.basename(os.path.dirname(os.path.dirname(r))), os.path.basename(os.path.dirname(r))) for r in runs}
    return {
        "total_runs": len(runs),
        "unique_cases": len(cases),
        "unique_case_scenario": len(cs),
        "by_date": dict(sorted(by_date.items())),
    }


def model_breakdown(rows):
    by_model = defaultdict(list)
    for r in rows:
        if r["run_name"] == "__TOTAL__":
            continue
        by_model[r["examinee_model"]].append(r)
    out = []
    for model, rs in sorted(by_model.items(), key=lambda x: -len(x[1])):
        scores = [float(r["completion_score_100"]) for r in rs if r["completion_score_100"]]
        trues = sum(int(r["true_items"]) for r in rs)
        totals = sum(int(r["total_items"]) for r in rs)
        out.append({
            "model": model,
            "runs": len(rs),
            "true": trues,
            "total": totals,
            "micro_pct": trues / totals * 100 if totals else 0.0,
            "macro_mean": statistics.mean(scores) if scores else 0.0,
            "median": statistics.median(scores) if scores else 0.0,
            "min": min(scores) if scores else 0.0,
            "max": max(scores) if scores else 0.0,
            "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        })
    return out


def gpt51_per_run_stats(run_models):
    """Aggregate per-run stats for gpt-5.1 from the long CSV."""
    target = {rd for rd, m in run_models.items() if m == "gpt-5.1"}
    per_run = defaultdict(lambda: {"true": 0, "total": 0, "case_id": None, "scenario_id": None})
    cat_true = defaultdict(int)
    cat_total = defaultdict(int)
    sub_true = defaultdict(int)
    sub_total = defaultdict(int)
    for r in load_long_rows():
        if r["run_dir"] not in target:
            continue
        try:
            v = int(r["value"])
        except Exception:
            continue
        rd = r["run_dir"]
        per_run[rd]["true"] += v
        per_run[rd]["total"] += 1
        per_run[rd]["case_id"] = r["case_id"]
        per_run[rd]["scenario_id"] = r["source_scenario_dir"]
        cat = r["category"]
        sub = r["subcategory"] or "(无)"
        cat_total[cat] += 1
        cat_true[cat] += v
        sub_total[(cat, sub)] += 1
        sub_true[(cat, sub)] += v
    return per_run, cat_true, cat_total, sub_true, sub_total


def crosstab(per_run, meta_idx, key_fn):
    grp = defaultdict(lambda: {"true": 0, "total": 0, "n": 0, "pcts": []})
    for d in per_run.values():
        m = meta_idx.get((d["case_id"], d["scenario_id"]))
        if not m:
            label = "(no-metadata)"
        else:
            label = key_fn(m) or "(missing)"
        grp[label]["true"] += d["true"]
        grp[label]["total"] += d["total"]
        grp[label]["n"] += 1
        if d["total"]:
            grp[label]["pcts"].append(d["true"] / d["total"] * 100)
    out = []
    for k, d in sorted(grp.items(), key=lambda x: -x[1]["n"]):
        out.append({
            "label": k,
            "n": d["n"],
            "true": d["true"],
            "total": d["total"],
            "micro_pct": d["true"] / d["total"] * 100 if d["total"] else 0.0,
            "macro_mean": statistics.mean(d["pcts"]) if d["pcts"] else 0.0,
        })
    return out


# ---------------------------------------------------------------------------
# Plotting
def _save(fig, name):
    p_png = FIG_DIR / f"{name}.png"
    fig.savefig(p_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return p_png


def plot_score_histogram(per_run):
    scores = [d["true"] / d["total"] * 100 for d in per_run.values() if d["total"]]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(scores, bins=[0, 20, 40, 60, 80, 90, 100, 100.001], edgecolor="black", color="#4C72B0")
    ax.set_title(f"GPT-5.1 run score distribution (n={len(scores)})")
    ax.set_xlabel("Completion score (0-100)")
    ax.set_ylabel("# runs")
    return _save(fig, "gpt51_score_histogram")


def plot_category_bar(cat_true, cat_total):
    cats = sorted(cat_total, key=lambda c: -cat_total[c])
    labels = [CATEGORY_EN.get(c, c) for c in cats]
    pcts = [cat_true[c] / cat_total[c] * 100 for c in cats]
    totals = [cat_total[c] for c in cats]
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(labels, pcts, color="#55A868")
    for bar, t, n in zip(bars, [cat_true[c] for c in cats], totals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{t}/{n}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("Completion (%)")
    ax.set_ylim(0, 105)
    ax.set_title("GPT-5.1 completion by category (micro)")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    return _save(fig, "gpt51_category_completion")


def plot_crosstab(rows, label, name):
    rows = [r for r in rows if r["n"] > 0]
    rows.sort(key=lambda r: -r["micro_pct"])
    labels = [r["label"] for r in rows]
    micro = [r["micro_pct"] for r in rows]
    macro = [r["macro_mean"] for r in rows]
    ns = [r["n"] for r in rows]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = range(len(labels))
    w = 0.4
    b1 = ax.bar([i - w / 2 for i in x], micro, width=w, label="micro %", color="#4C72B0")
    b2 = ax.bar([i + w / 2 for i in x], macro, width=w, label="macro mean %", color="#DD8452")
    for i, n in enumerate(ns):
        ax.text(i, max(micro[i], macro[i]) + 1, f"n={n}", ha="center", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("%")
    ax.set_ylim(0, 105)
    ax.set_title(f"GPT-5.1 score by {label}")
    ax.legend()
    return _save(fig, name)


def plot_metadata_coverage(scenarios):
    fields = ["scenario_title", "patient_age", "patient_gender", "department",
              "injury_body_part", "urgency_level", "patient_race", "patient_nationality"]
    pcts = [sum(1 for s in scenarios if s.get(f)) / len(scenarios) * 100 for f in fields]
    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.barh(fields[::-1], pcts[::-1], color="#8172B3")
    for bar, p in zip(bars, pcts[::-1]):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"{p:.1f}%", va="center", fontsize=8)
    ax.set_xlim(0, 110)
    ax.set_xlabel("Coverage %")
    ax.set_title(f"Scenario metadata coverage (N={len(scenarios)})")
    return _save(fig, "metadata_coverage")


def demographic_distributions(scenarios):
    """Return {field_label: [(category, count), ...]} (sorted by count desc, missing last)."""
    def _dist(values):
        c = Counter(values)
        miss = c.pop(None, 0)
        rows = sorted(c.items(), key=lambda x: -x[1])
        if miss:
            rows.append(("(missing)", miss))
        return rows

    return {
        "Department": _dist(norm_dept(s.get("department")) for s in scenarios),
        "Urgency": _dist(norm_urg(s.get("urgency_level")) for s in scenarios),
        "Gender": _dist(norm_gender(s.get("patient_gender")) for s in scenarios),
        "Age group": _dist(norm_age(s.get("patient_age")) for s in scenarios),
        "Race (raw, top 10)": _dist(s.get("patient_race") for s in scenarios)[:10],
        "Nationality (raw, top 10)": _dist(s.get("patient_nationality") for s in scenarios)[:10],
    }


def injury_body_part_tokens(scenarios, top_n=15):
    tok = Counter()
    for s in scenarios:
        v = s.get("injury_body_part")
        if not v:
            continue
        for p in re.split(r"[;,/、]| and ", str(v)):
            p = p.strip().lower()
            if p:
                tok[p] += 1
    return tok.most_common(top_n)


def plot_demographics(scenarios):
    """Multi-panel figure: department / urgency / gender / age group."""
    panels = [
        ("Department",  Counter(norm_dept(s.get("department"))     for s in scenarios)),
        ("Urgency",     Counter(norm_urg(s.get("urgency_level"))   for s in scenarios)),
        ("Gender",      Counter(norm_gender(s.get("patient_gender"))for s in scenarios)),
        ("Age group",   Counter(norm_age(s.get("patient_age"))     for s in scenarios)),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]
    for ax, (title, c), col in zip(axes.flat, panels, colors):
        miss = c.pop(None, 0)
        items = sorted(c.items(), key=lambda x: -x[1])
        if miss:
            items.append(("(missing)", miss))
        labels = [k for k, _ in items]
        vals = [v for _, v in items]
        ax.bar(labels, vals, color=col)
        ax.set_title(f"{title} (N={sum(vals)})")
        ax.set_ylabel("# scenarios")
        for i, v in enumerate(vals):
            ax.text(i, v + 0.5, str(v), ha="center", fontsize=8)
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=8)
    fig.tight_layout()
    return _save(fig, "scenario_demographics")


def plot_scenarios_per_case(scenarios):
    spc = Counter()
    for s in scenarios:
        spc[s["case_id"]] += 1
    dist = Counter(spc.values())
    keys = sorted(dist)
    counts = [dist[k] for k in keys]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar([str(k) for k in keys], counts, color="#C44E52")
    ax.set_xlabel("scenarios per case")
    ax.set_ylabel("# cases")
    ax.set_title(f"Scenarios per case (cases={len(spc)}, total scenarios={sum(spc.values())})")
    for i, c in enumerate(counts):
        ax.text(i, c + 0.3, str(c), ha="center", fontsize=8)
    return _save(fig, "scenarios_per_case")


def plot_per_case_scores(per_run, meta_idx):
    by_case = defaultdict(list)
    for d in per_run.values():
        if d["total"]:
            by_case[d["case_id"]].append(d["true"] / d["total"] * 100)
    cases = sorted(by_case, key=lambda c: -statistics.mean(by_case[c]))
    means = [statistics.mean(by_case[c]) for c in cases]
    ns = [len(by_case[c]) for c in cases]
    fig, ax = plt.subplots(figsize=(11, 5))
    bars = ax.bar(cases, means, color="#4C72B0")
    for i, (b, n) in enumerate(zip(bars, ns)):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                f"n={n}", ha="center", fontsize=7)
    ax.set_ylabel("Macro mean score (%)")
    ax.set_ylim(0, 110)
    ax.set_title(f"GPT-5.1 per-case macro mean (cases={len(cases)})")
    plt.setp(ax.get_xticklabels(), rotation=60, ha="right", fontsize=8)
    return _save(fig, "gpt51_per_case_mean")


# ---------------------------------------------------------------------------
# CSV exports
def export_per_case_scenario_csv(per_run):
    p = TBL_DIR / "gpt51_per_case_scenario.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "scenario_id", "true", "total", "completion_pct"])
        rows = sorted(per_run.values(), key=lambda d: (d["case_id"], d["scenario_id"]))
        for d in rows:
            pct = d["true"] / d["total"] * 100 if d["total"] else 0.0
            w.writerow([d["case_id"], d["scenario_id"], d["true"], d["total"], f"{pct:.4f}"])
    return p


def export_category_csv(cat_true, cat_total):
    p = TBL_DIR / "gpt51_category_completion.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["category", "true", "total", "completion_pct"])
        for c in sorted(cat_total, key=lambda c: -cat_total[c]):
            w.writerow([c, cat_true[c], cat_total[c], f"{cat_true[c]/cat_total[c]*100:.4f}"])
    return p


def export_metadata_flat_csv(scenarios):
    p = TBL_DIR / "scenario_metadata_flat.csv"
    fields = ["case_id", "scenario_id", "scenario_title", "department", "urgency_level",
              "patient_gender", "patient_age", "patient_race", "patient_nationality",
              "injury_body_part"]
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in scenarios:
            w.writerow({k: s.get(k) for k in fields})
    return p


# ---------------------------------------------------------------------------
# Markdown report
def _md_table(headers, rows, aligns=None):
    aligns = aligns or ["---"] * len(headers)
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(aligns) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def render_markdown(overall, models, per_run, cat_true, cat_total, sub_true, sub_total,
                    scenarios, ct_dept, ct_urg, ct_gender, ct_age):
    lines = []
    A = lines.append
    A("# 模拟评测统计报告\n")
    A("> 由 `compute_eval_stats.py` 自动生成。要更新数字、图表与表格，请重跑脚本。\n")

    A("## 1. 总体 run 概况\n")
    A(_md_table(
        ["指标", "值"],
        [["总 run 数", overall["total_runs"]],
         ["唯一 case 数", overall["unique_cases"]],
         ["唯一 case+scenario 数", overall["unique_case_scenario"]]],
    ))
    A("**按日期分布的 run 数**\n")
    A(_md_table(["日期", "run 数"], list(overall["by_date"].items())))

    A("\n## 2. 各模型整体表现\n")
    A(_md_table(
        ["模型", "runs", "micro %", "macro mean", "中位数", "stdev", "min/max"],
        [[m["model"], m["runs"], f"{m['micro_pct']:.2f}", f"{m['macro_mean']:.2f}",
          f"{m['median']:.2f}", f"{m['stdev']:.2f}", f"{m['min']:.2f}/{m['max']:.2f}"]
         for m in models],
    ))
    A("\n![score histogram](figures/gpt51_score_histogram.png)\n")

    A("\n## 3. GPT-5.1 评分项分类（micro）\n")
    cat_rows = []
    for c in sorted(cat_total, key=lambda c: -cat_total[c]):
        cat_rows.append([c, cat_true[c], cat_total[c], f"{cat_true[c]/cat_total[c]*100:.2f}%"])
    A(_md_table(["类别", "true", "total", "完成率"], cat_rows))
    A("\n> 「禁忌或危险动作」中 true 通常表示触发了不当动作，低分可能反而是好事，请结合条目原始定义解读。\n")
    A("\n![category](figures/gpt51_category_completion.png)\n")

    A("\n### 3.1 子类极端值（仅显示样本 ≥ 5 的子类）\n")
    sub_rows = []
    for (cat, sub), n in sorted(sub_total.items(), key=lambda x: -x[1]):
        if n < 5:
            continue
        t = sub_true[(cat, sub)]
        sub_rows.append([cat, sub[:40], t, n, f"{t/n*100:.2f}%"])
    A(_md_table(["类别", "子类", "true", "total", "完成率"], sub_rows))

    A("\n## 4. 元数据维度（来自 phase1_scenarios_meta.json）\n")
    cases_with_meta = len({s["case_id"] for s in scenarios})
    A(f"- 已生成元数据的 case 数：**{cases_with_meta}**\n- 总 scenario 数：**{len(scenarios)}**\n")
    A("\n![metadata coverage](figures/metadata_coverage.png)\n")
    A("\n![scenarios per case](figures/scenarios_per_case.png)\n")

    A("\n### 4.1 人群与场景维度分布\n")
    A("\n下面所有计数的分母都是 **总 scenario 数 = "
      f"{len(scenarios)}**；缺失的字段单独统计为 `(missing)`。\n")
    A("\n![demographics](figures/scenario_demographics.png)\n")
    demo = demographic_distributions(scenarios)
    for label, rows in demo.items():
        A(f"\n**{label}**\n")
        A(_md_table(["类别", "scenario 数", "占比"],
                    [[k, v, f"{v/len(scenarios)*100:.2f}%"] for k, v in rows]))
    A("\n**Injury body part — 高频 token (top 15)**\n")
    body = injury_body_part_tokens(scenarios)
    A(_md_table(["token", "出现次数"], [[k, v] for k, v in body]))

    A("\n## 5. GPT-5.1 评分 × 元数据交叉表\n")

    def render_ct(label, rows):
        A(f"\n### 5.x by {label}\n")
        A(_md_table(
            ["类别", "runs", "true", "total", "micro %", "macro mean %"],
            [[r["label"], r["n"], r["true"], r["total"],
              f"{r['micro_pct']:.2f}", f"{r['macro_mean']:.2f}"] for r in rows],
        ))

    render_ct("Department", ct_dept)
    A("\n![dept](figures/gpt51_score_by_department.png)\n")
    render_ct("Urgency", ct_urg)
    A("\n![urgency](figures/gpt51_score_by_urgency.png)\n")
    render_ct("Gender", ct_gender)
    A("\n![gender](figures/gpt51_score_by_gender.png)\n")
    render_ct("Age group", ct_age)
    A("\n![age](figures/gpt51_score_by_age.png)\n")

    A("\n## 6. GPT-5.1 每个案例的场景明细\n")
    by_case = defaultdict(list)
    for d in per_run.values():
        by_case[d["case_id"]].append(d)
    case_rows = []
    for cid in sorted(by_case):
        recs = sorted(by_case[cid], key=lambda d: d["scenario_id"])
        case_t = sum(r["true"] for r in recs)
        case_n = sum(r["total"] for r in recs)
        case_pct = case_t / case_n * 100 if case_n else 0
        per_scn = "; ".join(
            f"{r['scenario_id']}:{r['true']}/{r['total']}({(r['true']/r['total']*100 if r['total'] else 0):.0f}%)"
            for r in recs
        )
        case_rows.append([cid, len(recs), f"{case_t}/{case_n}", f"{case_pct:.2f}%", per_scn])
    A(_md_table(["case", "scn 数", "聚合 true/total", "聚合 %", "各场景明细"], case_rows))
    A("\n![per case](figures/gpt51_per_case_mean.png)\n")

    A("\n## 7. 数据文件\n")
    A("- `tables/gpt51_per_case_scenario.csv` — 每个 (case, scenario) 一行\n")
    A("- `tables/gpt51_category_completion.csv` — 每个评分类别一行\n")
    A("- `tables/scenario_metadata_flat.csv` — 全部场景的元数据扁平表\n")
    A("- `figures/*.png` — 上面各章节引用的图\n")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return REPORT_PATH


# ---------------------------------------------------------------------------
def main():
    print("[1/6] Loading run_models.json ...")
    run_models = load_run_models()

    print("[2/6] Loading scenario metadata ...")
    meta_idx, scenarios = load_scenario_metadata()

    print("[3/6] Reading evaluator CSVs ...")
    simple_rows = list(load_simple_rows())
    models = model_breakdown(simple_rows)

    print("[4/6] Aggregating GPT-5.1 long CSV ...")
    per_run, cat_true, cat_total, sub_true, sub_total = gpt51_per_run_stats(run_models)

    print("[5/6] Plotting ...")
    plot_score_histogram(per_run)
    plot_category_bar(cat_true, cat_total)
    plot_metadata_coverage(scenarios)
    plot_scenarios_per_case(scenarios)
    plot_demographics(scenarios)
    plot_per_case_scores(per_run, meta_idx)

    ct_dept = crosstab(per_run, meta_idx, lambda m: norm_dept(m.get("department")))
    ct_urg = crosstab(per_run, meta_idx, lambda m: norm_urg(m.get("urgency_level")))
    ct_gender = crosstab(per_run, meta_idx, lambda m: norm_gender(m.get("patient_gender")))
    ct_age = crosstab(per_run, meta_idx, lambda m: norm_age(m.get("patient_age")))
    plot_crosstab(ct_dept, "Department", "gpt51_score_by_department")
    plot_crosstab(ct_urg, "Urgency", "gpt51_score_by_urgency")
    plot_crosstab(ct_gender, "Gender", "gpt51_score_by_gender")
    plot_crosstab(ct_age, "Age group", "gpt51_score_by_age")

    print("[6/6] Writing tables and report ...")
    export_per_case_scenario_csv(per_run)
    export_category_csv(cat_true, cat_total)
    export_metadata_flat_csv(scenarios)

    overall = overall_run_summary()
    render_markdown(overall, models, per_run, cat_true, cat_total, sub_true, sub_total,
                    scenarios, ct_dept, ct_urg, ct_gender, ct_age)

    print(f"Done. Report -> {REPORT_PATH}")
    print(f"Figures -> {FIG_DIR}")
    print(f"Tables  -> {TBL_DIR}")


if __name__ == "__main__":
    main()
