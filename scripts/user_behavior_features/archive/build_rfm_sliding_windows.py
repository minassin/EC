"""Build rolling RFM snapshots and type-change tables.

The script reads the cleaned valid-order CSV once through DuckDB, filters bus
stations, aggregates to user-day grain, then walks backward by fixed steps.

高价值活跃轨迹最终落到四态（互斥且完备）：
    稳定高价值活跃            只有一段高价值活跃，且最后一个窗口仍在该状态
    回流高价值活跃            两段及以上，且最后一个窗口仍在该状态
    历史高价值活跃（当前已退出）  最后一个窗口已退出，但近 window_days 天仍有充电
    沉默高价值活跃            最后一个窗口已退出，且近 window_days 天完全没有充电
第 4 态的判据是「用户不在最后一个窗口的明细里」，等价于「近 window_days 天无充电」。
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from time import perf_counter

import pandas as pd

from build_user_behavior_features_pipeline import (
    ARCHIVE_FEATURE_ROOT,
    BUS_STATION_CATEGORY,
    f_level,
    f_score_frequency,
    m_level,
    m_score_single_energy,
    r_level,
    rfm_type,
    safe_path_label,
)


DEFAULT_WINDOW_DAYS = 90
DEFAULT_STEP_DAYS = 3
DEFAULT_HORIZON_DAYS = 180
HIGH_VALUE_ACTIVE = "高价值活跃用户"
STABLE_HIGH_VALUE = "稳定高价值活跃"
RETURNING_HIGH_VALUE = "回流高价值活跃"
EXITED_HIGH_VALUE = "历史高价值活跃（当前已退出）"
SILENT_HIGH_VALUE = "沉默高价值活跃"
HIGH_VALUE_STATES = (STABLE_HIGH_VALUE, RETURNING_HIGH_VALUE, EXITED_HIGH_VALUE, SILENT_HIGH_VALUE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建用户 RFM 滑动窗口变化表。")
    parser.add_argument("--cleaned-file", type=Path, required=True, help="清洗后的有效订单 CSV。")
    parser.add_argument("--label", default="mysql_run", help="输出批次名。")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS, help="窗口大小，默认 90 天。")
    parser.add_argument("--step-days", type=int, default=DEFAULT_STEP_DAYS, help="滑动步长，默认 3 天。")
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS, help="回看总跨度，默认 180 天。")
    parser.add_argument("--start-date", help="最早窗口结束日期，可选，格式 YYYY-MM-DD。")
    parser.add_argument("--end-date", help="最晚窗口结束日期，可选，格式 YYYY-MM-DD。")
    return parser.parse_args()


def qid(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qstr(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def output_dir(label: str) -> Path:
    return ARCHIVE_FEATURE_ROOT / safe_path_label(label) / "sliding_windows"


def window_end_dates(
    min_date: pd.Timestamp,
    max_date: pd.Timestamp,
    step_days: int,
    window_days: int,
    horizon_days: int,
    start_date: str | None,
) -> list[pd.Timestamp]:
    horizon_days = max(horizon_days, window_days)
    if start_date:
        start_limit = pd.to_datetime(start_date).normalize()
    else:
        start_limit = max_date.normalize() - pd.Timedelta(days=horizon_days - window_days)
        if start_limit < min_date.normalize():
            start_limit = min_date.normalize()
    current = max_date.normalize()
    dates: list[pd.Timestamp] = []
    while current >= start_limit:
        dates.append(current)
        next_date = current - pd.Timedelta(days=step_days)
        if next_date < start_limit and current != start_limit:
            current = start_limit
        else:
            current = next_date
    return list(reversed(dates))


def build_daily_table(cleaned_file: Path):
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("未检测到 duckdb，请先安装 duckdb。") from exc

    path_sql = qstr(str(cleaned_file))
    con = duckdb.connect()
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE user_daily AS
        SELECT
            CAST(TRY_CAST({qid("充电开始时间")} AS TIMESTAMP) AS DATE) AS order_date,
            TRIM(COALESCE({qid("用户识别主键")}, {qid("用户编码")}, '')) AS user_key,
            COUNT(*) AS order_count,
            SUM(TRY_CAST({qid("交易电量(kWh)（清洗后）")} AS DOUBLE)) AS energy_sum,
            MAX(TRY_CAST({qid("充电开始时间")} AS TIMESTAMP)) AS last_charge_time
        FROM read_csv({path_sql}, header=true, all_varchar=true, ignore_errors=true, union_by_name=true)
        WHERE COALESCE(NULLIF(TRIM({qid("用户识别主键")}), ''), NULLIF(TRIM({qid("用户编码")}), ''), '') <> ''
          AND COALESCE(NULLIF(TRIM({qid("场站类别")}), ''), '') <> {qstr(BUS_STATION_CATEGORY)}
          AND TRY_CAST({qid("充电开始时间")} AS TIMESTAMP) IS NOT NULL
        GROUP BY 1, 2;
        """
    )
    bounds = con.execute("SELECT MIN(order_date), MAX(order_date), COUNT(*) FROM user_daily").fetchone()
    if not bounds or bounds[0] is None or bounds[1] is None:
        raise SystemExit(f"没有可用于滑动窗口的订单数据：{cleaned_file}")
    print(f"用户日粒度表完成: {bounds[2]:,} 行，日期范围 {bounds[0]} 至 {bounds[1]}")
    return con, pd.Timestamp(bounds[0]), pd.Timestamp(bounds[1])


