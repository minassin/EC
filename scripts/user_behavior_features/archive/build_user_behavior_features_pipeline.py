r"""用户行为特征构建一体化流水线（可视化除外）。

默认分析 2022-2026 年年度清洗订单；可通过 `--years` 指定单年或多个年份。
只服务用户行为模式识别，不做资产规划、设施选址或需求预测。

运行：
    .\.venv\Scripts\python.exe scripts\user_behavior_features\archive\build_user_behavior_features_pipeline.py
    .\.venv\Scripts\python.exe scripts\user_behavior_features\archive\build_user_behavior_features_pipeline.py --years 2026
    .\.venv\Scripts\python.exe scripts\user_behavior_features\archive\build_user_behavior_features_pipeline.py --years 2024 2025

可视化仍单独运行：
    .\.venv\Scripts\python.exe scripts\user_behavior_features\archive\visualize_user_behavior_features.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Callable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = Path(os.environ.get("EC_OUTPUT_ROOT", PROJECT_ROOT / "outputs"))
CLEANED_DIR = OUTPUT_ROOT / "cleaned"
ARCHIVE_FEATURE_ROOT = OUTPUT_ROOT / "features" / "archive"
FEATURE_DIR = ARCHIVE_FEATURE_ROOT / "all_years"
DEFAULT_YEARS = [2022, 2023, 2024, 2025, 2026]
CUSTOM_CLEANED_FILE: Path | None = None
READ_CHUNKSIZE = 4_000_000

FIELD_SCOPE_OVERVIEW = FEATURE_DIR / "field_scope_overview.csv"
FIELD_AVAILABILITY = FEATURE_DIR / "field_availability.csv"
CATEGORY_DISTRIBUTION = FEATURE_DIR / "category_distribution.csv"
METRIC_DEFINITIONS = FEATURE_DIR / "metric_definitions.csv"
ORDER_BASE_FILE = FEATURE_DIR / "order_behavior_base.csv"
ORDER_BASE_OVERVIEW = FEATURE_DIR / "order_behavior_base_overview.csv"
USER_FEATURE_FILE = FEATURE_DIR / "user_behavior_features.csv"
USER_FEATURE_OVERVIEW = FEATURE_DIR / "user_behavior_features_overview.csv"
USER_STATION_DETAIL_FILE = FEATURE_DIR / "user_station_top3_detail.csv"
USER_TIME_DETAIL_FILE = FEATURE_DIR / "user_time_preference_detail.csv"
RFM_SEGMENT_FILE = FEATURE_DIR / "user_behavior_rfm_segments.csv"
RFM_OVERVIEW_FILE = FEATURE_DIR / "user_behavior_rfm_overview.csv"
FINAL_PROFILE_FILE = FEATURE_DIR / "final_user_behavior_profile.csv"
FINAL_LABEL_FILE = FEATURE_DIR / "final_user_behavior_labels.csv"
FINAL_OVERVIEW_FILE = FEATURE_DIR / "final_user_behavior_output_overview.csv"
STATION_TOP3_DETAIL_FILE = FEATURE_DIR / "user_station_top3_detail.csv"

REQUIRED_COLUMNS = [
    "交易流水号",
    "用户识别主键",
    "用户编码",
    "订单状态",
    "是否有效行为订单",
    "充电开始时间",
    "充电结束时间",
    "充电日期",
    "星期",
    "充电粗略时段",
    "是否跨日",
    "充电时长(min)",
    "交易电量(kWh)（清洗后）",
    "峰电量(kWh)",
    "平电量(kWh)",
    "谷电量(kWh)",
    "谷段电量占比",
    "实扣金额",
    "优惠金额",
    "是否使用优惠",
    "单度价格",
    "平均充电功率(kW)",
    "充电方式",
    "订单渠道",
    "订单来源",
    "充电站ID",
    "充电桩编号",
    "距上次充电间隔(h)",
    "是否复用上次站点",
    "结束原因分类",
    "异常原因",
]

CATEGORY_COLUMNS = ["订单状态", "是否有效行为订单", "充电粗略时段", "订单渠道", "充电方式", "结束原因分类"]
NUMERIC_COLUMNS = [
    "充电时长(min)",
    "交易电量(kWh)（清洗后）",
    "峰电量(kWh)",
    "平电量(kWh)",
    "谷电量(kWh)",
    "谷段电量占比",
    "实扣金额",
    "优惠金额",
    "单度价格",
    "平均充电功率(kW)",
    "距上次充电间隔(h)",
]
FAULT_END_REASONS = {"故障异常", "其他原因", "设备故障", "异常中断", "故障或异常结束"}
BUS_STATION_CATEGORY = "公交场站"
RECENT_WINDOW_DAYS = 90


def year_file(year: int | str) -> Path:
    if CUSTOM_CLEANED_FILE is not None:
        return CUSTOM_CLEANED_FILE
    return CLEANED_DIR / f"orders_{year}_standard_user_orders.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 archive 用户行为特征与标签结果。")
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=DEFAULT_YEARS,
        help="需要分析的年份，例如 --years 2026；默认 2022 2023 2024 2025 2026。",
    )
    parser.add_argument("--cleaned-file", type=Path, help="Directly use one cleaned orders CSV.")
    parser.add_argument("--label", help="Output label used with --cleaned-file.")
    parser.add_argument("--chunksize", type=int, default=READ_CHUNKSIZE, help="Rows per CSV chunk. Larger is faster but uses more memory.")
    parser.add_argument("--recent-window-days", type=int, default=RECENT_WINDOW_DAYS, help="只分析样本末次订单前指定天数内的有效订单，例如 90 或 180。")
    return parser.parse_args()


def output_dir_for_years(years: list[int]) -> Path:
    years = sorted(dict.fromkeys(years))
    if years == DEFAULT_YEARS:
        return ARCHIVE_FEATURE_ROOT / "all_years"
    if len(years) == 1:
        return ARCHIVE_FEATURE_ROOT / str(years[0])
    return ARCHIVE_FEATURE_ROOT / ("years_" + "_".join(map(str, years)))


def safe_path_label(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value).strip())
    return text or "custom"


def set_feature_dir(path: Path) -> None:
    global FEATURE_DIR
    global FIELD_SCOPE_OVERVIEW, FIELD_AVAILABILITY, CATEGORY_DISTRIBUTION, METRIC_DEFINITIONS
    global ORDER_BASE_FILE, ORDER_BASE_OVERVIEW, USER_FEATURE_FILE, USER_FEATURE_OVERVIEW
    global USER_STATION_DETAIL_FILE, USER_TIME_DETAIL_FILE, STATION_TOP3_DETAIL_FILE, RFM_SEGMENT_FILE, RFM_OVERVIEW_FILE, FINAL_PROFILE_FILE, FINAL_LABEL_FILE, FINAL_OVERVIEW_FILE

    FEATURE_DIR = path
    FIELD_SCOPE_OVERVIEW = FEATURE_DIR / "field_scope_overview.csv"
    FIELD_AVAILABILITY = FEATURE_DIR / "field_availability.csv"
    CATEGORY_DISTRIBUTION = FEATURE_DIR / "category_distribution.csv"
    METRIC_DEFINITIONS = FEATURE_DIR / "metric_definitions.csv"
    ORDER_BASE_FILE = FEATURE_DIR / "order_behavior_base.csv"
    ORDER_BASE_OVERVIEW = FEATURE_DIR / "order_behavior_base_overview.csv"
    USER_FEATURE_FILE = FEATURE_DIR / "user_behavior_features.csv"
    USER_FEATURE_OVERVIEW = FEATURE_DIR / "user_behavior_features_overview.csv"
    USER_STATION_DETAIL_FILE = FEATURE_DIR / "user_station_top3_detail.csv"
    USER_TIME_DETAIL_FILE = FEATURE_DIR / "user_time_preference_detail.csv"
    STATION_TOP3_DETAIL_FILE = USER_STATION_DETAIL_FILE
    RFM_SEGMENT_FILE = FEATURE_DIR / "user_behavior_rfm_segments.csv"
    RFM_OVERVIEW_FILE = FEATURE_DIR / "user_behavior_rfm_overview.csv"
    FINAL_PROFILE_FILE = FEATURE_DIR / "final_user_behavior_profile.csv"
    FINAL_LABEL_FILE = FEATURE_DIR / "final_user_behavior_labels.csv"
    FINAL_OVERVIEW_FILE = FEATURE_DIR / "final_user_behavior_output_overview.csv"


def configure_output_paths(years: list[int]) -> None:
    set_feature_dir(output_dir_for_years(years))


def configure_custom_output_paths(label: str) -> None:
    set_feature_dir(ARCHIVE_FEATURE_ROOT / safe_path_label(label))


def clean_text(value: object, default: str = "") -> str:
    if pd.isna(value):
        return default
    text = str(value).strip()
    if text.lower() == "nan":
        return default
    return text if text else default


def validate_cleaned_snapshot(path: Path) -> None:
    meta_path = path.with_suffix(".meta.json")
    if not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    expected_rows = int(meta.get("cleaned_rows", -1))
    expected_raw_orders = int(meta.get("cleaned_raw_orders", -1))
    if expected_rows < 0 or expected_raw_orders < 0:
        raise SystemExit(f"快照元数据不完整：{meta_path}")

    actual_rows = 0
    actual_raw_orders = 0
    for chunk in pd.read_csv(
        path,
        encoding="utf-8-sig",
        usecols=lambda c: c == "原始订单数" or c in REQUIRED_COLUMNS,
        dtype=object,
        chunksize=300_000,
    ):
        actual_rows += len(chunk)
        if "原始订单数" in chunk.columns:
            actual_raw_orders += int(pd.to_numeric(chunk["原始订单数"], errors="coerce").fillna(1).clip(lower=1).sum())

    if actual_rows != expected_rows or actual_raw_orders != expected_raw_orders:
        raise SystemExit(
            "清洗快照不一致，已停止构建用户特征。\n"
            f"- CSV: {path}\n"
            f"- meta: {meta_path}\n"
            f"- 行数: {actual_rows:,} != {expected_rows:,}\n"
            f"- 原始订单数合计: {actual_raw_orders:,} != {expected_raw_orders:,}"
        )


def is_yes(value: object) -> bool:
    return clean_text(value).lower() in {"是", "true", "1", "yes", "y"}


def yes_no(value: bool) -> str:
    return "是" if bool(value) else "否"


def to_num(series: pd.Series | object) -> pd.Series:
    if isinstance(series, pd.Series):
        return pd.to_numeric(series, errors="coerce")
    return pd.to_numeric(pd.Series(series), errors="coerce")


def normalize_period(value: object) -> str:
    text = clean_text(value, "未知")
    match = re.search(r"(?<!\d)(\d{1,2})(?::\d{2})?", text)
    if match:
        hour = min(max(int(match.group(1)), 0), 23)
        return f"{hour:02d}:00-{hour:02d}:59"
    return "未知"


def infer_period(hour: object) -> str:
    if pd.isna(hour):
        return "未知"
    h = min(max(int(hour), 0), 23)
    return f"{h:02d}:00-{h:02d}:59"


def period_from_start(start: pd.Series) -> pd.Series:
    return start.dt.hour.map(infer_period)


def duration_bucket(value: object) -> str:
    if pd.isna(value):
        return "未知"
    minutes = float(value)
    if minutes <= 30:
        return "短时"
    if minutes <= 240:
        return "正常"
    return "超长"


def safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def csv_chunks(path: Path, *, usecols: list[str] | None = None):
    return pd.read_csv(
        path,
        encoding="utf-8-sig",
        low_memory=False,
        chunksize=READ_CHUNKSIZE,
        usecols=usecols,
    )


def compute_recent_window_cutoff(path: Path, time_col: str = "充电开始时间", days: int = RECENT_WINDOW_DAYS) -> pd.Timestamp:
    latest = pd.NaT
    for chunk in csv_chunks(path, usecols=[time_col]):
        series = pd.to_datetime(chunk[time_col], errors="coerce")
        if series.notna().any():
            chunk_max = series.max()
            latest = chunk_max if pd.isna(latest) or chunk_max > latest else latest
    if pd.isna(latest):
        return pd.NaT
    return latest - pd.Timedelta(days=days)


def read_years(years: list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for year in years:
        path = year_file(year)
        if not path.exists():
            raise FileNotFoundError(f"缺少年度订单清洗主表：{path}")
        df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        df["数据年份"] = year
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def step_field_scope(years: list[int]) -> None:
    overview_rows: list[dict[str, object]] = []
    availability_rows: list[dict[str, object]] = []
    category_rows: list[dict[str, object]] = []

    for year in years:
        path = year_file(year)
        header = list(pd.read_csv(path, encoding="utf-8-sig", nrows=0).columns)
        total_rows = 0
        valid_rows = 0
        abnormal_rows = 0
        users: set[str] = set()
        min_start = pd.NaT
        max_start = pd.NaT
        missing_counts = {col: 0 for col in REQUIRED_COLUMNS}
        invalid_counts = {col: 0 for col in REQUIRED_COLUMNS}
        category_counts: dict[str, Counter] = {col: Counter() for col in CATEGORY_COLUMNS if col in header}

        for df in csv_chunks(path):
            total_rows += len(df)
            valid = df.get("是否有效行为订单", pd.Series(False, index=df.index)).map(is_yes)
            abnormal_reason = df.get("异常原因", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
            start_time = pd.to_datetime(df.get("充电开始时间"), errors="coerce")
            user_key = df.get("用户识别主键", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
            users.update(user_key[user_key.ne("")].unique())
            valid_rows += int(valid.sum())
            abnormal_rows += int((~valid | abnormal_reason.ne("")).sum())
            if start_time.notna().any():
                chunk_min = start_time.min()
                chunk_max = start_time.max()
                min_start = chunk_min if pd.isna(min_start) or chunk_min < min_start else min_start
                max_start = chunk_max if pd.isna(max_start) or chunk_max > max_start else max_start

            for col in REQUIRED_COLUMNS:
                if col not in df.columns:
                    continue
                raw = df[col]
                missing_counts[col] += int(raw.isna().sum())
                if col in NUMERIC_COLUMNS:
                    invalid_counts[col] += int(pd.to_numeric(raw, errors="coerce").isna().sum() - raw.isna().sum())

            for col in category_counts:
                series = df[col].map(normalize_period) if col == "充电粗略时段" else df[col].fillna("未知").astype(str).str.strip().replace("", "未知")
                category_counts[col].update(series.value_counts().to_dict())

        overview_rows.append(
            {
                "数据年份": year,
                "文件路径": str(path),
                "订单总数": int(total_rows),
                "用户数": int(len(users)),
                "有效订单数": int(valid_rows),
                "异常订单数": int(abnormal_rows),
                "最早充电开始日期": min_start.date().isoformat() if pd.notna(min_start) else "",
                "最晚充电开始日期": max_start.date().isoformat() if pd.notna(max_start) else "",
                "缺失必需字段": "无" if all(col in header for col in REQUIRED_COLUMNS) else "；".join([col for col in REQUIRED_COLUMNS if col not in header]),
            }
        )

        for col in REQUIRED_COLUMNS:
            availability_rows.append(
                {
                    "数据年份": year,
                    "字段": col,
                    "是否存在": "是" if col in header else "否",
                    "缺失数": missing_counts[col] if col in header else "",
                    "无法转换数": invalid_counts[col],
                }
            )

        for col, counts in category_counts.items():
            for value, count in counts.most_common(12):
                category_rows.append({"数据年份": year, "字段": col, "取值": value, "订单数": int(count), "占比": round(safe_div(count, total_rows), 4)})

    definitions = pd.DataFrame(
        [
            {"口径项": "统计周期", "定义": f"本次分析年份：{'、'.join(map(str, years))}。"},
            {"口径项": "用户主键", "定义": "优先使用 用户识别主键；缺失时参考 用户编码。"},
            {"口径项": "有效订单", "定义": "是否有效行为订单 == 是。"},
            {"口径项": "异常订单", "定义": "异常原因非空，或 是否有效行为订单 != 是。"},
            {"口径项": "沉默用户", "定义": "以样本最大充电日期为观察截止日，按距最近一次充电天数分档。"},
        ]
    )

    FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(overview_rows).to_csv(FIELD_SCOPE_OVERVIEW, index=False, encoding="utf-8-sig", lineterminator="\n")
    pd.DataFrame(availability_rows).to_csv(FIELD_AVAILABILITY, index=False, encoding="utf-8-sig", lineterminator="\n")
    pd.DataFrame(category_rows).to_csv(CATEGORY_DISTRIBUTION, index=False, encoding="utf-8-sig", lineterminator="\n")
    definitions.to_csv(METRIC_DEFINITIONS, index=False, encoding="utf-8-sig", lineterminator="\n")
    print("字段梳理与指标口径定义完成")
    print(pd.DataFrame(overview_rows).to_string(index=False))


def step_order_base_duckdb(years: list[int]) -> None:
    try:
        import duckdb
    except ImportError:
        print("未检测到 duckdb，回退到 pandas 分块生成订单级基础表。")
        step_order_base(years)
        return

    def qid(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def qstr(value: object) -> str:
        return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"

    def source_select(path: Path, year: object) -> str:
        p = qstr(path)
        y = qstr(year)
        fault_reasons = ", ".join(qstr(x) for x in FAULT_END_REASONS)
        return f"""
        WITH raw AS (
            SELECT *
            FROM read_csv({p}, header=true, all_varchar=true, ignore_errors=true, union_by_name=true)
        ),
        typed AS (
            SELECT
                {y} AS data_year,
                TRY_CAST({qid("充电开始时间")} AS TIMESTAMP) AS start_ts,
                TRY_CAST({qid("充电结束时间")} AS TIMESTAMP) AS end_ts,
                TRY_CAST({qid("充电时长(min)")} AS DOUBLE) AS duration_min,
                TRY_CAST({qid("交易电量(kWh)（清洗后）")} AS DOUBLE) AS energy_kwh,
                TRY_CAST({qid("峰电量(kWh)")} AS DOUBLE) AS peak_kwh,
                TRY_CAST({qid("平电量(kWh)")} AS DOUBLE) AS flat_kwh,
                TRY_CAST({qid("谷电量(kWh)")} AS DOUBLE) AS valley_kwh,
                TRY_CAST({qid("谷段电量占比")} AS DOUBLE) AS valley_share,
                TRY_CAST({qid("平均充电功率(kW)")} AS DOUBLE) AS power_kw,
                TRY_CAST({qid("实扣金额")} AS DOUBLE) AS paid_amount,
                TRY_CAST({qid("优惠金额")} AS DOUBLE) AS discount_amount,
                TRY_CAST({qid("单度价格")} AS DOUBLE) AS unit_price,
                TRY_CAST({qid("距上次充电间隔(h)")} AS DOUBLE) AS interval_h,
                CASE
                    WHEN start_ts IS NULL THEN '未知'
                    ELSE LPAD(CAST(EXTRACT(HOUR FROM start_ts) AS VARCHAR), 2, '0')
                         || ':00-' ||
                         LPAD(CAST(EXTRACT(HOUR FROM start_ts) AS VARCHAR), 2, '0')
                         || ':59'
                END AS period,
                COALESCE(NULLIF(TRIM({qid("异常原因")}), ''), '') AS abnormal_reason,
                COALESCE(NULLIF(TRIM({qid("结束原因分类")}), ''), '未知') AS end_reason,
                *
            FROM raw
        )
        SELECT
            data_year AS {qid("数据年份")},
            EXTRACT(YEAR FROM start_ts) AS {qid("自然年份")},
            TRIM(COALESCE({qid("交易流水号")}, '')) AS {qid("交易流水号")},
            TRIM(COALESCE({qid("用户识别主键")}, '')) AS {qid("用户识别主键")},
            TRIM(COALESCE({qid("用户编码")}, '')) AS {qid("用户编码")},
            start_ts AS {qid("充电开始时间")},
            end_ts AS {qid("充电结束时间")},
            CAST(start_ts AS DATE) AS {qid("充电日期")},
            EXTRACT(HOUR FROM start_ts) AS {qid("充电开始小时")},
            EXTRACT(MONTH FROM start_ts) AS {qid("月份")},
            COALESCE(NULLIF(TRIM({qid("星期")}), ''), '未知') AS {qid("星期")},
            CASE WHEN TRIM(COALESCE({qid("星期")}, '')) IN ('星期六', '星期日', '周六', '周日') THEN '是' ELSE '否' END AS {qid("是否周末")},
            period AS {qid("充电粗略时段")},
            CASE WHEN period IN ('凌晨（00:00-05:59）', '晚上（18:00-23:59）') THEN '是' ELSE '否' END AS {qid("是否夜间充电")},
            CASE WHEN period = '凌晨（00:00-05:59）' THEN '是' ELSE '否' END AS {qid("是否凌晨充电")},
            CASE WHEN start_ts IS NOT NULL AND end_ts IS NOT NULL AND CAST(start_ts AS DATE) <> CAST(end_ts AS DATE) THEN '是' ELSE '否' END AS {qid("是否跨日充电")},
            ROUND(duration_min, 3) AS {qid("单次充电时长(min)")},
            ROUND(energy_kwh, 3) AS {qid("单次充电量(kWh)")},
            ROUND(peak_kwh, 3) AS {qid("峰段电量(kWh)")},
            ROUND(flat_kwh, 3) AS {qid("平段电量(kWh)")},
            ROUND(valley_kwh, 3) AS {qid("谷段电量(kWh)")},
            ROUND(valley_share, 4) AS {qid("谷段电量占比")},
            ROUND(power_kw, 3) AS {qid("平均充电功率(kW)")},
            ROUND(paid_amount, 2) AS {qid("单次消费金额")},
            ROUND(COALESCE(discount_amount, 0), 2) AS {qid("优惠金额")},
            CASE WHEN {qid("是否使用优惠")} = '是' OR COALESCE(discount_amount, 0) > 0 THEN '是' ELSE '否' END AS {qid("是否使用优惠")},
            ROUND(COALESCE(discount_amount, 0) / NULLIF(COALESCE(paid_amount, 0) + COALESCE(discount_amount, 0), 0), 4) AS {qid("优惠金额占比")},
            ROUND(unit_price, 4) AS {qid("单度价格")},
            CASE WHEN duration_min <= 30 THEN '是' ELSE '否' END AS {qid("是否短时充电")},
            CASE WHEN duration_min > 240 THEN '是' ELSE '否' END AS {qid("是否长时充电")},
            CASE WHEN energy_kwh >= 40 THEN '是' ELSE '否' END AS {qid("是否高电量充电")},
            CASE WHEN energy_kwh <= 0 THEN '是' ELSE '否' END AS {qid("是否零电量")},
            CASE WHEN valley_share >= 0.5 THEN '是' ELSE '否' END AS {qid("是否谷段偏好订单")},
            COALESCE(NULLIF(TRIM({qid("充电方式")}), ''), '未知') AS {qid("充电方式")},
            COALESCE(NULLIF(TRIM({qid("订单渠道")}), ''), '未知') AS {qid("订单渠道")},
            COALESCE(NULLIF(TRIM({qid("订单来源")}), ''), '未知') AS {qid("订单来源")},
            TRIM(COALESCE({qid("充电站ID")}, '')) AS {qid("充电站ID")},
            TRIM(COALESCE({qid("充电桩编号")}, '')) AS {qid("充电桩编号")},
            ROUND(interval_h, 3) AS {qid("距上次充电间隔(h)")},
            CASE WHEN {qid("是否复用上次站点")} = '是' THEN '是' ELSE '否' END AS {qid("是否复用上次站点")},
            CASE WHEN {qid("是否有效行为订单")} = '是' THEN '是' ELSE '否' END AS {qid("是否有效行为订单")},
            CASE WHEN {qid("是否有效行为订单")} = '是' THEN '否' ELSE '是' END AS {qid("是否无效订单")},
            CASE WHEN {qid("是否有效行为订单")} = '是' AND (abnormal_reason NOT IN ('', '无', 'None', 'null') OR end_reason IN ({fault_reasons})) THEN '是' ELSE '否' END AS {qid("是否风险订单")},
            CASE WHEN end_reason IN ({fault_reasons}) THEN '是' ELSE '否' END AS {qid("是否异常结束订单")},
            CASE WHEN energy_kwh <= 0 THEN '是' ELSE '否' END AS {qid("是否零电量订单")},
            CASE WHEN duration_min > 240 THEN '是' ELSE '否' END AS {qid("是否超长充电订单")},
            end_reason AS {qid("结束原因分类")},
            abnormal_reason AS {qid("异常原因类型")}
        FROM typed
        WHERE start_ts >= TIMESTAMP '2020-01-01'
          AND start_ts < TIMESTAMP '2027-01-01'
          AND COALESCE(NULLIF(TRIM({qid("场站类别")}), ''), '') <> {qstr(BUS_STATION_CATEGORY)}
        """

    FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    for path in [ORDER_BASE_FILE, ORDER_BASE_OVERVIEW]:
        path.unlink(missing_ok=True)

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA preserve_insertion_order=false")
    con.execute(f"PRAGMA temp_directory={qstr(str(FEATURE_DIR))}")

    selects = [source_select(year_file(year), year) for year in years]
    union_sql = "\nUNION ALL\n".join(selects)
    con.execute(f"CREATE OR REPLACE TEMP TABLE order_base_raw AS {union_sql}")
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE order_base AS
        WITH sample AS (
            SELECT MAX(TRY_CAST({qid("充电开始时间")} AS TIMESTAMP)) AS sample_max
            FROM order_base_raw
        )
        SELECT r.*
        FROM order_base_raw r
        CROSS JOIN sample s
        WHERE r.{qid("充电开始时间")} IS NOT NULL
          AND r.{qid("充电开始时间")} >= s.sample_max - INTERVAL '{RECENT_WINDOW_DAYS} days'
        """
    )
    con.execute(f"COPY order_base TO {qstr(ORDER_BASE_FILE)} (HEADER, DELIMITER ',', QUOTE '\"')")

    overview = con.execute(
        f"""
        SELECT
            {qid("数据年份")},
            COUNT(*) AS {qid("订单级行为记录数")},
            COUNT(DISTINCT NULLIF(TRIM({qid("用户识别主键")}), '')) AS {qid("用户数")},
            SUM(CASE WHEN {qid("是否有效行为订单")} = '是' THEN 1 ELSE 0 END) AS {qid("有效行为订单数")},
            SUM(CASE WHEN {qid("是否无效订单")} = '是' THEN 1 ELSE 0 END) AS {qid("无效订单数")},
            SUM(CASE WHEN {qid("是否风险订单")} = '是' THEN 1 ELSE 0 END) AS {qid("风险订单数")},
            ROUND(SUM(CASE WHEN {qid("是否风险订单")} = '是' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0), 4) AS {qid("风险订单占比")}
        FROM order_base
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchdf()
    overview.to_csv(ORDER_BASE_OVERVIEW, index=False, encoding="utf-8-sig", lineterminator="\n")
    print("订单级基础特征提取完成（DuckDB 快速生成）")
    print(overview.to_string(index=False))


def step_order_base(years: list[int]) -> None:
    first = True
    overview_stats: dict[object, dict[str, object]] = {}
    latest = pd.NaT
    for year in years:
        path = year_file(year)
        for chunk in csv_chunks(path, usecols=["充电开始时间"]):
            start = pd.to_datetime(chunk["充电开始时间"], errors="coerce")
            if start.notna().any():
                chunk_max = start.max()
                latest = chunk_max if pd.isna(latest) or chunk_max > latest else latest
    cutoff = latest - pd.Timedelta(days=RECENT_WINDOW_DAYS) if pd.notna(latest) else pd.NaT

    for year in years:
        path = year_file(year)
        for df in csv_chunks(path):
            for col in REQUIRED_COLUMNS:
                if col not in df.columns:
                    df[col] = pd.NA

            df["数据年份"] = year
            start = pd.to_datetime(df["充电开始时间"], errors="coerce")
            end = pd.to_datetime(df["充电结束时间"], errors="coerce")
            if pd.notna(cutoff):
                df = df[start.notna() & (start >= cutoff)].copy()
                start = start.loc[df.index]
                end = end.loc[df.index]
            else:
                df = df[start.notna()].copy()
                start = start.loc[df.index]
                end = end.loc[df.index]
            if df.empty:
                continue
            period = period_from_start(start)
            missing_period = period.eq("未知") & start.notna()
            if missing_period.any():
                period.loc[missing_period] = df.loc[missing_period, "充电粗略时段"].map(normalize_period)

            amount = to_num(df["实扣金额"])
            discount = to_num(df["优惠金额"]).fillna(0)
            discount_den = amount.fillna(0) + discount
            valid = df["是否有效行为订单"].map(is_yes)
            abnormal_reason = df["异常原因"].fillna("").astype(str).str.strip().replace({"无": "", "None": "", "null": ""})
            end_reason = df["结束原因分类"].fillna("未知").astype(str).str.strip()
            fault_end = end_reason.isin(FAULT_END_REASONS)
            risk = valid & (abnormal_reason.ne("") | fault_end)
            duration = to_num(df["充电时长(min)"])
            energy = to_num(df["交易电量(kWh)（清洗后）"])
            valley_ratio = to_num(df["谷段电量占比"])

            out = pd.DataFrame(
                {
                    "数据年份": df["数据年份"],
                    "自然年份": start.dt.year.astype("Int64"),
                    "交易流水号": df["交易流水号"].fillna("").astype(str).str.strip(),
                    "用户识别主键": df["用户识别主键"].fillna("").astype(str).str.strip(),
                    "用户编码": df["用户编码"].fillna("").astype(str).str.strip(),
                    "充电开始时间": start,
                    "充电结束时间": end,
                    "充电日期": start.dt.date,
                    "充电开始小时": start.dt.hour.astype("Int64"),
                    "月份": start.dt.month.astype("Int64"),
                    "星期": df["星期"].fillna("未知").astype(str).str.strip(),
                    "是否周末": df["星期"].fillna("").astype(str).str.strip().isin(["星期六", "星期日", "周六", "周日"]).map(yes_no),
                    "充电粗略时段": period,
                    "是否夜间充电": period.isin(["凌晨（00:00-05:59）", "晚上（18:00-23:59）"]).map(yes_no),
                    "是否凌晨充电": period.eq("凌晨（00:00-05:59）").map(yes_no),
                    "是否跨日充电": (start.notna() & end.notna() & start.dt.date.ne(end.dt.date)).map(yes_no),
                    "单次充电时长(min)": duration.round(3),
                    "单次充电量(kWh)": energy.round(3),
                    "峰段电量(kWh)": to_num(df["峰电量(kWh)"]).round(3),
                    "平段电量(kWh)": to_num(df["平电量(kWh)"]).round(3),
                    "谷段电量(kWh)": to_num(df["谷电量(kWh)"]).round(3),
                    "谷段电量占比": valley_ratio.round(4),
                    "平均充电功率(kW)": to_num(df["平均充电功率(kW)"]).round(3),
                    "单次消费金额": amount.round(2),
                    "优惠金额": discount.round(2),
                    "是否使用优惠": (df["是否使用优惠"].map(is_yes) | discount.gt(0)).map(yes_no),
                    "优惠金额占比": (discount / discount_den.where(discount_den > 0)).fillna(0).round(4),
                    "单度价格": to_num(df["单度价格"]).round(4),
                    "是否短时充电": duration.le(30).map(yes_no),
                    "是否长时充电": duration.gt(240).map(yes_no),
                    "是否高电量充电": energy.ge(40).map(yes_no),
                    "是否零电量": energy.le(0).map(yes_no),
                    "是否谷段偏好订单": valley_ratio.ge(0.5).map(yes_no),
                    "充电方式": df["充电方式"].fillna("未知").astype(str).str.strip(),
                    "订单渠道": df["订单渠道"].fillna("未知").astype(str).str.strip(),
                    "订单来源": df["订单来源"].fillna("未知").astype(str).str.strip(),
                    "充电站ID": df["充电站ID"].fillna("").astype(str).str.strip(),
                    "充电桩编号": df["充电桩编号"].fillna("").astype(str).str.strip(),
                    "距上次充电间隔(h)": to_num(df["距上次充电间隔(h)"]).round(3),
                    "是否复用上次站点": df["是否复用上次站点"].map(is_yes).map(yes_no),
                    "是否有效行为订单": valid.map(yes_no),
                    "是否无效订单": (~valid).map(yes_no),
                    "是否风险订单": risk.map(yes_no),
                    "是否异常结束订单": fault_end.map(yes_no),
                    "是否零电量订单": energy.le(0).map(yes_no),
                    "是否超长充电订单": duration.gt(240).map(yes_no),
                    "结束原因分类": end_reason,
                    "异常原因类型": abnormal_reason,
                }
            )
            out = drop_bus_station_rows(out, source_name=f"{year}")
            out.to_csv(
                ORDER_BASE_FILE,
                index=False,
                mode="w" if first else "a",
                header=first,
                encoding="utf-8-sig",
                lineterminator="\n",
            )
            first = False

            stats = overview_stats.setdefault(
                year,
                {"订单级行为记录数": 0, "用户集": set(), "有效行为订单数": 0, "无效订单数": 0, "风险订单数": 0},
            )
            stats["订单级行为记录数"] += len(out)
            stats["用户集"].update(out["用户识别主键"].astype(str).str.strip().replace("", pd.NA).dropna().unique())
            stats["有效行为订单数"] += int(out["是否有效行为订单"].eq("是").sum())
            stats["无效订单数"] += int(out["是否无效订单"].eq("是").sum())
            stats["风险订单数"] += int(out["是否风险订单"].eq("是").sum())

    overview = pd.DataFrame(
        [
            {
                "数据年份": year,
                "订单级行为记录数": stats["订单级行为记录数"],
                "用户数": len(stats["用户集"]),
                "有效行为订单数": stats["有效行为订单数"],
                "无效订单数": stats["无效订单数"],
                "风险订单数": stats["风险订单数"],
                "风险订单占比": round(safe_div(stats["风险订单数"], stats["订单级行为记录数"]), 4),
            }
            for year, stats in overview_stats.items()
        ]
    )
    overview.to_csv(ORDER_BASE_OVERVIEW, index=False, encoding="utf-8-sig", lineterminator="\n")
    print("订单级基础特征提取完成")
    print(overview.to_string(index=False))


def top_value(series: pd.Series) -> tuple[str, float, int]:
    clean = series.fillna("未知").astype(str).str.strip()
    clean = clean[clean.ne("") & clean.ne("未知")]
    if clean.empty:
        return "", 0.0, 0
    counts = clean.value_counts()
    return str(counts.index[0]), safe_div(int(counts.iloc[0]), len(series)), int(counts.shape[0])


def drop_bus_station_rows(df: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    if "场站类别" not in df.columns:
        return df
    before = len(df)
    mask = df["场站类别"].fillna("").astype(str).str.strip().ne(BUS_STATION_CATEGORY)
    filtered = df.loc[mask].copy()
    after = len(filtered)
    if before != after:
        print(f"{source_name} 已剔除公交场站订单: {before - after:,} 条，保留 {after:,} 条")
    return filtered


def step_user_features() -> None:
    if not ORDER_BASE_FILE.exists():
        raise FileNotFoundError(f"缺少订单级行为明细，请先运行本流水线：{ORDER_BASE_FILE}")
    usecols = [
        "用户识别主键",
        "用户编码",
        "自然年份",
        "充电开始时间",
        "充电日期",
        "距上次充电间隔(h)",
        "充电粗略时段",
        "是否凌晨充电",
        "是否周末",
        "是否夜间充电",
        "是否跨日充电",
        "单次充电量(kWh)",
        "单次充电时长(min)",
        "平均充电功率(kW)",
        "是否短时充电",
        "是否长时充电",
        "是否高电量充电",
        "峰段电量(kWh)",
        "平段电量(kWh)",
        "谷段电量(kWh)",
        "谷段电量占比",
        "单次消费金额",
        "单度价格",
        "是否使用优惠",
        "优惠金额",
        "优惠金额占比",
        "是否谷段偏好订单",
        "充电方式",
        "订单渠道",
        "订单来源",
        "充电站ID",
        "充电桩编号",
        "是否复用上次站点",
        "是否风险订单",
        "是否异常结束订单",
        "是否零电量订单",
        "是否超长充电订单",
        "异常原因类型",
        "是否有效行为订单",
    ]
    header = pd.read_csv(ORDER_BASE_FILE, encoding="utf-8-sig", nrows=0).columns
    active_usecols = [col for col in usecols if col in header]
    user_stats: dict[str, dict[str, object]] = {}
    first_codes: dict[str, str] = {}
    top_columns = [
        "充电粗略时段",
        "充电方式",
        "订单渠道",
        "订单来源",
        "充电站ID",
        "充电桩编号",
        "异常原因类型",
    ]

    bool_fields = [
        ("是否凌晨充电", "凌晨充电次数", "凌晨充电占比"),
        ("是否周末", "周末充电次数", "周末充电占比"),
        ("是否夜间充电", "夜间充电次数", "夜间充电占比"),
        ("是否跨日充电", "跨日充电次数", "跨日充电占比"),
        ("是否短时充电", "短时充电次数", "短时充电占比"),
        ("是否长时充电", "长时充电次数", "长时充电占比"),
        ("是否高电量充电", "高电量充电次数", "高电量充电占比"),
        ("是否使用优惠", "优惠使用次数", "优惠使用率"),
        ("是否谷段偏好订单", "谷段偏好订单次数", "谷段偏好订单占比"),
        ("是否复用上次站点", "同站复用次数", "同站复用率"),
        ("是否风险订单", "风险订单数", "风险订单占比"),
        ("是否异常结束订单", "异常结束次数", "异常结束占比"),
        ("是否零电量订单", "零电量订单次数", "零电量订单占比"),
        ("是否超长充电订单", "超长充电订单次数", "超长充电订单占比"),
    ]
    numeric_fields = [
        ("单次充电量(kWh)", "累计充电量_kWh", "平均单次充电量_kWh"),
        ("单次充电时长(min)", None, "平均充电时长_min"),
        ("平均充电功率(kW)", None, "平均充电功率_kW"),
        ("峰段电量(kWh)", "累计峰段电量_kWh", None),
        ("平段电量(kWh)", "累计平段电量_kWh", None),
        ("谷段电量(kWh)", "累计谷段电量_kWh", None),
        ("谷段电量占比", None, "平均谷段电量占比"),
        ("单次消费金额", "累计消费金额", "平均单次消费金额"),
        ("单度价格", None, "平均单度价格"),
        ("优惠金额", "优惠金额合计", None),
        ("优惠金额占比", None, "平均优惠金额占比"),
        ("距上次充电间隔(h)", None, "平均复充间隔_h"),
    ]

    def ensure_user(user_key: str) -> dict[str, object]:
        if user_key not in user_stats:
            user_stats[user_key] = {
                "订单总数": 0,
                "年份": set(),
                "月份": set(),
                "日期": set(),
                "首次充电时间": pd.NaT,
                "最近充电时间": pd.NaT,
                "bool": Counter(),
                "num_sum": Counter(),
                "num_count": Counter(),
                "top": {col: Counter() for col in top_columns},
            }
        return user_stats[user_key]

    sample_max = pd.NaT
    for df in csv_chunks(ORDER_BASE_FILE, usecols=active_usecols):
        df["用户识别主键"] = df["用户识别主键"].fillna("").astype(str).str.strip()
        df = df[df["是否有效行为订单"].eq("是") & df["用户识别主键"].ne("")].copy()
        if df.empty:
            continue

        df["充电开始时间"] = pd.to_datetime(df["充电开始时间"], errors="coerce")
        df["充电日期"] = pd.to_datetime(df["充电日期"], errors="coerce")
        if df["充电开始时间"].notna().any():
            chunk_max = df["充电开始时间"].max()
            sample_max = chunk_max if pd.isna(sample_max) or chunk_max > sample_max else sample_max

        for col, _, _ in numeric_fields:
            if col in df.columns:
                df[col] = to_num(df[col])

        grouped = df.groupby("用户识别主键", sort=False)
        for user_key, group in grouped:
            stats = ensure_user(str(user_key))
            first_codes.setdefault(str(user_key), clean_text(group["用户编码"].iloc[0]) if "用户编码" in group.columns else "")
            stats["订单总数"] += len(group)
            stats["年份"].update(group["自然年份"].dropna().astype(str).unique())
            stats["月份"].update(group["充电日期"].dt.to_period("M").astype(str).unique())
            stats["日期"].update(group["充电日期"].dt.date.astype(str).unique())
            start_min = group["充电开始时间"].min()
            start_max = group["充电开始时间"].max()
            if pd.notna(start_min):
                stats["首次充电时间"] = start_min if pd.isna(stats["首次充电时间"]) or start_min < stats["首次充电时间"] else stats["首次充电时间"]
            if pd.notna(start_max):
                stats["最近充电时间"] = start_max if pd.isna(stats["最近充电时间"]) or start_max > stats["最近充电时间"] else stats["最近充电时间"]

            for source, _, _ in bool_fields:
                if source in group.columns:
                    stats["bool"][source] += int(group[source].eq("是").sum())

            for source, _, _ in numeric_fields:
                if source not in group.columns:
                    continue
                numeric = group[source].dropna()
                stats["num_sum"][source] += float(numeric.sum())
                stats["num_count"][source] += int(numeric.count())

            for source in top_columns:
                if source not in group.columns:
                    continue
                clean = group[source].fillna("未知").astype(str).str.strip()
                clean = clean[clean.ne("") & clean.ne("未知")]
                stats["top"][source].update(clean.value_counts().to_dict())

    if not user_stats:
        pd.DataFrame().to_csv(USER_FEATURE_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
        return

    rows: list[dict[str, object]] = []
    for user_key, stats in user_stats.items():
        total = int(stats["订单总数"])
        row: dict[str, object] = {
            "用户识别主键": user_key,
            "用户编码": first_codes.get(user_key, ""),
            "订单总数": total,
            "活跃年份数": len(stats["年份"]),
            "活跃月份数": len([x for x in stats["月份"] if x and x != "NaT"]),
            "活跃天数": len([x for x in stats["日期"] if x and x != "NaT"]),
            "首次充电时间": stats["首次充电时间"],
            "最近充电时间": stats["最近充电时间"],
            "距最近充电天数": int((sample_max - stats["最近充电时间"]).days) if pd.notna(sample_max) and pd.notna(stats["最近充电时间"]) else 0,
        }

        for source, count_name, ratio_name in bool_fields:
            count = int(stats["bool"][source])
            row[count_name] = count
            row[ratio_name] = safe_div(count, total)

        for source, sum_name, mean_name in numeric_fields:
            count = int(stats["num_count"][source])
            value_sum = float(stats["num_sum"][source])
            if sum_name:
                row[sum_name] = value_sum
            if mean_name:
                row[mean_name] = safe_div(value_sum, count)

        row["中位单次充电量_kWh"] = row.get("平均单次充电量_kWh", 0)
        row["中位充电时长_min"] = row.get("平均充电时长_min", 0)
        row["中位复充间隔_h"] = row.get("平均复充间隔_h", 0)
        row["复充间隔有效数"] = int(stats["num_count"].get("距上次充电间隔(h)", 0))
        interval_count = int(stats["num_count"].get("距上次充电间隔(h)", 0))
        if pd.notna(sample_max) and pd.notna(stats["首次充电时间"]):
            span_days = max(int((sample_max - stats["首次充电时间"]).days) + 1, 14)
        else:
            span_days = RECENT_WINDOW_DAYS
        row["充电跨度天数"] = span_days
        row["平均充电频率_天"] = safe_div(span_days, total) if total > 0 else pd.NA

        top_map = {
            "充电粗略时段": ("主充电时段", "主充电时段占比", None),
            "充电方式": ("常用充电方式", "常用充电方式占比", "充电方式数量"),
            "订单渠道": ("常用订单渠道", "常用订单渠道占比", "订单渠道数量"),
            "订单来源": ("常用订单来源", None, None),
            "充电站ID": ("主站点ID", "主站点占比", "使用站点数"),
            "充电桩编号": ("常用充电桩编号", "常用充电桩占比", "使用充电桩数"),
            "异常原因类型": ("主要异常原因", None, "异常原因类型数"),
        }
        for source, (value_name, share_name, unique_name) in top_map.items():
            counter = stats["top"][source]
            if counter:
                value, count = counter.most_common(1)[0]
                row[value_name] = value
                if share_name:
                    row[share_name] = safe_div(count, total)
                if unique_name:
                    row[unique_name] = len(counter)
                if source == "充电站ID":
                    station_feats = station_concentration_features(counter)
                    row.update(station_feats)
            else:
                row[value_name] = "无异常" if source == "异常原因类型" else ""
                if share_name:
                    row[share_name] = 0.0
                if unique_name:
                    row[unique_name] = 0
                if source == "充电站ID":
                    row.update(
                        {
                            "主站点占比": 0.0,
                            "前二站点占比": 0.0,
                            "前三站点占比": 0.0,
                            "前二站点最小占比": 0.0,
                            "前三站点最小占比": 0.0,
                        }
                    )
        period_counter = stats["top"].get("鍏呯數绮楃暐鏃舵", Counter())
        if period_counter:
            row.update(time_concentration_features(period_counter))
            period_top = period_counter.most_common(3)
            row["次充电时段"] = period_top[1][0] if len(period_top) > 1 else ""
            row["次充电时段占比"] = safe_div(period_top[1][1], total) if len(period_top) > 1 else 0.0
            row["第三充电时段"] = period_top[2][0] if len(period_top) > 2 else ""
            row["第三充电时段占比"] = safe_div(period_top[2][1], total) if len(period_top) > 2 else 0.0
        else:
            row.update(
                {
                    "主充电时段占比": 0.0,
                    "前二充电时段占比": 0.0,
                    "前三充电时段占比": 0.0,
                    "次充电时段占比": 0.0,
                    "第三充电时段": "",
                    "第三充电时段占比": 0.0,
                    "时段数": 0,
                    "次充电时段": "",
                }
            )
        row["时段偏好类型"] = time_preference_type(row)
        rows.append(row)

    features = pd.DataFrame(rows)

    features = features.reset_index(drop=True).fillna(0)
    for column in features.select_dtypes(include="number").columns:
        features[column] = features[column].round(4)
    features = features.sort_values(["订单总数", "累计消费金额", "累计充电量_kWh"], ascending=[False, False, False])
    features.to_csv(USER_FEATURE_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")

    time_rows: list[dict[str, object]] = []
    for _, row in features.iterrows():
        time_rows.append(
            {
                "用户识别主键": row.get("用户识别主键", ""),
                "用户编码": row.get("用户编码", ""),
                "订单总数": int(row.get("订单总数", 0) or 0),
                "主充电时段": row.get("主充电时段", ""),
                "主充电时段占比": round(float(row.get("主充电时段占比", 0) or 0), 4),
                "次充电时段": row.get("次充电时段", ""),
                "次充电时段占比": round(float(row.get("次充电时段占比", 0) or 0), 4),
                "第三充电时段": row.get("第三充电时段", ""),
                "第三充电时段占比": round(float(row.get("第三充电时段占比", 0) or 0), 4),
                "前二充电时段占比": round(float(row.get("前二充电时段占比", 0) or 0), 4),
                "前三充电时段占比": round(float(row.get("前三充电时段占比", 0) or 0), 4),
                "时段数": int(row.get("时段数", 0) or 0),
                "时段偏好类型": row.get("时段偏好类型", ""),
            }
        )
    time_detail = pd.DataFrame(time_rows)
    if not time_detail.empty:
        time_detail = time_detail.sort_values(["订单总数", "主充电时段占比", "用户识别主键"], ascending=[False, False, True])
    time_detail.to_csv(USER_TIME_DETAIL_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")

    station_rows: list[dict[str, object]] = []
    for user_key, stats in user_stats.items():
        counter = stats["top"].get("充电站ID", Counter())
        total = int(stats["订单总数"])
        if not counter or total <= 0:
            continue
        items = counter.most_common(3)
        row = {
            "用户识别主键": user_key,
            "用户编码": first_codes.get(user_key, ""),
            "订单总数": total,
            "使用站点数": len(counter),
            "前三站点合计占比": safe_div(sum(cnt for _, cnt in items), total),
        }
        for idx in range(1, 4):
            station_id, cnt = items[idx - 1] if idx - 1 < len(items) else ("", 0)
            row[f"第{idx}站点ID"] = station_id
            row[f"第{idx}站点次数"] = int(cnt)
            row[f"第{idx}站点占比"] = safe_div(cnt, total)
        station_rows.append(row)
    station_detail = pd.DataFrame(station_rows)
    if not station_detail.empty:
        station_detail = station_detail.sort_values(["订单总数", "使用站点数", "用户识别主键"], ascending=[False, False, True])
    station_detail.to_csv(STATION_TOP3_DETAIL_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")

    overview = pd.DataFrame(
        [
            {"指标": "用户数", "数值": int(len(features))},
            {"指标": "总有效订单数", "数值": int(features["订单总数"].sum())},
            {"指标": "累计充电量(kWh)", "数值": round(float(features["累计充电量_kWh"].sum()), 4)},
            {"指标": "累计消费金额", "数值": round(float(features["累计消费金额"].sum()), 2)},
            {"指标": "风险订单总数", "数值": int(features["风险订单数"].sum())},
        ]
    )
    overview.to_csv(USER_FEATURE_OVERVIEW, index=False, encoding="utf-8-sig", lineterminator="\n")
    print("用户级多维特征汇总完成")
    print(overview.to_string(index=False))
    print(f"时段偏好明细表已输出: {USER_TIME_DETAIL_FILE}")
    print(f"站点前三明细表已输出: {STATION_TOP3_DETAIL_FILE}")


def step_user_features_duckdb() -> None:
    if not ORDER_BASE_FILE.exists():
        raise FileNotFoundError(f"缺少订单级行为明细，请先运行本流水线：{ORDER_BASE_FILE}")
    try:
        import duckdb
    except ImportError:
        print("未检测到 duckdb，回退到 pandas 分块聚合。")
        step_user_features()
        return

    def qid(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def qstr(value: object) -> str:
        return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"

    path_sql = qstr(ORDER_BASE_FILE)
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA preserve_insertion_order=false")
    con.execute(f"PRAGMA temp_directory={qstr(str(FEATURE_DIR))}")
    yes = qstr("是")
    unknown = qstr("未知")
    no_abnormal = qstr("无异常")

    def bool_sum(col: str) -> str:
        return f"SUM(CASE WHEN {qid(col)} = {yes} THEN 1 ELSE 0 END)"

    def num(col: str) -> str:
        return f"TRY_CAST({qid(col)} AS DOUBLE)"

    def top_cte(cte: str, col: str, value_name: str, share_name: str | None = None, unique_name: str | None = None) -> str:
        select_cols = [
            "用户识别主键",
            f"value AS {qid(value_name)}",
        ]
        if share_name:
            select_cols.append(f"cnt * 1.0 / total AS {qid(share_name)}")
        if unique_name:
            select_cols.append(f"unique_count AS {qid(unique_name)}")
        return f"""
        {cte}_counts AS (
            SELECT
                {qid("用户识别主键")} AS 用户识别主键,
                COALESCE(NULLIF(TRIM({qid(col)}), ''), {unknown}) AS value,
                COUNT(*) AS cnt
            FROM valid
            WHERE COALESCE(NULLIF(TRIM({qid(col)}), ''), {unknown}) <> {unknown}
            GROUP BY 1, 2
        ),
        {cte} AS (
            SELECT {", ".join(select_cols)}
            FROM (
                SELECT
                    用户识别主键,
                    value,
                    cnt,
                    SUM(cnt) OVER (PARTITION BY 用户识别主键) AS total,
                    COUNT(*) OVER (PARTITION BY 用户识别主键) AS unique_count,
                    ROW_NUMBER() OVER (PARTITION BY 用户识别主键 ORDER BY cnt DESC, value) AS rn
                FROM {cte}_counts
            )
            WHERE rn = 1
        )
        """

    top_parts = [
        top_cte("top_period", "充电粗略时段", "主充电时段", "主充电时段占比"),
        top_cte("top_method", "充电方式", "常用充电方式", "常用充电方式占比", "充电方式数量"),
        top_cte("top_channel", "订单渠道", "常用订单渠道", "常用订单渠道占比", "订单渠道数量"),
        top_cte("top_source", "订单来源", "常用订单来源"),
        top_cte("top_station", "充电站ID", "主站点ID", "主站点占比", "使用站点数"),
        top_cte("top_pile", "充电桩编号", "常用充电桩编号", "常用充电桩占比", "使用充电桩数"),
        top_cte("top_reason", "异常原因类型", "主要异常原因", None, "异常原因类型数"),
    ]

    features_sql = f"""
    CREATE OR REPLACE TEMP TABLE valid AS
    SELECT *
    FROM read_csv({path_sql}, header=true, all_varchar=true, ignore_errors=true, union_by_name=true)
    WHERE COALESCE(NULLIF(TRIM({qid("用户识别主键")}), ''), '') <> ''
      AND {qid("是否有效行为订单")} = {yes};

    CREATE OR REPLACE TEMP TABLE typed AS
    SELECT
        {qid("用户识别主键")} AS {qid("用户识别主键")},
        ANY_VALUE({qid("用户编码")}) AS {qid("用户编码")},
        COUNT(*) AS {qid("订单总数")},
        COUNT(DISTINCT NULLIF({qid("自然年份")}, '')) AS {qid("活跃年份数")},
        COUNT(DISTINCT STRFTIME(TRY_CAST({qid("充电日期")} AS DATE), '%Y-%m')) AS {qid("活跃月份数")},
        COUNT(DISTINCT TRY_CAST({qid("充电日期")} AS DATE)) AS {qid("活跃天数")},
        MIN(TRY_CAST({qid("充电开始时间")} AS TIMESTAMP)) AS {qid("首次充电时间")},
        MAX(TRY_CAST({qid("充电开始时间")} AS TIMESTAMP)) AS {qid("最近充电时间")},
        {bool_sum("是否凌晨充电")} AS {qid("凌晨充电次数")},
        {bool_sum("是否周末")} AS {qid("周末充电次数")},
        {bool_sum("是否夜间充电")} AS {qid("夜间充电次数")},
        {bool_sum("是否跨日充电")} AS {qid("跨日充电次数")},
        {bool_sum("是否短时充电")} AS {qid("短时充电次数")},
        {bool_sum("是否长时充电")} AS {qid("长时充电次数")},
        {bool_sum("是否高电量充电")} AS {qid("高电量充电次数")},
        {bool_sum("是否使用优惠")} AS {qid("优惠使用次数")},
        {bool_sum("是否谷段偏好订单")} AS {qid("谷段偏好订单次数")},
        {bool_sum("是否复用上次站点")} AS {qid("同站复用次数")},
        {bool_sum("是否风险订单")} AS {qid("风险订单数")},
        {bool_sum("是否异常结束订单")} AS {qid("异常结束次数")},
        {bool_sum("是否零电量订单")} AS {qid("零电量订单次数")},
        {bool_sum("是否超长充电订单")} AS {qid("超长充电订单次数")},
        SUM({num("单次充电量(kWh)")}) AS {qid("累计充电量_kWh")},
        AVG({num("单次充电量(kWh)")}) AS {qid("平均单次充电量_kWh")},
        MEDIAN({num("单次充电量(kWh)")}) AS {qid("中位单次充电量_kWh")},
        AVG({num("单次充电时长(min)")}) AS {qid("平均充电时长_min")},
        MEDIAN({num("单次充电时长(min)")}) AS {qid("中位充电时长_min")},
        AVG({num("平均充电功率(kW)")}) AS {qid("平均充电功率_kW")},
        SUM({num("峰段电量(kWh)")}) AS {qid("累计峰段电量_kWh")},
        SUM({num("平段电量(kWh)")}) AS {qid("累计平段电量_kWh")},
        SUM({num("谷段电量(kWh)")}) AS {qid("累计谷段电量_kWh")},
        AVG({num("谷段电量占比")}) AS {qid("平均谷段电量占比")},
        SUM({num("单次消费金额")}) AS {qid("累计消费金额")},
        AVG({num("单次消费金额")}) AS {qid("平均单次消费金额")},
        AVG({num("单度价格")}) AS {qid("平均单度价格")},
        SUM({num("优惠金额")}) AS {qid("优惠金额合计")},
        AVG({num("优惠金额占比")}) AS {qid("平均优惠金额占比")},
        AVG({num("距上次充电间隔(h)")}) AS {qid("平均复充间隔_h")},
        MEDIAN({num("距上次充电间隔(h)")}) AS {qid("中位复充间隔_h")},
        COUNT({num("距上次充电间隔(h)")}) AS {qid("复充间隔有效数")}
    FROM valid
    GROUP BY {qid("用户识别主键")};

    WITH
    {",".join(top_parts)},
    sample AS (
        SELECT MAX(TRY_CAST({qid("充电开始时间")} AS TIMESTAMP)) AS sample_max
        FROM read_csv({path_sql}, header=true, all_varchar=true, ignore_errors=true, union_by_name=true)
    )
    SELECT
        typed.*,
        DATE_DIFF('day', CAST({qid("最近充电时间")} AS DATE), CAST(sample.sample_max AS DATE)) AS {qid("距最近充电天数")},
        {qid("凌晨充电次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("凌晨充电占比")},
        {qid("周末充电次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("周末充电占比")},
        {qid("夜间充电次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("夜间充电占比")},
        {qid("跨日充电次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("跨日充电占比")},
        {qid("短时充电次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("短时充电占比")},
        {qid("长时充电次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("长时充电占比")},
        {qid("高电量充电次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("高电量充电占比")},
        {qid("优惠使用次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("优惠使用率")},
        {qid("谷段偏好订单次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("谷段偏好订单占比")},
        {qid("同站复用次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("同站复用率")},
        {qid("风险订单数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("风险订单占比")},
        {qid("异常结束次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("异常结束占比")},
        {qid("零电量订单次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("零电量订单占比")},
        {qid("超长充电订单次数")} * 1.0 / NULLIF({qid("订单总数")}, 0) AS {qid("超长充电订单占比")},
        COALESCE(top_period.{qid("主充电时段")}, '') AS {qid("主充电时段")},
        COALESCE(top_period.{qid("主充电时段占比")}, 0) AS {qid("主充电时段占比")},
        COALESCE(top_method.{qid("常用充电方式")}, '') AS {qid("常用充电方式")},
        COALESCE(top_method.{qid("常用充电方式占比")}, 0) AS {qid("常用充电方式占比")},
        COALESCE(top_method.{qid("充电方式数量")}, 0) AS {qid("充电方式数量")},
        COALESCE(top_channel.{qid("常用订单渠道")}, '') AS {qid("常用订单渠道")},
        COALESCE(top_channel.{qid("常用订单渠道占比")}, 0) AS {qid("常用订单渠道占比")},
        COALESCE(top_channel.{qid("订单渠道数量")}, 0) AS {qid("订单渠道数量")},
        COALESCE(top_source.{qid("常用订单来源")}, '') AS {qid("常用订单来源")},
        COALESCE(top_station.{qid("主站点ID")}, '') AS {qid("主站点ID")},
        COALESCE(top_station.{qid("主站点占比")}, 0) AS {qid("主站点占比")},
        COALESCE(top_station.{qid("使用站点数")}, 0) AS {qid("使用站点数")},
        COALESCE(top_pile.{qid("常用充电桩编号")}, '') AS {qid("常用充电桩编号")},
        COALESCE(top_pile.{qid("常用充电桩占比")}, 0) AS {qid("常用充电桩占比")},
        COALESCE(top_pile.{qid("使用充电桩数")}, 0) AS {qid("使用充电桩数")},
        COALESCE(top_reason.{qid("主要异常原因")}, {no_abnormal}) AS {qid("主要异常原因")},
        COALESCE(top_reason.{qid("异常原因类型数")}, 0) AS {qid("异常原因类型数")},
        GREATEST(DATE_DIFF('day', CAST({qid("首次充电时间")} AS DATE), CAST(sample.sample_max AS DATE)) + 1, 14) AS {qid("充电跨度天数")},
        GREATEST(DATE_DIFF('day', CAST({qid("首次充电时间")} AS DATE), CAST(sample.sample_max AS DATE)) + 1, 14)::DOUBLE
            / NULLIF({qid("订单总数")}, 0) AS {qid("平均充电频率_天")}
    FROM typed
    CROSS JOIN sample
    LEFT JOIN top_period USING ({qid("用户识别主键")})
    LEFT JOIN top_method USING ({qid("用户识别主键")})
    LEFT JOIN top_channel USING ({qid("用户识别主键")})
    LEFT JOIN top_source USING ({qid("用户识别主键")})
    LEFT JOIN top_station USING ({qid("用户识别主键")})
    LEFT JOIN top_pile USING ({qid("用户识别主键")})
    LEFT JOIN top_reason USING ({qid("用户识别主键")})
    ORDER BY {qid("订单总数")} DESC, {qid("累计消费金额")} DESC, {qid("累计充电量_kWh")} DESC;
    """

    features = con.execute(features_sql).fetchdf()
    for column in features.select_dtypes(include="number").columns:
        features[column] = features[column].fillna(0).round(4)
    features.to_csv(USER_FEATURE_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    overview = pd.DataFrame(
        [
            {"指标": "用户数", "数值": int(len(features))},
            {"指标": "总有效订单数", "数值": int(features["订单总数"].sum()) if "订单总数" in features.columns else 0},
            {"指标": "累计充电量(kWh)", "数值": round(float(features["累计充电量_kWh"].sum()), 4) if "累计充电量_kWh" in features.columns else 0},
            {"指标": "累计消费金额", "数值": round(float(features["累计消费金额"].sum()), 2) if "累计消费金额" in features.columns else 0},
            {"指标": "风险订单总数", "数值": int(features["风险订单数"].sum()) if "风险订单数" in features.columns else 0},
        ]
    )
    overview.to_csv(USER_FEATURE_OVERVIEW, index=False, encoding="utf-8-sig", lineterminator="\n")
    print("用户级多维特征汇总完成（DuckDB 快速聚合）")
    print(overview.to_string(index=False))

    time_sql = f"""
    WITH period_counts AS (
        SELECT
            {qid("用户识别主键")} AS user_id,
            CASE
                WHEN TRY_CAST({qid("充电开始时间")} AS TIMESTAMP) IS NULL THEN {unknown}
                ELSE LPAD(CAST(EXTRACT(HOUR FROM TRY_CAST({qid("充电开始时间")} AS TIMESTAMP)) AS VARCHAR), 2, '0')
                     || ':00-' ||
                     LPAD(CAST(EXTRACT(HOUR FROM TRY_CAST({qid("充电开始时间")} AS TIMESTAMP)) AS VARCHAR), 2, '0')
                     || ':59'
            END AS period,
            COUNT(*) AS cnt
        FROM valid
        GROUP BY 1, 2
    ),
    ranked AS (
        SELECT
            user_id,
            period,
            cnt,
            SUM(cnt) OVER (PARTITION BY user_id) AS total_orders,
            COUNT(*) OVER (PARTITION BY user_id) AS period_total,
            ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY cnt DESC, period) AS rn
        FROM period_counts
    )
    SELECT
        user_id AS {qid("用户识别主键")},
         MAX(CASE WHEN rn = 2 THEN period END) AS {qid("次充电时段")},
         ROUND(MAX(CASE WHEN rn = 2 THEN cnt END) * 1.0 / NULLIF(MAX(total_orders), 0), 4) AS {qid("次充电时段占比")},
         MAX(CASE WHEN rn = 3 THEN period END) AS {qid("第三充电时段")},
         ROUND(MAX(CASE WHEN rn = 3 THEN cnt END) * 1.0 / NULLIF(MAX(total_orders), 0), 4) AS {qid("第三充电时段占比")},
         ROUND(SUM(CASE WHEN rn <= 2 THEN cnt ELSE 0 END) * 1.0 / NULLIF(MAX(total_orders), 0), 4) AS {qid("前二充电时段占比")},
         ROUND(SUM(CASE WHEN rn <= 3 THEN cnt ELSE 0 END) * 1.0 / NULLIF(MAX(total_orders), 0), 4) AS {qid("前三充电时段占比")},
        MAX(period_total) AS {qid("时段数")}
    FROM ranked
    GROUP BY user_id
    ORDER BY {qid("时段数")} DESC, {qid("用户识别主键")};
    """
    time_detail = con.execute(time_sql).fetchdf()
    for column in time_detail.select_dtypes(include="number").columns:
        time_detail[column] = time_detail[column].fillna(0).round(4)
    features = features.merge(time_detail, on="用户识别主键", how="left")
    for col, default in [
        ("主充电时段", ""),
        ("次充电时段", ""),
        ("主充电时段占比", 0.0),
        ("次充电时段占比", 0.0),
        ("第三充电时段", ""),
        ("第三充电时段占比", 0.0),
        ("前二充电时段占比", 0.0),
        ("前三充电时段占比", 0.0),
        ("时段数", 0),
    ]:
        if col not in features.columns:
            features[col] = default
    features["时段偏好类型"] = features.apply(time_preference_type, axis=1)
    for column in features.select_dtypes(include="number").columns:
        features[column] = features[column].fillna(0).round(4)
    features.to_csv(USER_FEATURE_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    print("用户级多维特征表已补写时段偏好字段")

    time_detail_out = features[
        [
            col
            for col in [
                "用户识别主键",
                "用户编码",
                "订单总数",
                "主充电时段",
                "主充电时段占比",
                "次充电时段",
                "次充电时段占比",
                "第三充电时段",
                "第三充电时段占比",
                "前二充电时段占比",
                "前三充电时段占比",
                "时段数",
                "时段偏好类型",
            ]
            if col in features.columns
        ]
    ].copy()
    if not time_detail_out.empty:
        time_detail_out = time_detail_out.sort_values(["订单总数", "主充电时段占比", "用户识别主键"], ascending=[False, False, True])
    time_detail_out.to_csv(USER_TIME_DETAIL_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    print(f"时段偏好明细表已输出: {USER_TIME_DETAIL_FILE}")

    station_sql = f"""
    WITH station_counts AS (
        SELECT
            {qid("用户识别主键")} AS user_id,
            ANY_VALUE({qid("用户编码")}) AS user_code,
            COALESCE(NULLIF(TRIM({qid("充电站ID")}), ''), {unknown}) AS station_id,
            COUNT(*) AS cnt
        FROM valid
        WHERE COALESCE(NULLIF(TRIM({qid("充电站ID")}), ''), {unknown}) <> {unknown}
        GROUP BY 1, 3
    ),
    ranked AS (
        SELECT
            user_id,
            user_code,
            station_id,
            cnt,
            SUM(cnt) OVER (PARTITION BY user_id) AS total_orders,
            COUNT(*) OVER (PARTITION BY user_id) AS station_total,
            ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY cnt DESC, station_id) AS rn
        FROM station_counts
    )
    SELECT
        user_id AS {qid("用户识别主键")},
        MAX(user_code) AS {qid("用户编码")},
        MAX(total_orders) AS {qid("订单总数")},
        MAX(station_total) AS {qid("使用站点数")},
        ROUND(SUM(CASE WHEN rn <= 3 THEN cnt ELSE 0 END) * 1.0 / NULLIF(MAX(total_orders), 0), 4) AS {qid("前三站点合计占比")},
        MAX(CASE WHEN rn = 1 THEN station_id END) AS {qid("第1站点ID")},
        MAX(CASE WHEN rn = 1 THEN cnt END) AS {qid("第1站点次数")},
        ROUND(MAX(CASE WHEN rn = 1 THEN cnt END) * 1.0 / NULLIF(MAX(total_orders), 0), 4) AS {qid("第1站点占比")},
        MAX(CASE WHEN rn = 2 THEN station_id END) AS {qid("第2站点ID")},
        MAX(CASE WHEN rn = 2 THEN cnt END) AS {qid("第2站点次数")},
        ROUND(MAX(CASE WHEN rn = 2 THEN cnt END) * 1.0 / NULLIF(MAX(total_orders), 0), 4) AS {qid("第2站点占比")},
        MAX(CASE WHEN rn = 3 THEN station_id END) AS {qid("第3站点ID")},
        MAX(CASE WHEN rn = 3 THEN cnt END) AS {qid("第3站点次数")},
        ROUND(MAX(CASE WHEN rn = 3 THEN cnt END) * 1.0 / NULLIF(MAX(total_orders), 0), 4) AS {qid("第3站点占比")}
    FROM ranked
    GROUP BY user_id
    ORDER BY {qid("订单总数")} DESC, {qid("使用站点数")} DESC, {qid("用户识别主键")};
    """
    station_detail = con.execute(station_sql).fetchdf()
    for column in station_detail.select_dtypes(include="number").columns:
        station_detail[column] = station_detail[column].fillna(0).round(4)
    station_detail.to_csv(STATION_TOP3_DETAIL_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    print(f"站点前三明细表已输出: {STATION_TOP3_DETAIL_FILE}")


def r_level(days: float) -> tuple[str, int]:
    silent_risk_limit = 90 if RECENT_WINDOW_DAYS >= 180 else 60
    if days <= 7:
        return "活跃", 1
    if days <= 14:
        return "近期活跃", 2
    if days <= 30:
        return "一般活跃", 3
    if days <= silent_risk_limit:
        return "沉默风险", 4
    return "沉默", 5


def f_score_frequency(value: float) -> int:
    """按平均充电频率（90天 / 订单数）做固定阈值评分，数值越小代表频率越高。"""
    if pd.isna(value):
        return 5
    if value <= 4:
        return 1
    if value <= 7:
        return 2
    if value <= 14:
        return 3
    if value <= 20:
        return 4
    return 5


def f_level(score: int) -> str:
    return {1: "高频", 2: "中高频", 3: "中频", 4: "低频", 5: "极低频"}[score]


def m_score_single_energy(kwh: float) -> int:
    """按平均单次交易电量评分，单位为 kWh。"""
    if kwh >= 40:
        return 5
    if kwh >= 30:
        return 4
    if kwh >= 20:
        return 3
    if kwh >= 10:
        return 2
    return 1


def m_level(score: int) -> str:
    return {5: "高价值", 4: "中高价值", 3: "中价值", 2: "低价值", 1: "极低价值"}[score]


def rfm_type(r: int, f: int, m: int) -> str:
    if r <= 2 and f <= 2 and m >= 4:
        return "高价值活跃用户"
    if r >= 4 and f <= 2 and m >= 4:
        return "高价值沉默风险用户"
    if r <= 2 and f >= 4:
        return "新近低频用户"
    if f <= 2 and m < 3:
        return "高频低价值用户"
    if f >= 4 and m <= 2:
        return "低频低价值用户"
    return "一般用户"


def station_preference_type(row: pd.Series) -> str:
    order_count = float(row.get("订单总数", 0) or 0)
    top1 = float(row.get("主站点占比", 0) or 0)
    top2 = float(row.get("前二站点占比", 0) or 0)
    top3 = float(row.get("前三站点占比", 0) or 0)
    top2_min = float(row.get("前二站点最小占比", 0) or 0)
    top3_min = float(row.get("前三站点最小占比", 0) or 0)
    station_count = float(row.get("使用站点数", 0) or 0)

    # 有效订单数不超过 5 次时单独标记为样本不足，不参与站点偏好判定。
    if order_count <= 5:
        return "样本不足"

    if top1 >= 0.5:
        return "固定站点用户"
    if top2 >= 0.6 and top2_min >= 0.2:
        return "双站点用户"
    if top3 >= 0.7 and top3_min >= 0.1:
        return "三站点用户"
    if station_count >= 5:
        return "多站流动用户"
    return "一般站点用户"


def station_concentration_features(counter: Counter) -> dict[str, float]:
    total = sum(counter.values())
    top = counter.most_common(3)
    counts = [count for _, count in top] + [0, 0, 0]
    top1 = counts[0]
    top2 = counts[0] + counts[1]
    top3 = counts[0] + counts[1] + counts[2]
    return {
        "主站点占比": safe_div(top1, total),
        "前二站点占比": safe_div(top2, total),
        "前三站点占比": safe_div(top3, total),
        "前二站点最小占比": safe_div(min(counts[0], counts[1]), total),
        "前三站点最小占比": safe_div(min(counts[0], counts[1], counts[2]), total),
    }


def time_concentration_features(counter: Counter) -> dict[str, float]:
    total = sum(counter.values())
    top = counter.most_common(3)
    counts = [count for _, count in top] + [0, 0, 0]
    return {
        "主充电时段占比": safe_div(counts[0], total),
        "前二充电时段占比": safe_div(counts[0] + counts[1], total),
        "次充电时段占比": safe_div(counts[1], total),
        "前三充电时段占比": safe_div(counts[0] + counts[1] + counts[2], total),
        "第三充电时段占比": safe_div(counts[2], total),
        "时段数": len(counter),
    }


def time_preference_type(row: pd.Series) -> str:
    order_count = float(row.get("订单总数", 0) or 0)
    if order_count <= 5:
        return "样本不足"
    top1 = float(row.get("主充电时段占比", 0) or 0)
    top2 = float(row.get("前二充电时段占比", 0) or 0)
    second = float(row.get("次充电时段占比", 0) or 0)
    top3 = float(row.get("前三充电时段占比", 0) or 0)
    third = float(row.get("第三充电时段占比", 0) or 0)
    if top1 >= 0.5:
        return "单时段偏好"
    if top2 >= 0.6 and second >= 0.15:
        return "双时段偏好"
    if top3 >= 0.7 and third >= 0.1:
        return "三时段偏好"
    if top1 >= 0.3:
        return "轻度时段偏好"
    return "无明显偏好"


def step_rfm_segments() -> None:
    df = pd.read_csv(USER_FEATURE_FILE, encoding="utf-8-sig", low_memory=False)
    for col in ["订单总数", "距最近充电天数", "累计消费金额", "累计充电量_kWh", "平均单次充电量_kWh", "平均充电频率_天", "复充间隔有效数", "优惠使用率", "平均谷段电量占比", "主站点占比", "前二站点占比", "前三站点占比", "前二站点最小占比", "前三站点最小占比", "同站复用率", "使用站点数", "风险订单占比", "异常结束占比", "零电量订单占比", "超长充电订单占比", "充电跨度天数"]:
        if col in df.columns:
            df[col] = to_num(df[col]).fillna(0)
    r_pairs = df["距最近充电天数"].map(r_level)
    df["R等级"] = r_pairs.map(lambda x: x[0])
    df["R分数"] = r_pairs.map(lambda x: x[1])
    df["F分数"] = df["平均充电频率_天"].map(lambda x: f_score_frequency(float(x)))
    df["M分数"] = df["平均单次充电量_kWh"].map(lambda x: m_score_single_energy(float(x)))
    df["F等级"] = df["F分数"].map(f_level)
    df["M等级"] = df["M分数"].map(m_level)
    df["RFM总分"] = df["R分数"] + df["F分数"] + df["M分数"]
    df["RFM均分"] = (df["RFM总分"] / 3).round(3)
    df["RFM类型"] = [rfm_type(r, f, m) for r, f, m in zip(df["R分数"], df["F分数"], df["M分数"])]
    df["价格敏感等级"] = df.apply(lambda row: "高价格敏感" if row["优惠使用率"] >= 0.5 or row["平均谷段电量占比"] >= 0.5 else "中价格敏感" if row["优惠使用率"] >= 0.2 or row["平均谷段电量占比"] >= 0.25 else "低价格敏感", axis=1)
    df["站点偏好类型"] = df.apply(station_preference_type, axis=1)
    df["异常风险等级"] = df.apply(
        lambda row: "高风险"
        if row["风险订单占比"] >= 0.2
        or row["异常结束占比"] >= 0.1
        or row["超长充电订单占比"] >= 0.2
        else "中风险"
        if row["风险订单占比"] >= 0.05
        or row["异常结束占比"] >= 0.03
        or row["超长充电订单占比"] >= 0.05
        else "低风险",
        axis=1,
    )
    df["用户标签"] = df.apply(build_labels, axis=1)
    df = df.sort_values(["RFM总分", "累计充电量_kWh", "订单总数"], ascending=[True, False, False])
    df.to_csv(RFM_SEGMENT_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    silent_risk_limit = 90 if RECENT_WINDOW_DAYS >= 180 else 60
    print(f"R 等级固定阈值（距样本末次有效充电天数，窗口 {RECENT_WINDOW_DAYS} 天）: <= 7 得 1 / <= 14 得 2 / <= 30 得 3 / <= {silent_risk_limit} 得 4 / > {silent_risk_limit} 得 5")
    print("F 等级固定阈值（平均充电频率_天 = 用户首次充电至样本末次充电的动态天数 / 订单数）: <= 4 得 1 / <= 7 得 2 / <= 14 得 3 / <= 20 得 4 / > 20 得 5")
    print("M 等级固定阈值（平均单次充电量_kWh）: < 10 得 1 / 10-19 得 2 / 20-29 得 3 / 30-39 得 4 / >= 40 得 5")
    overview_rows = []
    for field in ["R等级", "F等级", "M等级", "RFM类型", "价格敏感等级", "站点偏好类型", "异常风险等级"]:
        for value, count in df[field].value_counts().items():
            overview_rows.append({"统计维度": field, "类别": value, "用户数": int(count), "占比": round(safe_div(count, len(df)), 4)})
    pd.DataFrame(overview_rows).to_csv(RFM_OVERVIEW_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    print("分箱统计与 RFM 价值分析完成")
    print(pd.DataFrame(overview_rows).head(30).to_string(index=False))


def build_labels(row: pd.Series) -> str:
    labels: list[str] = []
    if row["RFM类型"] in {"高价值活跃用户", "高价值沉默风险用户"}:
        labels.append(row["RFM类型"])
    if row["F等级"] in {"高频", "中高频"}:
        labels.append("高频用户")
    if row["R等级"] in {"沉默风险", "沉默"}:
        labels.append("沉默风险用户")
    if row["价格敏感等级"] == "高价格敏感":
        labels.append("价格敏感用户")
    if row["站点偏好类型"] == "固定站点用户":
        labels.append("固定站点用户")
    if row["异常风险等级"] == "高风险":
        labels.append("异常风险用户")
    return "；".join(dict.fromkeys(labels or ["一般用户"]))


def step_final_profiles() -> None:
    features = pd.read_csv(USER_FEATURE_FILE, encoding="utf-8-sig", low_memory=False)
    segments = pd.read_csv(RFM_SEGMENT_FILE, encoding="utf-8-sig", low_memory=False)
    keep = ["用户识别主键", "R等级", "R分数", "F分数", "M分数", "F等级", "M等级", "RFM总分", "RFM均分", "RFM类型", "价格敏感等级", "站点偏好类型", "异常风险等级", "用户标签"]
    profile = features.merge(segments[[col for col in keep if col in segments.columns]], on="用户识别主键", how="left")
    profile["用户行为模式"] = profile.apply(lambda row: f"活跃度:{row.get('R等级', '')}；频次:{row.get('F等级', '')}；价值:{row.get('M等级', '')}；价格:{row.get('价格敏感等级', '')}；站点:{row.get('站点偏好类型', '')}；时段:{row.get('时段偏好类型', '')}；风险:{row.get('异常风险等级', '')}", axis=1)
    profile.to_csv(FINAL_PROFILE_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    label_cols = [col for col in ["用户识别主键", "用户编码", "订单总数", "活跃月份数", "最近充电时间", "距最近充电天数", "主充电时段", "主充电时段占比", "次充电时段", "次充电时段占比", "第三充电时段", "第三充电时段占比", "前二充电时段占比", "前三充电时段占比", "时段偏好类型", "累计充电量_kWh", "累计消费金额", "优惠使用率", "使用站点数", "R等级", "F等级", "M等级", "RFM总分", "RFM类型", "价格敏感等级", "站点偏好类型", "异常风险等级", "用户标签", "用户行为模式"] if col in profile.columns]
    labels = profile[label_cols].copy()
    labels.to_csv(FINAL_LABEL_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    overview = pd.DataFrame(
        [
            {"指标": "用户数", "数值": int(len(profile))},
            {"指标": "高风险用户数", "数值": int((profile["异常风险等级"] == "高风险").sum())},
            {"指标": "高价格敏感用户数", "数值": int((profile["价格敏感等级"] == "高价格敏感").sum())},
            {"指标": "固定站点用户数", "数值": int((profile["站点偏好类型"] == "固定站点用户").sum())},
        ]
    )
    overview.to_csv(FINAL_OVERVIEW_FILE, index=False, encoding="utf-8-sig", lineterminator="\n")
    print("最终用户行为特征表与标签结果输出完成")
    print(overview.to_string(index=False))


def run_step(index: int, total: int, title: str, func: Callable[[], None]) -> None:
    started = time.perf_counter()
    print("")
    print(f"[{index}/{total}] {title}")
    print("-" * 72)
    func()
    print(f"[{index}/{total}] {title} 完成，用时 {time.perf_counter() - started:.1f}s")


def main() -> None:
    global CUSTOM_CLEANED_FILE
    global READ_CHUNKSIZE
    global RECENT_WINDOW_DAYS

    args = parse_args()
    READ_CHUNKSIZE = max(50_000, int(args.chunksize))
    RECENT_WINDOW_DAYS = max(1, int(args.recent_window_days))
    if args.cleaned_file:
        CUSTOM_CLEANED_FILE = args.cleaned_file.expanduser().resolve()
        if not CUSTOM_CLEANED_FILE.exists():
            raise FileNotFoundError(f"cleaned file not found: {CUSTOM_CLEANED_FILE}")
        validate_cleaned_snapshot(CUSTOM_CLEANED_FILE)
        label = args.label or CUSTOM_CLEANED_FILE.stem
        years = [label]
        configure_custom_output_paths(label)
    else:
        years = sorted(dict.fromkeys(args.years))
        configure_output_paths(years)
    steps: list[tuple[str, Callable[[], None]]] = [
        ("字段梳理与指标口径定义", lambda: step_field_scope(years)),
        ("订单级基础特征提取", lambda: step_order_base_duckdb(years)),
        ("用户级多维特征汇总", step_user_features_duckdb),
        ("分箱统计与 RFM 价值分析", step_rfm_segments),
        ("最终用户行为特征表与标签结果输出", step_final_profiles),
    ]
    FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    print("用户行为特征构建一体化流水线启动")
    print(f"分析年份: {'、'.join(map(str, years))}")
    print(f"输出目录: {FEATURE_DIR}")
    for index, (title, func) in enumerate(steps, start=1):
        run_step(index, len(steps), title, func)
    print("")
    print("用户行为特征构建一体化流水线完成")
    print(f"总用时: {time.perf_counter() - started:.1f}s")
    print("最终重点产物:")
    for path in [FINAL_PROFILE_FILE, FINAL_LABEL_FILE, RFM_SEGMENT_FILE, FINAL_OVERVIEW_FILE]:
        print(f"- {path}")


if __name__ == "__main__":
    main()
