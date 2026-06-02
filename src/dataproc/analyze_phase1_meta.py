#!/usr/bin/env python3

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from xml.sax.saxutils import escape


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CASE_FILE = os.path.join(SCRIPT_DIR, "case_directories.json")
DEFAULT_OUT_DIR = os.path.join(SCRIPT_DIR, "metadata_stats")
FONT_STACK = "DejaVu Sans, Liberation Sans, Arial, sans-serif"


FIELDS = [
    "scenario_title",
    "department",
    "urgency_level",
    "patient_gender",
    "patient_age",
    "patient_race",
    "patient_nationality",
    "injury_body_part",
]

FIELD_LABELS = {
    "scenario_title": "Scenario title",
    "department": "Department",
    "urgency_level": "Urgency",
    "patient_gender": "Sex",
    "patient_age": "Age",
    "patient_race": "Race/ethnicity",
    "patient_nationality": "Nationality",
    "injury_body_part": "Body part",
}

HEATMAP_FIELD_LABELS = [
    ("scenario_title", "Title"),
    ("department", "Dept"),
    ("urgency_level", "Urgency"),
    ("patient_gender", "Sex"),
    ("patient_age", "Age"),
    ("patient_race", "Race"),
    ("patient_nationality", "Nation"),
    ("injury_body_part", "Injury"),
]


def is_missing(value):
    return value is None or str(value).strip() == ""


def load_case_directories(case_file):
    with open(case_file, "r", encoding="utf-8") as handle:
        directories = json.load(handle)
    if not isinstance(directories, list):
        raise SystemExit("case_directories.json must contain a JSON list")
    return directories


def normalize_gender(raw_value):
    if is_missing(raw_value):
        return "Missing"
    text = str(raw_value).strip().lower()
    if text in {"male", "man", "m", "boy", "男"}:
        return "Male"
    if text in {"female", "woman", "f", "girl", "女"}:
        return "Female"
    if "male or female" in text or "female/male" in text or "male/female" in text:
        return "Either sex"
    return "Other/unspecified"


def extract_age_years(raw_value):
    if is_missing(raw_value):
        return None
    text = str(raw_value).strip().lower()
    if "month" in text:
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if match:
            return float(match.group(1)) / 12.0
    if "岁" in text:
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if match:
            return float(match.group(1))
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text)
    if range_match:
        return (float(range_match.group(1)) + float(range_match.group(2))) / 2.0
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if match:
        return float(match.group(1))
    return None


def normalize_age_group(raw_value):
    years = extract_age_years(raw_value)
    if years is None:
        return "Missing"
    if years < 18:
        return "Pediatric"
    if years < 65:
        return "Adult"
    return "Older adult"


def normalize_department(raw_value):
    if is_missing(raw_value):
        return "Unspecified"
    text = str(raw_value).strip().lower()
    if "emergency" in text or text == "er":
        return "Emergency"
    if "麻醉" in str(raw_value):
        return "Anesthesiology"
    if "icu" in text:
        return "ICU"
    if "general medicine" in text or "medicine floor" in text:
        return "Inpatient medicine"
    if "outpatient" in text or "clinic" in text:
        return "Outpatient"
    if "surgical" in text:
        return "Surgical"
    if "psychiatry" in text:
        return "Psychiatry"
    return str(raw_value).strip()


def normalize_urgency(raw_value):
    if is_missing(raw_value):
        return "Missing"
    text = str(raw_value).strip().lower()
    if "elective" in text:
        return "Elective"
    if "very mild" in text or "mild" in text:
        return "Mild/non-acute"
    if "acute" in text or "紧急" in str(raw_value) or "severe" in text:
        return "Acute/emergent"
    return "Other specified"


