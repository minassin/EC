from __future__ import annotations

import argparse
from pathlib import Path
import os

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = Path(os.environ.get("EC_OUTPUT_ROOT", PROJECT_ROOT / "outputs"))
ARCHIVE_FEATURE_ROOT = OUTPUT_ROOT / "features" / "archive"

DEFAULT_LABEL = "mysql_run"
DEFAULT_HORIZON_DAYS = 180
DEFAULT_NEW_WINDOW_DAYS = 7
DEFAULT_RETURN_GAP_DAYS = 30
DEFAULT_ABNORMAL_WINDOW_DAYS = 14
BUS_STATION_CATEGORY = "公交场站"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="识别新用户、回流用户和近期异常终止用户。")
    parser.add_argument(
        "--cleaned-file",
        type=Path,
        default=Path(r"D:\EC-project\outputs\runs\mysql_run\cleaned\mysql_run_standard_user_orders.csv"),
        help="清洗后的有效订单 CSV",
    )
    parser.add_argument("--label", default=DEFAULT_LABEL, help="输出批次名")
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS, help="回看总跨度，默认 180 天")
    parser.add_argument("--new-window-days", type=int, default=DEFAULT_NEW_WINDOW_DAYS, help="新用户判定窗口，默认 7 天")
    parser.add_argument("--return-gap-days", type=int, default=DEFAULT_RETURN_GAP_DAYS, help="回流间隔阈值，默认 30 天")
    parser.add_argument("--abnormal-window-days", type=int, default=DEFAULT_ABNORMAL_WINDOW_DAYS, help="近期异常终止窗口，默认 14 天")
    return parser.parse_args()


