from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from cleaning.clean import STATION_CATEGORY_ORDER, save_station_category_chart  # noqa: E402

PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
MYSQL_EXE = Path(r"D:\mysql-9.7.0-winx64\mysql-9.7.0-winx64\bin\mysql.exe")
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(sys.executable)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MySQL 一键清洗 + 导出 + 分析出图 + 用户行为特征 + 分群落桶。")
    parser.add_argument("--input-dir", required=True, help="待导入的 Excel 根目录。")
    parser.add_argument("--table-like", default="%", help="原始表匹配规则，例如 Y20\\_%%。")
    parser.add_argument("--run-name", default="mysql_run", help="输出批次名。")
    parser.add_argument("--source-run-name", help="复用已有批次的清洗CSV，例如 mysql_run；适合跳过导出但生成新的特征结果。")
    parser.add_argument("--year-label", default="dataset", help="传给清洗脚本的批次标签。")
    parser.add_argument("--workers", type=int, default=2, help="清洗时的并行进程数。")
    parser.add_argument(
        "--clean-scope",
        choices=["full", "incremental"],
        default="full",
        help="第二步清洗范围：full 清洗全量原始表；incremental 只清洗新增导入且未在清洗结果中出现过的表。",
    )
    parser.add_argument("--force-reimport", "--reimport", action="store_true", help="即使表已存在且行数一致，也强制重新导入。")
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-port", default="3306")
    parser.add_argument("--db-user", default="root")
    parser.add_argument("--db-password", default="123456")
    parser.add_argument("--db-name", default="ec_all")
    parser.add_argument("--skip-import", action="store_true", help="跳过原始数据导入")
    parser.add_argument("--skip-clean", action="store_true", help="跳过数据库清洗")
    parser.add_argument("--skip-export", action="store_true", help="跳过清洗结果导出")
    parser.add_argument("--skip-analysis", action="store_true", help="跳过分析画图")
    parser.add_argument("--skip-feature", action="store_true", help="跳过用户行为特征构建")
    parser.add_argument("--skip-visualize", action="store_true", help="跳过用户行为特征可视化")
    parser.add_argument("--skip-sliding-windows", action="store_true", help="跳过滑动窗口高价值活跃轨迹构建")
    parser.add_argument("--skip-recent-segments", action="store_true", help="跳过近 7/14/180 天名单（新用户 / 回流 / 异常终止）")
    parser.add_argument("--skip-segmentation", action="store_true", help="跳过最终分群落桶")
    parser.add_argument("--sliding-window-days", type=int, default=90, help="滑动窗口大小，默认 90 天。")
    parser.add_argument("--sliding-step-days", type=int, default=1, help="滑动窗口步长，默认 1 天（90/1/180 = 91 个窗口）。")
    parser.add_argument("--sliding-horizon-days", type=int, default=180, help="滑动窗口回看总跨度，默认 180 天。")
    parser.add_argument("--recent-horizon-days", type=int, default=180, help="新用户 / 回流名单回看跨度，默认 180 天。")
    parser.add_argument("--recent-window-days", type=int, default=90, help="用户行为特征分析窗口，默认 90 天，可设为 180。")
    return parser.parse_args()


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, check=True, env=env)


def mysql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def make_defaults_file(args: argparse.Namespace) -> Path:
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cnf", encoding="utf-8", newline="\n") as tf:
        path = Path(tf.name)
        tf.write("[client]\n")
        tf.write(f"host={args.db_host}\n")
        tf.write(f"port={args.db_port}\n")
        tf.write(f"user={args.db_user}\n")
        tf.write(f"password={args.db_password}\n")
        tf.write("default-character-set=utf8mb4\n")
        tf.write("local-infile=1\n")
    return path


def mysql_query(args: argparse.Namespace, sql: str, *, capture: bool = False) -> subprocess.CompletedProcess:
    defaults_file = make_defaults_file(args)
    try:
        cmd = [
            str(MYSQL_EXE),
            f"--defaults-extra-file={defaults_file}",
            "--default-character-set=utf8mb4",
            "--local-infile=1",
            args.db_name,
            "--batch",
            "--quick",
            "-e",
            sql,
        ]
        return subprocess.run(
            cmd,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture else None,
        )
    finally:
        defaults_file.unlink(missing_ok=True)