def flatten_records(case_dirs):
    rows = []
    missing_meta_files = []
    for case_dir in case_dirs:
        case_id = os.path.basename(case_dir.rstrip(os.sep))
        meta_path = os.path.join(case_dir, "phase1_scenarios_meta.json")
        if not os.path.exists(meta_path):
            missing_meta_files.append(case_id)
            continue
        with open(meta_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for item in payload.get("scenarios", []):
            scenario_id = item.get("scenario_id")
            row = {
                "case_id": case_id,
                "scenario_id": scenario_id,
                "scenario_key": f"{case_id}-{scenario_id}",
                "meta_path": meta_path,
            }
            for field in FIELDS:
                row[field] = item.get(field)
                row[f"{field}_present"] = int(not is_missing(item.get(field)))
            row["gender_group"] = normalize_gender(item.get("patient_gender"))
            row["age_group"] = normalize_age_group(item.get("patient_age"))
            row["age_years_estimate"] = extract_age_years(item.get("patient_age"))
            row["department_group"] = normalize_department(item.get("department"))
            row["urgency_group"] = normalize_urgency(item.get("urgency_level"))
            rows.append(row)
    return rows, missing_meta_files


def count_by(rows, key, order=None):
    counter = Counter(row[key] for row in rows)
    if order:
        pairs = [(label, counter.get(label, 0)) for label in order]
        seen = set(order)
        extras = sorted((label, count) for label, count in counter.items() if label not in seen)
        return pairs + extras
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def field_completeness(rows):
    total = len(rows)
    result = []
    for field in FIELDS:
        present = sum(row[f"{field}_present"] for row in rows)
        result.append(
            {
                "field": field,
                "label": FIELD_LABELS[field],
                "present": present,
                "missing": total - present,
                "rate": (present / total) if total else 0.0,
            }
        )
    return result


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


class Svg:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                f'height="{height}" viewBox="0 0 {width} {height}">'
            ),
            "<defs>",
            "<style><![CDATA[",
            f"text {{ font-family: {FONT_STACK}; fill: #18212f; }}",
            ".title { font-size: 26px; font-weight: 700; }",
            ".subtitle { font-size: 13px; fill: #5e6878; }",
            ".panel-title { font-size: 18px; font-weight: 700; }",
            ".axis { font-size: 12px; fill: #556070; }",
            ".label { font-size: 13px; }",
            ".value { font-size: 12px; fill: #334155; }",
            ".small { font-size: 11px; fill: #5e6878; }",
            "]]></style>",
            "</defs>",
            '<rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/>',
        ]

    def add(self, chunk):
        self.lines.append(chunk)

    def rect(self, x, y, width, height, fill, stroke="none", rx=0, ry=0, opacity=1.0):
        self.add(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'fill="{fill}" stroke="{stroke}" rx="{rx}" ry="{ry}" opacity="{opacity}"/>'
        )

    def line(self, x1, y1, x2, y2, stroke="#d3d9e2", stroke_width=1):
        self.add(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{stroke}" stroke-width="{stroke_width}"/>'
        )

    def text(
        self,
        x,
        y,
        value,
        klass="label",
        anchor="start",
        fill=None,
        font_size=None,
        font_weight=None,
    ):
        attrs = [f'x="{x}"', f'y="{y}"', f'text-anchor="{anchor}"', f'class="{klass}"']
        if fill:
            attrs.append(f'fill="{fill}"')
        if font_size:
            attrs.append(f'font-size="{font_size}"')
        if font_weight:
            attrs.append(f'font-weight="{font_weight}"')
        self.add(f"<text {' '.join(attrs)}>{escape(str(value))}</text>")

    def save(self, path):
        ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(self.lines + ["</svg>"]))


def rounded_max(value):
    if value <= 0:
        return 1
    magnitude = 10 ** int(math.floor(math.log10(value)))
    normalized = value / magnitude
    if normalized <= 1:
        rounded = 1
    elif normalized <= 2:
        rounded = 2
    elif normalized <= 5:
        rounded = 5
    else:
        rounded = 10
    return rounded * magnitude


