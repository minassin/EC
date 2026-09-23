r"""年度订单数据清洗与用户行为特征生成脚本。

输入：
    data/XX年.xlsx

输出：
    outputs/cleaned/orders_YYYY_standard_user_orders.csv
    outputs/abnormal/orders_YYYY_abnormal_orders.csv
    outputs/logs/orders_YYYY_cleaning_log.xlsx
    outputs/analysis/cleaning_summary.md

运行方式：
    .\.venv\Scripts\python.exe scripts\cleaning\clean_2026_orders.py --dataset 26年
    .\.venv\Scripts\python.exe scripts\cleaning\clean_2026_orders.py --list-datasets
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import math
import os
from pathlib import Path
import re
import sys
import warnings
from typing import Iterable
from typing import Callable

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = Path(os.environ.get("EC_OUTPUT_ROOT", PROJECT_ROOT / "outputs"))
ASSET_STATION_PATH = DATA_DIR / "资产表.xlsx"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

warnings.filterwarnings(
    "ignore",
    message="Workbook contains no default style*",
    category=UserWarning,
    module="openpyxl.styles.stylesheet",
)

from reporting_utils import CLEANING_SUMMARY_MD, path_for_markdown, update_summary_section

PAID_STATUSES = {"支付完成", "异常已支付"}
HIGH_FREQUENCY_ANNUAL_ORDER_THRESHOLD = 365
SHORT_RESTART_GAP_MINUTES = 10
WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
STATION_CATEGORY_ORDER = ["高速", "公共场站", "专用场站", "公交场站", "其他"]
STATION_CATEGORY_PRIORITY = ["高速", "公交场站", "专用场站", "公共场站", "其他"]
TIME_CUTOFF_END = pd.Timestamp.now()
STATION_TYPE_MERGE_MAP = {
    "单位内部": "单位内部（专用）",
    "单位内部(专用)": "单位内部（专用）",
    "岸电": "其他",
}
EXTRA_FIELD_MAPPING_60: dict[str, str] = {
    "交易流水号": "order_id",
    "第三方交易流水号": "third_party_order_id",
    "充电方式": "charge_method",
    "订单状态": "status",
    "订单渠道": "channel",
    "订单来源": "source",
    "业务类型": "business_type",
    "交易电量（kwh）": "kwh",
    "交易电量(kwh)": "kwh",
    "电费": "EF",
    "服务费": "SF",
    "交易金额": "amount",
    "实扣金额": "A_AMOUNT",
    "A_amount": "A_AMOUNT",
    "A_AMOUNT": "A_AMOUNT",
    "A_EF": "A_EF",
    "A_SF": "A_SF",
    "实扣电费": "A_EF",
    "实扣服务费": "A_SF",
    "优惠金额": "coupon_discount",
    "优惠电费": "coupon_electric_discount",
    "优惠服务费": "coupon_service_discount",
    "优惠券优惠金额": "coupon_discount",
    "立减优惠金额": "promotion_discount",
    "其他优惠金额详情": "other_discount",
    "订单创建时间": "create_time",
    "订单支付时间": "pay_time",
    "充电开始时间": "start_time",
    "充电结束时间": "end_time",
    "订单上送时间": "upload_time",
    "交易结束原因": "end_reason",
    "是否后付费": "is_postpaid",
    "用户类型": "user_type",
    "充电桩编号": "charger_id",
    "充电站ID": "station_id",
    "充电站": "station_name",
    "充电站名称": "station_name",
    "尖电量（kwh）": "peak_kwh",
    "峰电量（kwh）": "peak_kwh",
    "平电量（kwh）": "flat_kwh",
    "谷电量（kwh）": "valley_kwh",
    "深谷电量（kwh）": "deep_valley_kwh",
    "低谷电量（kwh）": "low_valley_kwh",
    "抄表电量（kwh）": "meter_kwh",
    "用户编码": "user_id",
    "VIN码": "vin",
    "车牌号": "plate_no",
    "清分状态": "settlement_status",
    "清分ID": "settlement_id",
    "清分时间": "settlement_time",
    "开票状态": "invoice_status",
    "发票序列标识": "invoice_id",
    "充电枪编号": "gun_id",
    "账款额度ID": "account_quota_id",
}


def list_year_datasets() -> list[Path]:
    """列出 data 目录下形如 XX年.xlsx 的年度订单数据。"""
    files = [path for path in DATA_DIR.glob("*年.xlsx") if re.fullmatch(r"\d{2}年\.xlsx", path.name)]
    return sorted(files, key=lambda path: int(path.stem.replace("年", "")))


def resolve_year_dataset(dataset: str) -> tuple[Path, str, str, str]:
    """把 26、26年、26年.xlsx、2026 等写法统一成输入文件和输出标签。"""
    text = str(dataset).strip().strip('"').strip("'")
    name = Path(text).name
    if name.lower().endswith(".xlsx"):
        name = name[:-5]
    if name.endswith("年"):
        name = name[:-1]
    if not name.isdigit() or len(name) not in {2, 4}:
        raise SystemExit("数据集名称请使用类似 26、26年、26年.xlsx 或 2026 的格式。")

    year_full = int(name) if len(name) == 4 else 2000 + int(name)
    short_year = f"{year_full % 100:02d}"
    dataset_path = DATA_DIR / f"{short_year}年.xlsx"
    if not dataset_path.exists():
        available = "、".join(path.name for path in list_year_datasets()) or "无"
        raise SystemExit(f"未找到输入文件：{dataset_path}\n当前可用数据集：{available}")
    return dataset_path, f"{short_year}年", str(year_full), f"orders_{year_full}"


def build_output_paths(output_name: str) -> dict[str, Path]:
    prefix = output_name if str(output_name).startswith("orders_") else f"orders_{output_name}"
    return {
        "cleaned": OUTPUT_DIR / "cleaned" / f"{prefix}_standard_user_orders.csv",
        "abnormal": OUTPUT_DIR / "abnormal" / f"{prefix}_abnormal_orders.csv",
        "discard": OUTPUT_DIR / "discard" / f"{prefix}_discard_orders.csv",
        "log": OUTPUT_DIR / "logs" / f"{prefix}_cleaning_log.xlsx",
        "station_category_chart": OUTPUT_DIR / "analysis" / "images" / "cleaning" / f"{prefix}_station_category_distribution.svg",
        "high_frequency_compare_chart": OUTPUT_DIR / "analysis" / "images" / "cleaning" / f"{prefix}_high_frequency_exclusion_comparison.svg",
        "report": CLEANING_SUMMARY_MD,
    }


def resolve_input_file(file_arg: str) -> tuple[Path, str, str, str]:
    """解析单文件输入，支持绝对路径、相对路径和 data 目录下文件名。"""
    text = str(file_arg).strip().strip('"').strip("'")
    path = Path(text)
    candidates = [path]
    if not path.is_absolute():
        candidates.extend([DATA_DIR / path.name, DATA_DIR / path])
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            dataset_label = candidate.name
            year_label = candidate.stem
            output_name = candidate.stem
            return candidate, dataset_label, year_label, output_name
    raise SystemExit(f"未找到输入文件：{file_arg}")


def normalize_text(value: object, unknown: str | None = None) -> str | pd.NA:
    if pd.isna(value):
        return unknown if unknown is not None else pd.NA
    text = str(value).strip()
    if text.endswith(".0") and re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    if text in {"", "nan", "NaN", "None", "NONE", "无", "无效"}:
        return unknown if unknown is not None else pd.NA
    return text


def normalize_id(value: object) -> str | pd.NA:
    return normalize_text(value)


def normalize_category(value: object) -> str:
    text = normalize_text(value)
    return "未知" if pd.isna(text) else str(text)


def clean_station_id(value: object) -> str | pd.NA:
    return normalize_id(value)


def clean_asset_station_key(value: object) -> str | pd.NA:
    return normalize_id(value)


def normalize_station_key(value: object) -> str | pd.NA:
    text = normalize_id(value)
    if pd.isna(text):
        return pd.NA
    text = str(text).strip()
    if text.lower().startswith("station-"):
        text = text[8:].strip()
    return text or pd.NA


def normalize_station_type_text(value: object) -> str | pd.NA:
    text = normalize_text(value)
    if pd.isna(text):
        return pd.NA
    text = str(text).strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    return text or pd.NA


def series_from_frame(frame: pd.DataFrame, column: str, default: object = pd.NA) -> pd.Series:
    """从原始表中安全取列；若存在重复列，只取最左侧那一列。"""
    if column not in frame.columns:
        return pd.Series([default] * len(frame), index=frame.index)
    selected = frame.loc[:, column]
    if isinstance(selected, pd.DataFrame):
        selected = selected.iloc[:, 0]
    return selected.reindex(frame.index)


def parse_datetime_like_mysql_null(series: pd.Series) -> pd.Series:
    """对齐对方脚本前置 SQL：把 MySQL 零时间先视为 NULL 再解析。"""
    zero_datetime_values = {
        "0000-00-00",
        "0000-00-00 00:00:00",
        "0000/00/00",
        "0000/00/00 00:00:00",
    }
    cleaned = series.mask(series.astype(str).str.strip().isin(zero_datetime_values), pd.NA)
    return pd.to_datetime(cleaned, errors="coerce")


@lru_cache(maxsize=1)
def load_station_info() -> tuple[pd.DataFrame, dict[str, str]]:
    """读取资产表，返回站点属性表与 ID 桥接字典。"""
    if not ASSET_STATION_PATH.exists():
        return pd.DataFrame(), {}
    try:
        uuid_sheet = pd.read_excel(ASSET_STATION_PATH, sheet_name="UUID", engine="openpyxl", dtype=object)
        station_sheet = pd.read_excel(ASSET_STATION_PATH, sheet_name="站ID", engine="openpyxl", dtype=object)
    except Exception:
        return pd.DataFrame(), {}

    uuid_col = next((col for col in ("站uuid", "uuid", "UUID") if col in uuid_sheet.columns), None)
    code_col = next((col for col in ("站编码", "stationcode", "station_code", "站编码 ") if col in uuid_sheet.columns), None)
    stationid_col = next((col for col in ("stationid", "station_id", "站id", "站ID") if col in station_sheet.columns), None)
    type_col = next((col for col in ("站点类型", "场站类别") if col in station_sheet.columns), None)
    place_col = next((col for col in ("建设场所", "场所") if col in station_sheet.columns), None)
    if not uuid_col or not code_col or not stationid_col or not type_col:
        return pd.DataFrame(), {}

    station_sheet = station_sheet.copy()
    station_sheet["_stationid_key"] = station_sheet[stationid_col].map(normalize_station_key)
    station_sheet["sid"] = station_sheet["_stationid_key"]
    station_sheet = station_sheet.dropna(subset=["sid"]).drop_duplicates(subset=["sid"], keep="first").set_index("sid")

    station_name_col = next((col for col in ("stationname", "station_name", "站名", "站点名称") if col in station_sheet.columns), None)
    if station_name_col:
        station_sheet["_stationname_key"] = station_sheet[station_name_col].map(normalize_text)
    else:
        station_sheet["_stationname_key"] = pd.NA

    if place_col and place_col not in station_sheet.columns:
        station_sheet[place_col] = pd.NA

    bridge = {
        str(normalize_station_key(k)): str(normalize_station_key(v))
        for k, v in zip(uuid_sheet[uuid_col], uuid_sheet[code_col])
        if pd.notna(normalize_station_key(k)) and pd.notna(normalize_station_key(v))
    }

    print(f"1. 资产表读取完成：{len(station_sheet)} 个场站，{len(bridge)} 条 ID 对照记录")
    return station_sheet, bridge


def resolve_station_id(series: pd.Series, station_ids: pd.Index | pd.Series, bridge: dict[str, str]) -> pd.Series:
    """将订单中的站点 ID 统一为资产表使用的站编码。"""
    raw = series.map(normalize_station_key)
    resolved = raw.where(raw.isin(station_ids), raw.map(lambda x: bridge.get(x, x)))
    return resolved.map(normalize_station_key)


def station_type_to_category(site_type: object) -> str:
    text = normalize_station_type_text(site_type)
    if pd.isna(text):
        return "其他"
    text = str(text).strip()
    text = STATION_TYPE_MERGE_MAP.get(text, text)
    if not text or text in {"0", "其他", "未知"}:
        return "其他"
    if text in {"高速"}:
        return "高速"
    if text in {"城市公共" }:
        return "公共场站"
    if text in {
        "单位内部（专用）",
        "单位内部(专用)",
        "单位内部",
        "专用",
    }:
        return "专用场站"
    if text in {"公交", "公交场站"}:
        return "公交场站"
    return "其他"


def choose_station_category(categories: Iterable[object]) -> str:
    normalized = [str(category) for category in categories if pd.notna(category)]
    for category in STATION_CATEGORY_PRIORITY:
        if category in normalized:
            return category
    return "其他"


def build_station_name_category_map(station: pd.DataFrame) -> dict[str, str]:
    if station.empty or "站点类型" not in station.columns:
        return {}
    name_col = next((col for col in ("stationname", "station_name", "站名", "站点名称") if col in station.columns), None)
    if not name_col:
        return {}
    name_series = station[name_col].map(normalize_text)
    category_series = station["站点类型"].map(station_type_to_category)
    frame = pd.DataFrame({"name": name_series, "category": category_series}).dropna(subset=["name"])
    if frame.empty:
        return {}
    return {
        str(name): choose_station_category(group["category"])
        for name, group in frame.groupby("name", sort=False)
    }


def attach_station_category(result: pd.DataFrame, station: pd.DataFrame, bridge: dict[str, str]) -> pd.DataFrame:
    resolved_sid = resolve_station_id(result["充电站ID"], station.index, bridge)
    station_type = resolved_sid.map(station["站点类型"]) if "站点类型" in station.columns else pd.Series(index=result.index, dtype="object")
    result["场站类别"] = station_type.map(station_type_to_category).fillna("其他")
    name_map = build_station_name_category_map(station)
    if name_map and "充电站名称" in result.columns:
        name_category = result["充电站名称"].map(normalize_text).map(name_map).fillna("其他")
        result["场站类别"] = result["场站类别"].where(
            result["场站类别"].ne("其他") | name_category.eq("其他"),
            name_category,
        )
    return result


@lru_cache(maxsize=1)
def load_asset_station_category_map() -> dict[str, str]:
    station, bridge = load_station_info()
    if station.empty:
        return {}
    sample = pd.DataFrame({"充电站ID": station.index.to_series(index=station.index)})
    if "_stationname_key" in station.columns:
        sample["充电站名称"] = station.get(next((col for col in ("stationname", "station_name", "站名", "站点名称") if col in station.columns), ""), pd.NA)
    sample = attach_station_category(sample, station, bridge)
    return sample["场站类别"].to_dict()


@lru_cache(maxsize=1)
def load_asset_station_category_maps() -> tuple[dict[str, str], dict[str, str]]:
    station, bridge = load_station_info()
    if station.empty:
        return {}, {}
    sample = pd.DataFrame({"充电站ID": station.index.to_series(index=station.index)})
    if "_stationname_key" in station.columns:
        name_col = next((col for col in ("stationname", "station_name", "站名", "站点名称") if col in station.columns), None)
        if name_col:
            sample["充电站名称"] = station[name_col].values
    sample = attach_station_category(sample, station, bridge)
    id_map = sample["场站类别"].to_dict()
    name_map: dict[str, str] = {}
    if "_stationname_key" in station.columns:
        name_col = next((col for col in ("stationname", "station_name", "站名", "站点名称") if col in station.columns), None)
        if name_col:
            name_series = station[name_col].map(normalize_text)
            type_series = station["站点类型"].map(station_type_to_category) if "站点类型" in station.columns else pd.Series(index=station.index, dtype="object")
            name_map = {
                str(name): str(category)
                for name, category in zip(name_series, type_series)
                if pd.notna(name) and pd.notna(category)
    }
    return id_map, name_map


def to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """避免除以0或空值导致无意义结果。"""
    result = numerator / denominator
    return result.where(denominator.notna() & denominator.ne(0), pd.NA)


def calculate_high_frequency_threshold(start_times: pd.Series) -> tuple[int, int]:
    """按数据实际覆盖天数折算“年订单数超过365”的异常用户阈值。"""
    valid_start = pd.to_datetime(start_times, errors="coerce").dropna()
    if valid_start.empty:
        return HIGH_FREQUENCY_ANNUAL_ORDER_THRESHOLD, 0

    min_date = valid_start.min().normalize()
    max_date = valid_start.max().normalize()
    covered_days = max(1, int((max_date - min_date).days) + 1)
    threshold = math.ceil(HIGH_FREQUENCY_ANNUAL_ORDER_THRESHOLD * covered_days / 365)
    return max(1, min(HIGH_FREQUENCY_ANNUAL_ORDER_THRESHOLD, threshold)), covered_days


def join_reasons(reasons: Iterable[str | None]) -> str:
    valid = [reason for reason in reasons if reason]
    return "；".join(dict.fromkeys(valid)) if valid else ""


def remove_high_frequency_reason(reason: object) -> str:
    text = "" if pd.isna(reason) else str(reason)
    parts = [part.strip() for part in text.split("；") if part.strip()]
    parts = [part for part in parts if not part.startswith("同一id订单数超过动态阈值")]
    return "；".join(parts)


def classify_end_reason(value: object) -> str:
    text = normalize_text(value, "未知")
    text = str(text)
    upper = text.upper()
    if any(key in text for key in ["电动汽车充满", "充满停止", "达到SOC", "022AH", "BMS 正常", "BMS正常", "达到涓流充电停机条件", "预付金额不足", "达到设置充电电量停止", "本地停止充电"]):
        return "正常结束"
    if any(key in text for key in ["用户远程", "用户在充电桩", "APP停止", "用户拔枪", "车端S2主动断开"]):
        return "用户主动停止"
    if any(key in text for key in ["预充金额用完", "达到设置充电金额", "金额停止"]):
        return "金额/SOC达到"
    if any(key in text for key in ["后台停止", "系统自动结束", "订单超过48小时"]):
        return "后台/系统结束"
    fault_keywords = [
        "故障",
        "异常",
        "超时",
        "绝缘",
        "控制导引",
        "报文",
        "接触器",
        "输入电源",
        "连接故障",
        "BST",
        "BRM",
        "022CH",
        "022BH",
        "0253H",
        "010EH",
        "013DH",
        "0205H",
    ]
    if any(key in text for key in fault_keywords) or any(code in upper for code in ["0253H", "024E", "0241", "022C"]):
        return "故障异常"
    if text in {"未知", "0"} or text.isdigit():
        return "其他原因"
    return "其他原因"


def duration_bucket(minutes: object) -> str:
    if pd.isna(minutes):
        return "未知"
    value = float(minutes)
    if value < 0:
        return "时间异常"
    if value == 0:
        return "0min"
    bins = [
        (10, "0-10min"),
        (20, "10-20min"),
        (30, "20-30min"),
        (40, "30-40min"),
        (50, "40-50min"),
        (60, "50-60min"),
        (90, "60-90min"),
        (120, "90-120min"),
        (180, "120-180min"),
        (240, "180-240min"),
        (360, "240-360min"),
        (720, "360-720min"),
    ]
    for limit, label in bins:
        if value <= limit:
            return label
    return "720min以上"


def energy_bucket(kwh: object) -> str:
    if pd.isna(kwh):
        return "未知"
    value = float(kwh)
    if value == 0:
        return "0kWh"
    if value <= 10:
        return "0-10kWh"
    if value <= 20:
        return "10-20kWh"
    if value <= 40:
        return "20-40kWh"
    if value <= 60:
        return "40-60kWh"
    if value <= 80:
        return "60-80kWh"
    return "80kWh以上"


def interval_bucket(hours: object) -> str:
    if pd.isna(hours):
        return "首次"
    value = float(hours)
    if value <= 6:
        return "0-6h"
    if value <= 24:
        return "6-24h"
    if value <= 72:
        return "1-3天"
    if value <= 168:
        return "3-7天"
    if value <= 336:
        return "7-14天"
    return "14天以上"


def rough_period(hour: object) -> str:
    if pd.isna(hour):
        return "未知"
    h = int(hour)
    if 0 <= h <= 5:
        return "凌晨（0:00-5:59）"
    if 6 <= h <= 11:
        return "上午（6:00-11:59）"
    if 12 <= h <= 17:
        return "下午（12:00-17:59）"
    return "晚上（18:00-23:59）"


def time_of_use_schedule(date: pd.Timestamp) -> dict[str, list[tuple[int, int]]]:
    """Return peak/flat/valley hourly intervals for a given date.

    2025-07-01 and later use the seasonal tariff schedule; earlier dates keep the
    legacy three-segment schedule used by the source dataset.
    """
    if date >= pd.Timestamp("2025-07-01"):
        if date.month in {6, 7, 8, 12, 1, 2}:
            return {
                "peak": [(14, 22)],
                "flat": [(6, 11), (13, 14), (22, 24)],
                "valley": [(0, 6), (11, 13)],
            }
        return {
            "peak": [(15, 22)],
            "flat": [(0, 2), (6, 10), (14, 15), (22, 24)],
            "valley": [(2, 6), (10, 14)],
        }
    return {
        "peak": [(8, 11), (17, 22)],
        "flat": [(11, 17), (22, 24)],
        "valley": [(0, 8)],
    }


def _schedule_overlap(
    date: pd.Timestamp,
    starts: pd.Series,
    ends: pd.Series,
) -> pd.DataFrame:
    """Compute each order's overlap duration with peak/flat/valley intervals on one date."""
    columns: dict[str, pd.Series] = {}
    for segment, intervals in time_of_use_schedule(date).items():
        total = pd.Series(pd.Timedelta(0), index=starts.index, dtype="timedelta64[ns]")
        for start_hour, end_hour in intervals:
            interval_start = date + pd.Timedelta(hours=start_hour)
            interval_end = date + pd.Timedelta(hours=end_hour)
            clipped_start = starts.clip(lower=interval_start)
            clipped_end = ends.clip(upper=interval_end)
            overlap = (clipped_end - clipped_start).clip(lower=pd.Timedelta(0))
            total = total + overlap
        columns[segment] = total
    return pd.DataFrame(columns, index=starts.index)