def write_streaming_outputs(
    con,
    end_dates: list[pd.Timestamp],
    window_days: int,
    snapshots_file: Path,
    periods_file: Path,
    high_value_file: Path,
) -> tuple[int, int, int, dict[str, int]]:
    snapshots_file.parent.mkdir(parents=True, exist_ok=True)
    periods_file.parent.mkdir(parents=True, exist_ok=True)
    high_value_file.parent.mkdir(parents=True, exist_ok=True)

    snapshot_header_written = False
    periods_header_written = False
    total_snapshot_rows = 0
    total_period_rows = 0

    current_state: dict[str, dict[str, object]] = {}
    high_value_summary: dict[str, dict[str, object]] = {}
    last_window_users: set[str] = set()

    period_fieldnames = [
        "用户识别主键",
        "阶段开始日期",
        "阶段结束日期",
        "RFM类型",
        "持续窗口数",
        "上一阶段类型",
        "下一阶段类型",
        "是否高价值活跃阶段",
    ]
    high_value_fieldnames = [
        "用户识别主键",
        "高价值活跃开始日期",
        "高价值活跃结束日期",
        "持续窗口数",
        "高价值活跃段数",
        "上一阶段类型",
        "下一阶段类型",
        "高价值活跃状态",
    ]

    def close_segment(period_writer: csv.DictWriter, user_key: str, state: dict[str, object], next_type: str) -> None:
        nonlocal total_period_rows
        row_type = str(state["type"])
        row = {
            "用户识别主键": user_key,
            "阶段开始日期": state["start"],
            "阶段结束日期": state["end"],
            "RFM类型": row_type,
            "持续窗口数": int(state["count"]),
            "上一阶段类型": state["prev_type"],
            "下一阶段类型": next_type,
            "是否高价值活跃阶段": "是" if row_type == HIGH_VALUE_ACTIVE else "否",
        }
        period_writer.writerow(row)
        total_period_rows += 1

        if row_type == HIGH_VALUE_ACTIVE:
            summary = high_value_summary.setdefault(
                user_key,
                {
                    "用户识别主键": user_key,
                    "高价值活跃开始日期": state["start"],
                    "高价值活跃结束日期": state["end"],
                    "持续窗口数": 0,
                    "高价值活跃段数": 0,
                    "上一阶段类型": state["prev_type"],
                    "下一阶段类型": next_type,
                    "_first_prev_type": state["prev_type"],
                },
            )
            if summary["高价值活跃段数"] == 0:
                summary["高价值活跃开始日期"] = state["start"]
                summary["上一阶段类型"] = state["prev_type"]
                summary["_first_prev_type"] = state["prev_type"]
            summary["高价值活跃结束日期"] = state["end"]
            summary["持续窗口数"] = int(summary["持续窗口数"]) + int(state["count"])
            summary["高价值活跃段数"] = int(summary["高价值活跃段数"]) + 1
            summary["下一阶段类型"] = next_type

    with snapshots_file.open("w", encoding="utf-8-sig", newline="") as snapshot_handle, periods_file.open(
        "w", encoding="utf-8-sig", newline=""
    ) as periods_handle:
        period_writer = csv.DictWriter(periods_handle, fieldnames=period_fieldnames, lineterminator="\n")
        period_writer.writeheader()

        for index, end_date in enumerate(end_dates, start=1):
            start_date = end_date - pd.Timedelta(days=window_days - 1)
            sql = f"""
            SELECT
                {qstr(start_date.date().isoformat())} AS {qid("窗口开始日期")},
                {qstr(end_date.date().isoformat())} AS {qid("窗口结束日期")},
                user_key AS {qid("用户识别主键")},
                SUM(order_count) AS {qid("订单数")},
                MAX(last_charge_time) AS {qid("最近充电时间")},
                DATE_DIFF('day', CAST(MAX(last_charge_time) AS DATE), DATE {qstr(end_date.date().isoformat())}) AS {qid("距窗口末次充电天数")},
                GREATEST(DATE_DIFF('day', MIN(order_date), DATE {qstr(end_date.date().isoformat())}) + 1, 14) AS {qid("动态统计天数")},
                GREATEST(DATE_DIFF('day', MIN(order_date), DATE {qstr(end_date.date().isoformat())}) + 1, 14)::DOUBLE
                    / NULLIF(SUM(order_count), 0) AS {qid("平均充电频率_天")},
                SUM(energy_sum) / NULLIF(SUM(order_count), 0) AS {qid("平均单次充电量_kWh")}
            FROM user_daily
            WHERE order_date BETWEEN DATE {qstr(start_date.date().isoformat())} AND DATE {qstr(end_date.date().isoformat())}
            GROUP BY user_key
            ORDER BY user_key;
            """
            frame = con.execute(sql).fetchdf()
            if frame.empty:
                print(f"[{index}/{len(end_dates)}] 窗口 {start_date.date()} 至 {end_date.date()}，用户 0")
                continue

            frame["订单数"] = pd.to_numeric(frame["订单数"], errors="coerce").fillna(0).astype(int)
            frame["距窗口末次充电天数"] = pd.to_numeric(frame["距窗口末次充电天数"], errors="coerce").fillna(0)
            frame["平均充电频率_天"] = pd.to_numeric(frame["平均充电频率_天"], errors="coerce")
            frame["平均单次充电量_kWh"] = pd.to_numeric(frame["平均单次充电量_kWh"], errors="coerce")
            r_pairs = frame["距窗口末次充电天数"].map(lambda value: r_level(float(value)))
            frame["R等级"] = r_pairs.map(lambda value: value[0])
            frame["R分数"] = r_pairs.map(lambda value: value[1])
            frame["F分数"] = frame["平均充电频率_天"].map(lambda value: f_score_frequency(float(value)))
            frame["F等级"] = frame["F分数"].map(f_level)
            frame["M分数"] = frame["平均单次充电量_kWh"].map(lambda value: m_score_single_energy(float(value)))
            frame["M等级"] = frame["M分数"].map(m_level)
            frame["RFM类型"] = [rfm_type(r, f, m) for r, f, m in zip(frame["R分数"], frame["F分数"], frame["M分数"])]
            frame["是否高价值活跃"] = frame["RFM类型"].eq(HIGH_VALUE_ACTIVE).map({True: "是", False: "否"})
            frame = frame.sort_values(["用户识别主键"], ascending=[True]).reset_index(drop=True)
            snapshot_cols = [
                "窗口开始日期",
                "窗口结束日期",
                "用户识别主键",
                "订单数",
                "最近充电时间",
                "距窗口末次充电天数",
                "动态统计天数",
                "平均充电频率_天",
                "平均单次充电量_kWh",
                "R等级",
                "R分数",
                "F分数",
                "F等级",
                "M分数",
                "M等级",
                "RFM类型",
                "是否高价值活跃",
            ]
            frame = frame[snapshot_cols]
            frame.to_csv(snapshot_handle, index=False, header=not snapshot_header_written, encoding="utf-8-sig", lineterminator="\n")
            snapshot_header_written = True
            total_snapshot_rows += len(frame)

            for row in frame.itertuples(index=False, name=None):
                user_key = str(row[2])
                start_text = str(row[0])
                end_text = str(row[1])
                current_type = str(row[15])

                state = current_state.get(user_key)
                if state is None:
                    current_state[user_key] = {
                        "type": current_type,
                        "start": start_text,
                        "end": end_text,
                        "count": 1,
                        "prev_type": "无",
                    }
                    continue

                if current_type == state["type"]:
                    state["end"] = end_text
                    state["count"] = int(state["count"]) + 1
                    continue

                close_segment(period_writer, user_key, state, current_type)
                current_state[user_key] = {
                    "type": current_type,
                    "start": start_text,
                    "end": end_text,
                    "count": 1,
                    "prev_type": state["type"],
                }

            last_window_users = set(frame["用户识别主键"].astype(str))
            print(f"[{index}/{len(end_dates)}] 窗口 {start_date.date()} 至 {end_date.date()}，用户 {len(frame):,}")

        for user_key, state in current_state.items():
            close_segment(period_writer, user_key, state, "无")

        high_value_rows: list[dict[str, object]] = []
        state_counts: dict[str, int] = {name: 0 for name in HIGH_VALUE_STATES}
        for summary in high_value_summary.values():
            first_prev = str(summary.pop("_first_prev_type", "无"))
            last_next = str(summary["下一阶段类型"])
            segment_count = int(summary["高价值活跃段数"])
            user_key = str(summary["用户识别主键"])
            if user_key not in last_window_users:
                status = SILENT_HIGH_VALUE
            elif last_next == "无":
                status = RETURNING_HIGH_VALUE if segment_count > 1 else STABLE_HIGH_VALUE
            else:
                status = EXITED_HIGH_VALUE
            summary["高价值活跃状态"] = status
            high_value_rows.append(summary)
            state_counts[status] = state_counts.get(status, 0) + 1
        print(
            "高价值活跃四态: "
            + "，".join(f"{name} {state_counts.get(name, 0):,}" for name in HIGH_VALUE_STATES)
        )

    high_value_df = pd.DataFrame(high_value_rows)
    if not high_value_df.empty:
        high_value_df = high_value_df[
            [
                "用户识别主键",
                "高价值活跃开始日期",
                "高价值活跃结束日期",
                "持续窗口数",
                "高价值活跃段数",
                "上一阶段类型",
                "下一阶段类型",
                "高价值活跃状态",
            ]
        ].sort_values(["用户识别主键", "高价值活跃开始日期"], ascending=[True, True])
    high_value_df.to_csv(high_value_file, index=False, encoding="utf-8-sig", lineterminator="\n")
    return total_snapshot_rows, total_period_rows, len(high_value_df), state_counts