def draw_hbar_panel(svg, x, y, width, height, title, letter, items, color, formatter=None, max_value=None):
    svg.text(x, y + 8, f"{letter}. {title}", klass="panel-title")
    panel_top = y + 28
    label_width = min(180, int(width * 0.38))
    value_width = 86
    plot_x = x + label_width
    plot_width = width - label_width - value_width - 8
    plot_y = panel_top + 18
    plot_h = height - 52
    count = max(len(items), 1)
    row_h = plot_h / count
    if max_value is None:
        max_value = max((value for _, value in items), default=1)
    axis_max = rounded_max(max_value)
    for tick in range(5):
        tick_value = axis_max * tick / 4.0
        tick_x = plot_x + plot_width * tick / 4.0
        svg.line(tick_x, plot_y - 8, tick_x, plot_y + plot_h + 4, stroke="#e8ecf2")
        label = f"{tick_value:.0f}" if abs(tick_value - round(tick_value)) < 1e-6 else f"{tick_value:.1f}"
        svg.text(tick_x, plot_y - 12, label, klass="axis", anchor="middle")
    for index, (label, value) in enumerate(items):
        bar_y = plot_y + index * row_h + row_h * 0.2
        bar_h = row_h * 0.6
        value_w = 0 if axis_max == 0 else (value / axis_max) * plot_width
        svg.text(x, bar_y + bar_h * 0.72, label, klass="label")
        svg.rect(plot_x, bar_y, plot_width, bar_h, fill="#eef2f7", rx=5, ry=5)
        if value_w > 0:
            svg.rect(plot_x, bar_y, value_w, bar_h, fill=color, rx=5, ry=5)
        display = formatter(label, value) if formatter else str(value)
        svg.text(x + width - 2, bar_y + bar_h * 0.72, display, klass="value", anchor="end")


def draw_overview_figure(case_counts, completeness, out_path):
    svg = Svg(1440, 760)
    svg.text(54, 54, "Dataset Overview", klass="title")
    svg.text(
        54,
        80,
        "Phase-1 scenario metadata aggregated from case directories listed in case_directories.json",
        klass="subtitle",
    )
    case_items = list(case_counts)
    completeness_items = [(item["label"], item["present"]) for item in completeness]

    def complete_formatter(label, value):
        item = next(entry for entry in completeness if entry["label"] == label)
        return f"{item['rate'] * 100:.0f}% ({item['present']}/{item['present'] + item['missing']})"

    draw_hbar_panel(
        svg,
        54,
        118,
        620,
        580,
        "Scenarios per case",
        "A",
        case_items,
        color="#2d6aa6",
        formatter=lambda label, value: f"n={value}",
    )
    draw_hbar_panel(
        svg,
        736,
        118,
        650,
        580,
        "Metadata completeness by field",
        "B",
        completeness_items,
        color="#d97941",
        formatter=complete_formatter,
        max_value=max(value for _, value in completeness_items) if completeness_items else 1,
    )
    svg.text(54, 724, "Counts reflect 15 scenarios from 8 case directories.", klass="small")
    svg.save(out_path)


def draw_characteristics_figure(department_counts, age_counts, gender_counts, out_path):
    svg = Svg(1560, 540)
    svg.text(54, 54, "Scenario Characteristics", klass="title")
    svg.text(
        54,
        80,
        "Normalized descriptive categories used to summarize sparse free-text metadata",
        klass="subtitle",
    )
    draw_hbar_panel(
        svg,
        54,
        118,
        470,
        360,
        "Department group",
        "A",
        department_counts,
        color="#2a8f85",
        formatter=lambda label, value: f"n={value}",
    )
    draw_hbar_panel(
        svg,
        548,
        118,
        430,
        360,
        "Age group",
        "B",
        age_counts,
        color="#c05a49",
        formatter=lambda label, value: f"n={value}",
    )
    draw_hbar_panel(
        svg,
        1002,
        118,
        504,
        360,
        "Sex information",
        "C",
        gender_counts,
        color="#7a5ea7",
        formatter=lambda label, value: f"n={value}",
    )
    svg.text(
        54,
        508,
        "Department and urgency values were collapsed into broad groups because the source JSON stores heterogeneous free-text strings.",
        klass="small",
    )
    svg.save(out_path)


def draw_heatmap_figure(rows, out_path):
    width = 1280
    height = 720
    svg = Svg(width, height)
    svg.text(54, 54, "Scenario-Level Metadata Availability", klass="title")
    svg.text(
        54,
        80,
        "Green cells indicate explicitly available metadata in the source markdown-derived JSON; gray indicates missing values.",
        klass="subtitle",
    )
    start_x = 280
    start_y = 140
    cell_w = 96
    cell_h = 28
    fields = [field for field, _ in HEATMAP_FIELD_LABELS]
    for col_index, (_, short_label) in enumerate(HEATMAP_FIELD_LABELS):
        x = start_x + col_index * cell_w + cell_w / 2
        svg.text(x, start_y - 18, short_label, klass="axis", anchor="middle")
    for row_index, row in enumerate(rows):
        y = start_y + row_index * cell_h
        svg.text(54, y + 19, row["scenario_key"], klass="axis")
        for col_index, field in enumerate(fields):
            x = start_x + col_index * cell_w
            present = row[f"{field}_present"] == 1
            fill = "#4c9b74" if present else "#e5e7eb"
            svg.rect(x, y, cell_w - 6, cell_h - 6, fill=fill, rx=4, ry=4)
    legend_y = 620
    svg.rect(54, legend_y, 18, 18, fill="#4c9b74", rx=3, ry=3)
    svg.text(80, legend_y + 14, "Present", klass="axis")
    svg.rect(170, legend_y, 18, 18, fill="#e5e7eb", rx=3, ry=3)
    svg.text(196, legend_y + 14, "Missing", klass="axis")
    svg.save(out_path)