def allocate_time_of_use_energy(
    starts: pd.Series,
    ends: pd.Series,
    total_energy: pd.Series,
) -> pd.DataFrame:
    """Allocate order energy to peak/flat/valley by exact time overlap.

    Orders are grouped by start date. Because the cleaning rules exclude orders
    longer than 18 hours, a cross-midnight order can only span the start date and
    the next date, which keeps the exact split tractable.
    """
    starts = pd.to_datetime(starts, errors="coerce")
    ends = pd.to_datetime(ends, errors="coerce")
    total_energy = pd.to_numeric(total_energy, errors="coerce").fillna(0.0)

    durations = pd.DataFrame(
        {
            "peak": pd.Series(pd.Timedelta(0), index=starts.index),
            "flat": pd.Series(pd.Timedelta(0), index=starts.index),
            "valley": pd.Series(pd.Timedelta(0), index=starts.index),
        }
    )
    result = pd.DataFrame(
        {"peak": 0.0, "flat": 0.0, "valley": 0.0},
        index=starts.index,
    )
    valid = starts.notna() & ends.notna() & ends.ge(starts)
    if not valid.any():
        return result

    valid_starts = starts.loc[valid]
    valid_ends = ends.loc[valid]
    valid_energy = total_energy.loc[valid]
    start_dates = valid_starts.dt.normalize()

    for date, group_index in start_dates.groupby(start_dates).groups.items():
        same_day = valid_ends.loc[group_index].dt.normalize().eq(date)
        same_index = group_index[same_day]
        if not same_index.empty:
            same_day_durations = _schedule_overlap(
                date,
                valid_starts.loc[same_index],
                valid_ends.loc[same_index],
            )
            durations.loc[same_index, ["peak", "flat", "valley"]] = same_day_durations

        cross_index = group_index[~same_day]
        if cross_index.empty:
            continue

        midnight = date + pd.Timedelta(days=1)
        first_day = _schedule_overlap(
            date,
            valid_starts.loc[cross_index],
            pd.Series(midnight, index=cross_index),
        )
        next_day = _schedule_overlap(
            midnight,
            pd.Series(midnight, index=cross_index),
            valid_ends.loc[cross_index],
        )
        durations.loc[cross_index, ["peak", "flat", "valley"]] = first_day + next_day

    valid_index = valid.loc[valid].index
    duration_values = (valid_ends - valid_starts).dt.total_seconds().to_numpy(dtype="float64")
    energy_values = valid_energy.to_numpy(dtype="float64")
    for segment in ["peak", "flat", "valley"]:
        segment_values = durations.loc[valid_index, segment].dt.total_seconds().to_numpy(dtype="float64")
        allocated = np.divide(
            energy_values * segment_values,
            duration_values,
            out=np.zeros_like(energy_values, dtype="float64"),
            where=duration_values > 0,
        )
        result.loc[valid_index, segment] = allocated
    return result