def build_snapshots(con, end_dates: list[pd.Timestamp], window_days: int) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for index, end_date in enumerate(end_dates, start=1):
        start_date = end_date - pd.Timedelta(days=window_days - 1)
        sql = f"""
        SELECT
            {qstr(start_date.date().isoformat())} AS {qid("窗口开始日期")},
            {qstr(end_date.date().isoformat())} AS {qid("窗口结束日期")},
            user_key AS {qid("用户识别主键")},
            SUM(order_count) AS {qid("订单数")},
            MAX(last_charge_time) AS {qid("最近充电时间")},
            DATE_DIFF('day', CAST(MAX(last_charge_time) AS DATE), DATE {qstr(end_date.date().isoformat())}) AS {qid("距窗口末次充电天数")},
            GREATEST(DATE_DIFF('day', MIN(order_date), DATE {qstr(end_date.date().isoformat())}) + 1, 14) AS {qid("动态统计天数")},
            GREATEST(DATE_DIFF('day', MIN(order_date), DATE {qstr(end_date.date().isoformat())}) + 1, 14)::DOUBLE
                / NULLIF(SUM(order_count), 0) AS {qid("平均充电频率_天")},
            SUM(energy_sum) / NULLIF(SUM(order_count), 0) AS {qid("平均单次充电量_kWh")}
        FROM user_daily
        WHERE order_date BETWEEN DATE {qstr(start_date.date().isoformat())} AND DATE {qstr(end_date.date().isoformat())}
        GROUP BY user_key;
        """
        frame = con.execute(sql).fetchdf()
        if not frame.empty:
            frames.append(frame)
        print(f"[{index}/{len(end_dates)}] 窗口 {start_date.date()} 至 {end_date.date()}，用户 {len(frame):,}")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["订单数"] = pd.to_numeric(df["订单数"], errors="coerce").fillna(0).astype(int)
    df["距窗口末次充电天数"] = pd.to_numeric(df["距窗口末次充电天数"], errors="coerce").fillna(0)
    df["平均充电频率_天"] = pd.to_numeric(df["平均充电频率_天"], errors="coerce")
    df["平均单次充电量_kWh"] = pd.to_numeric(df["平均单次充电量_kWh"], errors="coerce")
    r_pairs = df["距窗口末次充电天数"].map(lambda value: r_level(float(value)))
    df["R等级"] = r_pairs.map(lambda value: value[0])
    df["R分数"] = r_pairs.map(lambda value: value[1])
    df["F分数"] = df["平均充电频率_天"].map(lambda value: f_score_frequency(float(value)))
    df["F等级"] = df["F分数"].map(f_level)
    df["M分数"] = df["平均单次充电量_kWh"].map(lambda value: m_score_single_energy(float(value)))
    df["M等级"] = df["M分数"].map(m_level)
    df["RFM类型"] = [rfm_type(r, f, m) for r, f, m in zip(df["R分数"], df["F分数"], df["M分数"])]
    df["是否高价值活跃"] = df["RFM类型"].eq(HIGH_VALUE_ACTIVE).map({True: "是", False: "否"})
    df = df.sort_values(["用户识别主键", "窗口结束日期"], ascending=[True, True])
    return df