def draw_single_series_hbar(svg, x, y, width, height, title, items, color):
    label_width = min(170, int(width * 0.42))
    value_width = 48
    plot_x = x + label_width
    plot_width = width - label_width - value_width - 10
    plot_y = y + 28
    plot_h = height - 34
    count = max(len(items), 1)
    row_h = plot_h / count
    max_value = max((value for _, value in items), default=1)
    axis_max = rounded_max(max_value)

    svg.text(x, y, title, klass="label", font_size=17, font_weight=700)
    for tick in range(5):
        tick_value = axis_max * tick / 4.0
        tick_x = plot_x + plot_width * tick / 4.0
        svg.line(tick_x, plot_y - 12, tick_x, plot_y + plot_h + 3, stroke="#dde4ee")
        label = f"{tick_value:.0f}" if abs(tick_value - round(tick_value)) < 1e-6 else f"{tick_value:.1f}"
        svg.text(tick_x, plot_y - 18, label, klass="axis", anchor="middle")
    for index, (label, value) in enumerate(items):
        bar_y = plot_y + index * row_h + row_h * 0.22
        bar_h = row_h * 0.56
        svg.text(x, bar_y + bar_h * 0.75, label, klass="axis", font_size=11)
        svg.rect(plot_x, bar_y, plot_width, bar_h, fill="#eef2f7", rx=3, ry=3)
        if value > 0:
            svg.rect(plot_x, bar_y, (value / axis_max) * plot_width, bar_h, fill=color, rx=3, ry=3)
        svg.text(x + width - 2, bar_y + bar_h * 0.75, str(value), klass="axis", anchor="end", font_size=11)


def draw_grouped_hbar(svg, x, y, width, height, title, labels, series_a, series_b, legend_a, legend_b, color_a, color_b):
    label_width = min(175, int(width * 0.42))
    value_width = 0
    plot_x = x + label_width
    plot_width = width - label_width - value_width - 6
    plot_y = y + 42
    plot_h = height - 54
    count = max(len(labels), 1)
    row_h = plot_h / count
    max_value = max(series_a + series_b + [1])
    axis_max = rounded_max(max_value)

    svg.text(x, y, title, klass="label", font_size=17, font_weight=700)
    legend_y = y + 18
    svg.rect(x, legend_y - 10, 12, 12, fill=color_a, rx=2, ry=2)
    svg.text(x + 18, legend_y, legend_a, klass="axis", font_size=11)
    second_x = x + 112
    svg.rect(second_x, legend_y - 10, 12, 12, fill=color_b, rx=2, ry=2)
    svg.text(second_x + 18, legend_y, legend_b, klass="axis", font_size=11)

    for tick in range(5):
        tick_value = axis_max * tick / 4.0
        tick_x = plot_x + plot_width * tick / 4.0
        svg.line(tick_x, plot_y - 12, tick_x, plot_y + plot_h + 3, stroke="#dde4ee")
        label = f"{tick_value:.0f}" if abs(tick_value - round(tick_value)) < 1e-6 else f"{tick_value:.1f}"
        svg.text(tick_x, plot_y - 18, label, klass="axis", anchor="middle")

    for index, label in enumerate(labels):
        row_top = plot_y + index * row_h + row_h * 0.15
        bar_h = row_h * 0.26
        gap = row_h * 0.1
        svg.text(x, row_top + bar_h + gap, label, klass="axis", font_size=11)
        svg.rect(plot_x, row_top, plot_width, bar_h, fill="#eef2f7", rx=2, ry=2)
        svg.rect(plot_x, row_top + bar_h + gap, plot_width, bar_h, fill="#eef2f7", rx=2, ry=2)
        if series_a[index] > 0:
            svg.rect(plot_x, row_top, (series_a[index] / axis_max) * plot_width, bar_h, fill=color_a, rx=2, ry=2)
        if series_b[index] > 0:
            svg.rect(
                plot_x,
                row_top + bar_h + gap,
                (series_b[index] / axis_max) * plot_width,
                bar_h,
                fill=color_b,
                rx=2,
                ry=2,
            )