def merge_short_restart_orders(df: pd.DataFrame, gap_minutes: int = SHORT_RESTART_GAP_MINUTES) -> tuple[pd.DataFrame, pd.DataFrame]:
    """合并同一用户同一站点下的短时重启订单。

    业务背景：用户在充电过程中可能因操作失误、设备故障、网络中断等原因
    导致订单异常结束并立即重新启动，形成多个时间间隔很短的订单。
    这些订单实际上属于同一次充电会话，需要合并以保证数据统计的准确性。

    合并规则：
        1. 同一用户（用户编码相同）
        2. 同一站点（充电站ID相同）
        3. 时间连续（当前订单开始时间 >= 上一订单结束时间）
        4. 间隔短暂（两订单间隔 <= gap_minutes，默认10分钟）

    合并策略：
        - 时间字段：取最早开始时间和最晚结束时间
        - 数值字段（电量、金额等）：累加求和
        - 类别字段（订单状态、充电方式等）：取第一条记录的值
        - 布尔字段（是否使用优惠等）：任一"是"则为"是"
        - 原因字段（结束原因等）：合并所有原因，去重后用"；"连接

    Args:
        df: 输入的订单DataFrame，包含充电订单明细数据
        gap_minutes: 判断为短时重启的最大时间间隔（分钟），默认SHORT_RESTART_GAP_MINUTES

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]:
            - merged: 合并后的订单DataFrame，每行代表一个充电会话
            - summary: 合并统计摘要，包含合并前后的订单数、减少数、合并组数
    """
    # 处理空数据情况，返回零值统计和空DataFrame副本
    if df.empty:
        summary = pd.DataFrame(
            [
                {"指标": "短时重启合并前订单数", "数值": 0},
                {"指标": "短时重启合并后会话数", "数值": 0},
                {"指标": "短时重启合并减少订单数", "数值": 0},
                {"指标": "短时重启合并组数", "数值": 0},
            ]
        )
        return df.copy(), summary

    # 创建工作副本，避免修改原始数据
    work = df.copy()
    if "交易流水号" not in work.columns:
        work["交易流水号"] = work.index.astype(str)

    # 转换时间字段为datetime格式，便于后续计算时间差
    work["_merge_start"] = pd.to_datetime(work["充电开始时间"], errors="coerce")
    work["_merge_end"] = pd.to_datetime(work["充电结束时间"], errors="coerce")

    # 按用户、开始时间、结束时间、流水号排序，确保同一用户的订单按时间顺序排列
    # 使用mergesort保证排序稳定性
    work = work.sort_values(
        ["用户编码", "_merge_start", "_merge_end", "交易流水号"],
        kind="mergesort",
    ).copy()

    # 获取前一行的关键字段，用于判断是否为连续订单
    prev_user = work["用户编码"].shift(1)      # 前一订单的用户编码
    prev_station = work["充电站ID"].shift(1)    # 前一订单的充电站ID
    prev_end = work["_merge_end"].shift(1)      # 前一订单的结束时间

    # 计算当前订单与前一订单的开始时间间隔（分钟）
    gap = (work["_merge_start"] - prev_end).dt.total_seconds() / 60

    # 判断是否为短时重启订单：同一用户、同一站点、时间连续、间隔在阈值内
    restart_flag = (
        work["用户编码"].eq(prev_user)          # 同一用户
        & work["充电站ID"].eq(prev_station)      # 同一站点
        & gap.ge(0)                              # 时间间隔非负（当前开始 >= 前一结束）
        & gap.le(gap_minutes)                    # 间隔在阈值范围内
    )

    # 使用累积和生成合并组编号：
    # 当restart_flag为False时（新组合开始），~restart_flag为True，cumsum增加
    # 同一组合内的订单具有相同的_merge_group值
    work["_merge_group"] = (~restart_flag).cumsum()

    # 定义需要累加求和的数值字段（电量、金额类）
    sum_cols = [
        "交易电量(kWh)（清洗后）",  # 总充电电量
        "峰电量(kWh)",              # 峰时段电量
        "平电量(kWh)",              # 平时段电量
        "谷电量(kWh)",              # 谷时段电量
        "实扣电费",                  # 实际电费
        "实扣服务费",                # 实际服务费
        "实扣金额",                  # 实际支付总金额
        "优惠金额",                  # 优惠金额
    ]

    # 定义取首值的类别字段（订单属性类）
    first_cols = [
        "交易流水号",
        "用户编码",
        "用户识别主键",
        "订单状态",
        "充电方式",
        "订单渠道",
        "订单来源",
        "用户类型",
        "充电站ID",
        "充电站名称",
        "场站类别",
        "建设场所",
        "充电桩编号",
    ]

    # 按用户编码和合并组进行分组聚合
    grouped = work.groupby(["用户编码", "_merge_group"], sort=False, dropna=False)

    # 执行分组聚合操作，不同字段采用不同的聚合策略
    existing_first_cols = [col for col in first_cols if col in work.columns]
    merged = grouped.agg(
        {
            # 类别字段：取第一条记录的值
            **{col: "first" for col in existing_first_cols},
            # 数值字段：累加求和
            **{col: "sum" for col in sum_cols},
            # 布尔字段：任一"是"则为"是"（或逻辑）
            "是否使用优惠": lambda s: "是" if (s == "是").any() else "否",
            # 原因字段：合并所有原因，去重后用"；"连接
            "结束原因分类": lambda s: join_reasons(s.tolist()),
            "原始结束原因": lambda s: join_reasons(s.tolist()),
            "异常原因": lambda s: join_reasons(s.tolist()),
            # 有效性字段：任一有效则整体有效
            "是否有效行为订单": lambda s: "是" if (s == "是").any() else "否",
            # 其他字段：取第一条记录的值
            "是否跨日": "first",
            "充电时长(min)": "first",
            "充电时长分布": "first",
            "电量分布": "first",
            "谷段电量占比": "first",
            "单度价格": "first",
            "平均充电功率(kW)": "first",
            "充电日期": "first",
            "月份": "first",
            "星期": "first",
            "开始小时": "first",
            "充电粗略时段": "first",
            "用户年内订单序号": "first",
            "用户月内订单序号": "first",
            "距上次充电间隔(h)": "first",
            "充电间隔分布": "first",
            "是否首次充电": "first",
            "是否复用上次站点": "first",
        }
    ).reset_index(drop=True)

    # 记录每个合并组包含的原始订单数量
    merged["原始订单数"] = grouped.size().to_numpy()
    # 标记是否发生了短时重启合并（原始订单数>1表示发生了合并）
    merged["是否短时重启合并"] = merged["原始订单数"].gt(1).map({True: "是", False: "否"})

    # 重新计算合并后的时间字段：
    # 开始时间取组内最早的开始时间，结束时间取组内最晚的结束时间
    merged["充电开始时间"] = pd.to_datetime(work.groupby(["用户编码", "_merge_group"], sort=False)["_merge_start"].min().to_numpy())
    merged["充电结束时间"] = pd.to_datetime(work.groupby(["用户编码", "_merge_group"], sort=False)["_merge_end"].max().to_numpy())

    # 基于合并后的开始时间，重新计算日期相关衍生字段
    merged["充电日期"] = merged["充电开始时间"].dt.date
    merged["月份"] = merged["充电开始时间"].dt.month.astype("Int64")
    merged["星期"] = merged["充电开始时间"].dt.weekday.map(lambda x: WEEKDAY_NAMES[int(x)] if pd.notna(x) else "未知")
    merged["开始小时"] = merged["充电开始时间"].dt.hour.astype("Int64")
    merged["充电粗略时段"] = merged["开始小时"].map(rough_period)
    # 判断是否跨日：开始日期与结束日期是否相同
    merged["是否跨日"] = (merged["充电开始时间"].dt.date != merged["充电结束时间"].dt.date).map({True: "是", False: "否"})

    # 重新计算合并后的充电时长（基于新的起止时间）
    merged["充电时长(min)"] = ((merged["充电结束时间"] - merged["充电开始时间"]).dt.total_seconds() / 60).round(3)
    merged["充电时长分布"] = merged["充电时长(min)"].map(duration_bucket)

    # 对数值字段进行精度处理和分布归类
    merged["交易电量(kWh)（清洗后）"] = merged["交易电量(kWh)（清洗后）"].round(3)
    merged["电量分布"] = merged["交易电量(kWh)（清洗后）"].map(energy_bucket)
    merged["峰电量(kWh)"] = merged["峰电量(kWh)"].round(3)
    merged["平电量(kWh)"] = merged["平电量(kWh)"].round(3)
    merged["谷电量(kWh)"] = merged["谷电量(kWh)"].round(3)
    # 重新计算谷段电量占比（谷电量/总电量）
    merged["谷段电量占比"] = safe_divide(merged["谷电量(kWh)"], merged["交易电量(kWh)（清洗后）"]).round(3)

    # 金额字段精度处理（保留2位小数）
    merged["实扣电费"] = merged["实扣电费"].round(2)
    merged["实扣服务费"] = merged["实扣服务费"].round(2)
    merged["实扣金额"] = merged["实扣金额"].round(2)
    merged["优惠金额"] = merged["优惠金额"].round(2)

    # 重新计算单价和平均功率（基于合并后的总电量和总时长）
    merged["单度价格"] = safe_divide(merged["实扣金额"], merged["交易电量(kWh)（清洗后）"]).round(3)
    merged["平均充电功率(kW)"] = safe_divide(merged["交易电量(kWh)（清洗后）"], merged["充电时长(min)"] / 60).round(3)

    # 废弃规则之后的主表即为有效订单，短时重启合并不再二次筛掉订单。
    merged["是否有效行为订单"] = "是"

    # 调整列顺序，确保输出字段的一致性和可读性
    # 列顺序按业务逻辑分组：基础信息 -> 时间信息 -> 电量信息 -> 金额信息 -> 标记信息
    merged = merged[
        [
            "交易流水号",
            "用户编码",
            "用户识别主键",
            "订单状态",
            "充电方式",
            "订单渠道",
            "订单来源",
            "用户类型",
            "充电站ID",
            "充电站名称",
            "场站类别",
            "建设场所",
            "充电桩编号",
            "充电开始时间",
            "充电结束时间",
            "充电日期",
            "星期",
            "充电粗略时段",
            "是否跨日",
            "充电时长(min)",
            "充电时长分布",
            "交易电量(kWh)（清洗后）",
            "电量分布",
            "峰电量(kWh)",
            "平电量(kWh)",
            "谷电量(kWh)",
            "谷段电量占比",
            "实扣电费",
            "实扣服务费",
            "实扣金额",
            "优惠金额",
            "是否使用优惠",
            "单度价格",
            "平均充电功率(kW)",
            "结束原因分类",
            "原始结束原因",
            "用户年内订单序号",
            "用户月内订单序号",
            "距上次充电间隔(h)",
            "充电间隔分布",
            "是否首次充电",
            "是否复用上次站点",
            "原始订单数",           # 新增：记录合并组包含的原始订单数
            "是否短时重启合并",      # 新增：标记是否发生合并
            "异常原因",
            "是否有效行为订单",
        ]
    ].copy()

    # 生成合并统计摘要，用于数据质量监控和报告输出
    summary = pd.DataFrame(
        [
            {"指标": "短时重启合并前订单数", "数值": int(len(df))},                    # 原始订单总数
            {"指标": "短时重启合并后会话数", "数值": int(len(merged))},                # 合并后会话总数
            {"指标": "短时重启合并减少订单数", "数值": int(len(df) - len(merged))},    # 合并减少的订单数
            {"指标": "短时重启合并组数", "数值": int((merged["原始订单数"] > 1).sum())}, # 发生合并的组数
        ]
    )
    return merged, summary