def export_table(args: argparse.Namespace, table_name: str, columns: list[str], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    selected = ", ".join(quote_ident(col) for col in columns)
    sql = f"SELECT {selected} FROM {quote_ident(table_name)};"
    fd, tmp_name = tempfile.mkstemp(suffix=".tsv")
    os.close(fd)
    tmp_tsv = Path(tmp_name)
    try:
        defaults_file = make_defaults_file(args)
        try:
            cmd = [
                str(MYSQL_EXE),
                f"--defaults-extra-file={defaults_file}",
                "--default-character-set=utf8mb4",
                "--batch",
                "--quick",
                "--raw",
                args.db_name,
                "-e",
                sql,
            ]
            with tmp_tsv.open("w", encoding="utf-8", newline="") as out:
                subprocess.run(cmd, check=True, text=True, encoding="utf-8", errors="replace", stdout=out)
        finally:
            defaults_file.unlink(missing_ok=True)

        first = True
        for chunk in pd.read_csv(
            tmp_tsv,
            sep="\t",
            dtype=object,
            keep_default_na=False,
            na_values=["NULL", "\\N"],
            low_memory=False,
            chunksize=200_000,
        ):
            chunk.to_csv(
                out_csv,
                index=False,
                mode="w" if first else "a",
                header=first,
                encoding="utf-8-sig",
                lineterminator="\n",
            )
            first = False
    finally:
        tmp_tsv.unlink(missing_ok=True)


def write_snapshot_meta(cleaned_csv: Path, abnormal_csv: Path, meta_path: Path, *, cleaned_table: str, abnormal_table: str) -> None:
    cleaned_rows = 0
    cleaned_raw_orders = 0
    abnormal_rows = 0
    if cleaned_csv.exists() and cleaned_csv.stat().st_size > 0:
        for chunk in pd.read_csv(cleaned_csv, usecols=["原始订单数"], encoding="utf-8-sig", dtype=object, chunksize=300_000):
            cleaned_rows += len(chunk)
            cleaned_raw_orders += int(pd.to_numeric(chunk["原始订单数"], errors="coerce").fillna(1).clip(lower=1).sum())
    if abnormal_csv.exists() and abnormal_csv.stat().st_size > 0:
        for chunk in pd.read_csv(abnormal_csv, encoding="utf-8-sig", dtype=object, chunksize=300_000):
            abnormal_rows += len(chunk)
    meta = {
        "cleaned_table": cleaned_table,
        "abnormal_table": abnormal_table,
        "cleaned_rows": cleaned_rows,
        "cleaned_raw_orders": cleaned_raw_orders,
        "abnormal_rows": abnormal_rows,
        "cleaned_csv": str(cleaned_csv),
        "abnormal_csv": str(abnormal_csv),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def save_cleaning_station_category_chart(cleaned_csv: Path, path: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    station_stats: dict[str, dict[str, object]] = {}
    blank_station_orders = 0
    usecols = ["充电站ID", "充电站名称", "场站类别", "充电开始时间", "原始订单数"]
    category_order = list(STATION_CATEGORY_ORDER)

    def normalize_category(value: object) -> str:
        text = "" if pd.isna(value) else str(value).strip()
        return text if text in STATION_CATEGORY_ORDER else "其他"

    for chunk in pd.read_csv(cleaned_csv, usecols=usecols, dtype=object, encoding="utf-8-sig", chunksize=300_000):
        if chunk.empty:
            continue
        chunk = chunk.copy()
        chunk["充电站ID"] = chunk["充电站ID"].fillna("").astype(str).str.strip()
        chunk["充电站名称"] = chunk["充电站名称"].fillna("未知场站").astype(str).str.strip().replace({"": "未知场站"})
        chunk["场站类别"] = chunk["场站类别"].map(normalize_category)
        chunk["充电开始时间"] = pd.to_datetime(chunk["充电开始时间"], errors="coerce")
        chunk["_order_weight"] = 1.0  # 会话口径：短时重启合并后一行即一单，与"清洗订单"汇总一致

        blank_station_orders += int(chunk.loc[chunk["充电站ID"].eq(""), "_order_weight"].sum())

        for sid, group in chunk.groupby("充电站ID", dropna=False):
            if not sid:
                continue
            stats = station_stats.setdefault(
                str(sid),
                {
                    "充电站ID": str(sid),
                    "充电站名称": "",
                    "场站类别": "",
                    "首次出现时间": pd.NaT,
                    "_category_weights": {cat: 0 for cat in category_order},
                    "订单数": 0,
                },
            )
            group_weight = int(group["_order_weight"].sum())
            stats["订单数"] = int(stats["订单数"]) + group_weight

            valid_times = group["充电开始时间"].dropna()
            if not valid_times.empty:
                first_time = valid_times.min()
                if pd.isna(stats["首次出现时间"]) or first_time < stats["首次出现时间"]:
                    stats["首次出现时间"] = first_time
                    name_series = group.loc[group["充电开始时间"].eq(first_time), "充电站名称"]
                    if not name_series.empty:
                        stats["充电站名称"] = str(name_series.iloc[0]).strip() or "未知场站"

            for category, weight in group.groupby("场站类别")["_order_weight"].sum().items():
                key = str(category) if str(category) in category_order else "其他"
                stats["_category_weights"][key] += int(weight)

    if not station_stats:
        empty = pd.DataFrame(columns=["场站类别", "订单数", "场站数", "占比"])
        save_station_category_chart(empty, path)
        return pd.DataFrame(columns=["充电站ID", "充电站名称", "场站类别", "首次出现时间", "订单数"])

    for sid, stats in station_stats.items():
        category_weights = stats.pop("_category_weights")  # type: ignore[assignment]
        chosen = max(category_weights.items(), key=lambda item: (item[1], -category_order.index(item[0])))
        stats["场站类别"] = chosen[0] if chosen[1] > 0 else "其他"
        rows.append(stats)

    station_detail = pd.DataFrame(rows)
    station_detail["首次出现时间"] = pd.to_datetime(station_detail["首次出现时间"], errors="coerce")
    station_detail = station_detail.sort_values(["场站类别", "订单数", "首次出现时间", "充电站ID"], ascending=[True, False, True, True]).reset_index(drop=True)

    chart_rows = []
    total = int(station_detail["订单数"].sum()) + blank_station_orders
    for category in STATION_CATEGORY_ORDER:
        group = station_detail[station_detail["场站类别"].eq(category)]
        category_orders = int(group["订单数"].sum())
        if category == "其他":
            # 充电站ID 为空的会话没有站点可归类，并入"其他"，但不计入场站数
            category_orders += blank_station_orders
        chart_rows.append(
            {
                "场站类别": category,
                "订单数": category_orders,
                "场站数": int(group["充电站ID"].nunique(dropna=True)),
                "占比": round(category_orders / total, 4) if total else 0,
            }
        )

    if blank_station_orders:
        print(f"注意：充电站ID 为空的会话 {blank_station_orders:,} 条已并入「其他」，不计入场站数", flush=True)

    save_station_category_chart(pd.DataFrame(chart_rows), path)
    print(f"清洗场站类别分布图: {path}", flush=True)
    return station_detail


def get_table_columns(args: argparse.Namespace, table_name: str) -> list[str]:
    result = mysql_query(args, f"SHOW COLUMNS FROM {quote_ident(table_name)};", capture=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    return [line.split("\t", 1)[0] for line in lines[1:] if line.split("\t", 1)[0]]


def main() -> None:
    args = parse_args()
    import_script = PROJECT_ROOT / "scripts" / "import_dataset_to_mysql.py"
    clean_script = PROJECT_ROOT / "scripts" / "clean_mysql_dataset.py"
    analyze_script = PROJECT_ROOT / "scripts" / "analysis" / "analyze.py"
    feature_script = PROJECT_ROOT / "scripts" / "user_behavior_features" / "archive" / "build_user_behavior_features_pipeline.py"
    visualize_script = PROJECT_ROOT / "scripts" / "user_behavior_features" / "archive" / "visualize_user_behavior_features.py"
    sliding_script = PROJECT_ROOT / "scripts" / "user_behavior_features" / "archive" / "build_rfm_sliding_windows.py"
    recent_script = PROJECT_ROOT / "scripts" / "user_behavior_features" / "archive" / "build_recent_user_segments.py"
    segmentation_script = PROJECT_ROOT / "scripts" / "user_behavior_features" / "archive" / "build_user_segmentation.py"
    run_dir = PROJECT_ROOT / "outputs" / "runs" / args.run_name
    source_run_name = args.source_run_name or args.run_name
    source_run_dir = PROJECT_ROOT / "outputs" / "runs" / source_run_name
    expected_cleaned = source_run_dir / "cleaned" / f"{source_run_name}_standard_user_orders.csv"
    fallback_dir = PROJECT_ROOT / "outputs" / "runs" / "mysql_run"
    fallback_cleaned = fallback_dir / "cleaned" / "mysql_run_standard_user_orders.csv"
    if not expected_cleaned.exists() and fallback_cleaned.exists() and source_run_name != "mysql_run":
        source_run_name = "mysql_run"
        source_run_dir = fallback_dir
        print(f"自动复用已有清洗CSV: {fallback_cleaned}")
    env = dict(os.environ)
    env["EC_OUTPUT_ROOT"] = str(run_dir)
    feature_root = PROJECT_ROOT / "outputs" / "features" / "archive"
    label_dir = feature_root / args.run_name
    feature_env = dict(os.environ)
    feature_env["EC_OUTPUT_ROOT"] = str(PROJECT_ROOT / "outputs")

    cleaned_table = f"{args.run_name}_cleaned_orders"
    abnormal_table = f"{args.run_name}_abnormal_orders"
    discard_table = f"{args.run_name}_discard_orders"
    overview_table = f"{args.run_name}_cleaning_overview"
    cleaned_csv = source_run_dir / "cleaned" / f"{source_run_name}_standard_user_orders.csv"
    abnormal_csv = source_run_dir / "abnormal" / f"{source_run_name}_abnormal_orders.csv"

    total_start = perf_counter()

    if not args.skip_import:
        step_start = perf_counter()
        print("[1/9] 导入原始数据到数据库")
        import_cmd = [
            str(PYTHON_EXE),
            str(import_script),
            "--input-dir", args.input_dir,
            "--db-host", args.db_host,
            "--db-port", args.db_port,
            "--db-user", args.db_user,
            "--db-password", args.db_password,
            "--db-name", args.db_name,
        ]
        if args.force_reimport:
            import_cmd.append("--force-reimport")
        run(import_cmd)
        print(f"[1/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[1/9] 跳过导入，用时 0.0s")

    if not args.skip_clean:
        step_start = perf_counter()
        print("[2/9] 开始数据库清洗")
        run([
            str(PYTHON_EXE),
            str(clean_script),
            "--table-like", args.table_like,
            "--year-label", args.year_label,
            "--workers", str(args.workers),
            "--clean-scope", args.clean_scope,
            "--cleaned-table", cleaned_table,
            "--abnormal-table", abnormal_table,
            "--discard-table", discard_table,
            "--overview-table", overview_table,
            "--db-host", args.db_host,
            "--db-port", args.db_port,
            "--db-user", args.db_user,
            "--db-password", args.db_password,
            "--db-name", args.db_name,
            "--mysql-exe", str(MYSQL_EXE),
        ])
        print(f"[2/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[2/9] 跳过清洗，用时 0.0s")

    if not args.skip_export:
        step_start = perf_counter()
        print("[3/9] 导出分析CSV")
        cleaned_cols = get_table_columns(args, cleaned_table)
        abnormal_cols = get_table_columns(args, abnormal_table)
        export_table(args, cleaned_table, cleaned_cols, cleaned_csv)
        export_table(args, abnormal_table, abnormal_cols, abnormal_csv)
        write_snapshot_meta(
            cleaned_csv,
            abnormal_csv,
            cleaned_csv.with_suffix(".meta.json"),
            cleaned_table=cleaned_table,
            abnormal_table=abnormal_table,
        )
        station_detail = save_cleaning_station_category_chart(
            cleaned_csv,
            run_dir / "analysis" / "images" / "cleaning" / f"{args.run_name}_station_category_distribution.svg",
        )
        if not station_detail.empty:
            station_detail_path = run_dir / "analysis" / "station_name_by_category.csv"
            station_detail_path.parent.mkdir(parents=True, exist_ok=True)
            station_detail.to_csv(station_detail_path, index=False, encoding="utf-8-sig", lineterminator="\n")
            print(f"站点分类明细已导出: {station_detail_path}", flush=True)
        print(f"[3/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[3/9] 跳过导出，用时 0.0s")

    if not args.skip_analysis:
        step_start = perf_counter()
        # analyze.py 这里会出两套图：matplotlib 那套 + 手写 SVG 那套（15 类分析图与
        # 2 张场站类别环形图，图型对齐 analysis/images/<run>/legacy/2026/）。
        # 手写 SVG 那套要另读一遍清洗表，加 --skip-legacy-svg 可只出前者。
        print("[4/9] 生成分析图表")
        run([
            str(PYTHON_EXE),
            str(analyze_script),
            "--cleaned-file", str(cleaned_csv),
            "--abnormal-file", str(abnormal_csv),
            "--image-prefix", args.run_name,
            "--label", args.run_name,
            "--split-by-year",
        ], env=env)
        print(f"[4/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[4/9] 跳过分析，用时 0.0s")

    if not args.skip_feature:
        step_start = perf_counter()
        print("[5/9] 构建用户行为特征")
        run([
            str(PYTHON_EXE),
            str(feature_script),
            "--cleaned-file", str(cleaned_csv),
            "--label", args.run_name,
            "--recent-window-days", str(args.recent_window_days),
        ], env=feature_env)
        print(f"[5/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[5/9] 跳过特征构建，用时 0.0s")

    if not args.skip_visualize:
        step_start = perf_counter()
        print("[6/9] 用户行为可视化")
        run([
            str(PYTHON_EXE),
            str(visualize_script),
            "--feature-dir", str(label_dir),
        ], env=feature_env)
        print(f"[6/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[6/9] 跳过可视化，用时 0.0s")

    if not args.skip_sliding_windows:
        step_start = perf_counter()
        print("[7/9] 构建滑动窗口高价值活跃轨迹")
        run([
            str(PYTHON_EXE),
            str(sliding_script),
            "--cleaned-file", str(cleaned_csv),
            "--label", args.run_name,
            "--window-days", str(args.sliding_window_days),
            "--step-days", str(args.sliding_step_days),
            "--horizon-days", str(args.sliding_horizon_days),
        ], env=feature_env)
        print(f"[7/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[7/9] 跳过滑动窗口，用时 0.0s")

    if not args.skip_recent_segments:
        step_start = perf_counter()
        print("[8/9] 生成近 7/14/180 天名单")
        run([
            str(PYTHON_EXE),
            str(recent_script),
            "--cleaned-file", str(cleaned_csv),
            "--label", args.run_name,
            "--horizon-days", str(args.recent_horizon_days),
        ], env=feature_env)
        print(f"[8/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[8/9] 跳过名单，用时 0.0s")

    if not args.skip_segmentation:
        step_start = perf_counter()
        print("[9/9] 最终分群落桶")
        run([
            str(PYTHON_EXE),
            str(segmentation_script),
            "--label", args.run_name,
        ], env=feature_env)
        print(f"[9/9] 完成，用时 {perf_counter() - step_start:.1f}s")
    else:
        print("[9/9] 跳过落桶，用时 0.0s")

    print(f"完成，总耗时 {perf_counter() - total_start:.1f}s")


if __name__ == "__main__":
    main()