def draw_method_style_composite(case_counts, completeness, department_counts, age_counts, out_path):
    blue = "#355C85"
    orange = "#D26A2E"
    svg = Svg(1500, 1100)

    svg.text(54, 70, "a. Scenario Coverage", klass="label", font_size=20, font_weight=700)
    draw_single_series_hbar(
        svg,
        54,
        100,
        610,
        360,
        "Scenarios per case directory",
        case_counts,
        blue,
    )

    svg.text(735, 70, "b. Metadata Completeness", klass="label", font_size=20, font_weight=700)
    labels = [item["label"] for item in completeness]
    present = [item["present"] for item in completeness]
    missing = [item["missing"] for item in completeness]
    draw_grouped_hbar(
        svg,
        735,
        100,
        690,
        360,
        "Present vs missing fields",
        labels,
        present,
        missing,
        "Present",
        "Missing",
        blue,
        orange,
    )

    svg.text(54, 520, "c. Patient Case Distributions", klass="label", font_size=20, font_weight=700)
    draw_single_series_hbar(
        svg,
        54,
        560,
        640,
        420,
        "Patient case distributions on department groups",
        department_counts,
        blue,
    )
    draw_single_series_hbar(
        svg,
        815,
        560,
        500,
        420,
        "Patient case distributions on age groups",
        age_counts,
        orange,
    )
    svg.save(out_path)


def draw_c_style_metadata_figure(completeness, out_path):
    blue = "#355C85"
    orange = "#D26A2E"
    svg = Svg(1320, 560)

    svg.text(54, 62, "c. Scenario Metadata Distributions", klass="label", font_size=20, font_weight=700)

    patient_fields = ["patient_gender", "patient_age", "patient_race", "patient_nationality"]
    clinical_fields = ["department", "urgency_level", "injury_body_part"]
    lookup = {item["field"]: item for item in completeness}

    patient_items = [lookup[field] for field in patient_fields]
    clinical_items = [lookup[field] for field in clinical_fields]

    draw_grouped_hbar(
        svg,
        54,
        118,
        540,
        350,
        "Metadata availability on patient profiles",
        [item["label"] for item in patient_items],
        [item["present"] for item in patient_items],
        [item["missing"] for item in patient_items],
        "Present",
        "Missing",
        blue,
        orange,
    )
    draw_grouped_hbar(
        svg,
        690,
        118,
        560,
        350,
        "Metadata availability on clinical contexts",
        [item["label"] for item in clinical_items],
        [item["present"] for item in clinical_items],
        [item["missing"] for item in clinical_items],
        "Present",
        "Missing",
        blue,
        orange,
    )
    svg.save(out_path)