def build_type_periods(snapshots: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for user_key, group in snapshots.sort_values(["用户识别主键", "窗口结束日期"]).groupby("用户识别主键", sort=False):
        group = group.reset_index(drop=True)
        period_no = (group["RFM类型"].ne(group["RFM类型"].shift())).cumsum()
        compact = (
            group.assign(_period=period_no)
            .groupby("_period", sort=False)
            .agg(
                阶段开始日期=("窗口开始日期", "min"),
                阶段结束日期=("窗口结束日期", "max"),
                RFM类型=("RFM类型", "first"),
                持续窗口数=("RFM类型", "size"),
            )
            .reset_index(drop=True)
        )
        compact["用户识别主键"] = user_key
        compact["上一阶段类型"] = compact["RFM类型"].shift(1).fillna("无")
        compact["下一阶段类型"] = compact["RFM类型"].shift(-1).fillna("无")
        compact["是否高价值活跃阶段"] = compact["RFM类型"].eq(HIGH_VALUE_ACTIVE).map({True: "是", False: "否"})
        for _, row in compact.iterrows():
            rows.append(row.to_dict())
    periods = pd.DataFrame(rows)
    if not periods.empty:
        periods = periods[
            [
                "用户识别主键",
                "阶段开始日期",
                "阶段结束日期",
                "RFM类型",
                "持续窗口数",
                "上一阶段类型",
                "下一阶段类型",
                "是否高价值活跃阶段",
            ]
        ]
    return periods


def high_value_active_periods(periods: pd.DataFrame, active_user_keys: set[str] | None = None) -> pd.DataFrame:
    """四态判定的内存版实现。

    active_user_keys 传入「最后一个窗口内出现过的用户」，未命中者判为「沉默高价值活跃」；
    不传时退化为三态（与旧行为兼容）。
    """
    if periods.empty:
        return periods.copy()
    out = periods.loc[periods["RFM类型"].eq(HIGH_VALUE_ACTIVE)].copy()
    out = out.rename(columns={"阶段开始日期": "高价值活跃开始日期", "阶段结束日期": "高价值活跃结束日期"})
    if out.empty:
        return out
    out = out.sort_values(["用户识别主键", "高价值活跃开始日期", "高价值活跃结束日期"]).reset_index(drop=True)
    rows: list[dict[str, object]] = []
    for user_key, group in out.groupby("用户识别主键", sort=False):
        group = group.reset_index(drop=True)
        prev_type = str(group.iloc[0]["上一阶段类型"])
        next_type = str(group.iloc[-1]["下一阶段类型"])
        segment_count = int(len(group))
        total_windows = int(group["持续窗口数"].sum())
        start_date = group["高价值活跃开始日期"].min()
        end_date = group["高价值活跃结束日期"].max()
        if active_user_keys is not None and str(user_key) not in active_user_keys:
            state = SILENT_HIGH_VALUE
        elif next_type == "无":
            state = RETURNING_HIGH_VALUE if segment_count > 1 else STABLE_HIGH_VALUE
        else:
            state = EXITED_HIGH_VALUE
        rows.append(
            {
                "用户识别主键": user_key,
                "高价值活跃开始日期": start_date,
                "高价值活跃结束日期": end_date,
                "持续窗口数": total_windows,
                "高价值活跃段数": segment_count,
                "上一阶段类型": prev_type,
                "下一阶段类型": next_type,
                "高价值活跃状态": state,
            }
        )
    return pd.DataFrame(rows)[
        [
            "用户识别主键",
            "高价值活跃开始日期",
            "高价值活跃结束日期",
            "持续窗口数",
            "高价值活跃段数",
            "上一阶段类型",
            "下一阶段类型",
            "高价值活跃状态",
        ]
    ]


def main() -> None:
    args = parse_args()
    started = perf_counter()
    cleaned_file = args.cleaned_file.expanduser().resolve()
    if not cleaned_file.exists():
        raise FileNotFoundError(f"cleaned file not found: {cleaned_file}")
    window_days = max(1, int(args.window_days))
    step_days = max(1, int(args.step_days))
    horizon_days = max(1, int(args.horizon_days))

    os.environ.setdefault("EC_OUTPUT_ROOT", str(Path.cwd() / "outputs"))
    out_dir = output_dir(args.label)
    out_dir.mkdir(parents=True, exist_ok=True)

    con, min_date, max_date = build_daily_table(cleaned_file)
    if args.end_date:
        max_date = min(max_date, pd.to_datetime(args.end_date).normalize())
    dates = window_end_dates(min_date, max_date, step_days, window_days, horizon_days, args.start_date)
    print(f"滑动窗口: 窗口 {window_days} 天，步长 {step_days} 天，回看跨度 {horizon_days} 天，共 {len(dates)} 个窗口")
    snapshots_file = out_dir / "user_rfm_window_snapshots.csv"
    periods_file = out_dir / "user_rfm_type_periods.csv"
    high_value_file = out_dir / "high_value_active_periods.csv"
    snapshot_rows, period_rows, high_value_rows, state_counts = write_streaming_outputs(
        con,
        dates,
        window_days,
        snapshots_file,
        periods_file,
        high_value_file,
    )

    overview = pd.DataFrame(
        [
            {"指标": "窗口数", "数值": len(dates)},
            {"指标": "窗口明细行数", "数值": snapshot_rows},
            {"指标": "类型阶段行数", "数值": period_rows},
            {"指标": "高价值活跃阶段数", "数值": high_value_rows},
            {"指标": "高价值活跃用户数", "数值": sum(state_counts.values())},
            {"指标": STABLE_HIGH_VALUE, "数值": state_counts.get(STABLE_HIGH_VALUE, 0)},
            {"指标": RETURNING_HIGH_VALUE, "数值": state_counts.get(RETURNING_HIGH_VALUE, 0)},
            {"指标": EXITED_HIGH_VALUE, "数值": state_counts.get(EXITED_HIGH_VALUE, 0)},
            {"指标": SILENT_HIGH_VALUE, "数值": state_counts.get(SILENT_HIGH_VALUE, 0)},
        ]
    )
    overview_file = out_dir / "sliding_window_overview.csv"
    overview.to_csv(overview_file, index=False, encoding="utf-8-sig", lineterminator="\n")

    print("滑动窗口 RFM 变化表输出完成")
    print(f"- 窗口明细表: {snapshots_file}")
    print(f"- 类型变化区间表: {periods_file}")
    print(f"- 高价值活跃专项表: {high_value_file}")
    print(f"- 概览表: {overview_file}")
    print(f"总耗时: {perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