def qid(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qstr(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pick_col(cols: list[str], candidates: list[str]) -> str | None:
    for name in candidates:
        if name in cols:
            return name
    return None


def col_expr(col: str | None, default: str = "''") -> str:
    if not col:
        return default
    return f"TRIM(COALESCE({qid(col)}, ''))"


def build_reason_expr(cols: list[str]) -> str:
    parts = [f"NULLIF(TRIM(COALESCE({qid(col)}, '')), '')" for col in cols]
    if not parts:
        return "''"
    return "COALESCE(" + ", ".join(parts) + ", '')"


def output_dir(label: str) -> Path:
    return ARCHIVE_FEATURE_ROOT / label / "recent_user_segments"


def load_high_value_users(label: str) -> dict[str, str]:
    """返回「180 天滑窗内出现过至少一段高价值活跃」的用户 → 高价值活跃状态。

    这是 02 与 03/04/05 的分水岭：命中即为「曾是高价值活跃」，会被滑窗三态吸收；
    未命中才是 02（回流非高价值活跃用户）。
    """
    path = ARCHIVE_FEATURE_ROOT / label / "sliding_windows" / "high_value_active_periods.csv"
    if path.exists():
        df = pd.read_csv(path, encoding="utf-8-sig")
        if not df.empty and "用户识别主键" in df.columns:
            state_col = "高价值活跃状态" if "高价值活跃状态" in df.columns else None
            result: dict[str, str] = {}
            for _, row in df.iterrows():
                user_id = str(row.get("用户识别主键", "")).strip()
                if not user_id:
                    continue
                result[user_id] = str(row.get(state_col, "")).strip() if state_col else ""
            return result
    fallback = load_high_value_map(label)
    return {user_id: "" for user_id in fallback}


def load_high_value_map(label: str) -> dict[str, list[tuple[str, str, str]]]:
    path = ARCHIVE_FEATURE_ROOT / label / "sliding_windows" / "user_rfm_type_periods.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty or "用户识别主键" not in df.columns:
        return {}
    result: dict[str, list[tuple[str, str, str]]] = {}
    type_col = "RFM类型"
    start_col = "阶段开始日期"
    end_col = "阶段结束日期"
    if type_col not in df.columns or start_col not in df.columns or end_col not in df.columns:
        return {}
    for _, row in df.iterrows():
        user_id = str(row.get("用户识别主键", "")).strip()
        if str(row.get(type_col, "")).strip() != "高价值活跃用户":
            continue
        if not user_id:
            continue
        result.setdefault(user_id, []).append(
            (
                str(row.get(start_col, "")).strip(),
                str(row.get(end_col, "")).strip(),
                str(row.get("是否高价值活跃阶段", "")).strip(),
            )
        )
    return result


def main() -> None:
    args = parse_args()
    cleaned_file = args.cleaned_file.expanduser().resolve()
    if not cleaned_file.exists():
        raise FileNotFoundError(cleaned_file)

    out_dir = output_dir(args.label)
    out_dir.mkdir(parents=True, exist_ok=True)

    header = pd.read_csv(cleaned_file, nrows=0, encoding="utf-8-sig")
    cols = list(header.columns)
    user_col = pick_col(cols, ["用户识别主键", "用户编码", "用户ID", "user_id", "交易流水号"])
    start_col = pick_col(cols, ["充电开始时间", "开始时间", "start_time"])
    if not user_col or not start_col:
        raise SystemExit("清洗文件缺少必要字段：用户列或充电开始时间列。")

    station_col = pick_col(cols, ["场站类别", "场站类型", "站点类别"])
    if station_col:
        bus_station_filter = f"AND {col_expr(station_col)} <> {qstr(BUS_STATION_CATEGORY)}"
        print(f"已按 {station_col} <> {BUS_STATION_CATEGORY} 对齐主表口径（名单与主表同源）。")
    else:
        bus_station_filter = ""
        print(f"警告：清洗文件未找到场站类别列，未剔除 {BUS_STATION_CATEGORY} 订单。")

    reason_candidates = [
        c
        for c in [
            pick_col(cols, ["异常原因"]),
            pick_col(cols, ["主要异常原因"]),
            pick_col(cols, ["异常结束原因"]),
            pick_col(cols, ["结束原因分类"]),
            pick_col(cols, ["结束原因"]),
        ]
        if c
    ]

    import duckdb

    con = duckdb.connect()
    path_sql = "'" + str(cleaned_file).replace("'", "''") + "'"
    reason_expr = build_reason_expr(reason_candidates)
    abnormal_like = " OR ".join(
        [
            "reason_text LIKE '%异常%'",
            "reason_text LIKE '%失败%'",
            "reason_text LIKE '%取消%'",
            "reason_text LIKE '%撤销%'",
            "reason_text LIKE '%作废%'",
            "reason_text LIKE '%无效%'",
        ]
    )
    normal_like = " OR ".join(
        [
            "reason_text LIKE '%正常结束%'",
            "reason_text LIKE '%正常终止%'",
            "reason_text LIKE '%主动终止%'",
            "reason_text LIKE '%用户主动%'",
            "reason_text LIKE '%用户终止%'",
            "reason_text LIKE '%人工终止%'",
            "reason_text LIKE '%手动终止%'",
            "reason_text LIKE '%已完成%'",
            "reason_text LIKE '%完成%'",
            "reason_text LIKE '%金额%'",
            "reason_text LIKE '%SOC%'",
            "reason_text LIKE '%soc%'",
            "reason_text LIKE '%余额%'",
            "reason_text LIKE '%费用%'",
            "reason_text LIKE '%封顶%'",
            "reason_text LIKE '%达到%'",
        ]
    )

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE base AS
        SELECT
            TRY_CAST({qid(start_col)} AS TIMESTAMP) AS start_ts,
            CAST(TRY_CAST({qid(start_col)} AS TIMESTAMP) AS DATE) AS start_date,
            {col_expr(user_col)} AS user_id,
            {reason_expr} AS reason_text
        FROM read_csv({path_sql}, header=true, all_varchar=true, ignore_errors=true, union_by_name=true)
        WHERE {col_expr(user_col)} <> ''
          AND TRY_CAST({qid(start_col)} AS TIMESTAMP) IS NOT NULL
          {bus_station_filter};
        """
    )

    sample_max = con.execute("SELECT MAX(start_ts) FROM base").fetchone()[0]
    if sample_max is None:
        raise SystemExit("未找到可用订单时间。")

    sample_end = pd.Timestamp(sample_max).normalize()
    horizon_start = (sample_end - pd.Timedelta(days=max(args.horizon_days, 1) - 1)).date().isoformat()
    recent_start = (sample_end - pd.Timedelta(days=max(args.new_window_days, 1) - 1)).date().isoformat()
    abnormal_start = (sample_end - pd.Timedelta(days=max(args.abnormal_window_days, 1) - 1)).date().isoformat()
    return_cutoff = (sample_end - pd.Timedelta(days=max(args.return_gap_days, 1))).date().isoformat()

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE window_base AS
        SELECT *
        FROM base
        WHERE start_date BETWEEN DATE '{horizon_start}' AND DATE '{sample_end.date().isoformat()}';
        """
    )

    new_users = con.execute(
        f"""
        SELECT
            user_id AS "用户识别主键",
            MIN(start_ts) AS "首次充电时间",
            MAX(start_ts) AS "最近充电时间",
            COUNT(*) AS "180天有效订单数",
            SUM(CASE WHEN start_date >= DATE '{recent_start}' THEN 1 ELSE 0 END) AS "近7天订单数"
        FROM window_base
        GROUP BY user_id
        HAVING MIN(start_date) >= DATE '{recent_start}'
        ORDER BY MIN(start_ts), user_id;
        """
    ).fetchdf()

    return_events = con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE user_days AS
        SELECT
            user_id,
            start_date,
            MIN(start_ts) AS first_ts_in_day,
            COUNT(*) AS day_orders
        FROM window_base
        GROUP BY user_id, start_date;

        WITH ordered AS (
            SELECT
                user_id,
                start_date,
                first_ts_in_day,
                day_orders,
                LAG(start_date) OVER (PARTITION BY user_id ORDER BY start_date) AS prev_date
            FROM user_days
        ),
        events AS (
            SELECT
                user_id AS "用户识别主键",
                prev_date AS "上次充电日期",
                start_date AS "回流日期",
                DATE_DIFF('day', prev_date, start_date) AS "断档天数"
            FROM ordered
            WHERE prev_date IS NOT NULL
              AND DATE_DIFF('day', prev_date, start_date) > {args.return_gap_days}
              AND start_date >= DATE '{recent_start}'
        ),
        user_stats AS (
            SELECT
                user_id,
                COUNT(*) AS total_orders_180d,
                COUNT(*) FILTER (WHERE start_date < DATE '{recent_start}') AS orders_before_7d,
                COUNT(*) FILTER (WHERE start_date >= DATE '{recent_start}') AS orders_in_recent_7d
            FROM window_base
            GROUP BY 1
        )
        SELECT
            e."用户识别主键",
            e."上次充电日期",
            e."回流日期",
            e."断档天数",
            COALESCE(s.total_orders_180d, 0) AS "180天充电总次数",
            COALESCE(s.orders_before_7d, 0) AS "回流前充电次数",
            COALESCE(s.orders_in_recent_7d, 0) AS "近7天充电次数"
        FROM events e
        LEFT JOIN user_stats s ON e."用户识别主键" = s.user_id
        ORDER BY "回流日期", "用户识别主键";
        """
    ).fetchdf()

    recent_abnormal = con.execute(
        f"""
        WITH flagged AS (
            SELECT
                user_id AS "用户识别主键",
                start_ts AS "异常时间",
                start_date AS "异常日期",
                reason_text AS "异常原因"
            FROM window_base
            WHERE start_date >= DATE '{abnormal_start}'
              AND reason_text <> ''
              AND ({abnormal_like})
              AND NOT ({normal_like})
        ),
        reason_counts AS (
            SELECT
                "用户识别主键",
                "异常原因",
                COUNT(*) AS reason_cnt
            FROM flagged
            GROUP BY 1, 2
        ),
        reason_ranked AS (
            SELECT
                "用户识别主键",
                "异常原因",
                reason_cnt,
                ROW_NUMBER() OVER (
                    PARTITION BY "用户识别主键"
                    ORDER BY reason_cnt DESC, "异常原因"
                ) AS rn
            FROM reason_counts
        ),
        reason_summary AS (
            SELECT
                "用户识别主键",
                STRING_AGG("异常原因" || '(' || CAST(reason_cnt AS VARCHAR) || ')', '；' ORDER BY reason_cnt DESC, "异常原因") AS "异常原因明细",
                COUNT(*) AS "异常原因类型数"
            FROM reason_counts
            GROUP BY 1
        )
        SELECT
            f."用户识别主键",
            MIN(f."异常时间") AS "首次异常时间",
            MAX(f."异常时间") AS "最近异常时间",
            COUNT(*) AS "近14天异常终止次数",
            rs."异常原因明细",
            rs."异常原因类型数",
            MAX(CASE WHEN rr.rn = 1 THEN rr."异常原因" END) AS "主要异常原因"
        FROM flagged f
        LEFT JOIN reason_summary rs USING ("用户识别主键")
        LEFT JOIN reason_ranked rr USING ("用户识别主键")
        GROUP BY f."用户识别主键", rs."异常原因明细", rs."异常原因类型数"
        ORDER BY "最近异常时间" DESC, f."用户识别主键";
        """
    ).fetchdf()

    hv_map = load_high_value_map(args.label)
    hv_users = load_high_value_users(args.label)
    if not hv_users:
        print("警告：未找到滑动窗口高价值活跃产物，回流表的「180天内是否出现高价值活跃」将全部写「否」。")
    returning_hv_count = 0
    if not return_events.empty:
        return_events["用户识别主键"] = return_events["用户识别主键"].astype(str)
        return_events["断档前是否高价值活跃用户"] = "否"
        return_events["断档前高价值活跃状态"] = ""
        return_events["断档前高价值活跃开始日期"] = ""
        return_events["断档前高价值活跃结束日期"] = ""
        for idx, row in return_events.iterrows():
            user_id = str(row["用户识别主键"]).strip()
            prev_date = str(row["上次充电日期"])
            segments = hv_map.get(user_id, [])
            if not segments:
                continue

            for start_date, end_date, state in segments:
                matched = bool(start_date and end_date and start_date <= prev_date <= end_date)
                if not matched:
                    continue

                return_events.at[idx, "断档前高价值活跃状态"] = state or "是"
                return_events.at[idx, "断档前高价值活跃开始日期"] = start_date
                return_events.at[idx, "断档前高价值活跃结束日期"] = end_date
                return_events.at[idx, "断档前是否高价值活跃用户"] = "是"
                break
        keys = return_events["用户识别主键"].astype(str)
        is_hv = keys.isin(hv_users.keys())
        return_events["180天内是否出现高价值活跃"] = is_hv.map({True: "是", False: "否"})
        return_events["180天内高价值活跃状态"] = keys.map(hv_users).fillna("")
        returning_hv_count = int(is_hv.sum())
        hv_state_counts = return_events.loc[is_hv, "180天内高价值活跃状态"].value_counts()
        brief = "、".join(f"{name} {count:,}" for name, count in hv_state_counts.items())
        print(
            f"回流名单 {len(return_events):,} 人：180 天内出现过高价值活跃 {returning_hv_count:,} 人"
            + (f"，其中 {brief}" if brief else "")
            + f"；其余 {len(return_events) - returning_hv_count:,} 人为 02 回流非高价值活跃用户。"
        )
        if "是否历史高价值活跃用户" in return_events.columns:
            return_events = return_events.drop(columns=["是否历史高价值活跃用户"])
        if "历史高价值活跃状态" in return_events.columns:
            return_events = return_events.drop(columns=["历史高价值活跃状态"])
        if "历史高价值活跃开始日期" in return_events.columns:
            return_events = return_events.drop(columns=["历史高价值活跃开始日期"])
        if "历史高价值活跃结束日期" in return_events.columns:
            return_events = return_events.drop(columns=["历史高价值活跃结束日期"])

    if not new_users.empty:
        new_users["用户识别主键"] = new_users["用户识别主键"].astype(str)

    overview = pd.DataFrame(
        [
            {"指标": "样本截止日期", "数值": sample_end.date().isoformat()},
            {"指标": "已剔除场站类别", "数值": BUS_STATION_CATEGORY},
            {"指标": "观察窗口天数", "数值": int(args.horizon_days)},
            {"指标": "新用户窗口天数", "数值": int(args.new_window_days)},
            {"指标": "回流间隔阈值天数", "数值": int(args.return_gap_days)},
            {"指标": "异常窗口天数", "数值": int(args.abnormal_window_days)},
            {"指标": "新用户数", "数值": int(len(new_users))},
            {"指标": "回流用户数", "数值": int(len(return_events))},
            {"指标": "回流中曾高价值活跃用户数", "数值": returning_hv_count},
            {"指标": "回流非高价值活跃用户数（02）", "数值": int(len(return_events)) - returning_hv_count},
            {"指标": "近期异常终止用户数", "数值": int(len(recent_abnormal))},
        ]
    )

    new_users_file = out_dir / "new_users_7d.csv"
    return_users_file = out_dir / "returning_users_7d.csv"
    abnormal_users_file = out_dir / "recent_abnormal_14d.csv"
    overview_file = out_dir / "recent_user_segments_overview.csv"

    new_users.to_csv(new_users_file, index=False, encoding="utf-8-sig", lineterminator="\n")
    return_events.to_csv(return_users_file, index=False, encoding="utf-8-sig", lineterminator="\n")
    recent_abnormal.to_csv(abnormal_users_file, index=False, encoding="utf-8-sig", lineterminator="\n")
    overview.to_csv(overview_file, index=False, encoding="utf-8-sig", lineterminator="\n")

    print("近期用户分层完成")
    print(f"- 新用户表: {new_users_file}")
    print(f"- 回流用户表: {return_users_file}")
    print(f"- 近期异常终止用户表: {abnormal_users_file}")
    print(f"- 概览表: {overview_file}")


if __name__ == "__main__":
    main()