def draw_c_style_metadata_figure_matplotlib(completeness, out_base_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    blue = "#355C85"
    orange = "#D26A2E"
    text_color = "#2a3340"
    grid_color = "#e4e8ef"

    patient_fields = ["patient_gender", "patient_age", "patient_race", "patient_nationality"]
    clinical_fields = ["department", "urgency_level", "injury_body_part"]
    lookup = {item["field"]: item for item in completeness}
    patient_items = [lookup[field] for field in patient_fields]
    clinical_items = [lookup[field] for field in clinical_fields]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.labelcolor": text_color,
            "xtick.color": "#576274",
            "ytick.color": "#576274",
            "axes.edgecolor": "#ffffff",
        }
    )

    fig = plt.figure(figsize=(12.6, 5.2), facecolor="white")
    gs = fig.add_gridspec(1, 2, left=0.06, right=0.985, top=0.78, bottom=0.16, wspace=0.28)
    axes = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])]

    def plot_panel(ax, items, title):
        labels = [item["label"] for item in items]
        present = [item["present"] for item in items]
        missing = [item["missing"] for item in items]
        y = list(range(len(labels)))
        delta = 0.18
        bar_h = 0.28

        ax.barh([v - delta for v in y], present, height=bar_h, color=blue, edgecolor=blue)
        ax.barh([v + delta for v in y], missing, height=bar_h, color=orange, edgecolor=orange)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=11)
        ax.invert_yaxis()
        max_x = rounded_max(max(present + missing + [1]))
        ax.set_xlim(0, max_x)
        ax.xaxis.tick_top()
        ax.tick_params(axis="x", labelsize=10, length=0)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color=grid_color, linewidth=0.8)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title(title, fontsize=12.5, fontweight="bold", color=text_color, loc="left", pad=10)

    plot_panel(axes[0], patient_items, "Metadata availability on patient profiles")
    plot_panel(axes[1], clinical_items, "Metadata availability on clinical contexts")

    legend_handles = [Patch(facecolor=blue, edgecolor=blue, label="Present"), Patch(facecolor=orange, edgecolor=orange, label="Missing")]
    axes[0].legend(
        handles=legend_handles,
        frameon=False,
        fontsize=10.5,
        ncol=2,
        handlelength=0.8,
        handleheight=0.8,
        handletextpad=0.4,
        columnspacing=3.0,
        loc="upper left",
        bbox_to_anchor=(0.0, 1.08),
    )
    axes[1].legend(
        handles=legend_handles,
        frameon=False,
        fontsize=10.5,
        ncol=2,
        handlelength=0.8,
        handleheight=0.8,
        handletextpad=0.4,
        columnspacing=3.0,
        loc="upper left",
        bbox_to_anchor=(0.0, 1.08),
    )

    fig.text(0.06, 0.92, "c. Scenario Metadata Distributions", fontsize=14, fontweight="bold", color=text_color)

    pdf_path = out_base_path + ".pdf"
    png_path = out_base_path + ".png"
    fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    fig.savefig(png_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return [pdf_path, png_path]


def convert_svg(svg_path):
    converter = shutil.which("rsvg-convert")
    if not converter:
        return []
    generated = []
    png_path = svg_path[:-4] + ".png"
    pdf_path = svg_path[:-4] + ".pdf"
    commands = [
        [converter, "-f", "png", "-o", png_path, svg_path],
        [converter, "-f", "pdf", "-o", pdf_path, svg_path],
    ]
    for command in commands:
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            generated.append(command[4])
        except subprocess.CalledProcessError:
            pass
    return generated


def write_flat_csv(rows, path):
    ensure_dir(os.path.dirname(path))
    columns = [
        "case_id",
        "scenario_id",
        "scenario_key",
        "scenario_title",
        "department",
        "urgency_level",
        "patient_gender",
        "patient_age",
        "patient_race",
        "patient_nationality",
        "injury_body_part",
        "department_group",
        "urgency_group",
        "gender_group",
        "age_group",
        "age_years_estimate",
        "meta_path",
    ]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_summary_json(payload, path):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def write_figure_notes(path, summary):
    total = summary["total_scenarios"]
    missing = summary["missing_meta_files"]
    lines = [
        "# Paper figure plan",
        "",
        "## Recommended main-text figures",
        "",
        "1. Figure 1 (`figure_1_dataset_overview`)",
        "   Use this as the dataset description figure in Methods or Results.",
        "   Panel A shows the number of scenarios in each MedEdPORTAL case directory.",
        "   Panel B shows metadata completeness for every extracted field.",
        "",
        "2. Figure 2 (`figure_2_scenario_characteristics`)",
        "   Use this as the descriptive summary of scenario composition.",
        "   It reports normalized department groups, age groups, and sex information categories.",
        "",
        "## Recommended supplementary figure",
        "",
        "1. Figure S1 (`figure_s1_metadata_heatmap`)",
        "   Use this to document scenario-level missingness and justify why the analysis focuses on descriptive counts.",
        "",
        "## Why these figures fit a paper",
        "",
        f"- The dataset is small ({total} scenarios), so count-focused figures are more defensible than inferential plots.",
        "- The source JSON stores multiple fields as sparse free text, so broad standardized groups are clearer than raw-string plots.",
        "- Metadata completeness is a core quality-control result because the extraction rule only allows explicitly stated information.",
        "",
        "## Figures not recommended",
        "",
        "- Pie charts: too hard to compare across sparse categories and small counts.",
        "- Radar charts: visually noisy and not standard for methodological reporting.",
        "- Fine-grained age histograms: ages include ranges and textual descriptions, so exact-bin histograms would overstate precision.",
        "",
        "## Current dataset snapshot",
        "",
        f"- Case directories listed: {summary['total_case_directories']}",
        f"- Case directories with metadata JSON: {summary['case_directories_with_meta']}",
        f"- Total scenarios aggregated: {summary['total_scenarios']}",
        f"- Missing metadata JSON files: {', '.join(missing) if missing else 'None'}",
        f"- Report generated: {summary['generated_at']}",
        "",
    ]
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def build_summary(case_dirs, rows, missing_meta_files):
    completeness = field_completeness(rows)
    department_counts = count_by(
        rows,
        "department_group",
        order=[
            "Emergency",
            "Anesthesiology",
            "ICU",
            "Inpatient medicine",
            "Outpatient",
            "Surgical",
            "Psychiatry",
            "Unspecified",
        ],
    )
    age_counts = count_by(
        rows,
        "age_group",
        order=["Pediatric", "Adult", "Older adult", "Missing"],
    )
    gender_counts = count_by(
        rows,
        "gender_group",
        order=["Male", "Female", "Either sex", "Other/unspecified", "Missing"],
    )
    urgency_counts = count_by(
        rows,
        "urgency_group",
        order=["Acute/emergent", "Mild/non-acute", "Elective", "Other specified", "Missing"],
    )
    case_counts = Counter(row["case_id"] for row in rows)
    case_count_pairs = sorted(case_counts.items(), key=lambda item: (-item[1], item[0]))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_case_directories": len(case_dirs),
        "case_directories_with_meta": len(case_dirs) - len(missing_meta_files),
        "missing_meta_files": missing_meta_files,
        "total_scenarios": len(rows),
        "case_counts": [{"case_id": case_id, "scenario_count": count} for case_id, count in case_count_pairs],
        "field_completeness": completeness,
        "department_group_counts": [{"label": label, "count": count} for label, count in department_counts],
        "age_group_counts": [{"label": label, "count": count} for label, count in age_counts],
        "gender_group_counts": [{"label": label, "count": count} for label, count in gender_counts],
        "urgency_group_counts": [{"label": label, "count": count} for label, count in urgency_counts],
    }