def safe_write_excel(path: Path, sheets: dict[str, pd.DataFrame]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidates = [path] + [path.with_name(f"{path.stem}_new{'' if i == 1 else '_' + str(i)}{path.suffix}") for i in range(1, 20)]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            with pd.ExcelWriter(candidate, engine="openpyxl") as writer:
                for sheet_name, df in sheets.items():
                    df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            apply_workbook_style(candidate)
            return candidate
        except PermissionError as exc:
            last_error = exc
            continue
    raise PermissionError(f"无法写入 {path}，请关闭 Excel/WPS 中打开的同名文件。") from last_error


def safe_write_csv(path: Path, df: pd.DataFrame) -> Path:
    """大表使用 CSV 输出，避免 xlsx 写入 20 万行时过慢。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d %H:%M:%S", lineterminator="\n")
    except PermissionError as exc:
        raise PermissionError(f"无法写入 {path}，请关闭 Excel/WPS 中打开的同名文件。") from exc
    return path


def apply_workbook_style(path: Path) -> None:
    wb = load_workbook(path)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for col_cells in ws.columns:
            col_letter = get_column_letter(col_cells[0].column)
            max_len = max(len(str(cell.value)) if cell.value is not None else 0 for cell in col_cells[:200])
            ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 34)
    wb.save(path)


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "（无记录）"

    def fmt(value: object) -> str:
        if pd.isna(value):
            return ""
        return str(value).replace("|", "\\|").replace("\n", " ")

    headers = [fmt(col) for col in df.columns]
    rows = [[fmt(value) for value in row] for row in df.to_numpy()]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def build_station_category_distribution(df: pd.DataFrame) -> pd.DataFrame:
    counts = df["场站类别"].fillna("其他").value_counts()
    total = int(counts.sum()) if len(counts) else 0
    if "充电站ID" in df.columns:
        station_counts = df.assign(_场站类别=df["场站类别"].fillna("其他")).groupby("_场站类别")["充电站ID"].nunique(dropna=True)
    else:
        station_counts = pd.Series(dtype="int64")
    rows = []
    for category in STATION_CATEGORY_ORDER:
        value = int(counts.get(category, 0))
        station_count = int(station_counts.get(category, 0))
        rows.append(
            {
                "场站类别": category,
                "订单数": value,
                "场站数": station_count,
                "占比": round(value / total, 4) if total else 0,
            }
        )
    return pd.DataFrame(rows)


def escape_svg_text(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def save_station_category_chart(distribution: pd.DataFrame, path: Path) -> Path:
    """输出场站订单分布图。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = distribution[distribution["订单数"] > 0].copy()
    if data.empty:
        data = distribution.copy()

    width, height = 920, 420
    cx, cy, r = 180, 210, 120
    legend_x = 360
    total = float(data["订单数"].sum()) if len(data) else 0.0
    if total <= 0:
        total = 1.0
    colors = {
        "高速": "#d97706",
        "专用场站": "#0f766e",
        "公交场站": "#7c3aed",
        "公共场站": "#2563eb",
        "其他": "#64748b",
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:"Microsoft YaHei","SimHei",Arial,sans-serif;fill:#1f2937;}</style>',
        '<text x="24" y="36" font-size="22" font-weight="700">场站订单分布</text>',
        '<text x="24" y="60" font-size="12" fill="#64748b">按场站类别统计订单分布，并标注每类去重场站数</text>',
    ]

    start_angle = -90.0
    if len(data) == 1:
        row = data.iloc[0]
        color = colors.get(str(row["场站类别"]), "#64748b")
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>')
    else:
        for _, row in data.iterrows():
            value = float(row["订单数"])
            share = value / total
            angle = share * 360.0
            end_angle = start_angle + angle
            start_rad = math.radians(start_angle)
            end_rad = math.radians(end_angle)
            x1 = cx + r * math.cos(start_rad)
            y1 = cy + r * math.sin(start_rad)
            x2 = cx + r * math.cos(end_rad)
            y2 = cy + r * math.sin(end_rad)
            large_arc = 1 if angle > 180 else 0
            color = colors.get(str(row["场站类别"]), "#64748b")
            path_d = (
                f"M {cx} {cy} L {x1:.2f} {y1:.2f} "
                f"A {r} {r} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z"
            )
            parts.append(f'<path d="{path_d}" fill="{color}"/>')
            start_angle = end_angle

    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r * 0.58}" fill="#ffffff"/>')
    parts.append(f'<text x="{cx}" y="{cy - 6}" font-size="18" text-anchor="middle" font-weight="700">订单分布</text>')
    parts.append(f'<text x="{cx}" y="{cy + 18}" font-size="12" text-anchor="middle" fill="#64748b">按订单数占比</text>')

    for i, (_, row) in enumerate(data.iterrows()):
        y = 110 + i * 62
        category = str(row["场站类别"])
        value = int(row["订单数"])
        station_count = int(row.get("场站数", 0))
        pct = float(row["占比"])
        color = colors.get(category, "#64748b")
        parts.extend(
            [
                f'<rect x="{legend_x}" y="{y - 16}" width="18" height="18" rx="3" fill="{color}"/>',
                f'<text x="{legend_x + 28}" y="{y}" font-size="15">{escape_svg_text(category)}</text>',
                f'<text x="{legend_x + 165}" y="{y}" font-size="14" fill="#475569">{value:,}单（{pct:.1%}）</text>',
                f'<text x="{legend_x + 165}" y="{y + 22}" font-size="13" fill="#64748b">场站 {station_count:,} 个</text>',
            ]
        )

    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def save_high_frequency_exclusion_chart(before_count: int, after_count: int, threshold: int, covered_days: int, path: Path) -> Path:
    """输出高频异常用户剔除前后订单数对比图。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 980, 520
    left, right, top, bottom = 120, 70, 88, 92
    plot_w = width - left - right
    plot_h = 220
    base_y = top + plot_h
    values = [
        ("剔除前候选主表", before_count, "#2563eb"),
        ("剔除后保留", after_count, "#16a34a"),
    ]
    max_value = max(before_count, after_count, 1)
    bar_w = plot_w / len(values) * 0.58
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:"Microsoft YaHei","SimHei",Arial,sans-serif;fill:#1f2937;}.title{font-size:22px;font-weight:700;}.subtitle{font-size:12px;fill:#64748b;}.label{font-size:13px;}.small{font-size:12px;fill:#475569;}.white{font-size:12px;fill:#ffffff;font-weight:700;}</style>',
        '<text x="24" y="36" class="title">高频异常剔除前后订单数对比</text>',
        f'<text x="24" y="60" class="subtitle">按数据覆盖天数动态折算阈值；覆盖天数 {covered_days} 天，阈值 {threshold} 单</text>',
    ]
    for tick in range(5):
        y = top + plot_h * tick / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>')

    for i, (label, value, color) in enumerate(values):
        h = float(value) / max_value * plot_h
        x = left + i * (plot_w / len(values)) + (plot_w / len(values) - bar_w) / 2
        y = base_y - h
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h, 2):.1f}" rx="5" fill="{color}"/>')
        parts.append(f'<text class="white" x="{x + bar_w / 2:.1f}" y="{max(y - 8, top + 14):.1f}" text-anchor="middle">{value:,}</text>')
        parts.append(f'<text class="label" x="{x + bar_w / 2:.1f}" y="{height - 52}" text-anchor="middle">{escape_svg_text(label)}</text>')

    parts.append(f'<line x1="{left}" y1="{base_y}" x2="{width-right}" y2="{base_y}" stroke="#94a3b8" stroke-width="1.2"/>')
    parts.append(f'<text class="small" x="{left}" y="{height - 26}">说明：剔除前为候选主表订单数，剔除后为保留订单数，二者差值即剔除订单数。</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def build_cleaned_orders(
    raw: pd.DataFrame,
    year_label: str,
    *,
    generate_charts: bool = True,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Path | None, pd.DataFrame]:
    df = raw.copy()
    total_rows = len(df)

    def report_progress(current: int, label: str) -> None:
        if progress_cb is not None:
            progress_cb(current, total_rows, label)

    report_progress(0, "开始清洗")
    if df.columns.duplicated().any():
        df = df.T.groupby(level=0).first().T
    report_progress(min(total_rows, int(total_rows * 5 / 100)), "整理字段")

    required_input_cols = [
        "order_id",
        "charger_id",
        "station_id",
        "user_id",
        "start_time",
        "end_time",
        "kwh",
        "meter_kwh",
        "A_EF",
        "A_SF",
        "EF",
        "SF",
        "coupon_discount",
        "promotion_discount",
        "other_discount",
        "peak_kwh",
        "flat_kwh",
        "valley_kwh",
        "sharp_kwh",
        "deep_valley_kwh",
        "low_valley_kwh",
        "vin",
        "charge_method",
        "status",
        "channel",
        "source",
        "station_name",
        "end_reason",
        "user_type",
        "EXCEL",
    ]
    for col in required_input_cols:
        if col not in df.columns:
            df[col] = pd.NA

    for col in ["order_id", "charger_id", "station_id", "user_id", "EXCEL"]:
        if col in df.columns:
            df[col] = df[col].map(normalize_id)

    for col in ["charge_method", "status", "channel", "source", "station_name", "end_reason", "user_type"]:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = df[col].map(normalize_category)
    raw_order_id = series_from_frame(df, "order_id")
    raw_user_id = series_from_frame(df, "user_id")
    raw_station_id = series_from_frame(df, "station_id").map(normalize_station_key)
    raw_status = series_from_frame(df, "status").map(normalize_category)
    raw_start = parse_datetime_like_mysql_null(series_from_frame(df, "start_time"))
    raw_end = parse_datetime_like_mysql_null(series_from_frame(df, "end_time"))
    raw_kwh = to_number(series_from_frame(df, "kwh"))
    raw_meter_kwh = to_number(series_from_frame(df, "meter_kwh"))
    raw_nominal_electric_fee = to_number(series_from_frame(df, "EF")).fillna(0)
    raw_nominal_service_fee = to_number(series_from_frame(df, "SF")).fillna(0)
    raw_peak_kwh = to_number(series_from_frame(df, "peak_kwh")).fillna(0)
    raw_flat_kwh = to_number(series_from_frame(df, "flat_kwh")).fillna(0)
    raw_valley_kwh = to_number(series_from_frame(df, "valley_kwh")).fillna(0)
    raw_deep_valley_kwh = to_number(series_from_frame(df, "deep_valley_kwh")).fillna(0)
    raw_low_valley_kwh = to_number(series_from_frame(df, "low_valley_kwh")).fillna(0)
    raw_sharp_kwh = to_number(series_from_frame(df, "sharp_kwh")).fillna(0)

    # 对齐对方 import_to_mysql.py 的 MySQL 数值列行为：
    # DOUBLE 字段中的空数值参与 CASE 判定时按 0 处理。
    judge_kwh = raw_kwh.fillna(0)
    judge_meter_kwh = raw_meter_kwh.fillna(0)
    raw_charge_seconds = (raw_end - raw_start).dt.total_seconds()
    raw_data_judge = pd.Series(0, index=df.index, dtype="Int64")
    raw_data_judge_reason = pd.Series("", index=df.index, dtype="object")
    raw_judge_rules = [
        (raw_status.isin({"废弃", "已取消"}), 1, "订单状态选择"),
        (judge_kwh > 800, 2, "交易电量大于800"),
        (judge_meter_kwh > 800, 3, "抄表电量大于800"),
        (raw_nominal_electric_fee > 500, 4, "电费大于500"),
        (raw_nominal_service_fee > 500, 5, "服务费大于500"),
        ((raw_nominal_electric_fee + raw_nominal_service_fee) > 800, 6, "交易金额大于800"),
        (judge_kwh < 0, 7, "交易电量小于0"),
        (((raw_nominal_electric_fee / judge_kwh.where(judge_kwh.ne(0), pd.NA)) > 4), 8, "电费/电量大于4"),
        (judge_meter_kwh < 0, 9, "抄表电量小于0"),
        (raw_charge_seconds < 0, 10, "充电时长小于0秒"),
        ((judge_kwh < 0.1) & ((raw_nominal_service_fee + raw_nominal_electric_fee) != 0), 11, "电量小于0.1且总费用不为0"),
        ((raw_charge_seconds > 0) & (judge_kwh < 0.1), 12, "充电时长大于0但电量小于0.1"),
        ((judge_kwh - judge_meter_kwh).abs() > 0.1, 13, "充电量和抄表电量误差大于0.1"),
        (
            (
                raw_peak_kwh
                + raw_sharp_kwh
                + raw_flat_kwh
                + raw_valley_kwh
                + raw_deep_valley_kwh
                + raw_low_valley_kwh
                - judge_kwh
            ).abs() > 0.1,
            14,
            "分时电量和充电量误差大于0.1",
        ),
        (raw_charge_seconds > 64800, 15, "充电时长大于18小时"),
        (
            raw_start.isna() | raw_end.isna(),
            16,
            "充电开始或结束时间缺失",
        ),
        (
            raw_start.lt(pd.Timestamp("2020-01-01"))
            | raw_start.gt(TIME_CUTOFF_END),
            17,
            "充电开始时间超出 2020-01-01 至 2026-07-31 23:59:59 范围",
        ),
    ]
    for mask, code, reason in raw_judge_rules:
        hit = raw_data_judge.eq(0) & mask.fillna(False)
        raw_data_judge.loc[hit] = code
        raw_data_judge_reason.loc[hit] = reason
    main_order_mask = raw_data_judge.eq(0)
    report_progress(min(total_rows, int(total_rows * 35 / 100)), "废弃规则判定")

    out = df.loc[main_order_mask].copy()
    out["交易流水号"] = raw_order_id.loc[main_order_mask]
    out["用户编码"] = raw_user_id.loc[main_order_mask]
    out["用户识别主键"] = raw_user_id.loc[main_order_mask]
    out["订单状态"] = raw_status.loc[main_order_mask]
    out["充电方式"] = out["charge_method"]
    out["订单渠道"] = out["channel"]
    out["订单来源"] = out["source"]
    out["用户类型"] = out["user_type"].map(normalize_category) if "user_type" in out.columns else pd.Series(["未知用户类型"] * len(out), index=out.index)
    out["充电站ID"] = raw_station_id.loc[main_order_mask]
    out["充电站名称"] = out["station_name"]
    out["充电桩编号"] = out["charger_id"]
    out["充电开始时间"] = raw_start.loc[main_order_mask]
    out["充电结束时间"] = raw_end.loc[main_order_mask]
    out["充电日期"] = out["充电开始时间"].dt.date
    out["月份"] = out["充电开始时间"].dt.month.astype("Int64")
    out["星期"] = out["充电开始时间"].dt.weekday.map(lambda x: WEEKDAY_NAMES[int(x)] if pd.notna(x) else "未知")
    start_hour = out["充电开始时间"].dt.hour
    out["开始小时"] = start_hour.astype("Int64")
    out["充电粗略时段"] = start_hour.map(rough_period)
    out["是否跨日"] = (out["充电开始时间"].dt.date != out["充电结束时间"].dt.date).map({True: "是", False: "否"})
    out["充电时长(min)"] = ((out["充电结束时间"] - out["充电开始时间"]).dt.total_seconds() / 60).round(3)
    out["充电时长分布"] = out["充电时长(min)"].map(duration_bucket)
    out["交易电量(kWh)（清洗后）"] = raw_kwh.where(raw_kwh.notna(), raw_meter_kwh).loc[main_order_mask].round(3)
    out["电量分布"] = out["交易电量(kWh)（清洗后）"].map(energy_bucket)
    allocated_energy = allocate_time_of_use_energy(
        out["充电开始时间"],
        out["充电结束时间"],
        out["交易电量(kWh)（清洗后）"],
    )
    out["峰电量(kWh)"] = allocated_energy["peak"].round(3)
    out["平电量(kWh)"] = allocated_energy["flat"].round(3)
    out["谷电量(kWh)"] = allocated_energy["valley"].round(3)
    out["谷段电量占比"] = (out["谷电量(kWh)"].where(out["交易电量(kWh)（清洗后）"] > 0, pd.NA) / out["交易电量(kWh)（清洗后）"].where(out["交易电量(kWh)（清洗后）"] > 0, pd.NA)).round(3)
    out["实扣电费"] = to_number(out["A_EF"]).fillna(0).round(2)
    out["实扣服务费"] = to_number(out["A_SF"]).fillna(0).round(2)
    out["实扣金额"] = (out["实扣电费"] + out["实扣服务费"]).round(2)
    nominal_amount = to_number(out["EF"]).fillna(0) + to_number(out["SF"]).fillna(0)
    coupon_discount = to_number(out["coupon_discount"]).fillna(0)
    promotion_discount = to_number(out["promotion_discount"]).fillna(0)
    other_discount = to_number(out["other_discount"]).fillna(0)
    discount_total = pd.concat([coupon_discount + promotion_discount + other_discount, (nominal_amount - out["实扣金额"]).clip(lower=0)], axis=1).max(axis=1)
    out["优惠金额"] = discount_total.round(2)
    out["是否使用优惠"] = (discount_total > 0).map({True: "是", False: "否"})
    out["单度价格"] = (out["实扣金额"].where(out["交易电量(kWh)（清洗后）"] > 0, pd.NA) / out["交易电量(kWh)（清洗后）"].where(out["交易电量(kWh)（清洗后）"] > 0, pd.NA)).round(3)
    out["平均充电功率(kW)"] = (out["交易电量(kWh)（清洗后）"].where(out["充电时长(min)"] > 0, pd.NA) / (out["充电时长(min)"].where(out["充电时长(min)"] > 0, pd.NA) / 60)).round(3)
    out["结束原因分类"] = out["end_reason"].map(classify_end_reason)
    out["原始结束原因"] = out["end_reason"]

    out["数据判定"] = raw_data_judge.loc[main_order_mask].to_numpy()
    out["数据判定原因"] = raw_data_judge_reason.loc[main_order_mask].to_numpy()
    out["异常原因"] = ""
    discard = df.loc[~main_order_mask].copy()
    discard["交易流水号"] = raw_order_id.loc[~main_order_mask]
    discard["数据判定"] = raw_data_judge.loc[~main_order_mask].to_numpy()
    discard["数据判定原因"] = raw_data_judge_reason.loc[~main_order_mask].to_numpy()
    discard["异常原因"] = discard["数据判定原因"]

    station, bridge = load_station_info()
    if not station.empty and "站点类型" in station.columns:
        resolved_station_id = resolve_station_id(out["充电站ID"], station.index, bridge)
        out["场站类别"] = resolved_station_id.map(station["站点类型"]).map(station_type_to_category).fillna("其他")
        station_name_category_map = build_station_name_category_map(station)
        if station_name_category_map:
            name_category = out["充电站名称"].map(normalize_text).map(station_name_category_map).fillna("其他")
            out["场站类别"] = out["场站类别"].where(
                out["场站类别"].ne("其他") | name_category.eq("其他"),
                name_category,
            )
        if "建设场所" in station.columns:
            out["建设场所"] = resolved_station_id.map(station["建设场所"])
        else:
            out["建设场所"] = pd.NA
    else:
        out["场站类别"] = "其他"
        out["建设场所"] = pd.NA
    discard["场站类别"] = pd.NA
    discard["建设场所"] = pd.NA

    out["_row_id"] = out.index
    main_order = out.copy()
    order_for_seq = main_order.sort_values(["用户编码", "充电开始时间", "交易流水号"], kind="mergesort").copy()
    order_for_seq["用户年内订单序号"] = order_for_seq.groupby("用户编码", dropna=False).cumcount() + 1
    order_for_seq["用户月内订单序号"] = order_for_seq.groupby(["用户编码", "月份"], dropna=False).cumcount() + 1
    prev_start = order_for_seq.groupby("用户编码", dropna=False)["充电开始时间"].shift(1)
    prev_station = order_for_seq.groupby("用户编码", dropna=False)["充电站ID"].shift(1)
    order_for_seq["距上次充电间隔(h)"] = ((order_for_seq["充电开始时间"] - prev_start).dt.total_seconds() / 3600).round(3)
    order_for_seq["充电间隔分布"] = order_for_seq["距上次充电间隔(h)"].map(interval_bucket)
    order_for_seq["是否首次充电"] = order_for_seq["用户年内订单序号"].eq(1).map({True: "是", False: "否"})
    same_station = order_for_seq["充电站ID"].eq(prev_station)
    has_previous_order = order_for_seq["用户年内订单序号"].gt(1)
    comparable_station = has_previous_order & order_for_seq["充电站ID"].notna() & prev_station.notna()
    order_for_seq["是否复用上次站点"] = "首次充电"
    order_for_seq.loc[has_previous_order & ~comparable_station, "是否复用上次站点"] = "站点缺失"
    order_for_seq.loc[comparable_station, "是否复用上次站点"] = same_station.loc[comparable_station].map({True: "是", False: "否"})

    behavior_cols = [
        "_row_id",
        "交易流水号",
        "用户年内订单序号",
        "用户月内订单序号",
        "距上次充电间隔(h)",
        "充电间隔分布",
        "是否首次充电",
        "是否复用上次站点",
    ]
    out = out.merge(order_for_seq[behavior_cols], on="_row_id", how="left")
    out = out.drop(columns=["_row_id"])
    report_progress(min(total_rows, int(total_rows * 55 / 100)), "生成用户行为特征")

    out["是否有效行为订单"] = "是"

    cleaned_candidate = out.copy()
    cleaned, merge_stats = merge_short_restart_orders(cleaned_candidate)
    report_progress(min(total_rows, int(total_rows * 80 / 100)), "合并短时重启")

    cleaned = cleaned.sort_values(["用户编码", "充电开始时间", "交易流水号"], kind="mergesort").copy()
    cleaned["月份"] = cleaned["充电开始时间"].dt.month.astype("Int64")
    cleaned["用户年内订单序号"] = cleaned.groupby("用户编码", dropna=False).cumcount() + 1
    cleaned["用户月内订单序号"] = cleaned.groupby(["用户编码", "月份"], dropna=False).cumcount() + 1
    prev_start = cleaned.groupby("用户编码", dropna=False)["充电开始时间"].shift(1)
    prev_station = cleaned.groupby("用户编码", dropna=False)["充电站ID"].shift(1)
    cleaned["距上次充电间隔(h)"] = ((cleaned["充电开始时间"] - prev_start).dt.total_seconds() / 3600).round(3)
    cleaned["充电间隔分布"] = cleaned["距上次充电间隔(h)"].map(interval_bucket)
    cleaned["是否首次充电"] = cleaned["用户年内订单序号"].eq(1).map({True: "是", False: "否"})
    same_station = cleaned["充电站ID"].eq(prev_station)
    has_previous_order = cleaned["用户年内订单序号"].gt(1)
    comparable_station = has_previous_order & cleaned["充电站ID"].notna() & prev_station.notna()
    cleaned["是否复用上次站点"] = "首次充电"
    cleaned.loc[has_previous_order & ~comparable_station, "是否复用上次站点"] = "站点缺失"
    cleaned.loc[comparable_station, "是否复用上次站点"] = same_station.loc[comparable_station].map({True: "是", False: "否"}).to_numpy()
    cleaned["是否有效行为订单"] = "是"
    cleaned["场站类别"] = cleaned["场站类别"].where(cleaned["场站类别"].isin(STATION_CATEGORY_ORDER), "其他")
    out["场站类别"] = out["场站类别"].where(out["场站类别"].isin(STATION_CATEGORY_ORDER), "其他")

    export_order = [
        "交易流水号",
        "用户编码",
        "用户识别主键",
        "订单状态",
        "充电方式",
        "订单渠道",
        "订单来源",
        "用户类型",
        "充电站ID",
        "充电站名称",
        "场站类别",
        "建设场所",
        "充电桩编号",
        "充电开始时间",
        "充电结束时间",
        "充电日期",
        "星期",
        "充电粗略时段",
        "是否跨日",
        "充电时长(min)",
        "充电时长分布",
        "交易电量(kWh)（清洗后）",
        "电量分布",
        "峰电量(kWh)",
        "平电量(kWh)",
        "谷电量(kWh)",
        "谷段电量占比",
        "实扣电费",
        "实扣服务费",
        "实扣金额",
        "优惠金额",
        "是否使用优惠",
        "单度价格",
        "平均充电功率(kW)",
        "结束原因分类",
        "原始结束原因",
        "用户年内订单序号",
        "用户月内订单序号",
        "距上次充电间隔(h)",
        "充电间隔分布",
        "是否首次充电",
        "是否复用上次站点",
        "原始订单数",
        "是否短时重启合并",
        "异常原因",
        "是否有效行为订单",
    ]
    for col in export_order:
        if col not in cleaned.columns:
            cleaned[col] = pd.NA
    cleaned = cleaned[export_order]
    abnormal = out[
        out["结束原因分类"].isin(["故障异常", "其他原因"])
    ].copy()

    overview = pd.DataFrame(
        [
            {"指标": "原始订单数", "数值": len(raw)},
            {"指标": "时间范围内订单数", "数值": len(df)},
            {"指标": "废弃规则后保留订单数", "数值": int(main_order_mask.sum())},
            {"指标": "主表保留合并后会话数", "数值": len(cleaned)},
            {"指标": "废弃记录数", "数值": len(discard)},
            {"指标": "异常记录数", "数值": len(abnormal)},
            {"指标": "正常表零电量订单数", "数值": int((cleaned["交易电量(kWh)（清洗后）"] <= 0).sum()) if "交易电量(kWh)（清洗后）" in cleaned.columns else 0},
            {"指标": "废弃表零电量订单数", "数值": int((to_number(raw_kwh.loc[~main_order_mask]).fillna(0) <= 0).sum())},
            {"指标": "有效行为订单数", "数值": int(cleaned["是否有效行为订单"].eq("是").sum())},
            {"指标": "短时重启合并前订单数", "数值": int(merge_stats.loc[merge_stats["指标"] == "短时重启合并前订单数", "数值"].iloc[0]) if not merge_stats.empty else 0},
            {"指标": "短时重启合并后会话数", "数值": int(merge_stats.loc[merge_stats["指标"] == "短时重启合并后会话数", "数值"].iloc[0]) if not merge_stats.empty else 0},
            {"指标": "短时重启合并减少订单数", "数值": int(merge_stats.loc[merge_stats["指标"] == "短时重启合并减少订单数", "数值"].iloc[0]) if not merge_stats.empty else 0},
            {"指标": "短时重启合并组数", "数值": int(merge_stats.loc[merge_stats["指标"] == "短时重启合并组数", "数值"].iloc[0]) if not merge_stats.empty else 0},
            {"指标": "用户数", "数值": int(cleaned["用户编码"].nunique(dropna=True))},
            {"指标": "站点数", "数值": int(cleaned["充电站ID"].nunique(dropna=True))},
            {"指标": "场站类别数", "数值": int(cleaned["场站类别"].nunique(dropna=True))},
        ]
    )
    field_rules = pd.DataFrame(
        [
            {"字段": "用户识别主键", "处理": "本表无 VIN，使用 user_id 作为用户识别主键。"},
            {"字段": "时间字段", "处理": "统一解析 start_time/end_time，派生星期、粗略时段、跨日、充电时长；月份和开始小时仅用于内部计算，不导出。"},
            {"字段": "场站类别", "处理": "按 data/资产表.xlsx 的 UUID 工作表与站ID工作表联动：订单充电站ID 先匹配 UUID.站uuid 得到站编码，再匹配 站ID.stationid，最终按站点类型归并为高速、公共场站、专用场站和其他。"},
            {"字段": "建设场所", "处理": "随站点属性一并从资产表站ID工作表带出，用于保留场站开放属性原始标记。"},
            {"字段": "费用字段", "处理": "实扣金额=实扣电费+实扣服务费；优惠金额取显式优惠与账面差额的较大值。"},
            {"字段": "分时电量", "处理": "按 2025-07-01 前后分时规则，根据订单起止时间与峰/平/谷时段重叠比例重新分摊电量，并计算谷段电量占比。"},
            {"字段": "用户行为特征", "处理": "按用户和开始时间排序，生成年内序号、月内序号、距上次充电间隔、是否复用上次站点；首单标为“首次充电”，站点缺失时标为“站点缺失”，不留空白。"},
            {"字段": "主表筛选", "处理": "字段统一和基础数值解析后，按 BI 优化 SQL 的数据判定口径筛选；判定为 0 的订单进入有效主表并继续后续处理，非 0 的订单进入废弃表，不参与场站映射、行为统计、短时重启合并和分析；时间缺失或超出 2020-01-01 至 2026-07-31 23:59:59 范围的订单也并入废弃表；故障异常/其他原因订单另从有效主表复制到异常表。"},
        ]
    )
    missing_stats = pd.DataFrame(
        [
            {
                "字段": col,
                "缺失数": int(cleaned[col].isna().sum()),
                "缺失率": round(float(cleaned[col].isna().mean()), 4),
                "唯一值数": int(cleaned[col].nunique(dropna=True)),
            }
            for col in cleaned.columns
        ]
    )
    high_frequency_chart: Path | None = None
    if generate_charts and False:
        high_frequency_chart = save_high_frequency_exclusion_chart(
            before_count=int(main_order_mask.sum()),
            after_count=int(len(cleaned_candidate)),
            threshold=0,
            covered_days=0,
            path=OUTPUT_DIR / "analysis" / "images" / "cleaning" / f"orders_{year_label}_high_frequency_exclusion_comparison.svg",
        )

    station_category_distribution = build_station_category_distribution(out[out["数据判定"].eq(0)].copy())
    report_progress(total_rows, "完成")
    return cleaned, abnormal, discard, overview, pd.concat([field_rules, missing_stats], ignore_index=True), high_frequency_chart, station_category_distribution


def write_report(
    cleaned: pd.DataFrame,
    abnormal: pd.DataFrame,
    overview: pd.DataFrame,
    station_category_distribution: pd.DataFrame,
    station_category_chart: Path,
    high_frequency_compare_chart: Path,
    cleaned_path: Path,
    abnormal_path: Path,
    log_path: Path,
    report_path: Path,
    dataset_path: Path,
    dataset_label: str,
    year_label: str,
) -> Path:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    valid = cleaned[cleaned["是否有效行为订单"] == "是"]
    top_period = valid["充电粗略时段"].value_counts().idxmax() if not valid.empty else "无"
    top_channel = valid["订单渠道"].value_counts().idxmax() if not valid.empty else "无"
    median_duration = valid["充电时长(min)"].median() if not valid.empty else 0
    avg_kwh = valid["交易电量(kWh)（清洗后）"].mean() if not valid.empty else 0
    repeat_base = valid[valid["是否复用上次站点"].isin(["是", "否"])] if "是否复用上次站点" in valid else valid.iloc[0:0]
    repeat_station_rate = repeat_base["是否复用上次站点"].eq("是").mean() if not repeat_base.empty else 0
    input_rel = path_for_markdown(dataset_path)
    lines = [
        "## 输入与输出",
        f"- 输入文件：`{input_rel}`",
        f"- 清洗后主表：`{path_for_markdown(cleaned_path)}`",
        f"- 异常订单表：`{path_for_markdown(abnormal_path)}`",
        f"- 清洗日志：`{path_for_markdown(log_path)}`",
        "",
        "## 清洗过程概述",
        "1. 本表没有 VIN 字段，因此以 `user_id` 作为用户识别主键。",
        "2. 字段统一和基础数值解析后，按 BI 优化 SQL 的数据判定规则筛选有效订单；判定为 0 的订单进入主表，非 0 的订单进入废弃表，时间缺失或早于 2020 年、晚于 2026-07-31 23:59:59 的订单也并入废弃表，随后只对主表合并短时重启订单。",
        "3. 电量、金额、优惠按业务含义清洗；分时电量按订单起止时间与峰/平/谷时段重叠比例重新分摊，分类字段缺失填“未知”。",
        "4. 派生充电时长、粗略时段、星期、单度价格、平均功率、谷段电量占比等订单级特征；清洗结果不再导出月份、开始小时和小时区间。",
        "5. 场站类别采用资产表 UUID->站编码->站ID 的统一映射口径，并同步输出建设场所字段；最终只保留高速、公共场站、专用场站和其他四类。",
        "6. 按用户时间顺序派生用户年内订单序号、月内订单序号、距上次充电间隔、是否复用上次站点；首单标为“首次充电”，不再留空。",
        "7. 清洗后主表和异常订单表输出为 CSV，以减少大表写入耗时；清洗日志仍输出为 Excel，便于查看字段规则和缺失统计。",
        "",
        "## 场站类别划分规则",
        "| 场站类别 | 判定规则 |",
        "| --- | --- |",
        "| 高速 | 以资产表站点类型为准：高速 |",
        "| 公共场站 | 以资产表站点类型为准：城市公共、联行社会 |",
        "| 专用场站 | 以资产表站点类型为准：单位内部(专用)、单位内部、专用、公交 |",
        "| 其他 | 资产表站点类型为空、为 0、为其他或无法映射到四类时兜底 |",
        "",
        "## 场站订单分布",
        dataframe_to_markdown(station_category_distribution),
        "",
        f"![场站订单分布]({path_for_markdown(station_category_chart)})",
        "",
        "## 输出规模",
        dataframe_to_markdown(overview),
        "",
        "## 行为模式初步结论",
        f"- 有效行为订单的主要充电时段：{top_period}",
        f"- 有效行为订单的主要入口渠道：{top_channel}",
        f"- 有效行为订单充电时长中位数：{median_duration:.1f} min",
        f"- 有效行为订单平均电量：{avg_kwh:.2f} kWh",
        f"- 与上一次充电使用同一站点的比例（仅统计有上次站点可比较的订单）：{repeat_station_rate:.1%}",
        "",
        "## 运行命令",
        "```powershell",
        "cd D:\\EC-project",
        f".\\.venv\\Scripts\\python.exe scripts\\cleaning\\clean_2026_orders.py --dataset {dataset_label}",
        f".\\.venv\\Scripts\\python.exe scripts\\cleaning\\clean_2026_orders.py --list-datasets",
        "# 出图：scripts\\analysis\\analyze.py（由总控 scripts\\run_mysql_full_pipeline.py 第 4 步调用）",
        "```",
    ]
    return update_summary_section(
        report_path,
        f"orders_{year_label}_cleaning",
        f"{year_label}年订单清洗",
        lines,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清洗 data 目录下形如 XX年.xlsx 的年度订单数据。")
    parser.add_argument("--dataset", default="26年", help="数据集名称，例如 26、26年、26年.xlsx 或 2026。默认：26年")
    parser.add_argument("--file", help="直接指定单个 Excel 文件名或路径。")
    parser.add_argument("--list-datasets", action="store_true", help="列出 data 目录下可用的 XX年.xlsx 数据集后退出。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_datasets:
        datasets = list_year_datasets()
        if not datasets:
            print("data 目录下暂未找到 XX年.xlsx 数据集。")
            return
        print("可用年度订单数据集：")
        for path in datasets:
            print(f"- {path.name}")
        return

    if args.file:
        data_file, dataset_label, year_label, output_name = resolve_input_file(args.file)
    else:
        data_file, dataset_label, year_label, output_name = resolve_year_dataset(args.dataset)
    outputs = build_output_paths(output_name)
    print(f"开始读取: {data_file}", flush=True)
    raw = pd.read_excel(
        data_file,
        sheet_name=0,
    )
    print(f"读取完成: {len(raw):,} 行", flush=True)

    # 60-column format detection and mapping
    # If the file is 60-column standard export, it uses slightly different names than the project's internal 26-column standard.
    mapping_60 = {k: v for k, v in EXTRA_FIELD_MAPPING_60.items() if k in raw.columns}
    if mapping_60:
        print(f"[Mapping] 60-column format detected, renaming {len(mapping_60)} columns.")
        raw = raw.rename(columns=mapping_60)

    # Ensure critical internal columns exist to avoid KeyError later
    critical_cols = [
        "order_id", "charger_id", "station_id", "user_id", "start_time", "end_time", 
        "kwh", "meter_kwh", "A_EF", "A_SF", "EF", "SF", 
        "coupon_discount", "promotion_discount", "other_discount",
        "peak_kwh", "flat_kwh", "valley_kwh", "vin"
    ]
    for col in critical_cols:
        if col not in raw.columns:
            raw[col] = pd.NA

    print("开始清洗", flush=True)
    cleaned, abnormal, discard, overview, log_detail, high_frequency_chart, station_category_distribution = build_cleaned_orders(raw, year_label)
    print(
        f"清洗完成: 主表 {len(cleaned):,} 行, 异常 {len(abnormal):,} 行, 废弃 {len(discard):,} 行",
        flush=True,
    )
    station_category_chart = save_station_category_chart(station_category_distribution, outputs["station_category_chart"])
    cleaned_path = safe_write_csv(outputs["cleaned"], cleaned)
    abnormal_path = safe_write_csv(outputs["abnormal"], abnormal)
    discard_path = safe_write_csv(outputs["discard"], discard)
    log_path = safe_write_excel(outputs["log"], {"清洗概览": overview, "字段规则与缺失统计": log_detail})
    report_path = write_report(
        cleaned,
        abnormal,
        overview,
        station_category_distribution,
        station_category_chart,
        high_frequency_chart,
        cleaned_path,
        abnormal_path,
        log_path,
        outputs["report"],
        data_file,
        dataset_label,
        year_label,
    )
    print(f"{year_label}年订单清洗完成")
    print(f"清洗后主表: {cleaned_path}")
    print(f"异常订单表: {abnormal_path}")
    print(f"废弃订单表: {discard_path}")
    print(f"清洗日志: {log_path}")
    print(f"清洗说明: {report_path}")
    print(overview.to_string(index=False))


if __name__ == "__main__":
    main()

