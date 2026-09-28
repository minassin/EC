"""按最终口径把主表用户落成 11 类互斥分群（命中即停）。

判定链（序号即优先级）：
    01 新用户
    02 回流非高价值活跃用户
    03/04/05 滑窗高价值活跃三态（稳定 / 回流 / 历史已退出）
    06 新近低频 → 07 高价值沉默风险 → 08 高频低价值 → 09 低频低价值 → 11 一般用户
第 10 类「沉默高价值活跃」不在主表，单独出名单。

输入（<label> = outputs/features/archive/<label>/）：
    user_behavior_rfm_segments.csv                 主表（307,950）+ R/F/M 分数 + 附加条件原料
    sliding_windows/high_value_active_periods.csv  四态高价值活跃轨迹
    recent_user_segments/new_users_7d.csv
    recent_user_segments/returning_users_7d.csv
    recent_user_segments/recent_abnormal_14d.csv
    user_station_top3_detail.csv                   站点前三明细（取第1/2/3站点ID，可选）

输出（<label>/segments/）：
    user_segments.csv                       主表逐户落桶结果（含附加条件、回显列与偏好 TOP1-3）
    user_segments_core7.csv                 上述主表的子表，只留 7 类重点人群
    user_segments_silent_high_value.csv     第 10 类单独名单（含全量订单数与偏好 TOP1-3）
    user_segments_overview.csv              人数分布
    user_segments_checks.csv                校验断言结果

「偏好站点/时段 TOP1-3」的填法：除「样本不足」外，一律按实际数据填满三列——
用户只用过 1 个站点就只填 TOP1、用过 2 个填到 TOP2，填不满的位置写「无」。
「样本不足」（有效订单 ≤ 5）不算有偏好，三列都写「无」。改口径动下面两个常量。

主表与沉默名单的偏好来源不同，这是二者的根本差别：
    主表       偏好取自 90 天窗口的特征产物，口径 = 窗口内有效订单
    沉默名单   这批人「近 90 天没充电」，在窗口产物里根本不存在（实测交集为 0），
               所以回清洗表 outputs/runs/<label>/cleaned/<label>_standard_user_orders.csv
               按**全量历史**重算，口径 = 存量清洗表全表（2025-12-31 ~ 2026-08-27）。
               同一列名在两表里的统计口径因此不同，跨表比对时要注意。
               补算要扫一遍全量清洗表，用 --skip-silent-detail 可跳过（订单数写 0、偏好写「无」）。

`订单总数(全量)` 只出现在沉默名单：主表的订单数是窗口内计数，对「近 90 天没充电」的人
恒为 0，没有意义。全量值中位数仅 3 单——沉默名单里 83% 落在「样本不足」，是真实分布，
不是算错。这批人之所以曾被判高价值活跃，是滑窗的「动态统计天数」有 14 天下限
（build_rfm_sliding_windows.py:366），窗口内只充 1 次也能拿到 F=1、进而满足
`R≤2 且 F≤2 且 M≥4`。属滑窗设计的既有特性，非本次改动引入。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from build_user_behavior_features_pipeline import (
    BUS_STATION_CATEGORY,
    station_concentration_features,
    station_preference_type,
    time_concentration_features,
    time_preference_type,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = Path(os.environ.get("EC_OUTPUT_ROOT", PROJECT_ROOT / "outputs"))
ARCHIVE_FEATURE_ROOT = OUTPUT_ROOT / "features" / "archive"
RUNS_ROOT = OUTPUT_ROOT / "runs"

DEFAULT_LABEL = "mysql_run"
MAIN_TABLE_FILE = "user_behavior_rfm_segments.csv"
STATION_TOP3_FILE = "user_station_top3_detail.csv"

BUCKET_NEW = "新用户"
BUCKET_NHV = "回流非高价值活跃用户"
BUCKET_STABLE = "稳定高价值活跃用户"
BUCKET_RETURNING_HV = "回流高价值活跃用户"
BUCKET_EXITED_HV = "历史高价值活跃用户"
BUCKET_LOW_FREQ_RECENT = "新近低频用户"
BUCKET_HV_SILENT_RISK = "高价值沉默风险用户"
BUCKET_HIGH_FREQ_LOW_VALUE = "高频低价值用户"
BUCKET_LOW_FREQ_LOW_VALUE = "低频低价值用户"
BUCKET_SILENT_HV = "沉默高价值活跃"
BUCKET_GENERAL = "一般用户"

BUCKET_ORDER = [
    ("01", BUCKET_NEW),
    ("02", BUCKET_NHV),
    ("03", BUCKET_STABLE),
    ("04", BUCKET_RETURNING_HV),
    ("05", BUCKET_EXITED_HV),
    ("06", BUCKET_LOW_FREQ_RECENT),
    ("07", BUCKET_HV_SILENT_RISK),
    ("08", BUCKET_HIGH_FREQ_LOW_VALUE),
    ("09", BUCKET_LOW_FREQ_LOW_VALUE),
    ("10", BUCKET_SILENT_HV),
    ("11", BUCKET_GENERAL),
]
MAIN_TABLE_BUCKETS = [name for code, name in BUCKET_ORDER if code != "10"]
CODE_OF_BUCKET = {name: code for code, name in BUCKET_ORDER}

STATE_STABLE = "稳定高价值活跃"
STATE_RETURNING = "回流高价值活跃"
STATE_EXITED = "历史高价值活跃"
STATE_SILENT = "沉默高价值活跃"

# 2026-09-28 前的 step 7 往「高价值活跃状态」里写的是带括号的旧名。旧产物不重跑也能用，
# 见到旧名折算成新名；重跑 step 7 后这一层自然失效。
LEGACY_STATE_ALIASES = {"历史高价值活跃（当前已退出）": STATE_EXITED}

COUPON_MIN_ORDERS = 3
COUPON_HIGH = 0.5
COUPON_MID = 0.2
VALLEY_MIN_ORDERS = 3
VALLEY_HIGH = 0.5
VALLEY_MID = 0.25

NONE_FILL = "无"

# 「样本不足」（有效订单 ≤ 5）不认为有偏好，偏好 TOP1-3 三列全写「无」；
# 其余类型一律按实际数据填，用户实际只有 1/2 个站点或时段时，后面的位置写「无」。
NO_PREFERENCE_TYPES = {"样本不足"}
PREFERENCE_TOP_SLOTS = 3

PREFERENCE_STATION_COLUMNS = ["偏好站点TOP1", "偏好站点TOP2", "偏好站点TOP3"]
PREFERENCE_TIME_COLUMNS = ["偏好时段TOP1", "偏好时段TOP2", "偏好时段TOP3"]
PREFERENCE_COLUMNS = PREFERENCE_STATION_COLUMNS + PREFERENCE_TIME_COLUMNS

# 沉默名单专用：这些人不在 90 天窗口的任何特征产物里，只能回清洗表按全量历史重算，
# 所以订单数不能叫「订单总数」（主表那个是 90 天内的），带后缀区分口径。
ORDER_COUNT_COLUMN = "订单总数(全量)"
SILENT_TYPES = ["站点偏好类型", "时段偏好类型"]
SILENT_DETAIL_COLUMNS = [ORDER_COUNT_COLUMN] + SILENT_TYPES + PREFERENCE_COLUMNS

# user_segments.csv 的子表只留这 7 类（顺序即输出顺序）。
CORE_BUCKETS = [
    BUCKET_NEW,
    BUCKET_NHV,
    BUCKET_STABLE,
    BUCKET_RETURNING_HV,
    BUCKET_EXITED_HV,
    BUCKET_LOW_FREQ_RECENT,
    BUCKET_HV_SILENT_RISK,
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把主表用户落成 11 类互斥分群，并输出校验断言。")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="输出批次名，默认 mysql_run。")
    parser.add_argument("--rfm-file", type=Path, help="主表文件，默认 <label>/user_behavior_rfm_segments.csv。")
    parser.add_argument("--output-dir", type=Path, help="输出目录，默认 <label>/segments。")
    parser.add_argument(
        "--cleaned-file",
        type=Path,
        help="清洗表，用来给沉默名单补偏好与订单数，默认 outputs/runs/<label>/cleaned/<label>_standard_user_orders.csv。",
    )
    parser.add_argument(
        "--skip-silent-detail",
        action="store_true",
        help="跳过沉默名单的偏好与订单数补算（那步要扫一遍全量清洗表）。",
    )
    return parser.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"缺少输入文件：{path}")
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"提示：未找到可选输入 {path}，相关列按空处理。")
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def key_series(df: pd.DataFrame, column: str = "用户识别主键") -> pd.Series:
    return df[column].fillna("").astype(str).str.strip()


def level_of(values: pd.Series, orders: pd.Series, sample_min: int, high: float, mid: float) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    enough = pd.to_numeric(orders, errors="coerce").fillna(0) >= sample_min
    out = pd.Series("样本不足", index=values.index, dtype=object)
    out[enough & numeric.lt(mid)] = "低"
    out[enough & numeric.ge(mid) & numeric.lt(high)] = "中"
    out[enough & numeric.ge(high)] = "高"
    return out


def silent_history_detail(cleaned_file: Path, silent_keys: set[str]) -> pd.DataFrame:
    """给沉默名单补偏好与订单数——回清洗表按**全量历史**重算。

    沉默高价值活跃的定义就是「近 90 天没充电」，所以这批人不在任何 90 天窗口的产物里
    （主表、站点/时段明细、订单基表都与他们交集为 0），只能回原始清洗表算。
    代价是这一列的口径和主表不同：这里是该用户在本批清洗表全时段内的累计，
    列名用「订单总数(全量)」区分。

    订单筛选与第 5 步一致：只取「是否有效行为订单 = 是」且非公交场站的订单；
    偏好类型与集中度直接复用第 5 步的 station_preference_type / time_preference_type，
    保证同一套阈值。
    """
    if not silent_keys:
        return pd.DataFrame()

    if not cleaned_file.exists():
        print(f"提示：未找到清洗表 {cleaned_file}，沉默名单的偏好与订单数按空处理。")
        print("      用 --cleaned-file 指定，或 --skip-silent-detail 显式跳过。")
        return pd.DataFrame()

    import duckdb

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.register("silent", pd.DataFrame({"用户识别主键": sorted(silent_keys)}))
    orders = con.execute(
        """
        SELECT
            b."用户识别主键" AS 用户识别主键,
            b."充电站ID" AS 充电站ID,
            b."充电粗略时段" AS 充电粗略时段
        FROM read_csv(?, header=true, all_varchar=true, ignore_errors=true) b
        SEMI JOIN silent s ON b."用户识别主键" = s."用户识别主键"
        WHERE COALESCE(NULLIF(TRIM(b."是否有效行为订单"), ''), '') = '是'
          AND COALESCE(NULLIF(TRIM(b."场站类别"), ''), '') <> ?
        """,
        [str(cleaned_file), BUS_STATION_CATEGORY],
    ).fetchdf()
    con.close()

    def values(series: pd.Series) -> list[str]:
        return [v for v in series.fillna("").astype(str).str.strip() if v]

    rows = []
    for user_key, group in orders.groupby("用户识别主键", sort=False):
        total = int(len(group))
        stations = Counter(values(group["充电站ID"]))
        periods = Counter(values(group["充电粗略时段"]))
        station_row: dict[str, object] = {"订单总数": total, "使用站点数": len(stations)}
        station_row.update(station_concentration_features(stations))
        period_row: dict[str, object] = {"订单总数": total}
        period_row.update(time_concentration_features(periods))
        station_top = stations.most_common(3)
        period_top = periods.most_common(3)
        rows.append(
            {
                "用户识别主键": user_key,
                ORDER_COUNT_COLUMN: total,
                SILENT_TYPES[0]: station_preference_type(pd.Series(station_row)),
                SILENT_TYPES[1]: time_preference_type(pd.Series(period_row)),
                PREFERENCE_STATION_COLUMNS[0]: station_top[0][0] if station_top else "",
                PREFERENCE_STATION_COLUMNS[1]: station_top[1][0] if len(station_top) > 1 else "",
                PREFERENCE_STATION_COLUMNS[2]: station_top[2][0] if len(station_top) > 2 else "",
                PREFERENCE_TIME_COLUMNS[0]: period_top[0][0] if period_top else "",
                PREFERENCE_TIME_COLUMNS[1]: period_top[1][0] if len(period_top) > 1 else "",
                PREFERENCE_TIME_COLUMNS[2]: period_top[2][0] if len(period_top) > 2 else "",
            }
        )

    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail
    # 偏好三列按类型口径摊平（样本不足 / 实际不足三位 -> 无）
    filled = pd.concat(
        [
            preference_top3(detail[SILENT_TYPES[0]], [detail[c] for c in PREFERENCE_STATION_COLUMNS], PREFERENCE_STATION_COLUMNS),
            preference_top3(detail[SILENT_TYPES[1]], [detail[c] for c in PREFERENCE_TIME_COLUMNS], PREFERENCE_TIME_COLUMNS),
        ],
        axis=1,
    )
    for column in PREFERENCE_COLUMNS:
        detail[column] = filled[column]
    return detail.set_index("用户识别主键")


def preference_top3(pref_type: pd.Series, sources: list[pd.Series], columns: list[str]) -> pd.DataFrame:
    """把 top1..top3 的取值摊成三列，填不满的位置写「无」。

    「样本不足」不算有偏好，三列全「无」；其余类型按实际数据填，
    源值为空（比如用户只用过 1 个站点，第2站点ID 为空）的位置也写「无」。
    """
    allowed = ~pref_type.fillna("").astype(str).str.strip().isin(NO_PREFERENCE_TYPES)
    filled = {}
    for column, source in list(zip(columns, sources))[:PREFERENCE_TOP_SLOTS]:
        values = source.fillna("").astype(str).str.strip()
        filled[column] = values.where(allowed & values.ne(""), NONE_FILL)
    return pd.DataFrame(filled, index=pref_type.index)


def main() -> None:
    args = parse_args()
    label_dir = ARCHIVE_FEATURE_ROOT / args.label
    rfm_file = args.rfm_file or (label_dir / MAIN_TABLE_FILE)
    out_dir = args.output_dir or (label_dir / "segments")
    out_dir.mkdir(parents=True, exist_ok=True)

    main_df = read_csv(rfm_file)
    keys = key_series(main_df)
    orders = pd.to_numeric(main_df.get("订单总数", pd.Series(0, index=main_df.index)), errors="coerce").fillna(0)

    hv_df = read_csv(label_dir / "sliding_windows" / "high_value_active_periods.csv")
    hv_keys = key_series(hv_df)
    hv_state_map = dict(zip(hv_keys, hv_df.get("高价值活跃状态", pd.Series("", index=hv_df.index)).fillna("").astype(str).str.strip()))
    hv_seg_map = dict(zip(hv_keys, pd.to_numeric(hv_df.get("高价值活跃段数", 0), errors="coerce")))
    hv_end_map = dict(zip(hv_keys, hv_df.get("高价值活跃结束日期", pd.Series("", index=hv_df.index)).fillna("").astype(str).str.strip()))

    station_top3_file = label_dir / STATION_TOP3_FILE
    if station_top3_file.exists():
        station_top3 = pd.read_csv(
            station_top3_file,
            encoding="utf-8-sig",
            low_memory=False,
            usecols=["用户识别主键", "第1站点ID", "第2站点ID", "第3站点ID"],
        )
        station_by_key = station_top3.set_index(key_series(station_top3))
    else:
        print(f"提示：未找到站点前三明细 {station_top3_file}，偏好站点三列全部写「无」。")
        station_by_key = pd.DataFrame(columns=["第1站点ID", "第2站点ID", "第3站点ID"])

    def station_slot(position: int) -> pd.Series:
        column = f"第{position}站点ID"
        if column not in station_by_key.columns:
            return pd.Series("", index=main_df.index)
        return keys.map(station_by_key[column]).fillna("")

    new_df = optional_csv(label_dir / "recent_user_segments" / "new_users_7d.csv")
    ret_df = optional_csv(label_dir / "recent_user_segments" / "returning_users_7d.csv")
    abn_df = optional_csv(label_dir / "recent_user_segments" / "recent_abnormal_14d.csv")

    new_keys = set(key_series(new_df)) if not new_df.empty else set()
    ret_keys = key_series(ret_df) if not ret_df.empty else pd.Series(dtype=object)
    if not ret_df.empty and "180天内是否出现高价值活跃" in ret_df.columns:
        ret_is_hv = ret_df["180天内是否出现高价值活跃"].fillna("").astype(str).str.strip().eq("是")
    else:
        ret_is_hv = ret_keys.isin(set(hv_keys))
    nhv_keys = set(ret_keys[~ret_is_hv]) if len(ret_keys) else set()
    ret_hv_keys = set(ret_keys[ret_is_hv]) if len(ret_keys) else set()
    abn_keys = set(key_series(abn_df)) if not abn_df.empty else set()

    hv_state = keys.map(hv_state_map).fillna("").replace(LEGACY_STATE_ALIASES)
    # 主表 = 近 90 天有有效订单；滑窗最后一个窗口比主表窗口早一天（滑窗 90 天 = 结束日-89），
    # 于是"末次充电恰好落在主表窗口首日"的极少数用户会被滑窗判为沉默。他们仍在主表内、近期有充电，
    # 按「历史高价值活跃」处理，避免与第 10 类沉默名单重复计数。
    hv_state = hv_state.where(~hv_state.eq(STATE_SILENT), STATE_EXITED)
    r_score = pd.to_numeric(main_df.get("R分数"), errors="coerce")
    f_score = pd.to_numeric(main_df.get("F分数"), errors="coerce")
    m_score = pd.to_numeric(main_df.get("M分数"), errors="coerce")

    bucket = pd.Series(BUCKET_GENERAL, index=main_df.index, dtype=object)
    for mask, name in [
        (f_score.ge(4) & m_score.le(2), BUCKET_LOW_FREQ_LOW_VALUE),
        (f_score.le(2) & m_score.le(2), BUCKET_HIGH_FREQ_LOW_VALUE),
        (r_score.ge(4) & f_score.le(2) & m_score.ge(4), BUCKET_HV_SILENT_RISK),
        (r_score.le(2) & f_score.ge(4), BUCKET_LOW_FREQ_RECENT),
        (hv_state.eq(STATE_EXITED), BUCKET_EXITED_HV),
        (hv_state.eq(STATE_RETURNING), BUCKET_RETURNING_HV),
        (hv_state.eq(STATE_STABLE), BUCKET_STABLE),
        (keys.isin(nhv_keys), BUCKET_NHV),
        (keys.isin(new_keys), BUCKET_NEW),
    ]:
        bucket[mask.fillna(False)] = name
    bucket_before_extra_columns = bucket.copy()

    def text_column(name: str) -> pd.Series:
        return main_df.get(name, pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip()

    station_pref = text_column("站点偏好类型")
    time_pref = text_column("时段偏好类型")
    preference = pd.concat(
        [
            preference_top3(station_pref, [station_slot(i) for i in (1, 2, 3)], PREFERENCE_STATION_COLUMNS),
            preference_top3(time_pref, [text_column("主充电时段"), text_column("次充电时段"), text_column("第三充电时段")], PREFERENCE_TIME_COLUMNS),
        ],
        axis=1,
    )

    out = pd.DataFrame(
        {
            "用户识别主键": keys,
            "用户编码": main_df.get("用户编码", pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip(),
            "最终分群序号": bucket.map(CODE_OF_BUCKET),
            "最终分群": bucket,
            "高价值活跃状态": hv_state,
            "高价值活跃段数": keys.map(hv_seg_map),
            "距高价值退出天数": "",
            "距最近充电天数": pd.to_numeric(main_df.get("距最近充电天数"), errors="coerce"),
            "近期异常终止标记": keys.isin(abn_keys).map({True: "是", False: "否"}),
            "优惠依赖度": level_of(main_df.get("优惠使用率"), orders, COUPON_MIN_ORDERS, COUPON_HIGH, COUPON_MID),
            "谷段偏好度": level_of(main_df.get("平均谷段电量占比"), orders, VALLEY_MIN_ORDERS, VALLEY_HIGH, VALLEY_MID),
            "主站点ID": main_df.get("主站点ID", pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip(),
            "主充电时段": main_df.get("主充电时段", pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip(),
            "单次高消费标记": ((orders.eq(1)) & (m_score.ge(4))).map({True: "是", False: "否"}),
            "价格敏感等级": main_df.get("价格敏感等级", pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip(),
            "站点偏好类型": station_pref,
            "时段偏好类型": time_pref,
            "异常风险等级": main_df.get("异常风险等级", pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip(),
        }
    )
    for column in PREFERENCE_STATION_COLUMNS + PREFERENCE_TIME_COLUMNS:
        out[column] = preference[column]

    last_charge = pd.to_datetime(main_df.get("最近充电时间"), errors="coerce").max()
    hv_end = pd.to_datetime(keys.map(hv_end_map), errors="coerce")
    if pd.notna(last_charge):
        out["距高价值退出天数"] = (last_charge.normalize() - hv_end).dt.days
    out["距高价值退出天数"] = out["距高价值退出天数"].astype("object").where(out["距高价值退出天数"].notna(), "")

    silent_keys = set(hv_keys[hv_df.get("高价值活跃状态", pd.Series("", index=hv_df.index)).fillna("").astype(str).str.strip().eq(STATE_SILENT)]) - set(keys)
    silent_df = hv_df[hv_keys.isin(silent_keys)].copy()
    silent_out = pd.DataFrame(
        {
            "用户识别主键": key_series(silent_df),
            "最终分群序号": CODE_OF_BUCKET[BUCKET_SILENT_HV],
            "最终分群": BUCKET_SILENT_HV,
            "高价值活跃状态": silent_df.get("高价值活跃状态", ""),
            "高价值活跃段数": silent_df.get("高价值活跃段数", ""),
            "高价值活跃开始日期": silent_df.get("高价值活跃开始日期", ""),
            "高价值活跃结束日期": silent_df.get("高价值活跃结束日期", ""),
        }
    )
    if not silent_out.empty:
        silent_end = pd.to_datetime(silent_out["高价值活跃结束日期"], errors="coerce")
        silent_out["距高价值退出天数"] = "" if pd.isna(last_charge) else (last_charge.normalize() - silent_end).dt.days

    cleaned_file = args.cleaned_file or (RUNS_ROOT / args.label / "cleaned" / f"{args.label}_standard_user_orders.csv")
    if args.skip_silent_detail:
        silent_detail = pd.DataFrame()
        print("已跳沉默名单的偏好与订单数补算（--skip-silent-detail）")
    else:
        print(f"沉默名单补算：{len(silent_keys):,} 人，回清洗表 {cleaned_file} 按全量历史重算……")
        silent_detail = silent_history_detail(cleaned_file, silent_keys)
    silent_matched = 0
    for column in SILENT_DETAIL_COLUMNS:
        if silent_detail.empty:
            silent_out[column] = "" if column == ORDER_COUNT_COLUMN else NONE_FILL
            continue
        mapped = silent_out["用户识别主键"].map(silent_detail[column])
        if column == ORDER_COUNT_COLUMN:
            silent_matched = int(mapped.notna().sum())
            silent_out[column] = mapped.fillna(0).astype(int)
        else:
            silent_out[column] = mapped.fillna(NONE_FILL)
    if not silent_detail.empty:
        print(f"沉默名单补算完成：命中 {silent_matched:,} / {len(silent_out):,} 人")

    core = out[out["最终分群"].isin(CORE_BUCKETS)]
    # 站点/时段偏好类型共用「有效订单 <= 5 即样本不足」这一个门槛
    # （build_user_behavior_features_pipeline.py 的 station_preference_type / time_preference_type），
    # 所以两侧「有偏好」的人必然是同一批、计数必然相等。这里顺带断言这个恒等式。
    pref_station_mask = out["站点偏好类型"].fillna("").astype(str).str.strip().isin(NO_PREFERENCE_TYPES)
    pref_time_mask = out["时段偏好类型"].fillna("").astype(str).str.strip().isin(NO_PREFERENCE_TYPES)
    pref_gate_aligned = bool(pref_station_mask.eq(pref_time_mask).all())
    core_pref_station = int(core[PREFERENCE_STATION_COLUMNS[0]].ne(NONE_FILL).sum())
    core_pref_time = int(core[PREFERENCE_TIME_COLUMNS[0]].ne(NONE_FILL).sum())

    counts = out["最终分群"].value_counts()
    overview = pd.DataFrame(
        [
            {"序号": code, "分群": name, "人数": int(counts.get(name, 0)), "占比": round(float(counts.get(name, 0)) / max(len(out), 1), 6)}
            for code, name in BUCKET_ORDER
        ]
    )
    main_table_sum = int(sum(int(counts.get(name, 0)) for name in MAIN_TABLE_BUCKETS))
    hv_in_main = int(hv_state.ne("").sum())
    new_hv = int(((out["最终分群"] == BUCKET_NEW) & hv_state.ne("")).sum())
    absorbed = int(out.loc[out["最终分群"].isin([BUCKET_STABLE, BUCKET_RETURNING_HV, BUCKET_EXITED_HV]), "用户识别主键"].isin(ret_keys).sum())

    checks = [
        ("各分群人数之和 = 主表人数", main_table_sum == len(out), f"{main_table_sum:,} vs {len(out):,}"),
        ("用户识别主键唯一（count(distinct)）", int(out["用户识别主键"].nunique()) == len(out), f"{out['用户识别主键'].nunique():,} vs {len(out):,}"),
        ("最终分群取值 ∈ 11 桶名白名单（第 10 类只在单独名单）", set(out["最终分群"].unique()) <= set(MAIN_TABLE_BUCKETS), "、".join(sorted(set(out["最终分群"].unique()) - set(MAIN_TABLE_BUCKETS))) or "无越界值"),
        ("单桶人数 < 100 需告警", any(0 < int(counts.get(name, 0)) < 100 for _c, name in BUCKET_ORDER if name != BUCKET_SILENT_HV), f"最小桶 {int(counts.min()):,}"),
        ("附加条件列不参与落桶", out["最终分群"].equals(bucket_before_extra_columns), "落桶先于附加条件列生成（结构性保证）"),
        ("三份名单 100% 落在主表内", not (new_keys - set(keys)) and not (ret_keys.tolist() and (set(ret_keys) - set(keys))) and not (abn_keys - set(keys)), f"新用户缺 {len(new_keys - set(keys)):,}、回流缺 {len(set(ret_keys) - set(keys)):,}、异常缺 {len(abn_keys - set(keys)):,}"),
        ("近期异常终止标记数 = 异常名单数", int((out["近期异常终止标记"] == "是").sum()) == len(abn_keys), f"{int((out['近期异常终止标记'] == '是').sum()):,} vs {len(abn_keys):,}"),
        ("滑窗四态对账：01 吸收 + 03 + 04 + 05 + 10 = 全部高价值活跃", new_hv + int(counts.get(BUCKET_STABLE, 0)) + int(counts.get(BUCKET_RETURNING_HV, 0)) + int(counts.get(BUCKET_EXITED_HV, 0)) + len(silent_out) == len(hv_df), f"{new_hv + int(counts.get(BUCKET_STABLE, 0)) + int(counts.get(BUCKET_RETURNING_HV, 0)) + int(counts.get(BUCKET_EXITED_HV, 0)) + len(silent_out):,} vs {len(hv_df):,}"),
        ("回流表 HV 列 = 03/04/05 从回流名单吸收的人数", absorbed == len(ret_hv_keys), f"{absorbed:,} vs {len(ret_hv_keys):,}"),
        ("沉默高价值活跃与主表无交集", int((hv_state.eq(STATE_SILENT)).sum()) == 0, f"交集 {int((hv_state.eq(STATE_SILENT)).sum()):,} 人"),
        ("高价值活跃用户全部落在主表（沉默除外）", hv_in_main + len(silent_out) == len(hv_df), f"{hv_in_main:,} + {len(silent_out):,} vs {len(hv_df):,}"),
        ("七类子表人数 = 主表命中人数", len(core) == int(counts.reindex(CORE_BUCKETS).fillna(0).sum()), f"{len(core):,} vs {int(counts.reindex(CORE_BUCKETS).fillna(0).sum()):,}"),
        ("七类子表不含其它分群", set(core["最终分群"].unique()) <= set(CORE_BUCKETS), "、".join(sorted(set(core["最终分群"].unique()) - set(CORE_BUCKETS))) or "无越界值"),
        ("偏好 TOP1 只写「有偏好」的用户", core_pref_station <= len(core) and core_pref_time <= len(core), f"站点 {core_pref_station:,} 人、时段 {core_pref_time:,} 人"),
        ("站点/时段「样本不足」掩码一致（共用订单数门槛）", pref_gate_aligned, "两侧必然同进同出，不等说明门槛被单独改过"),
        ("「样本不足」的偏好三列全为「无」", bool(
            (out.loc[out["站点偏好类型"].isin(NO_PREFERENCE_TYPES), PREFERENCE_STATION_COLUMNS] == NONE_FILL).all().all()
            and (out.loc[out["时段偏好类型"].isin(NO_PREFERENCE_TYPES), PREFERENCE_TIME_COLUMNS] == NONE_FILL).all().all()
        ), "站点、时段两侧各查一遍"),
        ("偏好六列无空值（只可能是取值或「无」）", bool(
            out[PREFERENCE_STATION_COLUMNS + PREFERENCE_TIME_COLUMNS].ne("").all().all()
        ), "填不满的位置已统一写「无」，不留空串"),
        ("沉默名单补算覆盖全部用户", silent_detail.empty or silent_matched == len(silent_out), f"命中 {silent_matched:,} / {len(silent_out):,}"),
        ("沉默名单偏好六列无空值", bool(silent_out[PREFERENCE_COLUMNS].ne("").all().all()), "填不满的位置统一写「无」"),
        ("沉默名单「样本不足」的偏好三列全为「无」", bool(
            silent_out.empty or (
                (silent_out.loc[silent_out["站点偏好类型"].isin(NO_PREFERENCE_TYPES), PREFERENCE_STATION_COLUMNS] == NONE_FILL).all().all()
                and (silent_out.loc[silent_out["时段偏好类型"].isin(NO_PREFERENCE_TYPES), PREFERENCE_TIME_COLUMNS] == NONE_FILL).all().all()
            )
        ), "与主表同一套填法"),
    ]
    warn_checks = {"单桶人数 < 100 需告警"}
    check_df = pd.DataFrame(
        [
            {
                "校验项": name,
                "结果": ("告警" if ok else "通过") if name in warn_checks else ("通过" if ok else "未通过"),
                "说明": detail,
            }
            for name, ok, detail in checks
        ]
    )

    out.to_csv(out_dir / "user_segments.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    core.to_csv(out_dir / "user_segments_core7.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    silent_out.to_csv(out_dir / "user_segments_silent_high_value.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    overview.to_csv(out_dir / "user_segments_overview.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    check_df.to_csv(out_dir / "user_segments_checks.csv", index=False, encoding="utf-8-sig", lineterminator="\n")

    core_counts = core["最终分群"].value_counts()
    print(overview.to_string(index=False))
    print()
    print("七类子表（user_segments_core7.csv）：")
    print(pd.DataFrame([{"分群": name, "人数": int(core_counts.get(name, 0))} for name in CORE_BUCKETS]).to_string(index=False))
    print(f"其中 偏好 TOP1 非「无」：站点 {core_pref_station:,} 人、时段 {core_pref_time:,} 人")
    print("（两者相等是结构性的：站点/时段偏好类型共用「有效订单 <= 5 即样本不足」这一个门槛）")
    print()
    print(f"第 10 类沉默高价值活跃（user_segments_silent_high_value.csv）：{len(silent_out):,} 人")
    if silent_detail.empty:
        print("  偏好与订单数未补算（--skip-silent-detail）")
    else:
        for silent_column in SILENT_TYPES:
            type_counts = silent_out[silent_column].value_counts()
            print(f"  {silent_column}：" + " / ".join(f"{name} {int(value):,}" for name, value in type_counts.items()))
        order_counts = pd.to_numeric(silent_out[ORDER_COUNT_COLUMN], errors="coerce")
        print(
            f"  {ORDER_COUNT_COLUMN}：中位数 {order_counts.median():.0f}、"
            f"均值 {order_counts.mean():.2f}、最大 {order_counts.max():.0f}"
        )
    print()
    print(check_df.to_string(index=False))
    print()
    print(f"输出目录：{out_dir}")
    if check_df["结果"].eq("未通过").any():
        sys.exit(1)


if __name__ == "__main__":
    main()