def main():
    parser = argparse.ArgumentParser(description="Aggregate phase1 scenario metadata and generate paper-ready figures.")
    parser.add_argument("--case-file", default=DEFAULT_CASE_FILE, help="Path to case_directories.json")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Directory for tables and figures")
    args = parser.parse_args()

    case_dirs = load_case_directories(args.case_file)
    rows, missing_meta_files = flatten_records(case_dirs)
    if not rows:
        raise SystemExit("No scenario metadata rows found.")

    ensure_dir(args.out_dir)
    tables_dir = os.path.join(args.out_dir, "tables")
    figures_dir = os.path.join(args.out_dir, "figures")
    ensure_dir(tables_dir)
    ensure_dir(figures_dir)

    summary = build_summary(case_dirs, rows, missing_meta_files)
    write_flat_csv(rows, os.path.join(tables_dir, "scenario_metadata_flat.csv"))
    write_summary_json(summary, os.path.join(tables_dir, "metadata_summary.json"))
    write_figure_notes(os.path.join(args.out_dir, "paper_figure_plan.md"), summary)

    completeness = field_completeness(rows)
    figure_c_style_base = os.path.join(figures_dir, "figure_c_style_metadata_distributions")
    generated_variants = draw_c_style_metadata_figure_matplotlib(completeness, figure_c_style_base)

    manifest = {
        "case_file": os.path.abspath(args.case_file),
        "out_dir": os.path.abspath(args.out_dir),
        "generated_files": [
            os.path.join(tables_dir, "scenario_metadata_flat.csv"),
            os.path.join(tables_dir, "metadata_summary.json"),
            os.path.join(args.out_dir, "paper_figure_plan.md"),
        ]
        + generated_variants,
    }
    write_summary_json(manifest, os.path.join(args.out_dir, "manifest.json"))

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nOutputs written to:", os.path.abspath(args.out_dir))


if __name__ == "__main__":
    main()
