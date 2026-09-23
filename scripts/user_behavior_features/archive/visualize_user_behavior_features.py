from __future__ import annotations

import argparse
import csv
import html
import os
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = Path(os.environ.get("EC_OUTPUT_ROOT", PROJECT_ROOT / "outputs"))
ARCHIVE_FEATURE_ROOT = OUTPUT_ROOT / "features" / "archive"
DEFAULT_YEARS = [2022, 2023, 2024, 2025, 2026]
FEATURE_DIR = ARCHIVE_FEATURE_ROOT / "all_years"
INPUT_FILE = FEATURE_DIR / "final_user_behavior_labels.csv"
IMAGE_DIR = FEATURE_DIR / "images" / "user_behavior_patterns"

FONT = "Microsoft YaHei, SimHei, Arial, sans-serif"
COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#64748b"]
PRICE_COLORS = {"高价格敏感": "#dc2626", "中价格敏感": "#f59e0b", "低价格敏感": "#2563eb", "未分层": "#94a3b8"}
RISK_COLORS = {"高风险": "#dc2626", "中风险": "#f59e0b", "低风险": "#16a34a", "未分层": "#94a3b8"}
TIME_PREF_COLORS = {"单时段偏好": "#2563eb", "双时段偏好": "#7c3aed", "三时段偏好": "#0891b2", "轻度时段偏好": "#f59e0b", "无明显偏好": "#16a34a", "样本不足": "#94a3b8"}
PERIOD_ORDER = [
    *(f"{hour:02d}:00-{hour:02d}:59" for hour in range(24)),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate archive user behavior charts.")
    parser.add_argument("--feature-dir", type=Path, help="Use a specific feature directory.")
    parser.add_argument("--years", nargs="+", type=int, default=DEFAULT_YEARS, help="Years to visualize, e.g. --years 2026.")
    return parser.parse_args()


def output_dir_for_years(years: list[int]) -> Path:
    years = sorted(dict.fromkeys(years))
    if years == DEFAULT_YEARS:
        return ARCHIVE_FEATURE_ROOT / "all_years"
    if len(years) == 1:
        return ARCHIVE_FEATURE_ROOT / str(years[0])
    return ARCHIVE_FEATURE_ROOT / ("years_" + "_".join(map(str, years)))


def configure_paths(years: list[int]) -> None:
    global FEATURE_DIR, INPUT_FILE, IMAGE_DIR
    FEATURE_DIR = output_dir_for_years(years)
    INPUT_FILE = FEATURE_DIR / "final_user_behavior_labels.csv"
    IMAGE_DIR = FEATURE_DIR / "images" / "user_behavior_patterns"


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def fmt_int(value: int | float) -> str:
    return f"{int(round(value)):,}"


def pct(value: int | float, total: int | float) -> float:
    return 0.0 if total <= 0 else float(value) / float(total) * 100.0


def sorted_counter(counter: Counter[str], order: list[str] | None = None) -> list[tuple[str, int]]:
    if order:
        seen = set(order)
        rows = [(label, int(counter.get(label, 0))) for label in order if counter.get(label, 0) > 0]
        rows.extend((label, int(value)) for label, value in counter.items() if label not in seen and value > 0)
        return rows
    return [(label, int(value)) for label, value in counter.most_common() if value > 0]


def write_svg(path: Path, width: int, height: int, title: str, subtitle: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <style>
    .title {{ font-family: {FONT}; font-size: 22px; font-weight: 700; fill: #111827; }}
    .subtitle {{ font-family: {FONT}; font-size: 13px; fill: #4b5563; }}
    .label {{ font-family: {FONT}; font-size: 12px; fill: #111827; }}
    .small {{ font-family: {FONT}; font-size: 11px; fill: #374151; }}
  </style>
  <text class="title" x="34" y="38">{esc(title)}</text>
  <text class="subtitle" x="34" y="60">{esc(subtitle)}</text>
{body}
</svg>'''
    path.write_text(svg, encoding="utf-8")


def donut_chart(path: Path, title: str, subtitle: str, rows: list[tuple[str, int]], total: int, color_map: dict[str, str] | None = None) -> None:
    rows = [(label, value) for label, value in rows if value > 0]
    if not rows:
        write_svg(path, 640, 260, title, subtitle, '<text class="label" x="34" y="120">No data</text>')
        return
    width = 1040
    height = max(320, 150 + len(rows) * 30)
    cx, cy, r = 170, 170, 92
    start = -90.0
    parts: list[str] = []
    for idx, (label, value) in enumerate(rows):
        sweep = 360.0 * value / total if total > 0 else 0.0
        end = start + sweep
        x1 = cx + r * __import__("math").cos(__import__("math").radians(start))
        y1 = cy + r * __import__("math").sin(__import__("math").radians(start))
        x2 = cx + r * __import__("math").cos(__import__("math").radians(end))
        y2 = cy + r * __import__("math").sin(__import__("math").radians(end))
        large = 1 if sweep > 180 else 0
        color = (color_map or {}).get(label, COLORS[idx % len(COLORS)])
        parts.append(f'<path d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {r} {r} 0 {large} 1 {x2:.1f} {y2:.1f} Z" fill="{color}" stroke="#ffffff" stroke-width="1"/>')
        start = end
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="52" fill="#ffffff"/>')
    parts.append(f'<text class="label" x="{cx}" y="{cy - 2}" text-anchor="middle">{fmt_int(total)}</text>')
    parts.append(f'<text class="small" x="{cx}" y="{cy + 18}" text-anchor="middle">orders</text>')
    legend_x = 360
    legend_y = 102
    for idx, (label, value) in enumerate(rows):
        y = legend_y + idx * 28
        color = (color_map or {}).get(label, COLORS[idx % len(COLORS)])
        parts.append(f'<rect x="{legend_x}" y="{y}" width="13" height="13" rx="2" fill="{color}"/>')
        parts.append(f'<text class="label" x="{legend_x + 20}" y="{y + 11}">{esc(label)}</text>')
        parts.append(f'<text class="small" x="{legend_x + 260}" y="{y + 11}" text-anchor="end">{fmt_int(value)} ({pct(value, total):.1f}%)</text>')
    write_svg(path, width, height, title, subtitle, "\n".join(parts))


def horizontal_bar_chart(path: Path, title: str, subtitle: str, rows: list[tuple[str, int]], total: int, width: int = 1100, row_height: int = 40) -> None:
    rows = [(label, value) for label, value in rows if value > 0]
    if not rows:
        write_svg(path, 640, 260, title, subtitle, '<text class="label" x="34" y="120">No data</text>')
        return
    left = 260
    top = 96
    plot_w = width - left - 210
    height = max(260, top + len(rows) * row_height + 28)
    max_value = max(value for _, value in rows)
    parts: list[str] = []
    for idx, (label, value) in enumerate(rows):
        y = top + idx * row_height
        bar_w = 0 if max_value <= 0 else value / max_value * plot_w
        color = COLORS[idx % len(COLORS)]
        parts.append(f'<text class="label" x="{left - 12}" y="{y + 18}" text-anchor="end">{esc(label)}</text>')
        parts.append(f'<rect x="{left}" y="{y + 4}" width="{bar_w:.1f}" height="22" rx="6" fill="{color}"/>')
        parts.append(f'<text class="small" x="{left + bar_w + 8:.1f}" y="{y + 19}">{fmt_int(value)} ({pct(value, total):.1f}%)</text>')
    write_svg(path, width, height, title, subtitle, "\n".join(parts))


def vertical_bar_chart(path: Path, title: str, subtitle: str, rows: list[tuple[str, int]], total: int, width: int = 1000) -> None:
    rows = [(label, value) for label, value in rows if value > 0]
    if not rows:
        write_svg(path, 640, 260, title, subtitle, '<text class="label" x="34" y="120">No data</text>')
        return
    top = 100
    left = 64
    bar_w = 92
    gap = 26
    plot_h = 250
    height = 420
    max_value = max(value for _, value in rows)
    parts: list[str] = []
    for idx, (label, value) in enumerate(rows):
        x = left + idx * (bar_w + gap)
        h = 0 if max_value <= 0 else value / max_value * plot_h
        color = COLORS[idx % len(COLORS)]
        parts.append(f'<rect x="{x}" y="{top + plot_h - h:.1f}" width="{bar_w}" height="{h:.1f}" rx="8" fill="{color}"/>')
        parts.append(f'<text class="small" x="{x + bar_w / 2:.1f}" y="{top + plot_h - h - 8:.1f}" text-anchor="middle">{fmt_int(value)}</text>')
        parts.append(f'<text class="label" x="{x + bar_w / 2:.1f}" y="{top + plot_h + 20}" text-anchor="middle">{esc(label)}</text>')
    write_svg(path, width, height, title, subtitle, "\n".join(parts))


def stacked_100_bar_chart(path: Path, title: str, subtitle: str, nested: dict[str, Counter[str]], category_order: list[str], color_map: dict[str, str]) -> None:
    rows = [(label, counter) for label, counter in nested.items() if sum(counter.values()) > 0]
    rows.sort(key=lambda item: sum(item[1].values()), reverse=True)
    if not rows:
        write_svg(path, 640, 260, title, subtitle, '<text class="label" x="34" y="120">No data</text>')
        return
    width = 1160
    row_height = 56
    left = 230
    top = 108
    plot_w = 700
    height = 140 + len(rows) * row_height
    parts: list[str] = []
    for idx, category in enumerate(category_order):
        x = left + idx * 130
        parts.append(f'<rect x="{x}" y="78" width="14" height="14" rx="2" fill="{color_map.get(category, COLORS[idx % len(COLORS)])}"/>')
        parts.append(f'<text class="small" x="{x + 20}" y="90">{esc(category)}</text>')
    for row_idx, (group, counter) in enumerate(rows):
        y = top + row_idx * row_height
        row_total = sum(counter.values())
        parts.append(f'<text class="label" x="{left - 12}" y="{y + 24}" text-anchor="end">{esc(group)}</text>')
        x_cursor = left
        for category in category_order:
            value = counter.get(category, 0)
            if value <= 0 or row_total <= 0:
                continue
            seg_w = value / row_total * plot_w
            color = color_map.get(category, "#94a3b8")
            parts.append(f'<rect x="{x_cursor:.1f}" y="{y + 5}" width="{seg_w:.1f}" height="28" fill="{color}"/>')
            font_size = 11 if seg_w >= 36 else 10
            parts.append(f'<text x="{x_cursor + seg_w / 2:.1f}" y="{y + 24}" text-anchor="middle" dominant-baseline="middle" font-size="{font_size}" fill="#ffffff" font-family="{FONT}">{pct(value, row_total):.0f}%</text>')
            x_cursor += seg_w
        parts.append(f'<rect x="{left}" y="{y + 5}" width="{plot_w}" height="28" fill="none" stroke="#e2e8f0"/>')
        parts.append(f'<text class="small" x="{left + plot_w + 14}" y="{y + 24}">{fmt_int(row_total)}</text>')
    write_svg(path, width, height, title, subtitle, "\n".join(parts))


def heatmap_chart(path: Path, title: str, subtitle: str, nested: dict[str, Counter[str]], category_order: list[str]) -> None:
    rows = [(label, counter) for label, counter in nested.items() if sum(counter.values()) > 0]
    rows.sort(key=lambda item: sum(item[1].values()), reverse=True)
    if not rows:
        write_svg(path, 640, 260, title, subtitle, '<text class="label" x="34" y="120">No data</text>')
        return
    width = 1040
    cell_w = 148
    cell_h = 54
    left = 240
    top = 132
    height = top + len(rows) * cell_h + 54
    parts: list[str] = []
    for idx, category in enumerate(category_order):
        x = left + idx * cell_w
        parts.append(f'<text class="small" x="{x + cell_w / 2:.1f}" y="108" text-anchor="middle">{esc(category)}</text>')
    for row_idx, (group, counter) in enumerate(rows):
        y = top + row_idx * cell_h
        parts.append(f'<text class="label" x="{left - 12}" y="{y + 30}" text-anchor="end">{esc(group)}</text>')
        row_total = sum(counter.values())
        for col_idx, category in enumerate(category_order):
            value = counter.get(category, 0)
            ratio = 0.0 if row_total <= 0 else value / row_total
            fill = f"rgba(37, 99, 235, {0.15 + 0.75 * ratio:.3f})"
            x = left + col_idx * cell_w
            parts.append(f'<rect x="{x}" y="{y + 6}" width="{cell_w - 6}" height="40" rx="6" fill="{fill}" stroke="#e5e7eb"/>')
            parts.append(f'<text x="{x + (cell_w - 6) / 2:.1f}" y="{y + 24}" text-anchor="middle" font-size="12" fill="#111827" font-family="{FONT}">{pct(value, row_total):.0f}%</text>')
            parts.append(f'<text x="{x + (cell_w - 6) / 2:.1f}" y="{y + 38}" text-anchor="middle" font-size="10" fill="#374151" font-family="{FONT}">{fmt_int(value)}</text>')
    write_svg(path, width, height, title, subtitle, "\n".join(parts))


def read_behavior_data() -> dict[str, object]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    r_counter: Counter[str] = Counter()
    f_counter: Counter[str] = Counter()
    m_counter: Counter[str] = Counter()
    station_pref_counter: Counter[str] = Counter()
    time_pref_counter: Counter[str] = Counter()
    rfm_counter: Counter[str] = Counter()
    label_counter: Counter[str] = Counter()
    period_counter: Counter[str] = Counter()
    price_counter: Counter[str] = Counter()
    risk_counter: Counter[str] = Counter()
    price_by_rfm: dict[str, Counter[str]] = defaultdict(Counter)
    risk_by_rfm: dict[str, Counter[str]] = defaultdict(Counter)
    station_pref_by_rfm: dict[str, Counter[str]] = defaultdict(Counter)
    time_pref_by_rfm: dict[str, Counter[str]] = defaultdict(Counter)
    total = 0
    station_reference_total = 0
    time_reference_total = 0

    with INPUT_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            total += 1
            rfm_type = str(row.get("RFM类型", "")).strip() or "未分层"
            r_level = str(row.get("R等级", "")).strip() or "未分层"
            f_level = str(row.get("F等级", "")).strip() or "未分层"
            m_level = str(row.get("M等级", "")).strip() or "未分层"
            station_pref = str(row.get("站点偏好类型", "")).strip() or "样本不足"
            time_pref = str(row.get("时段偏好类型", "")).strip() or "样本不足"
            price_level = str(row.get("价格敏感等级", "")).strip() or "未分层"
            risk_level = str(row.get("异常风险等级", "")).strip() or "未分层"
            period = str(row.get("主充电时段", "")).strip() or "未识别"

            r_counter[r_level] += 1
            f_counter[f_level] += 1
            m_counter[m_level] += 1
            rfm_counter[rfm_type] += 1
            price_counter[price_level] += 1
            risk_counter[risk_level] += 1
            period_counter[period] += 1
            price_by_rfm[rfm_type][price_level] += 1
            risk_by_rfm[rfm_type][risk_level] += 1

            if station_pref != "样本不足":
                station_reference_total += 1
                station_pref_counter[station_pref] += 1
                station_pref_by_rfm[rfm_type][station_pref] += 1
            if time_pref != "样本不足":
                time_reference_total += 1
                time_pref_counter[time_pref] += 1
                time_pref_by_rfm[rfm_type][time_pref] += 1

            labels = str(row.get("用户标签", "")).replace(";", "；").split("；")
            for label in labels:
                label = label.strip()
                if label:
                    label_counter[label] += 1

    return {
        "total": total,
        "station_reference_total": station_reference_total,
        "time_reference_total": time_reference_total,
        "r_counter": r_counter,
        "f_counter": f_counter,
        "m_counter": m_counter,
        "station_pref_counter": station_pref_counter,
        "time_pref_counter": time_pref_counter,
        "rfm_counter": rfm_counter,
        "label_counter": label_counter,
        "period_counter": period_counter,
        "price_counter": price_counter,
        "risk_counter": risk_counter,
        "price_by_rfm": price_by_rfm,
        "risk_by_rfm": risk_by_rfm,
        "station_pref_by_rfm": station_pref_by_rfm,
        "time_pref_by_rfm": time_pref_by_rfm,
    }


def clean_old_images() -> None:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    for path in IMAGE_DIR.glob("*.svg"):
        try:
            path.unlink()
        except OSError:
            pass


def main() -> None:
    args = parse_args()
    if args.feature_dir:
        global FEATURE_DIR, INPUT_FILE, IMAGE_DIR
        FEATURE_DIR = args.feature_dir.expanduser().resolve()
        INPUT_FILE = FEATURE_DIR / "final_user_behavior_labels.csv"
        IMAGE_DIR = FEATURE_DIR / "images" / "user_behavior_patterns"
    else:
        configure_paths(sorted(dict.fromkeys(args.years)))

    data = read_behavior_data()
    total = int(data["total"])
    station_reference_total = int(data["station_reference_total"])
    time_reference_total = int(data["time_reference_total"])
    r_counter: Counter[str] = data["r_counter"]  # type: ignore[assignment]
    f_counter: Counter[str] = data["f_counter"]  # type: ignore[assignment]
    m_counter: Counter[str] = data["m_counter"]  # type: ignore[assignment]
    station_pref_counter: Counter[str] = data["station_pref_counter"]  # type: ignore[assignment]
    time_pref_counter: Counter[str] = data["time_pref_counter"]  # type: ignore[assignment]
    rfm_counter: Counter[str] = data["rfm_counter"]  # type: ignore[assignment]
    label_counter: Counter[str] = data["label_counter"]  # type: ignore[assignment]
    period_counter: Counter[str] = data["period_counter"]  # type: ignore[assignment]
    price_counter: Counter[str] = data["price_counter"]  # type: ignore[assignment]
    risk_counter: Counter[str] = data["risk_counter"]  # type: ignore[assignment]
    price_by_rfm: dict[str, Counter[str]] = data["price_by_rfm"]  # type: ignore[assignment]
    risk_by_rfm: dict[str, Counter[str]] = data["risk_by_rfm"]  # type: ignore[assignment]
    station_pref_by_rfm: dict[str, Counter[str]] = data["station_pref_by_rfm"]  # type: ignore[assignment]
    time_pref_by_rfm: dict[str, Counter[str]] = data["time_pref_by_rfm"]  # type: ignore[assignment]

    clean_old_images()

    donut_chart(IMAGE_DIR / "rfm_type_donut.svg", "RFM 用户类型占比（环形图）", "用户最终标签中 RFM 类型的整体结构", rfm_counter.most_common(), total)
    horizontal_bar_chart(IMAGE_DIR / "rfm_type_distribution.svg", "RFM 用户类型分布", "按最终用户标签统计 RFM 类型", rfm_counter.most_common(), total)

    donut_chart(IMAGE_DIR / "r_level_donut.svg", "R 等级占比（环形图）", "按距最近充电天数统计用户活跃度", sorted_counter(r_counter, ["活跃", "近期活跃", "一般活跃", "沉默风险", "沉默"]), total)
    horizontal_bar_chart(IMAGE_DIR / "r_level_distribution.svg", "R 等级分布", "按距最近充电天数统计用户活跃度", sorted_counter(r_counter, ["活跃", "近期活跃", "一般活跃", "沉默风险", "沉默"]), total)

    donut_chart(IMAGE_DIR / "f_level_donut.svg", "F 等级占比（环形图）", "按有效订单频率统计用户充电频次", sorted_counter(f_counter, ["高频", "中高频", "中频", "低频", "极低频"]), total)
    horizontal_bar_chart(IMAGE_DIR / "f_level_distribution.svg", "F 等级分布", "按有效订单频率统计用户充电频次", sorted_counter(f_counter, ["高频", "中高频", "中频", "低频", "极低频"]), total)

    donut_chart(IMAGE_DIR / "m_level_donut.svg", "M 等级占比（环形图）", "按累计充电量统计用户价值", sorted_counter(m_counter, ["高价值", "中高价值", "中价值", "低价值", "极低价值"]), total)
    horizontal_bar_chart(IMAGE_DIR / "m_level_distribution.svg", "M 等级分布", "按累计充电量统计用户价值", sorted_counter(m_counter, ["高价值", "中高价值", "中价值", "低价值", "极低价值"]), total)

    station_pref_order = ["固定站点用户", "双站点用户", "三站点用户", "多站流动用户", "一般站点用户"]
    station_pref_colors = {
        "固定站点用户": "#2563eb",
        "双站点用户": "#7c3aed",
        "三站点用户": "#0891b2",
        "多站流动用户": "#f59e0b",
        "一般站点用户": "#16a34a",
    }
    donut_chart(IMAGE_DIR / "station_preference_donut.svg", "站点偏好类型占比（环形图）", "固定站点、多站流动和一般站点用户的整体结构", sorted_counter(station_pref_counter, station_pref_order), station_reference_total, station_pref_colors)
    horizontal_bar_chart(IMAGE_DIR / "station_preference_distribution.svg", "站点偏好类型分布", "按主站点占比、前二和前三站点集中度统计用户站点偏好", sorted_counter(station_pref_counter, station_pref_order), station_reference_total)
    stacked_100_bar_chart(
        IMAGE_DIR / "station_preference_by_rfm_type.svg",
        "不同 RFM 用户类型的站点偏好结构",
        "每行归一到 100%，用于比较不同 RFM 用户类型内部的站点偏好差异",
        station_pref_by_rfm,
        station_pref_order,
        station_pref_colors,
    )

    time_pref_order = ["单时段偏好", "双时段偏好", "三时段偏好", "轻度时段偏好", "无明显偏好"]
    donut_chart(IMAGE_DIR / "time_preference_donut.svg", "时段偏好类型占比（环形图）", "按充电开始时间集中度统计用户时段偏好", sorted_counter(time_pref_counter, time_pref_order), time_reference_total, TIME_PREF_COLORS)
    horizontal_bar_chart(IMAGE_DIR / "time_preference_distribution.svg", "时段偏好类型分布", "按充电开始时间集中度统计用户是否具有时段偏好", sorted_counter(time_pref_counter, time_pref_order), time_reference_total)
    stacked_100_bar_chart(IMAGE_DIR / "time_preference_by_rfm_type.svg", "不同 RFM 用户类型的时段偏好结构", "每行归一到 100%，用于比较不同 RFM 用户类型内部的时段偏好差异", time_pref_by_rfm, time_pref_order, TIME_PREF_COLORS)

    vertical_bar_chart(IMAGE_DIR / "main_charge_period_distribution.svg", "用户主充电时段分布", "按每个用户出现最多的充电粗略时段统计", sorted_counter(period_counter, PERIOD_ORDER), total)

    donut_chart(IMAGE_DIR / "price_sensitivity_donut.svg", "用户价格敏感等级占比（环形图）", "按优惠使用、谷段偏好等价格相关特征形成的用户级标签", sorted_counter(price_counter, ["高价格敏感", "中价格敏感", "低价格敏感"]), total, PRICE_COLORS)
    stacked_100_bar_chart(IMAGE_DIR / "price_sensitivity_by_rfm_type.svg", "不同 RFM 用户类型的价格敏感结构", "每行归一到 100%，用于比较不同用户类型内部价格敏感差异", price_by_rfm, ["高价格敏感", "中价格敏感", "低价格敏感"], PRICE_COLORS)

    heatmap_chart(IMAGE_DIR / "risk_level_by_rfm_type_heatmap.svg", "不同 RFM 用户类型的异常风险结构", "每个单元格显示该 RFM 类型内的风险等级占比和人数", risk_by_rfm, ["高风险", "中风险", "低风险"])
    donut_chart(IMAGE_DIR / "risk_level_donut.svg", "用户异常风险等级占比（环形图）", "用于快速观察低风险、中风险、高风险用户的整体结构", sorted_counter(risk_counter, ["高风险", "中风险", "低风险"]), total, RISK_COLORS)
    horizontal_bar_chart(IMAGE_DIR / "risk_level_distribution.svg", "用户异常风险等级分布", "按异常结束、零电量和超长充电等特征形成的风险标签", sorted_counter(risk_counter, ["高风险", "中风险", "低风险"]), total)

    horizontal_bar_chart(IMAGE_DIR / "user_label_top.svg", "用户行为标签 Top 分布", "用户可同时拥有多个标签，因此各标签占比之和可能超过 100%", label_counter.most_common(10), total)

    print("可视化完成，输出目录:")
    print(IMAGE_DIR)


if __name__ == "__main__":
    main()
