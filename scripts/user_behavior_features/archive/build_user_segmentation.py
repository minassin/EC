"""按最终口径把主表用户落成 11 类互斥分群（命中即停）。

判定链（序号即优先级）：
    01 新用户
    02 回流非高价值活跃用户（NHV）
    03/04/05 滑窗高价值活跃三态（稳定 / 回流 / 历史已退出）
    06 新近低频 → 07 高价值沉默风险 → 08 高频低价值 → 09 低频低价值 → 11 一般用户
第 10 类「沉默高价值活跃」不在主表，单独出名单。

输入（<label> = outputs/features/archive/<label>/）：
    user_behavior_rfm_segments.csv                 主表（307,950）+ R/F/M 分数 + 附加条件原料
    sliding_windows/high_value_active_periods.csv  四态高价值活跃轨迹
    recent_user_segments/new_users_7d.csv
    recent_user_segments/returning_users_7d.csv
    recent_user_segments/recent_abnormal_14d.csv

输出（<label>/segments/）：
    user_segments.csv                       主表逐户落桶结果（含附加条件与回显列）
    user_segments_silent_high_value.csv     第 10 类单独名单
    user_segments_overview.csv              人数分布
    user_segments_checks.csv                校验断言结果
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = Path(os.environ.get("EC_OUTPUT_ROOT", PROJECT_ROOT / "outputs"))
ARCHIVE_FEATURE_ROOT = OUTPUT_ROOT / "features" / "archive"

DEFAULT_LABEL = "mysql_run"
MAIN_TABLE_FILE = "user_behavior_rfm_segments.csv"

BUCKET_NEW = "新用户"
BUCKET_NHV = "回流非高价值活跃用户（NHV）"
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
STATE_EXITED = "历史高价值活跃（当前已退出）"
STATE_SILENT = "沉默高价值活跃"

COUPON_MIN_ORDERS = 3
COUPON_HIGH = 0.5
COUPON_MID = 0.2
VALLEY_MIN_ORDERS = 3
VALLEY_HIGH = 0.5
VALLEY_MID = 0.25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把主表用户落成 11 类互斥分群，并输出校验断言。")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="输出批次名，默认 mysql_run。")
    parser.add_argument("--rfm-file", type=Path, help="主表文件，默认 <label>/user_behavior_rfm_segments.csv。")
    parser.add_argument("--output-dir", type=Path, help="输出目录，默认 <label>/segments。")
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

    hv_state = keys.map(hv_state_map).fillna("")
    # 主表 = 近 90 天有有效订单；滑窗最后一个窗口比主表窗口早一天（滑窗 90 天 = 结束日-89），
    # 于是"末次充电恰好落在主表窗口首日"的极少数用户会被滑窗判为沉默。他们仍在主表内、近期有充电，
    # 按「历史高价值活跃（当前已退出）」处理，避免与第 10 类沉默名单重复计数。
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
            "站点偏好类型": main_df.get("站点偏好类型", pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip(),
            "时段偏好类型": main_df.get("时段偏好类型", pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip(),
            "异常风险等级": main_df.get("异常风险等级", pd.Series("", index=main_df.index)).fillna("").astype(str).str.strip(),
        }
    )

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
    silent_out.to_csv(out_dir / "user_segments_silent_high_value.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    overview.to_csv(out_dir / "user_segments_overview.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
    check_df.to_csv(out_dir / "user_segments_checks.csv", index=False, encoding="utf-8-sig", lineterminator="\n")

    print(overview.to_string(index=False))
    print()
    print(check_df.to_string(index=False))
    print()
    print(f"输出目录：{out_dir}")
    if check_df["结果"].eq("未通过").any():
        sys.exit(1)


if __name__ == "__main__":
    main()
