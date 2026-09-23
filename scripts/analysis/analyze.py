from __future__ import annotations

import argparse
import html
import os
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from math import cos, log10, pi, sin
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import warnings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = Path(os.environ.get("EC_OUTPUT_ROOT", PROJECT_ROOT / "outputs"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成订单分析图表")
    parser.add_argument("--cleaned-file", type=Path, required=True)
    parser.add_argument("--abnormal-file", type=Path)
    parser.add_argument("--image-prefix", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--split-by-year", action="store_true")
    parser.add_argument("--keep-cross-year", action="store_true", help="保留订单年份与来源表年份不一致的订单（默认剔除，仅影响绘图）。")
    parser.add_argument("--min-year-orders", type=int, default=1000, help="分年度出图的最小样本量，低于该值的年份跳过出图。")
    parser.add_argument(
        "--skip-legacy-svg",
        action="store_true",
        help="跳过手工 SVG 图型（15 类分析图 + 2 张环形图）。这批图要另读一遍清洗表，峰值内存较高。",
    )
    return parser.parse_args()


def output_dir(cleaned_file: Path, image_prefix: str) -> Path:
    run_dir = cleaned_file.parent.parent
    return run_dir / "analysis" / "images" / image_prefix


def normalize_category(series: pd.Series) -> pd.Series:
    allowed = ["高速", "公共场站", "专用场站", "公交场站", "其他"]
    s = series.fillna("其他").astype(str).str.strip()
    return s.where(s.isin(allowed), "其他")


def month_key(dt: pd.Series) -> pd.Series:
    return dt.dt.to_period("M").astype(str)


def setup_plotting() -> None:
    warnings.filterwarnings(
        "ignore",
        category=UserWarning,
        message=r".*Glyph.*missing from font.*"
    )
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "figure.facecolor": "#ffffff",
            "axes.facecolor": "#ffffff",
            "axes.edgecolor": "#d1d5db",
            "axes.labelcolor": "#111827",
            "xtick.color": "#374151",
            "ytick.color": "#374151",
        }
    )
    sns.set_theme(style="white")


def save_svg(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def save_png(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", dpi=300, bbox_inches="tight")
    plt.close(fig)


@dataclass
class YearAgg:
    daily: Counter = field(default_factory=Counter)
    monthly: Counter = field(default_factory=Counter)
    duration_hist: np.ndarray = field(default_factory=lambda: np.zeros(12, dtype=np.int64))
    hour_hist: np.ndarray = field(default_factory=lambda: np.zeros(24, dtype=np.int64))
    category_counts: Counter = field(default_factory=Counter)
    valley_samples: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)


def _priority_sample(
    existing_values: np.ndarray,
    existing_keys: np.ndarray,
    new_values: np.ndarray,
    limit: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    if new_values.size == 0:
        return existing_values, existing_keys
    new_keys = rng.random(new_values.size)
    if existing_values.size == 0:
        if new_values.size > limit:
            idx = np.argpartition(new_keys, limit - 1)[:limit]
            return new_values[idx], new_keys[idx]
        return new_values, new_keys
    values = np.concatenate([existing_values, new_values])
    keys = np.concatenate([existing_keys, new_keys])
    if values.size <= limit:
        return values, keys
    idx = np.argpartition(keys, limit - 1)[:limit]
    return values[idx], keys[idx]


def _update_sample(
    store: dict[str, tuple[np.ndarray, np.ndarray]],
    key: str,
    values: np.ndarray,
    limit: int,
    rng: np.random.Generator,
) -> None:
    if key not in store:
        store[key] = (np.array([], dtype=float), np.array([], dtype=float))
    vals, keys = store[key]
    store[key] = _priority_sample(vals, keys, values, limit, rng)


_TABLE_YEAR_PATTERNS = (
    re.compile(r"^[yY](\d{2})(?!\d)"),
    re.compile(r"^[yY](20\d{2})(?!\d)"),
    re.compile(r"[_\-.][yY](\d{2})(?!\d)"),
)


def source_table_year(value: object) -> "int | None":
    """从来源表名解析年份，例如 y26_m1_t_2601_01_09 -> 2026；解析不出返回 None（不参与过滤）。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    for index, pattern in enumerate(_TABLE_YEAR_PATTERNS):
        match = pattern.search(text) if index == 2 else pattern.match(text)
        if not match:
            continue
        digits = match.group(1)
        year = int(digits) + 2000 if len(digits) == 2 else int(digits)
        if 2000 <= year <= 2099:
            return year
    match = re.search(r"(20\d{2})", text)
    if match:
        year = int(match.group(1))
        if 2000 <= year <= 2099:
            return year
    return None


def aggregate(
    cleaned_file: Path,
    split_by_year: bool,
    *,
    drop_cross_year: bool = True,
) -> tuple[YearAgg, dict[int, YearAgg]]:
    usecols = ["充电开始时间", "充电时长(min)", "场站类别", "谷段电量占比", "来源表"]
    chunksize = 400_000
    rng = np.random.default_rng(42)
    overall = YearAgg()
    years: dict[int, YearAgg] = defaultdict(YearAgg)
    cross_year_rows = 0

    for chunk in pd.read_csv(
        cleaned_file,
        usecols=lambda c: c in usecols,
        encoding="utf-8-sig",
        low_memory=False,
        chunksize=chunksize,
    ):
        start = pd.to_datetime(chunk.get("充电开始时间"), errors="coerce")
        duration = pd.to_numeric(chunk.get("充电时长(min)"), errors="coerce")
        valley = pd.to_numeric(chunk.get("谷段电量占比"), errors="coerce")
        category = normalize_category(
            chunk.get("场站类别", pd.Series(index=chunk.index, dtype=object))
        )

        source = chunk.get("来源表")
        if source is None:
            table_year = pd.Series(pd.NA, index=chunk.index, dtype="Int64")
        else:
            table_year = source.map(source_table_year).astype("Int64")
        in_table_year = table_year.isna() | start.dt.year.eq(table_year)
        if drop_cross_year:
            cross_year_rows += int((start.notna() & ~in_table_year).sum())
        else:
            in_table_year = pd.Series(True, index=chunk.index)

        valid_start = start.notna() & in_table_year
        if not valid_start.any():
            continue

        years_in_chunk = start.loc[valid_start].dt.year
        dates = start.loc[valid_start].dt.date
        months = month_key(start.loc[valid_start])
        hours = start.loc[valid_start].dt.hour
        dur = duration.loc[valid_start].fillna(0).clip(lower=0, upper=720)
        cat = category.loc[valid_start]
        v = valley.loc[valid_start].clip(lower=0, upper=1)

        def update_one(agg: YearAgg, mask: pd.Series | np.ndarray) -> None:
            if isinstance(mask, pd.Series):
                mask = mask.to_numpy()
            if not mask.any():
                return
            idx = np.flatnonzero(mask)
            d = dates.iloc[idx]
            m = months.iloc[idx]
            h = hours.iloc[idx].to_numpy()
            du = dur.iloc[idx].to_numpy(dtype=float)
            ca = cat.iloc[idx]
            va = v.iloc[idx].to_numpy(dtype=float)

            agg.daily.update(pd.Series(d).value_counts().to_dict())
            agg.monthly.update(pd.Series(m).value_counts().to_dict())
            agg.duration_hist += np.histogram(
                du,
                bins=[0, 10, 20, 30, 40, 60, 70, 80, 90, 120, 180, 240, np.inf],
            )[0]
            agg.hour_hist += np.histogram(h, bins=np.arange(25))[0]
            agg.category_counts.update(ca.value_counts().to_dict())
            _update_sample(agg.valley_samples, "全部场站", va, 200_000, rng)
            _update_sample(
                agg.valley_samples,
                "公共场站",
                va[ca.to_numpy() == "公共场站"],
                200_000,
                rng,
            )
            _update_sample(
                agg.valley_samples,
                "高速",
                va[ca.to_numpy() == "高速"],
                200_000,
                rng,
            )

        update_one(overall, np.ones(len(start.loc[valid_start]), dtype=bool))

        if split_by_year:
            for year in sorted(years_in_chunk.unique()):
                update_one(years[int(year)], years_in_chunk == year)

    if drop_cross_year and cross_year_rows:
        print(
            f"绘图口径剔除跨年订单 {cross_year_rows:,} 行"
            "（订单年份与来源表年份不一致，仅影响图表，不改动清洗数据）"
        )

    return overall, years


def _beautify_axis(ax: plt.Axes) -> None:
    ax.grid(axis="y", color="#e5e7eb", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#9ca3af")
    ax.spines["bottom"].set_color("#9ca3af")
    ax.tick_params(colors="#374151")


def _add_bar_labels(ax: plt.Axes, bars, fmt: str = "{:,.0f}", pad: float = 0.01) -> None:
    ymax = max((bar.get_height() for bar in bars), default=0)
    for bar in bars:
        h = bar.get_height()
        if h <= 0:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            h + ymax * pad,
            fmt.format(h),
            ha="center",
            va="bottom",
            fontsize=10,
            color="#111827",
        )


def plot_station_category(agg: YearAgg, path: Path, title_suffix: str = "") -> None:
    cats = ["高速", "公共场站", "专用场站", "公交场站", "其他"]
    counts = [agg.category_counts.get(cat, 0) for cat in cats]
    fig, ax = plt.subplots(figsize=(9.2, 5.2), dpi=300)
    bars = ax.bar(cats, counts, color=["#2563EB", "#16A34A", "#F59E0B", "#DC2626", "#64748B"], width=0.58)
    ax.set_title(f"场站类别分布{title_suffix}", fontsize=16, loc="left", pad=14, fontweight="bold")
    ax.set_ylabel("订单数", fontsize=12)
    ax.tick_params(axis="x", labelrotation=0, labelsize=11)
    ax.tick_params(axis="y", labelsize=11)
    _add_bar_labels(ax, bars)
    _beautify_axis(ax)
    save_svg(fig, path)


def plot_daily_monthly(agg: YearAgg, out_dir: Path, title_suffix: str = "") -> None:
    daily = pd.Series(agg.daily).sort_index()
    monthly = pd.Series(agg.monthly).sort_index()

    if not daily.empty:
        fig, ax = plt.subplots(figsize=(10.6, 5.4), dpi=300)
        ax.plot(daily.index.astype(str), daily.values, color="#2563EB", linewidth=1.8)
        ax.set_title(f"每日订单趋势{title_suffix}", fontsize=16, loc="left", pad=14, fontweight="bold")
        ax.set_ylabel("订单数", fontsize=12)
        ax.tick_params(axis="x", labelrotation=45, labelsize=9)
        ax.tick_params(axis="y", labelsize=11)
        _beautify_axis(ax)
        save_svg(fig, out_dir / "daily_orders_trend.svg")

    if not monthly.empty:
        fig, ax = plt.subplots(figsize=(10.6, 5.4), dpi=300)
        ax.plot(monthly.index.astype(str), monthly.values, color="#16A34A", linewidth=1.8)
        ax.set_title(f"月度订单趋势{title_suffix}", fontsize=16, loc="left", pad=14, fontweight="bold")
        ax.set_ylabel("订单数", fontsize=12)
        ax.tick_params(axis="x", labelrotation=45, labelsize=10)
        ax.tick_params(axis="y", labelsize=11)
        _beautify_axis(ax)
        save_svg(fig, out_dir / "monthly_orders_trend.svg")


def plot_histograms(agg: YearAgg, out_dir: Path, title_suffix: str = "") -> None:
    if agg.duration_hist.sum() > 0:
        fig, ax = plt.subplots(figsize=(10.6, 5.4), dpi=300)
        labels = [
            "0-10",
            "10-20",
            "20-30",
            "30-40",
            "40-60",
            "60-70",
            "70-80",
            "80-90",
            "90-120",
            "120-180",
            "180-240",
            "240+",
        ]
        bars = ax.bar(labels, agg.duration_hist, color="#7C3AED", width=0.62)
        ax.set_title(f"充电时长分布{title_suffix}", fontsize=16, loc="left", pad=14, fontweight="bold")
        ax.set_ylabel("订单数", fontsize=12)
        ax.tick_params(axis="x", labelrotation=35, labelsize=9)
        ax.tick_params(axis="y", labelsize=11)
        _add_bar_labels(ax, bars)
        _beautify_axis(ax)
        save_svg(fig, out_dir / "duration_distribution.svg")

    if agg.hour_hist.sum() > 0:
        fig, ax = plt.subplots(figsize=(10.6, 5.4), dpi=300)
        bars = ax.bar(range(24), agg.hour_hist, color="#0891B2", width=0.62)
        ax.set_title(f"开始小时分布{title_suffix}", fontsize=16, loc="left", pad=14, fontweight="bold")
        ax.set_xlabel("小时", fontsize=12)
        ax.set_ylabel("订单数", fontsize=12)
        ax.set_xticks(range(24))
        ax.tick_params(axis="x", labelsize=10)
        ax.tick_params(axis="y", labelsize=11)
        _add_bar_labels(ax, bars, pad=0.008)
        _beautify_axis(ax)
        save_svg(fig, out_dir / "start_hour_distribution.svg")


def plot_valley_distribution(agg: YearAgg, out_dir: Path, title_suffix: str = "") -> None:
    fig, ax = plt.subplots(figsize=(10, 6), dpi=300)
    fig.subplots_adjust(left=0.10, right=0.98, top=0.90, bottom=0.14)
    labels = ["全部场站", "公共场站", "高速"]
    colors = ["#2563EB", "#16A34A", "#DC2626"]

    for label, color in zip(labels, colors, strict=True):
        vals, _ = agg.valley_samples.get(label, (np.array([]), np.array([])))
        vals = np.asarray(vals, dtype=float)
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            continue
        if vals.size >= 2 and float(np.nanstd(vals)) > 0:
            sns.kdeplot(
                x=vals,
                fill=True,
                alpha=0.18,
                linewidth=2.2,
                color=color,
                label=label,
                ax=ax,
                clip=(0, 1),
                bw_adjust=1.1,
            )
        else:
            # 样本内取值恒定（或只有 1 条）时核密度无定义，改画竖线并标注，避免整条系列凭空消失、图例变空
            ax.axvline(
                float(vals[0]),
                color=color,
                linewidth=2.2,
                alpha=0.9,
                label=f"{label}（恒定 {vals[0]:.2f}）",
            )
        sns.histplot(
            x=vals,
            stat="density",
            bins=50,
            element="step",
            fill=False,
            color=color,
            ax=ax,
            alpha=0.20,
        )

    ax.set_title(f"谷段电量占比分布曲线{title_suffix}", fontsize=15, pad=14)
    ax.set_xlabel("谷段电量占比", fontsize=12)
    ax.set_ylabel("概率密度", fontsize=12)
    ax.set_xlim(0, 1)
    ax.set_xticks(np.linspace(0, 1, 11))
    ax.set_xticklabels([f"{int(x * 100)}%" for x in np.linspace(0, 1, 11)], fontsize=10)
    ax.tick_params(axis="y", labelsize=10)
    ax.margins(y=0.12)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles=handles, frameon=False, fontsize=10)
    ax.grid(alpha=0.25)
    save_png(fig, out_dir / "valley_ratio_curve_by_station_type.png")
    save_svg(fig, out_dir / "valley_ratio_curve_by_station_type.svg")


def write_summary(agg: YearAgg, out_dir: Path, label: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {label} 分析概览",
        "",
        f"- 场站类别数: {len([k for k, v in agg.category_counts.items() if v > 0])}",
        f"- 日粒度天数: {len(agg.daily)}",
        f"- 月粒度月份数: {len(agg.monthly)}",
    ]
    (out_dir / "behavior_analysis_summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_for_agg(agg: YearAgg, out_dir: Path, suffix: str = "") -> None:
    plot_station_category(agg, out_dir / "station_category_distribution.svg", suffix)
    plot_daily_monthly(agg, out_dir, suffix)
    plot_histograms(agg, out_dir, suffix)
    plot_valley_distribution(agg, out_dir, suffix)
    write_summary(agg, out_dir, suffix or "overall")


def fill_missing_year_assets(source_dir: Path, year_dir: Path) -> None:
    if not source_dir.exists() or not year_dir.exists():
        return
    for path in source_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".svg", ".png"}:
            continue
        target = year_dir / path.name
        if target.exists():
            continue
        shutil.copy2(path, target)


# ===== 手写 SVG 图型（移植自 analyze_2026_orders.py）====

# =============================================================================
# 手写 SVG 图型（移植自已删除的 scripts/analysis/analyze_2026_orders.py）
# -----------------------------------------------------------------------------
# 2026-09-22 从该脚本当时仅存的编译缓存 scripts/analysis/analyze_2026_orders.pyc
# 逐函数复原：调色板与分箱顺序常量、14 个手写 SVG 绘图原语、分组辅助，以及
# legacy/2026/ 那 18 类图里需要出图的 15 类装配逻辑。复原后该 .pyc 已删，
# 本文件即这批图型的唯一出处。
#
# 正确性以 legacy/2026/ 为基准逐张校验：17 张与 .pyc 直出的产物逐字节一致
# （见 legacy/README.md）。改动这里的分箱、调色板或标签顺序都会让图对不上。
#
# 与 matplotlib 那套产物互不影响：这些函数直接拼 SVG 字符串并写文件，
# 不经过 rcParams，因此不受 setup_plotting() 的字体设置约束。
# =============================================================================


# ---- 旧脚本的配色与顺序常量 ----
BLUE = "#2563EB"
GREEN = "#16A34A"
AMBER = "#F59E0B"
RED = "#DC2626"
TEAL = "#0891B2"
SLATE = "#64748B"
PURPLE = "#7C3AED"
PINK = "#DB2777"
PALETTE = [BLUE, GREEN, AMBER, RED, PURPLE, TEAL, SLATE, PINK]
START_PERIOD_ORDER = ["凌晨", "上午", "下午", "晚上"]
DURATION_DETAIL_LABELS = ["0-10min", "10-20min", "20-30min", "30-40min", "40-60min", "60-90min", "90-120min", "120-180min", "180-240min", "240min以上"]
DURATION_CORE_LABELS = ["0-10min", "10-20min", "20-30min", "30-60min", "60-120min", "120min以上"]
ENERGY_DURATION_TYPE_ORDER = ["短时低电量", "短时高电量", "常规补能", "长时高电量", "长时低电量"]
INTERVAL_ORDER = ["首次", "0-6h", "6-24h", "1-3天", "3-7天", "7-14天", "14天以上"]
ABNORMAL_REASON_ORDER = ["零电量", "故障或异常结束", "时间/功率/电量异常", "支付/金额异常", "订单渠道缺失", "其他异常"]
STATION_CATEGORY_ORDER = ["高速", "公共场站", "专用场站", "公交场站", "其他"]
STATION_CATEGORY_COLORS = ["#DC2626", "#2563EB", "#16A34A", "#F59E0B", "#64748B"]
LEGACY_SVG_COLUMNS = [
    "是否有效行为订单",
    "场站类别",
    "充电开始时间",
    "充电时长(min)",
    "交易电量(kWh)（清洗后）",
    "星期",
    "是否复用上次站点",
    "订单渠道",
    "是否使用优惠",
    "谷段电量占比",
    "来源表",
]
LEGACY_DONUT_COLUMNS = ["场站类别", "充电站ID", "原始订单数", "来源表", "充电开始时间"]
LEGACY_DATETIME_COLUMNS = ("充电开始时间",)
LEGACY_NUMERIC_COLUMNS = (
    "充电时长(min)",
    "交易电量(kWh)（清洗后）",
    "谷段电量占比",
    "原始订单数",
)


# ===== 分组辅助 =====

def get_start_hour(df: pd.DataFrame) -> pd.Series:
    """统一获取开始小时，优先使用清洗表字段，没有则从开始时间推导。"""
    if "开始小时" in df.columns:
        return pd.to_numeric(df["开始小时"], errors="coerce")
    return pd.to_datetime(df["充电开始时间"], errors="coerce").dt.hour


def start_period_from_hour(hour: object) -> str:
    """把开始小时映射为粗略时段（凌晨/上午/下午/晚上）。"""
    if pd.isna(hour):
        return "未知"
    h = int(hour)
    if 0 <= h <= 5:
        return "凌晨"
    if 6 <= h <= 11:
        return "上午"
    if 12 <= h <= 17:
        return "下午"
    return "晚上"


def duration_detail_group(series: pd.Series) -> pd.Series:
    """按 10/20/30/40/60/90/120/180/240min 细分充电时长。"""
    return pd.cut(
        series,
        bins=[0, 10, 20, 30, 40, 60, 90, 120, 180, 240, float("inf")],
        labels=DURATION_DETAIL_LABELS,
        include_lowest=True,
        right=True,
    )


def duration_core_group(series: pd.Series) -> pd.Series:
    """按核心口径（0/10/20/30/60/120min）粗分充电时长。"""
    return pd.cut(
        series,
        bins=[0, 10, 20, 30, 60, 120, float("inf")],
        labels=DURATION_CORE_LABELS,
        include_lowest=True,
        right=True,
    )


def energy_duration_type(df: pd.DataFrame) -> pd.Series:
    """电量-时长组合类型的向量化实现。

    等价于旧脚本逐行调用的 energy_duration_type(row)：规则在时长维度上互斥
    （<=30min / >120min / 其余），且「短时」优先于「长时」，因此按优先级从低到高
    赋值即可得到与「命中即停」相同的首条规则。

    注意不要对时长/电量做 fillna：旧实现里 NaN 走的是 `float(x or 0)`，
    而 bool(nan) 为 True，所以 NaN 会保留下来，四条规则全不命中，落回「常规补能」。
    若填成 0，NaN 时长会被错判为「短时低电量」。
    """
    duration = pd.to_numeric(df["充电时长(min)"], errors="coerce")
    energy = pd.to_numeric(df["交易电量(kWh)（清洗后）"], errors="coerce")
    out = pd.Series("常规补能", index=df.index, dtype=object)
    out[duration.gt(120) & energy.le(20)] = "长时低电量"
    out[duration.gt(120) & energy.gt(40)] = "长时高电量"
    out[duration.le(30) & energy.gt(20)] = "短时高电量"
    out[duration.le(30) & energy.le(20)] = "短时低电量"
    return out


def add_analysis_columns(valid: pd.DataFrame) -> pd.DataFrame:
    """补齐旧脚本派生的 5 个分析列。"""
    valid["分析开始小时"] = get_start_hour(valid)
    valid["分析开始时段"] = valid["分析开始小时"].map(start_period_from_hour)
    valid["分析时长细分"] = duration_detail_group(valid["充电时长(min)"])
    valid["分析时长核心分组"] = duration_core_group(valid["充电时长(min)"])
    valid["电量时长组合类型"] = energy_duration_type(valid)
    return valid



# ===== SVG 绘图原语 =====

def esc(value):
    """转义 HTML 特殊字符（含引号）。"""
    return html.escape(str(value), quote=True)


def write_svg(path, width, height, body, title, subtitle=""):
    """写出带统一样式表与标题的 SVG 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_text = f'<text class="subtitle" x="24" y="58">{esc(subtitle)}</text>' if subtitle else ""
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>
text {{ font-family: "Microsoft YaHei", "SimHei", Arial, sans-serif; fill: #1f2937; }}
.title {{ font-size: 20px; font-weight: 700; }}
.subtitle {{ font-size: 14px; fill: #64748b; }}
.axis {{ stroke: #9ca3af; stroke-width: 1; }}
.grid {{ stroke: #e5e7eb; stroke-width: 1; }}
.label {{ font-size: 14px; }}
.small {{ font-size: 14px; fill: #4b5563; }}
.white {{ font-size: 11px; fill: #ffffff; font-weight: 700; }}
</style>
<rect width="100%" height="100%" fill="#ffffff"/>
<text class="title" x="24" y="34">{esc(title)}</text>
{subtitle_text}
{body}
</svg>"""
    path.write_text(svg, encoding="utf-8")


def top_plus_other(series, top_n):
    """取前 top_n 项，其余合并为“其他”。"""
    top = series.sort_values(ascending=False).head(top_n)
    other = series.sum() - top.sum()
    if other > 0:
        top.loc["其他"] = other
    return top


def percent_bar_chart(series, path, title, subtitle=""):
    """绘制横向百分比条形图。"""
    width, height = 980, 560
    left, right, top, bottom = 180, 94, 78, 54
    values = series.astype(float)
    total = max(values.sum(), 1)
    max_value = max(values.max(), 1)
    row_h = (height - top - bottom) / len(values)
    plot_w = width - left - right
    parts = []
    pct_labels = {}
    if total:
        pct_labels = {name: round(float(value) / total * 100, 1) for name, value in values.items()}
        diff = round(100.0 - sum(pct_labels.values()), 1)
        if diff:
            adjust_name = values.idxmax()
            pct_labels[adjust_name] = round(pct_labels[adjust_name] + diff, 1)
    for i, (name, value) in enumerate(values.items()):
        y = top + i * row_h + 6
        bar_w = value / max_value * plot_w
        pct = pct_labels.get(name, value / total * 100)
        color = PALETTE[i % len(PALETTE)]
        parts.append(f'<text class="label" x="{left - 8}" y="{y + row_h * 0.54}" text-anchor="end">{esc(name)}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_w:.1f}" height="{max(row_h - 10, 8):.1f}" rx="3" fill="{color}"/>')
        parts.append(f'<text class="small" x="{left + bar_w + 8:.1f}" y="{y + row_h * 0.54}">{int(value)} / {pct:.1f}%</text>')
    parts.append(f'<line class="axis" x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}"/>')
    write_svg(path, width, height, "\n".join(parts), title, subtitle)


def donut_chart(
    series,
    path,
    title,
    center_label="订单数",
    height=520,
    cy=278,
    legend_top=116,
):
    """绘制环形占比图（含图例与中心文案）。"""
    width = 860
    cx, radius, inner = 250, 150, 84
    total = float(series.sum())
    angle = -pi / 2
    parts = []
    pct_labels = {}
    if total:
        pct_labels = {name: round(float(value) / total * 100, 1) for name, value in series.items()}
        diff = round(100.0 - sum(pct_labels.values()), 1)
        if diff:
            adjust_name = series.astype(float).idxmax()
            pct_labels[adjust_name] = round(pct_labels[adjust_name] + diff, 1)
    for i, (name, value) in enumerate(series.items()):
        frac = float(value) / total if total else 0
        end = angle + frac * 2 * pi
        x1, y1 = cx + radius * cos(angle), cy + radius * sin(angle)
        x2, y2 = cx + radius * cos(end), cy + radius * sin(end)
        large = 1 if frac > 0.5 else 0
        color = PALETTE[i % len(PALETTE)]
        parts.append(f'<path d="M {cx} {cy} L {x1:.2f} {y1:.2f} A {radius} {radius} 0 {large} 1 {x2:.2f} {y2:.2f} Z" fill="{color}" stroke="#fff" stroke-width="2"/>')
        y = legend_top + i * 32
        parts.append(f'<rect x="500" y="{y - 13}" width="16" height="16" fill="{color}"/>')
        parts.append(f'<text class="label" x="524" y="{y}">{esc(name)}：{int(value)}（{pct_labels.get(name, frac * 100):.1f}%）</text>')
        angle = end
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{inner}" fill="#fff"/>')
    parts.append(f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" style="font-size:14px;font-weight:700">{int(total)}</text>')
    parts.append(f'<text class="small" x="{cx}" y="{cy + 20}" text-anchor="middle">{esc(center_label)}</text>')
    write_svg(path, width, height, "\n".join(parts), title)


def line_chart(series, path, title, subtitle=""):
    """绘制带网格与数据标签的折线图。"""
    width, height = 980, 540
    left, right, top, bottom = 72, 38, 78, 74
    values = series.astype(float)
    max_value = max(values.max(), 1)
    min_value = min(values.min(), 0)
    plot_w = width - left - right
    plot_h = height - top - bottom
    labels = [str(x) for x in values.index]
    points = []
    for i, value in enumerate(values):
        x = left + plot_w * i / max(len(values) - 1, 1)
        y = top + plot_h - (value - min_value) / max(max_value - min_value, 1) * plot_h
        points.append((x, y, value))
    parts = []
    for tick in range(5):
        y = top + plot_h * tick / 4
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>')
    path_d = " ".join([f"{x:.1f},{y:.1f}" for x, y, _ in points])
    parts.append(f'<polyline points="{path_d}" fill="none" stroke="{BLUE}" stroke-width="3"/>')
    for i, (x, y, value) in enumerate(points):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{BLUE}"/>')
        if len(points) <= 20 or i % max(len(points) // 12, 1) == 0 or i == len(points) - 1:
            label_y = max(y - 12, top + 14)
            parts.append(f'<rect x="{x - 22:.1f}" y="{label_y - 13:.1f}" width="44" height="16" rx="3" fill="#ffffff" stroke="#bfdbfe"/>')
            parts.append(f'<text class="small" x="{x:.1f}" y="{label_y:.1f}" text-anchor="middle">{int(value)}</text>')
            parts.append(f'<text class="small" x="{x:.1f}" y="{height - 44}" text-anchor="middle" transform="rotate(-35 {x:.1f},{height - 44})">{esc(labels[i])}</text>')
    parts.append(f'<line class="axis" x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}"/>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}"/>')
    write_svg(path, width, height, "\n".join(parts), title, subtitle)


def histogram(series, bins, labels, path, title, subtitle=""):
    """绘制分箱直方图（高于中位数的柱子用 TEAL）。"""
    width, height = 920, 540
    left, right, top, bottom = 76, 42, 78, 76
    counts = pd.cut(series.dropna(), bins=bins, labels=labels, right=True, include_lowest=True).value_counts().reindex(labels).fillna(0)
    max_count = max(float(counts.max()), 1)
    plot_w = width - left - right
    plot_h = height - top - bottom
    bar_w = plot_w / len(counts) * 0.68
    parts = []
    for i, (label, count) in enumerate(counts.items()):
        h = float(count) / max_count * plot_h
        x = left + i * plot_w / len(counts) + (plot_w / len(counts) - bar_w) / 2
        y = top + plot_h - h
        color = TEAL if count >= counts.median() else PURPLE
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h, 2):.1f}" fill="{color}" rx="3"/>')
        parts.append(f'<text class="small" x="{x + bar_w / 2:.1f}" y="{y - 7:.1f}" text-anchor="middle">{int(count)}</text>')
        parts.append(f'<text class="small" x="{x + bar_w / 2:.1f}" y="{height - 46}" text-anchor="middle">{esc(label)}</text>')
    parts.append(f'<line class="axis" x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}"/>')
    write_svg(path, width, height, "\n".join(parts), title, subtitle)


def stacked_percent_chart(counts: pd.DataFrame, path: Path, title: str, subtitle: str = '', show_table: bool = True) -> None:
    """百分比堆叠条形图：按行归一化展示结构占比，可选附表。"""
    width = 1120
    height = 700 if show_table else max(390, 178 + 64 * max(len(counts), 1))
    left, right, top, bottom = 154, 204, 86 if show_table else 102, 58
    if counts.empty:
        write_svg(path, width, height, '<text class="label" x="154" y="130">无可用数据</text>', title, subtitle)
        return
    counts = counts.fillna(0).astype(float)
    rows = list(counts.iterrows())
    bar_h = 34
    row_gap = 64
    bar_left = left
    bar_right = width - right
    plot_w = bar_right - bar_left
    table_top = top + row_gap * len(rows) + 24
    parts = []
    for r, (row_label, row) in enumerate(rows):
        total = max(float(row.sum()), 1)
        row_percent_labels = {}
        nonzero_cols = [col for col, value in row.items() if float(value) > 0]
        if nonzero_cols:
            rounded = {col: round(float(row[col]) / total * 100, 1) for col in nonzero_cols}
            diff = round(100.0 - sum(rounded.values()), 1)
            adjust_col = max(nonzero_cols, key=lambda col: float(row[col]))
            rounded[adjust_col] = round(rounded[adjust_col] + diff, 1)
            row_percent_labels = rounded
        y = top + r * row_gap
        x = left
        parts.append(f'<text class="label" x="{left - 12}" y="{y + 22:.1f}" text-anchor="end">{esc(row_label)}</text>')
        for c, (col, value) in enumerate(row.items()):
            pct = float(value) / total
            w = pct * plot_w
            color = PALETTE[c % len(PALETTE)]
            if w > 0:
                parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{bar_h:.1f}" fill="{color}"/>')
                text_x = x + w / 2
                text_y = y + bar_h / 2 + 4
                pct_text = f"{row_percent_labels.get(col, pct * 100):.1f}%"
                display_pct = row_percent_labels.get(col, pct * 100)
                if display_pct < 0.2:
                    x += w
                    continue
                if display_pct >= 4:
                    parts.append(f'<text class="white" x="{text_x:.1f}" y="{text_y:.1f}" text-anchor="middle">{pct_text}</text>')
                else:
                    is_tail_category = col in {'长时高电量', '长时低电量'}
                    label_y = y - 8
                    if col == '长时低电量':
                        label_x = left + plot_w + 4
                        anchor = 'start'
                    elif row_label == '专用场站' and col == '长时高电量':
                        label_x = min(max(text_x, left + 18), left + plot_w - 18)
                        anchor = 'middle'
                    elif col == '长时高电量':
                        label_x = left + plot_w - 4
                        anchor = 'end'
                    elif is_tail_category:
                        label_x = min(max(text_x + (-16 if col == '长时高电量' else 16), left + 18), left + plot_w - 18)
                        anchor = 'middle'
                    else:
                        label_x = min(max(text_x, left + 18), left + plot_w - 18)
                        anchor = 'middle'
                    parts.append(f'<text class="small" x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{anchor}">{pct_text}</text>')
            x += w
        parts.append(f'<text class="small" x="{left + plot_w / 2:.1f}" y="{y + bar_h + 18:.1f}" text-anchor="middle">合计 {int(total)}</text>')
    legend_x, legend_y = width - right + 40, top
    for c, col in enumerate(counts.columns):
        y = legend_y + c * 26
        parts.append(f'<rect x="{legend_x}" y="{y - 13}" width="14" height="14" fill="{PALETTE[c % len(PALETTE)]}"/>')
        parts.append(f'<text class="small" x="{legend_x + 22}" y="{y}">{esc(col)}</text>')
    if not show_table:
        write_svg(path, width, height, "\n".join(parts), title, subtitle)
        return
    table_x = left
    table_y = table_top
    cell_h = 34
    first_col_w = 124
    other_col_w = max((plot_w - first_col_w) / len(counts.columns), 90)
    table_cols = ['日期属性', *list(counts.columns)]
    parts.append(f'<text class="label" x="{table_x}" y="{table_y - 14}">总体数据数目表</text>')
    for i, header in enumerate(table_cols):
        x0 = table_x + (0 if i == 0 else first_col_w + (i - 1) * other_col_w)
        w = first_col_w if i == 0 else other_col_w
        parts.append(f'<rect x="{x0:.1f}" y="{table_y:.1f}" width="{w:.1f}" height="{cell_h:.1f}" fill="#f8fafc" stroke="#cbd5e1"/>')
        parts.append(f'<text class="small" x="{x0 + w / 2:.1f}" y="{table_y + 21:.1f}" text-anchor="middle">{esc(header)}</text>')
    for r, (row_label, row) in enumerate(rows):
        y = table_y + cell_h * (r + 1)
        parts.append(f'<rect x="{table_x:.1f}" y="{y:.1f}" width="{first_col_w:.1f}" height="{cell_h:.1f}" fill="#ffffff" stroke="#cbd5e1"/>')
        parts.append(f'<text class="small" x="{table_x + first_col_w / 2:.1f}" y="{y + 21:.1f}" text-anchor="middle">{esc(row_label)}</text>')
        total = int(row.sum())
        for c, (col, value) in enumerate(row.items()):
            x0 = table_x + first_col_w + c * other_col_w
            parts.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{other_col_w:.1f}" height="{cell_h:.1f}" fill="#ffffff" stroke="#cbd5e1"/>')
            pct = float(value) / max(float(row.sum()), 1) * 100
            parts.append(f'<text class="small" x="{x0 + other_col_w / 2:.1f}" y="{y + 21:.1f}" text-anchor="middle">{int(value)} / {pct:.1f}%</text>')
    write_svg(path, width, height, "\n".join(parts), title, subtitle)


def multi_line_chart(data: pd.DataFrame, path: Path, title: str, subtitle: str = '') -> None:
    """多折线图：按列绘制时序走势。"""
    width, height = 980, 540
    left, right, top, bottom = 76, 120, 82, 78
    if data.empty:
        write_svg(path, width, height, '<text class="label" x="76" y="130">无可用数据</text>', title, subtitle)
        return
    values = data.astype(float)
    max_value = max(float(values.max().max()), 1)
    min_value = min(float(values.min().min()), 0)
    plot_w = width - left - right
    plot_h = height - top - bottom
    labels = [str(x) for x in values.index]
    parts = []
    for tick in range(5):
        y = top + plot_h * tick / 4
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>')
    for c, col in enumerate(values.columns):
        color = PALETTE[c % len(PALETTE)]
        points = []
        for i, value in enumerate(values[col]):
            x = left + plot_w * i / max(len(values) - 1, 1)
            y = top + plot_h - (float(value) - min_value) / max(max_value - min_value, 1) * plot_h
            points.append((x, y, float(value)))
        path_d = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in points)
        parts.append(f'<polyline points="{path_d}" fill="none" stroke="{color}" stroke-width="3"/>')
        for i, (x, y, value) in enumerate(points):
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
            if i == len(points) - 1:
                parts.append(f'<text class="small" x="{x + 8:.1f}" y="{y + 4:.1f}">{esc(col)}</text>')
            if len(points) <= 12:
                parts.append(f'<text class="small" x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle">{value:.1f}</text>')
        for i, label in enumerate(labels):
            x = left + plot_w * i / max(len(values) - 1, 1)
            parts.append(f'<text class="small" x="{x:.1f}" y="{height - 48}" text-anchor="middle">{esc(label)}</text>')
    parts.append(f'<line class="axis" x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}"/>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}"/>')
    write_svg(path, width, height, "\n".join(parts), title, subtitle)


def metric_bar_chart(stats: pd.DataFrame, path: Path, title: str, subtitle: str = '') -> None:
    """横向条形图：逐维度对比时长中位数、均电量与订单数。"""
    width, height = 980, 520
    left, right, top, bottom = 150, 190, 82, 56
    if stats.empty:
        write_svg(path, width, height, '<text class="label" x="150" y="130">无可用数据</text>', title, subtitle)
        return
    stats = stats.copy()
    max_value = max(float(stats['时长中位数'].max()), 1)
    row_h = (height - top - bottom) / len(stats)
    plot_w = width - left - right
    parts = []
    for i, (name, row) in enumerate(stats.iterrows()):
        y = top + i * row_h + 9
        value = float(row['时长中位数'])
        bar_w = value / max_value * plot_w
        color = PALETTE[i % len(PALETTE)]
        parts.append(f'<text class="label" x="{left - 10}" y="{y + row_h * 0.42:.1f}" text-anchor="end">{esc(name)}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_w:.1f}" height="{max(row_h - 18, 10):.1f}" rx="3" fill="{color}"/>')
        parts.append(f'<text class="small" x="{left + bar_w + 8:.1f}" y="{y + row_h * 0.42:.1f}">中位 {value:.1f}min，均电量 {float(row["平均电量"]):.1f}kWh，订单 {int(row["订单数"])}</text>')
    parts.append(f'<line class="axis" x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}"/>')
    write_svg(path, width, height, "\n".join(parts), title, subtitle)


def grouped_rate_bar_chart(stats: pd.DataFrame, path: Path, title: str, subtitle: str = '') -> None:
    """分组柱状图：逐维度对比各指标占比。"""
    width, height = 1060, 470
    left, right, top, bottom = 120, 150, 82, 78
    y_max = 0.4
    if stats.empty:
        write_svg(path, width, height, '<text class="label" x="120" y="130">无可用数据</text>', title, subtitle)
        return
    metrics = [col for col in stats.columns if col != '订单数']
    plot_w = width - left - right
    plot_h = height - top - bottom
    group_w = plot_w / max(len(stats), 1)
    bar_w = min(46, group_w / max(len(metrics), 1) * 0.58)
    parts = []
    for tick in range(6):
        y = top + plot_h * tick / 5
        value = (y_max - tick * y_max / 5) * 100
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>')
        parts.append(f'<text class="small" x="{left - 10}" y="{y + 5:.1f}" text-anchor="end">{value:.0f}%</text>')
    for i, (category, row) in enumerate(stats.iterrows()):
        group_left = left + i * group_w
        for j, metric in enumerate(metrics):
            value = float(row[metric])
            h = min(max(value, 0), y_max) / y_max * plot_h
            x = group_left + group_w / 2 - (len(metrics) * bar_w + (len(metrics) - 1) * 10) / 2 + j * (bar_w + 10)
            y = top + plot_h - h
            color = PALETTE[j % len(PALETTE)]
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h, 2):.1f}" rx="3" fill="{color}"/>')
            parts.append(f'<text class="small" x="{x + bar_w / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle">{value * 100:.1f}%</text>')
        parts.append(f'<text class="small" x="{group_left + group_w / 2:.1f}" y="{height - 48}" text-anchor="middle">{esc(category)}</text>')
        if '订单数' in stats.columns:
            parts.append(f'<text class="small" x="{group_left + group_w / 2:.1f}" y="{height - 28}" text-anchor="middle">订单 {int(row["订单数"]):,}</text>')
    legend_x = width - right + 20
    for j, metric in enumerate(metrics):
        y = top + j * 28
        parts.append(f'<rect x="{legend_x}" y="{y - 13}" width="14" height="14" fill="{PALETTE[j % len(PALETTE)]}"/>')
        parts.append(f'<text class="small" x="{legend_x + 22}" y="{y}">{esc(metric)}</text>')
    parts.append(f'<line class="axis" x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}"/>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}"/>')
    write_svg(path, width, height, "\n".join(parts), title, subtitle)


def duration_energy_heatmap(df, path):
    """充电时长-电量组合矩阵热力图。"""
    width, height = 980, 540
    left, top = 130, 96
    cell_w, cell_h = 128, 56
    duration_labels = ["0-10min", "10-20min", "20-30min", "30-60min", "60-120min", "120min以上"]
    energy_labels = ["0-10kWh", "10-20kWh", "20-40kWh", "40-60kWh", "60kWh以上"]
    duration_group = pd.cut(
        df["充电时长(min)"].dropna(),
        bins=[0, 10, 20, 30, 60, 120, float("inf")],
        labels=duration_labels,
        include_lowest=True,
    )
    energy_group = pd.cut(
        df.loc[duration_group.index, "交易电量(kWh)（清洗后）"],
        bins=[0, 10, 20, 40, 60, float("inf")],
        labels=energy_labels,
        include_lowest=True,
    )
    matrix = pd.crosstab(duration_group, energy_group).reindex(
        index=duration_labels, columns=energy_labels, fill_value=0
    )
    total = max(float(matrix.to_numpy().sum()), 1)
    max_intensity = max(log10(float(matrix.to_numpy().max()) + 1), 1)
    parts = []
    for c, label in enumerate(energy_labels):
        x = left + c * cell_w
        parts.append(
            f'<text class="label" x="{x + cell_w / 2:.1f}" y="{top - 18}" text-anchor="middle">{esc(label)}</text>'
        )
    for r, label in enumerate(duration_labels):
        y = top + r * cell_h
        parts.append(
            f'<text class="label" x="{left - 12}" y="{y + cell_h * 0.58:.1f}" text-anchor="end">{esc(label)}</text>'
        )
        for c, energy_label in enumerate(energy_labels):
            value = int(matrix.loc[label, energy_label])
            intensity = log10(value + 1) / max_intensity if value else 0
            red = int(248 - intensity * 140)
            green = int(250 - intensity * 105)
            blue = int(252 - intensity * 40)
            color = f"rgb({red},{green},{blue})"
            x = left + c * cell_w
            pct = value / total * 100
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 3}" height="{cell_h - 3}" fill="{color}" rx="2"/>'
            )
            if not value:
                continue
            text_color = "white" if intensity > 0.64 else "small"
            parts.append(
                f'<text class="{text_color}" x="{x + cell_w / 2:.1f}" y="{y + 24:.1f}" text-anchor="middle">{value}</text>'
            )
            parts.append(
                f'<text class="{text_color}" x="{x + cell_w / 2:.1f}" y="{y + 42:.1f}" text-anchor="middle">{pct:.1f}%</text>'
            )
    parts.append(
        f'<text class="small" x="{left}" y="{height - 34}">颜色按订单数对数缩放，避免头部组合压低其他格子的可读性。</text>'
    )
    write_svg(path, width, height, "\n".join(parts), "充电时长-电量组合矩阵", "识别短时补能、常规补能、长时大电量等行为结构")


def crosstab_heatmap(
    matrix, path, title, subtitle="", cell_height=120, axis_font_size=None, cell_font_size=None
):
    """通用交叉表热力图（订单数 + 总体占比）。"""
    left, top = 142, 92
    cell_w, cell_h = 136, cell_height
    width = max(980, left + cell_w * max(len(matrix.columns), 1) + 86)
    matrix = matrix.fillna(0).astype(float)
    height = top + cell_h * max(len(matrix.index), 1) + 78
    total = max(float(matrix.to_numpy().sum()), 1)
    max_intensity = max(log10(float(matrix.to_numpy().max()) + 1), 1)
    parts = []
    pct_labels = {}
    nonzero_cells = []
    for row_label in matrix.index:
        for col_label in matrix.columns:
            if float(matrix.loc[row_label, col_label]) > 0:
                nonzero_cells.append((row_label, col_label))
                pct_labels[(row_label, col_label)] = round(
                    float(matrix.loc[row_label, col_label]) / total * 100, 1
                )
    if nonzero_cells:
        diff = round(100.0 - sum(pct_labels.values()), 1)
        if diff:
            adjust_cell = max(nonzero_cells, key=lambda cell: float(matrix.loc[cell[0], cell[1]]))
            pct_labels[adjust_cell] = round(pct_labels[adjust_cell] + diff, 1)
    axis_style = f' style="font-size:{axis_font_size}px"' if axis_font_size else ""
    cell_style = f' style="font-size:{cell_font_size}px"' if cell_font_size else ""
    for c, label in enumerate(matrix.columns):
        x = left + c * cell_w
        parts.append(
            f'<text class="small" x="{x + cell_w / 2:.1f}" y="{top - 16}" text-anchor="middle"{axis_style}>{esc(label)}</text>'
        )
    for r, label in enumerate(matrix.index):
        y = top + r * cell_h
        parts.append(
            f'<text class="label" x="{left - 12}" y="{y + cell_h * 0.58:.1f}" text-anchor="end"{axis_style}>{esc(label)}</text>'
        )
        for c, col in enumerate(matrix.columns):
            value = int(matrix.loc[label, col])
            intensity = log10(value + 1) / max_intensity if value else 0
            red = int(241 - intensity * 70)
            green = int(245 - intensity * 98)
            blue = int(249 - intensity * 150)
            color = f"rgb({red},{green},{blue})"
            x = left + c * cell_w
            pct = pct_labels.get((label, col), value / total * 100)
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 3}" height="{cell_h - 3}" fill="{color}" rx="2"/>'
            )
            if not value:
                continue
            text_class = "white" if intensity > 0.58 else "small"
            parts.append(
                f'<text class="{text_class}" x="{x + cell_w / 2:.1f}" y="{y + cell_h * 0.42:.1f}" text-anchor="middle"{cell_style}>{value}</text>'
            )
            parts.append(
                f'<text class="{text_class}" x="{x + cell_w / 2:.1f}" y="{y + cell_h * 0.68:.1f}" text-anchor="middle"{cell_style}>{pct:.1f}%</text>'
            )
    parts.append(
        f'<text class="small" x="{left}" y="{height - 28}">单元格为订单数和总体占比，颜色越深表示越集中。</text>'
    )
    write_svg(path, width, height, "\n".join(parts), title, subtitle)


def heatmap_hour_weekday(df, path):
    """星期-小时充电订单热力图。"""
    width, height = 1120, 500
    left, top = 92, 82
    cell_w, cell_h = 39, 40
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    hour_series = get_start_hour(df)
    pivot = pd.crosstab(df["星期"], hour_series).reindex(
        index=weekdays, columns=list(range(24)), fill_value=0
    )
    max_value = max(float(pivot.to_numpy().max()), 1)
    parts = []
    for h in range(24):
        x = left + h * cell_w
        parts.append(
            f'<text class="small" x="{x + cell_w / 2:.1f}" y="{top - 14}" text-anchor="middle">{h}</text>'
        )
    for r, weekday in enumerate(weekdays):
        y = top + r * cell_h
        parts.append(
            f'<text class="label" x="{left - 12}" y="{y + cell_h * 0.62:.1f}" text-anchor="end">{weekday}</text>'
        )
        for h in range(24):
            value = float(pivot.loc[weekday, h])
            intensity = value / max_value
            blue = int(245 - intensity * 150)
            color = f"rgb({blue},{blue + 4},{255})"
            x = left + h * cell_w
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" fill="{color}" rx="2"/>'
            )
            if intensity > 0.62:
                parts.append(
                    f'<text class="white" x="{x + cell_w / 2:.1f}" y="{y + cell_h * 0.62:.1f}" text-anchor="middle">{int(value)}</text>'
                )
    parts.append(
        f'<text class="small" x="{left}" y="{height - 32}">颜色越深表示订单越集中；横轴为开始小时。</text>'
    )
    write_svg(path, width, height, "\n".join(parts), "星期-小时充电订单热力图", "用于观察用户最常开始充电的时间组合")



# ===== 图型装配 =====

def plot_legacy_svg_charts(valid: pd.DataFrame, image_dir) -> list[tuple[str, object]]:
    """产出与 legacy/2026/ 对应的 15 类手写 SVG 图，返回 (标题, 路径) 列表。"""
    images: list[tuple[str, object]] = []

    # 1. 星期-小时充电订单热力图
    path = image_dir / "weekday_hour_heatmap.svg"
    heatmap_hour_weekday(valid, path)
    images.append(("星期-小时充电订单热力图", path))

    # 2. 核心充电时长结构
    path = image_dir / "duration_core_structure.svg"
    duration_core_counts = (
        valid["分析时长核心分组"].value_counts().reindex(DURATION_CORE_LABELS).dropna()
    )
    percent_bar_chart(
        duration_core_counts, path,
        "核心充电时长结构", "按用户行为解释口径汇总：短时补能、常规补能和长时停留",
    )
    images.append(("核心充电时长结构", path))

    # 3. 工作日/周末充电时长结构
    weekend_group = valid["星期"].isin(["星期六", "星期日"]).map({True: "周末", False: "工作日"})
    weekend_duration = pd.crosstab(weekend_group, valid["分析时长核心分组"]).reindex(
        index=["工作日", "周末"], columns=DURATION_CORE_LABELS, fill_value=0
    )
    path = image_dir / "weekday_weekend_duration_structure.svg"
    stacked_percent_chart(
        weekend_duration, path,
        "工作日/周末充电时长结构", "比较不同日期属性下，短时补能和长时补能占比",
    )
    images.append(("工作日/周末充电时长结构", path))

    # 4. 充电开始时段与时长结构
    period_duration = pd.crosstab(valid["分析开始时段"], valid["分析时长细分"]).reindex(
        index=START_PERIOD_ORDER, columns=DURATION_DETAIL_LABELS, fill_value=0
    )
    path = image_dir / "start_period_duration_structure.svg"
    crosstab_heatmap(
        period_duration, path,
        "充电开始时段与时长结构", "行表示开始时段，列表示单次充电时长；颜色越深表示订单越集中",
        axis_font_size=17, cell_font_size=16,
    )
    images.append(("充电开始时段与时长结构", path))

    # 5. 月度充电时长趋势
    # 只取两列再 dropna：旧脚本对整帧 dropna().copy()，在 2270 万行上要多占一整份内存
    monthly_source = valid[["充电开始时间", "充电时长(min)"]].dropna().copy()
    if not monthly_source.empty:
        monthly_source["月份"] = monthly_source["充电开始时间"].dt.to_period("M").astype(str)
        monthly_duration = monthly_source.groupby("月份")["充电时长(min)"].agg(
            中位时长="median", P75时长=lambda values: values.quantile(0.75)
        )
        path = image_dir / "monthly_duration_trend.svg"
        multi_line_chart(
            monthly_duration, path,
            "月度充电时长趋势", "用中位数和 P75 同时观察常规时长与长时订单变化",
        )
        images.append(("月度充电时长趋势", path))

    # 6. 充电开始时段占比（环形图）
    path = image_dir / "charge_period_donut.svg"
    donut_chart(
        valid["分析开始时段"].value_counts().reindex(START_PERIOD_ORDER).dropna(),
        path, "充电开始时段占比",
    )
    images.append(("充电粗略时段占比", path))

    if "场站类别" in valid.columns:
        # 7. 不同场站类别的充电时长与电量
        station_stats = (
            valid.groupby("场站类别")
            .agg(订单数=("充电时长(min)", "size"),
                 时长中位数=("充电时长(min)", "median"),
                 平均电量=("交易电量(kWh)（清洗后）", "mean"))
            .sort_values("订单数", ascending=False)
        )
        path = image_dir / "station_category_duration_energy.svg"
        metric_bar_chart(
            station_stats, path,
            "不同场站类别的充电时长与电量", "柱长表示时长中位数，标签补充平均电量和订单数",
        )
        images.append(("不同场站类别的充电时长与电量", path))

        # 8. 不同场站类别的充电开始时段结构
        station_period = pd.crosstab(valid["场站类别"], valid["分析开始时段"]).reindex(
            columns=START_PERIOD_ORDER, fill_value=0
        )
        path = image_dir / "station_category_start_period_structure.svg"
        stacked_percent_chart(
            station_period, path,
            "不同场站类别的充电开始时段结构", "比较高速、公共、专用和其他场站的时段偏好",
        )
        images.append(("不同场站类别的充电开始时段结构", path))

        # 9. 场站类别-充电时长结构矩阵
        station_duration = pd.crosstab(valid["场站类别"], valid["分析时长细分"]).reindex(
            columns=DURATION_DETAIL_LABELS, fill_value=0
        )
        path = image_dir / "station_category_duration_heatmap.svg"
        crosstab_heatmap(
            station_duration, path,
            "场站类别-充电时长结构矩阵", "定位不同场站场景下的短时、常规和长时补能差异",
            axis_font_size=19, cell_font_size=18,
        )
        images.append(("场站类别-充电时长结构矩阵", path))

        # 10. 不同场站类别的电量-时长组合结构（并入「总体」一行）
        station_energy_duration = pd.crosstab(
            valid["场站类别"], valid["电量时长组合类型"]
        ).reindex(columns=ENERGY_DURATION_TYPE_ORDER, fill_value=0)
        overall_energy_duration = (
            valid["电量时长组合类型"].value_counts()
            .reindex(ENERGY_DURATION_TYPE_ORDER).fillna(0)
        )
        station_energy_duration = pd.concat(
            [pd.DataFrame([overall_energy_duration], index=["总体"]), station_energy_duration]
        )
        path = image_dir / "station_category_energy_duration_type_structure.svg"
        stacked_percent_chart(
            station_energy_duration, path,
            "不同场站类别的电量-时长组合结构",
            "比较高速、公共、专用和其他场站的短时高电量、长时低电量和常规补能差异",
            show_table=False,
        )
        images.append(("不同场站类别的电量-时长组合结构", path))

        # 11. 不同场站类别的同站复用率
        if "是否复用上次站点" in valid.columns:
            comparable_reuse = valid.loc[
                valid["是否复用上次站点"].isin(["是", "否"]),
                ["场站类别", "是否复用上次站点"],
            ].copy()
            if not comparable_reuse.empty:
                reuse_counts = pd.crosstab(
                    comparable_reuse["场站类别"], comparable_reuse["是否复用上次站点"]
                ).reindex(columns=["是", "否"], fill_value=0)
                overall_reuse = (
                    comparable_reuse["是否复用上次站点"].value_counts()
                    .reindex(["是", "否"]).fillna(0)
                )
                reuse_counts = pd.concat(
                    [pd.DataFrame([overall_reuse], index=["总体"]), reuse_counts]
                )
                path = image_dir / "station_category_reuse_rate.svg"
                stacked_percent_chart(
                    reuse_counts, path,
                    "不同场站类别的同站复用率",
                    "仅统计是否复用上次站点为“是/否”的可比较订单，排除首次充电和站点缺失",
                    show_table=False,
                )
                images.append(("不同场站类别的同站复用率", path))

    # 12. 不同场站类别的优惠与谷段偏好对比
    if {"是否使用优惠", "谷段电量占比"}.issubset(valid.columns):
        # 旧脚本对整帧 copy() 后再挂两列；这里只拼一张三列的窄表，省下一份整帧内存
        price_source = pd.DataFrame({
            "场站类别": valid["场站类别"],
            "是否优惠订单": valid["是否使用优惠"].eq("是"),
            "是否谷段偏好": pd.to_numeric(valid["谷段电量占比"], errors="coerce").ge(0.25),
        })
        price_stats = price_source.groupby("场站类别").agg(
            优惠使用率=("是否优惠订单", "mean"),
            谷段偏好占比=("是否谷段偏好", "mean"),
            订单数=("是否优惠订单", "size"),
        )
        overall_price = pd.DataFrame(
            [{
                "优惠使用率": float(price_source["是否优惠订单"].mean()),
                "谷段偏好占比": float(price_source["是否谷段偏好"].mean()),
                "订单数": int(len(price_source)),
            }],
            index=["总体"],
        )
        price_stats = pd.concat([overall_price, price_stats])
        path = image_dir / "station_category_discount_valley_compare.svg"
        grouped_rate_bar_chart(
            price_stats, path,
            "不同场站类别的优惠与谷段偏好对比",
            "优惠使用率=使用优惠订单占比；谷段偏好=谷段电量占比>=25%的订单占比",
        )
        images.append(("不同场站类别的优惠与谷段偏好对比", path))

    # 13. 充电时长-电量组合矩阵
    path = image_dir / "duration_energy_matrix.svg"
    duration_energy_heatmap(valid, path)
    images.append(("充电时长-电量组合矩阵", path))

    # 14. 订单渠道 Top10+其他
    path = image_dir / "channel_top10_bar.svg"
    percent_bar_chart(
        top_plus_other(valid["订单渠道"].value_counts(), 10),
        path, "订单渠道 Top10+其他", "看用户主要入口渠道",
    )
    images.append(("订单渠道 Top10+其他", path))

    # 15. 谷段电量占比分布
    path = image_dir / "valley_ratio_distribution.svg"
    histogram(
        valid["谷段电量占比"],
        [0, 0.01, 0.25, 0.5, 0.75, 1.0],
        ["0", "0-25%", "25-50%", "50-75%", "75-100%"],
        path, "谷段电量占比分布", "用于观察用户是否偏好低谷时段充电",
    )
    images.append(("谷段电量占比分布", path))

    return images



# ===== 场站类别环形图 =====

def station_category_totals(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """按固定场站类别顺序，返回 (订单数, 场站数)。"""
    seen = set(df["场站类别"].dropna().unique())
    present = [c for c in STATION_CATEGORY_ORDER if c in seen]
    extra = sorted(seen - set(STATION_CATEGORY_ORDER))
    order = pd.to_numeric(df["原始订单数"], errors="coerce").fillna(1.0)
    weights = df.assign(_订单权重=order).groupby("场站类别")["_订单权重"].sum()
    stations = df.groupby("场站类别")["充电站ID"].nunique()
    keys = present + extra
    return weights.reindex(keys).fillna(0), stations.reindex(keys).fillna(0)


def _donut_axes(ax, values: pd.Series) -> None:
    colors = [
        STATION_CATEGORY_COLORS[STATION_CATEGORY_ORDER.index(k)]
        if k in STATION_CATEGORY_ORDER else "#94A3B8"
        for k in values.index
    ]
    ax.pie(
        values.values,
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.42, "edgecolor": "#ffffff", "linewidth": 1.6},
    )
    ax.set_aspect("equal")


def plot_order_donut_with_station_count(
    order_values: pd.Series, station_values: pd.Series, out_dir, save_svg
) -> None:
    """场站类别订单占比及场站数量（图例同时标注场站数）。"""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.8, 5.6), dpi=300)
    fig.subplots_adjust(left=0.02, right=0.62, top=0.84, bottom=0.06)
    _donut_axes(ax, order_values)

    total = float(order_values.sum())
    ax.text(0, 0.06, f"{int(round(total)):,}", ha="center", va="center", fontsize=22, fontweight="bold")
    ax.text(0, -0.12, "订单总数", ha="center", va="center", fontsize=12, color="#64748b")

    legend_lines = [
        f"{name}：{int(round(order_values[name])):,} 单 / "
        f"{(order_values[name] / total * 100 if total else 0):.1f}% ｜ "
        f"{int(station_values[name]):,} 个场站"
        for name in order_values.index
    ]
    fig.text(0.64, 0.78, "场站类别订单占比及场站数量", fontsize=15, fontweight="bold", va="top")
    fig.text(0.64, 0.70, "环形面积表示订单数占比，图例同时标注对应场站数量", fontsize=10, color="#64748b", va="top")
    for index, line in enumerate(legend_lines):
        fig.text(0.64, 0.60 - index * 0.075, line, fontsize=11, va="top")
    save_svg(fig, out_dir / "station_category_order_donut_with_station_count.svg")


def plot_station_count_donut(station_values: pd.Series, out_dir, save_svg) -> None:
    """场站类别数量占比。"""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.6, 5.2), dpi=300)
    fig.subplots_adjust(left=0.02, right=0.60, top=0.86, bottom=0.06)
    _donut_axes(ax, station_values)

    total = float(station_values.sum())
    ax.text(0, 0.06, f"{int(round(total)):,}", ha="center", va="center", fontsize=22, fontweight="bold")
    ax.text(0, -0.12, "场站数", ha="center", va="center", fontsize=12, color="#64748b")

    fig.text(0.62, 0.80, "场站类别数量占比", fontsize=15, fontweight="bold", va="top")
    for index, name in enumerate(station_values.index):
        share = station_values[name] / total * 100 if total else 0
        fig.text(0.62, 0.70 - index * 0.085,
                 f"{name}：{int(station_values[name]):,}（{share:.1f}%）", fontsize=11, va="top")
    save_svg(fig, out_dir / "station_category_station_count_donut.svg")


def plot_station_category_donuts(df: pd.DataFrame, out_dir, save_svg) -> None:
    """产出 2 类场站类别环形图（订单占比含场站数 / 场站数量占比）。"""
    order_values, station_values = station_category_totals(df)
    plot_order_donut_with_station_count(order_values, station_values, out_dir, save_svg)
    plot_station_count_donut(station_values, out_dir, save_svg)



# ===== 读取层 =====

def read_projected(path: Path, columns: list[str]) -> pd.DataFrame:
    """只读指定的列，全部按字符串读入，随后统一做类型转换。

    优先用 duckdb 投影读（快且省内存）；环境里没有 duckdb 时退回 pandas 分块读，
    仍然只保留需要的列。两条路径的返回值口径一致。
    """
    if not path.exists():
        raise FileNotFoundError(f"缺少输入文件：{path}")
    try:
        return _projected_read_duckdb(path, columns)
    except ImportError:
        return _projected_read_pandas(path, columns)


def _projected_read_duckdb(path: Path, columns: list[str]) -> pd.DataFrame:
    import duckdb

    quoted = ", ".join('"%s"' % c.replace('"', '""') for c in columns)
    target = str(path.resolve()).replace("\\", "/")
    connection = duckdb.connect()
    try:
        # all_varchar 防止类型推断改变下游口径（例如把充电站ID 当整数）
        return connection.execute(
            f"SELECT {quoted} FROM read_csv(?, header=true, all_varchar=true)",
            [target],
        ).df()
    finally:
        connection.close()


def _projected_read_pandas(path: Path, columns: list[str]) -> pd.DataFrame:
    wanted = set(columns)
    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        path,
        usecols=lambda c: c in wanted,
        encoding="utf-8-sig",
        low_memory=False,
        dtype=str,
        chunksize=1_000_000,
    ):
        chunks.append(chunk)
    if not chunks:
        return pd.DataFrame(columns=columns)
    return pd.concat(chunks, ignore_index=True)


def coerce_legacy_columns(df: pd.DataFrame) -> pd.DataFrame:
    """复刻旧 read_cleaned_orders 的日期/数值转换。"""
    for column in LEGACY_DATETIME_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], errors="coerce")
    for column in LEGACY_NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def intern_strings(df: pd.DataFrame) -> pd.DataFrame:
    """把文本列的字符串去重后重建，让整列指向同一份唯一值。

    投影读回的 2270 万行里，文本列每个单元格都是独立的 Python str 对象：
    像「是否使用优惠」只有 2 个取值，却要占 1.4 GB。factorize 重建后
    dtype 与取值顺序都不变，内存降到指针数组 + 一份去重字符串。
    """
    for column in df.columns:
        series = df[column]
        if series.dtype != object and not isinstance(series.dtype, pd.StringDtype):
            continue
        codes, uniques = pd.factorize(series)
        if len(uniques) == 0:
            continue
        values = np.asarray(uniques, dtype=object)[np.where(codes < 0, 0, codes)]
        if (codes < 0).any():
            values[codes < 0] = None
        df[column] = pd.Series(values, index=df.index, dtype=series.dtype)
    return df


def drop_cross_year_rows(df: pd.DataFrame, source_table_year) -> pd.DataFrame:
    """剔除「充电开始年份 ≠ 来源表年份」的跨年订单，与 aggregate() 的绘图口径对齐。"""
    if "来源表" not in df.columns or "充电开始时间" not in df.columns:
        return df
    table_year = pd.to_numeric(df["来源表"].map(source_table_year), errors="coerce")
    start_year = pd.to_datetime(df["充电开始时间"], errors="coerce").dt.year
    keep = table_year.isna() | start_year.isna() | start_year.eq(table_year)
    dropped = int((~keep).sum())
    if dropped:
        print(f"绘图口径剔除跨年订单 {dropped:,} 行（订单年份与来源表年份不一致，仅影响图表）")
    # 先过滤再 intern：df.loc[keep] 会重建文本列、丢掉字符串共享，
    # 反过来写会让 2270 万行重新退回「每格一个 str 对象」。
    return df.loc[keep].reset_index(drop=True)


def require_legacy_columns(cleaned_file: Path) -> None:
    """提前校验列齐不齐。

    投影读是直接按列名下推的，缺列时抛的是 duckdb / pandas 的底层错误，
    看不出是「这份清洗表口径不同」还是「代码写错了」。这里先读表头，缺哪列说哪列。
    """
    import csv

    needed = list(dict.fromkeys(LEGACY_SVG_COLUMNS + LEGACY_DONUT_COLUMNS))
    with cleaned_file.open(encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle), [])
    missing = [column for column in needed if column not in header]
    if missing:
        raise KeyError(
            f"清洗表缺少手写 SVG 图型所需的列：{missing}（表头共 {len(header)} 列）"
            f"，要么换一份清洗表，要么加 --skip-legacy-svg 跳过这批图：{cleaned_file}"
        )


def load_legacy_frame(cleaned_file: Path, source_table_year) -> pd.DataFrame:
    """为手写 SVG 图型载入「有效行为订单」子集。

    顺序：投影读 → 类型转换 → 剔除跨年 → 字符串去重。
    类型转换要放在去重之前，否则时间列会先被当成文本去重（2270 万个互不相同的取值）。
    """
    require_legacy_columns(cleaned_file)
    df = read_projected(cleaned_file, LEGACY_SVG_COLUMNS)
    df = intern_strings(drop_cross_year_rows(coerce_legacy_columns(df), source_table_year))
    if "是否有效行为订单" not in df.columns:
        raise KeyError(f"清洗表缺少「是否有效行为订单」列：{cleaned_file}")
    return df[df["是否有效行为订单"] == "是"].copy()


# ===== 手写 SVG 图型入口 =====
# 这批图来自旧脚本 scripts/analysis/analyze_2026_orders.py，源码与编译缓存均已删除，
# 与上面 matplotlib 那一套互不影响：直接拼 SVG 字符串写文件，不读 rcParams。
# 图型集合以 legacy/<年>/ 目录为准：15 类分析图 + 2 张场站类别环形图。

def legacy_targets(
    frame: pd.DataFrame, out_dir: Path, split_by_year: bool, min_year_orders: int
) -> list[tuple[Path, "set[int] | None"]]:
    """规划 [(目标目录, 年份集合)]；年份集合为 None 表示整帧出图。

    单年份数据刻意不按年份过滤：旧脚本对整帧出图，「充电开始时间」为空的行
    也会计入，过滤掉会让图对不上。
    """
    if not split_by_year:
        return [(out_dir, None)]
    start_year = frame["充电开始时间"].dt.year
    years = sorted(int(year) for year in start_year.dropna().unique())
    if not years:
        return [(out_dir, None)]
    if len(years) == 1:
        return [(out_dir / str(years[0]), None)]
    targets: list[tuple[Path, set[int]]] = []
    skipped: list[str] = []
    for year in years:
        count = int(start_year.eq(year).sum())
        if count < min_year_orders:
            skipped.append(f"{year}年（{count:,} 单）")
            continue
        targets.append((out_dir / str(year), {year}))
    if skipped:
        print(
            f"跳过样本量过小的年份（阈值 --min-year-orders {min_year_orders}）: "
            + "、".join(skipped)
        )
    return targets


def plot_legacy_svg_assets(
    cleaned_file: Path,
    out_dir: Path,
    split_by_year: bool,
    source_table_year,
    min_year_orders: int,
) -> None:
    """产出 legacy/ 口径的手写 SVG 图型：15 类分析图 + 2 张场站类别环形图。

    与 aggregate() 的流式聚合不同，这批图要逐行求分组、分位数和交叉表，
    因此一次性投影读入所需列，再按年份切片复用同一份帧。
    """
    frame = load_legacy_frame(cleaned_file, source_table_year)
    add_analysis_columns(frame)
    print(f"手写 SVG 图型：有效行为订单 {len(frame):,} 行")

    # 环形图按「原始订单数」加权，不过滤有效行为订单，列也不同，单独投影读一次
    donut = drop_cross_year_rows(
        coerce_legacy_columns(read_projected(cleaned_file, LEGACY_DONUT_COLUMNS)),
        source_table_year,
    )
    # coerce_legacy_columns 已把这两列转成 datetime，这里直接取年份
    donut_year = donut["充电开始时间"].dt.year
    frame_year = frame["充电开始时间"].dt.year

    for target_dir, year_set in legacy_targets(frame, out_dir, split_by_year, min_year_orders):
        if year_set is None:
            svg_frame, donut_frame = frame, donut
        else:
            svg_frame = frame[frame_year.isin(year_set)]
            donut_frame = donut[donut_year.isin(year_set)]
        target_dir.mkdir(parents=True, exist_ok=True)
        produced = plot_legacy_svg_charts(svg_frame, target_dir)
        plot_station_category_donuts(donut_frame, target_dir, save_svg)
        print(f"[手写 SVG] {len(produced) + 2} 张 -> {target_dir}")


def main() -> None:
    args = parse_args()
    setup_plotting()
    out_dir = output_dir(args.cleaned_file, args.image_prefix)
    overall, per_year = aggregate(
        args.cleaned_file,
        args.split_by_year,
        drop_cross_year=not args.keep_cross_year,
    )
    print(f"分析图输出目录: {out_dir}")

    run_for_agg(overall, out_dir, "（全部年份）" if args.split_by_year else "")
    print(f"[overall] 输出目录: {out_dir}")

    if args.split_by_year:
        skipped: list[str] = []
        for year, agg in sorted(per_year.items()):
            year_orders = int(sum(agg.daily.values()))
            if year_orders < args.min_year_orders:
                skipped.append(f"{year}年（{year_orders:,} 单）")
                continue
            year_dir = out_dir / str(year)
            run_for_agg(agg, year_dir, f"（{year}年）")
            fill_missing_year_assets(out_dir, year_dir)
            print(f"[year {year}] 输出目录: {year_dir}")
        if skipped:
            print(f"跳过样本量过小的年份（阈值 --min-year-orders {args.min_year_orders}）: " + "、".join(skipped))

    if args.skip_legacy_svg:
        print("已跳出手工 SVG 图型（--skip-legacy-svg）")
    else:
        plot_legacy_svg_assets(
            args.cleaned_file,
            out_dir,
            args.split_by_year,
            source_table_year,
            args.min_year_orders,
        )


if __name__ == "__main__":
    main()
