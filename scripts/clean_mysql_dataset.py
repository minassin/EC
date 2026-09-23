from __future__ import annotations

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor, as_completed
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MYSQL_EXE = Path(r"D:\mysql-9.7.0-winx64\mysql-9.7.0-winx64\bin\mysql.exe")
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from cleaning.clean import (  # noqa: E402
    EXTRA_FIELD_MAPPING_60,
    build_cleaned_orders,
)

YEAR_SOURCE_TABLE_PATTERN = re.compile(r"(?i)^y\d{2}")

EXCLUDED_SOURCE_TABLES = {
    "桩id",
    "站id",
    "资产id",
}


CRITICAL_COLS = [
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
    "vin",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 MySQL 原始订单表读取数据，复用清洗规则后写回结果表。")
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-port", default="3306")
    parser.add_argument("--db-user", default="root")
    parser.add_argument("--db-password", default="123456")
    parser.add_argument("--db-name", default="ec_all")
    parser.add_argument("--mysql-exe", default=str(MYSQL_EXE))
    parser.add_argument("--table-like", default="%", help="原始表匹配规则，例如 Y20\\_%% 或 dataset\\_%%；默认全部表。")
    parser.add_argument("--source-table", help="直接指定一张原始表，例如对方导入脚本生成的 data。指定后忽略 --table-like。")
    parser.add_argument("--year-label", default="dataset", help="传给清洗规则的年份/批次标签，仅用于日志与部分派生字段。")
    parser.add_argument("--cleaned-table", default="dataset_cleaned_orders")
    parser.add_argument("--abnormal-table", default="dataset_abnormal_orders")
    parser.add_argument("--discard-table", default="dataset_discard_orders")
    parser.add_argument("--overview-table", default="dataset_cleaning_overview")
    parser.add_argument("--if-exists", choices=["replace", "append"], default="replace", help="结果表已存在时的处理方式。")
    parser.add_argument(
        "--clean-scope",
        choices=["full", "incremental"],
        default="full",
        help="full 清洗匹配到的全部原始表；incremental 只清洗结果 overview 表中未出现过的新增原始表。",
    )
    parser.add_argument("--limit-tables", type=int, help="只处理前 N 张表，调试用。")
    parser.add_argument("--workers", type=int, default=1, help="并行进程数，默认 1。")
    parser.add_argument("--dry-run", action="store_true", help="只列出将处理的表，不读取和写入。")
    return parser.parse_args()


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


def run_mysql(args: argparse.Namespace, sql: str, *, capture: bool = False) -> subprocess.CompletedProcess:
    defaults_file = make_defaults_file(args)
    try:
        cmd = [
            str(Path(args.mysql_exe)),
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


def list_source_tables(args: argparse.Namespace) -> list[str]:
    if args.source_table:
        return [args.source_table]
    sql = (
        "SELECT TABLE_NAME\n"
        "FROM information_schema.TABLES\n"
        f"WHERE TABLE_SCHEMA = {mysql_literal(args.db_name)}\n"
        f"  AND TABLE_NAME LIKE {mysql_literal(args.table_like)}\n"
        "ORDER BY TABLE_NAME;"
    )
    result = run_mysql(args, sql, capture=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    tables = [line for line in lines if line != "TABLE_NAME"]
    excluded = {args.cleaned_table, args.abnormal_table, args.discard_table, args.overview_table}
    tables = [
        table
        for table in tables
        if table not in excluded
        and table.strip().lower() not in {name.lower() for name in EXCLUDED_SOURCE_TABLES}
        and YEAR_SOURCE_TABLE_PATTERN.match(table.strip())
    ]
    if args.limit_tables:
        tables = tables[: args.limit_tables]
    return tables


def table_exists_for_args(args: argparse.Namespace, table_name: str) -> bool:
    sql = (
        "SELECT COUNT(*)\n"
        "FROM information_schema.TABLES\n"
        f"WHERE TABLE_SCHEMA = {mysql_literal(args.db_name)}\n"
        f"  AND TABLE_NAME = {mysql_literal(table_name)};"
    )
    result = run_mysql(args, sql, capture=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    values = [line for line in lines if line != "COUNT(*)"]
    return bool(values and int(values[0]) > 0)


def get_processed_source_tables(args: argparse.Namespace) -> set[str]:
    if not table_exists_for_args(args, args.overview_table):
        return set()
    columns_sql = (
        "SELECT COUNT(*)\n"
        "FROM information_schema.COLUMNS\n"
        f"WHERE TABLE_SCHEMA = {mysql_literal(args.db_name)}\n"
        f"  AND TABLE_NAME = {mysql_literal(args.overview_table)}\n"
        "  AND COLUMN_NAME = '来源表';"
    )
    col_result = run_mysql(args, columns_sql, capture=True)
    col_lines = [line.strip() for line in col_result.stdout.splitlines() if line.strip()]
    col_values = [line for line in col_lines if line != "COUNT(*)"]
    if not col_values or int(col_values[0]) == 0:
        return set()
    result = run_mysql(
        args,
        f"SELECT DISTINCT {quote_ident('来源表')} FROM {quote_ident(args.overview_table)} WHERE {quote_ident('来源表')} IS NOT NULL;",
        capture=True,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {line for line in lines if line != "来源表"}


def ensure_local_infile(args: argparse.Namespace) -> None:
    sql = "SET GLOBAL local_infile = 1;"
    try:
        run_mysql(args, sql)
        print("local_infile 已尝试开启。")
    except subprocess.CalledProcessError:
        print("local_infile 自动开启失败，请手动执行: SET GLOBAL local_infile = 1;")


def dump_table_to_temp_tsv(args: argparse.Namespace, table_name: str) -> Path:
    temp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".tsv", encoding="utf-8", newline="")
    temp_path = Path(temp.name)
    temp.close()
    defaults_file = make_defaults_file(args)
    try:
        cmd = [
            str(Path(args.mysql_exe)),
            f"--defaults-extra-file={defaults_file}",
            args.db_name,
            "--default-character-set=utf8mb4",
            "--batch",
            "--quick",
            "-e",
            f"SELECT * FROM {quote_ident(table_name)};",
        ]
        with temp_path.open("w", encoding="utf-8", newline="") as out:
            subprocess.run(cmd, check=True, text=True, encoding="utf-8", errors="replace", stdout=out)
    finally:
        defaults_file.unlink(missing_ok=True)
    return temp_path


def read_raw_table(args: argparse.Namespace, table_name: str) -> pd.DataFrame:
    temp_path = dump_table_to_temp_tsv(args, table_name)
    try:
        raw = pd.read_csv(
            temp_path,
            sep="\t",
            dtype=object,
            keep_default_na=False,
            na_values=["NULL", "\\N"],
            low_memory=False,
        )
    finally:
        temp_path.unlink(missing_ok=True)
    source_columns = list(raw.columns)
    internal_columns = set(EXTRA_FIELD_MAPPING_60.values()) | set(CRITICAL_COLS)
    mapping_60 = {source: target for source, target in EXTRA_FIELD_MAPPING_60.items() if source in raw.columns}
    recognized_columns = {col for col in source_columns if col in internal_columns} | set(mapping_60.values())
    print(
        f"  -> 读取字段: {len(source_columns)} 列；内部标准字段: {len(recognized_columns)}/{len(internal_columns)}；二次字段映射: {len(mapping_60)} 列",
        flush=True,
    )
    if mapping_60:
        raw = raw.rename(columns=mapping_60)
    if raw.columns.duplicated().any():
        dup_count = int(raw.columns.duplicated().sum())
        print(f"  -> 检测到重复列，已去重: {dup_count} 列")
        raw = raw.T.groupby(level=0, sort=False).first().T
    for col in CRITICAL_COLS:
        if col not in raw.columns:
            raw[col] = pd.NA
    return raw


def normalize_for_tsv(value: object) -> str:
    if pd.isna(value):
        return r"\N"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    return r"\N" if text == "" or text == "NaT" else text


def create_table_sql(table_name: str, columns: list[str]) -> str:
    cols = ",\n  ".join(f"{quote_ident(col)} LONGTEXT NULL DEFAULT NULL" for col in columns)
    return (
        "SET NAMES utf8mb4;\n"
        f"CREATE TABLE IF NOT EXISTS {quote_ident(table_name)} (\n  {cols}\n) "
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci ROW_FORMAT=DYNAMIC;"
    )


def existing_columns(args: argparse.Namespace, table_name: str) -> set[str]:
    sql = (
        "SELECT COLUMN_NAME\n"
        "FROM information_schema.COLUMNS\n"
        f"WHERE TABLE_SCHEMA = {mysql_literal(args.db_name)}\n"
        f"  AND TABLE_NAME = {mysql_literal(table_name)}\n"
        "ORDER BY ORDINAL_POSITION;"
    )
    result = run_mysql(args, sql, capture=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {line for line in lines if line != "COLUMN_NAME"}


def ensure_result_table(args: argparse.Namespace, table_name: str, columns: list[str], *, first_write: bool) -> None:
    if first_write and args.if_exists == "replace":
        run_mysql(args, f"DROP TABLE IF EXISTS {quote_ident(table_name)};")
    current = existing_columns(args, table_name)
    if not current:
        run_mysql(args, create_table_sql(table_name, columns))
        return
    missing = [col for col in columns if col not in current]
    if missing:
        alter = ", ".join(f"ADD COLUMN {quote_ident(col)} LONGTEXT NULL DEFAULT NULL" for col in missing)
        run_mysql(args, f"ALTER TABLE {quote_ident(table_name)} {alter};")


def load_dataframe(args: argparse.Namespace, df: pd.DataFrame, table_name: str) -> None:
    if df.empty:
        return
    columns = list(df.columns)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".tsv", encoding="utf-8", newline="") as tf:
        temp_path = Path(tf.name)
        writer = csv.writer(tf, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(columns)
        for row in df.itertuples(index=False, name=None):
            writer.writerow([normalize_for_tsv(value) for value in row])
    try:
        load_sql = (
            "SET NAMES utf8mb4;\n"
            f"LOAD DATA LOCAL INFILE {mysql_literal(str(temp_path))}\n"
            f"INTO TABLE {quote_ident(table_name)}\n"
            "CHARACTER SET utf8mb4\n"
            "FIELDS TERMINATED BY '\\t'\n"
            "OPTIONALLY ENCLOSED BY '\"'\n"
            "ESCAPED BY '\\\\'\n"
            "LINES TERMINATED BY '\\n'\n"
            "IGNORE 1 LINES\n"
            f"({', '.join(quote_ident(col) for col in columns)});"
        )
        run_mysql(args, load_sql)
    finally:
        temp_path.unlink(missing_ok=True)


def add_source_columns(df: pd.DataFrame, source_table: str) -> pd.DataFrame:
    result = df.copy()
    result.insert(0, "来源表", source_table)
    return result


def overview_metric(overview: pd.DataFrame, name: str, default: int = 0) -> int:
    if overview.empty or "指标" not in overview.columns or "数值" not in overview.columns:
        return default
    matched = overview.loc[overview["指标"].eq(name), "数值"]
    if matched.empty:
        return default
    value = pd.to_numeric(matched.iloc[0], errors="coerce")
    return default if pd.isna(value) else int(value)


def make_worker_config(args: argparse.Namespace) -> dict[str, object]:
    return {
        "db_host": args.db_host,
        "db_port": args.db_port,
        "db_user": args.db_user,
        "db_password": args.db_password,
        "db_name": args.db_name,
        "mysql_exe": args.mysql_exe,
        "year_label": args.year_label,
    }


def run_one_table_worker(payload: tuple[str, dict[str, object]]) -> dict[str, object]:
    table_name, config = payload
    worker_args = argparse.Namespace(**config)
    started = perf_counter()
    print(f"[{table_name}] 开始读取并清洗", flush=True)
    raw = read_raw_table(worker_args, table_name)
    cleaned, abnormal, discard, overview, _, _, _ = build_cleaned_orders(
        raw,
        worker_args.year_label,
        generate_charts=False,
    )
    cleaned = add_source_columns(cleaned, table_name)
    abnormal = add_source_columns(abnormal, table_name)
    discard = add_source_columns(discard, table_name)
    overview = add_source_columns(overview, table_name)

    tmp_dir = Path(tempfile.gettempdir()) / "ec_mysql_clean"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cleaned_path = tmp_dir / f"{table_name}__cleaned.tsv"
    abnormal_path = tmp_dir / f"{table_name}__abnormal.tsv"
    discard_path = tmp_dir / f"{table_name}__discard.tsv"
    overview_path = tmp_dir / f"{table_name}__overview.tsv"
    cleaned.to_csv(cleaned_path, index=False, encoding="utf-8-sig", sep="\t", lineterminator="\n")
    abnormal.to_csv(abnormal_path, index=False, encoding="utf-8-sig", sep="\t", lineterminator="\n")
    discard.to_csv(discard_path, index=False, encoding="utf-8-sig", sep="\t", lineterminator="\n")
    overview.to_csv(overview_path, index=False, encoding="utf-8-sig", sep="\t", lineterminator="\n")
    print(
        f"[{table_name}] 完成读取并清洗: 原始 {len(raw):,} 行, 清洗 {len(cleaned):,} 行, 异常 {len(abnormal):,} 行, 废弃 {len(discard):,} 行, 用时 {perf_counter() - started:.1f}s",
        flush=True,
    )

    return {
        "table": table_name,
        "rows_raw": len(raw),
        "rows_before_discard": overview_metric(overview, "时间范围内订单数", len(raw)),
        "rows_after_discard": overview_metric(overview, "废弃规则后保留订单数", len(cleaned) + len(discard)),
        "restart_before": overview_metric(overview, "短时重启合并前订单数", len(cleaned)),
        "restart_after": overview_metric(overview, "短时重启合并后会话数", len(cleaned)),
        "restart_reduced": overview_metric(overview, "短时重启合并减少订单数", 0),
        "restart_groups": overview_metric(overview, "短时重启合并组数", 0),
        "rows_cleaned": len(cleaned),
        "rows_abnormal": len(abnormal),
        "rows_discard": len(discard),
        "cleaned_path": cleaned_path,
        "abnormal_path": abnormal_path,
        "discard_path": discard_path,
        "overview_path": overview_path,
        "seconds": round(perf_counter() - started, 1),
    }


def main() -> None:
    args = parse_args()
    mysql_exe = Path(args.mysql_exe)
    if not mysql_exe.exists():
        raise SystemExit(f"mysql.exe 不存在: {mysql_exe}")

    ensure_local_infile(args)
    tables = list_source_tables(args)
    if args.clean_scope == "incremental":
        processed_tables = get_processed_source_tables(args)
        before_count = len(tables)
        tables = [table for table in tables if table not in processed_tables]
        args.if_exists = "append"
        print(
            f"增量清洗模式: 匹配原始表 {before_count} 张，已清洗 {len(processed_tables)} 张，本次新增待清洗 {len(tables)} 张。",
            flush=True,
        )
    if not tables:
        print(f"未找到需要清洗的原始表: {args.table_like}")
        return
    print(f"待处理原始表: {len(tables)} 张")
    for table in tables[:10]:
        print(f"  - {table}")
    if len(tables) > 10:
        print(f"  ... 其余 {len(tables) - 10} 张省略")
    if args.dry_run:
        return

    total_start = perf_counter()
    first_cleaned = True
    first_abnormal = True
    first_discard = True
    first_overview = True
    total_raw = 0
    total_before_discard = 0
    total_after_discard = 0
    total_restart_before = 0
    total_restart_after = 0
    total_restart_reduced = 0
    total_restart_groups = 0
    total_cleaned = 0
    total_abnormal = 0
    total_discard = 0

    workers = max(1, int(args.workers or 1))
    if workers == 1:
        for index, table_name in enumerate(tables, start=1):
            started = perf_counter()
            print(f"\n[{index}/{len(tables)}] 开始处理: {table_name}", flush=True)
            raw = read_raw_table(args, table_name)
            total_raw += len(raw)
            print(f"  -> 原始行数: {len(raw):,}", flush=True)

            cleaned, abnormal, discard, overview, _, _, _ = build_cleaned_orders(
                raw,
                args.year_label,
                generate_charts=False,
            )
            cleaned = add_source_columns(cleaned, table_name)
            abnormal = add_source_columns(abnormal, table_name)
            discard = add_source_columns(discard, table_name)
            overview = add_source_columns(overview, table_name)
            before_discard = overview_metric(overview, "时间范围内订单数", len(raw))
            after_discard = overview_metric(overview, "废弃规则后保留订单数", len(cleaned) + len(discard))
            restart_before = overview_metric(overview, "短时重启合并前订单数", len(cleaned))
            restart_after = overview_metric(overview, "短时重启合并后会话数", len(cleaned))
            restart_reduced = overview_metric(overview, "短时重启合并减少订单数", 0)
            restart_groups = overview_metric(overview, "短时重启合并组数", 0)

            ensure_result_table(args, args.cleaned_table, list(cleaned.columns), first_write=first_cleaned)
            ensure_result_table(args, args.abnormal_table, list(abnormal.columns), first_write=first_abnormal)
            ensure_result_table(args, args.discard_table, list(discard.columns), first_write=first_discard)
            ensure_result_table(args, args.overview_table, list(overview.columns), first_write=first_overview)
            first_cleaned = False
            first_abnormal = False
            first_discard = False
            first_overview = False

            load_dataframe(args, cleaned, args.cleaned_table)
            load_dataframe(args, abnormal, args.abnormal_table)
            load_dataframe(args, discard, args.discard_table)
            load_dataframe(args, overview, args.overview_table)

            total_before_discard += before_discard
            total_after_discard += after_discard
            total_restart_before += restart_before
            total_restart_after += restart_after
            total_restart_reduced += restart_reduced
            total_restart_groups += restart_groups
            total_cleaned += len(cleaned)
            total_abnormal += len(abnormal)
            total_discard += len(discard)
            elapsed = perf_counter() - started
            print(
                f"  -> 废弃处理: 前 {before_discard:,} 行，后 {after_discard:,} 行，缩减 {len(discard):,} 行",
                flush=True,
            )
            print(
                f"  -> 短时重启合并: 前 {restart_before:,} 行，后 {restart_after:,} 个会话，减少 {restart_reduced:,} 行，合并组 {restart_groups:,} 组",
                flush=True,
            )
            print(
                f"  -> 完成: 合并后清洗 {len(cleaned):,} 行，异常 {len(abnormal):,} 行，废弃 {len(discard):,} 行，"
                f"写入 {args.cleaned_table}/{args.abnormal_table}/{args.discard_table} | {elapsed:.1f}s",
                flush=True,
            )
    else:
        print(f"启用并行处理: {workers} 个进程", flush=True)
        worker_config = make_worker_config(args)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(run_one_table_worker, (table_name, worker_config)): (index, table_name)
                for index, table_name in enumerate(tables, start=1)
            }
            print(f"已提交 {len(tables)} 个表到并行队列", flush=True)
            for future in as_completed(future_map):
                index, table_name = future_map[future]
                result = future.result()
                print(f"\n[{index}/{len(tables)}] 处理完成: {table_name}", flush=True)
                total_raw += int(result["rows_raw"])
                total_before_discard += int(result["rows_before_discard"])
                total_after_discard += int(result["rows_after_discard"])
                total_restart_before += int(result["restart_before"])
                total_restart_after += int(result["restart_after"])
                total_restart_reduced += int(result["restart_reduced"])
                total_restart_groups += int(result["restart_groups"])
                total_cleaned += int(result["rows_cleaned"])
                total_abnormal += int(result["rows_abnormal"])
                total_discard += int(result["rows_discard"])

                cleaned_df = pd.read_csv(result["cleaned_path"], sep="\t", encoding="utf-8-sig", dtype=object, low_memory=False)
                abnormal_df = pd.read_csv(result["abnormal_path"], sep="\t", encoding="utf-8-sig", dtype=object, low_memory=False)
                discard_df = pd.read_csv(result["discard_path"], sep="\t", encoding="utf-8-sig", dtype=object, low_memory=False)
                overview_df = pd.read_csv(result["overview_path"], sep="\t", encoding="utf-8-sig", dtype=object, low_memory=False)

                ensure_result_table(args, args.cleaned_table, list(cleaned_df.columns), first_write=first_cleaned)
                ensure_result_table(args, args.abnormal_table, list(abnormal_df.columns), first_write=first_abnormal)
                ensure_result_table(args, args.discard_table, list(discard_df.columns), first_write=first_discard)
                ensure_result_table(args, args.overview_table, list(overview_df.columns), first_write=first_overview)
                first_cleaned = False
                first_abnormal = False
                first_discard = False
                first_overview = False

                load_dataframe(args, cleaned_df, args.cleaned_table)
                load_dataframe(args, abnormal_df, args.abnormal_table)
                load_dataframe(args, discard_df, args.discard_table)
                load_dataframe(args, overview_df, args.overview_table)

                Path(result["cleaned_path"]).unlink(missing_ok=True)
                Path(result["abnormal_path"]).unlink(missing_ok=True)
                Path(result["discard_path"]).unlink(missing_ok=True)
                Path(result["overview_path"]).unlink(missing_ok=True)

                print(
                    f"  -> 废弃处理: 前 {int(result['rows_before_discard']):,} 行，后 {int(result['rows_after_discard']):,} 行，缩减 {int(result['rows_discard']):,} 行",
                    flush=True,
                )
                print(
                    f"  -> 短时重启合并: 前 {int(result['restart_before']):,} 行，后 {int(result['restart_after']):,} 个会话，减少 {int(result['restart_reduced']):,} 行，合并组 {int(result['restart_groups']):,} 组",
                    flush=True,
                )
                print(
                    f"  -> 完成: 合并后清洗 {int(result['rows_cleaned']):,} 行，异常 {int(result['rows_abnormal']):,} 行，废弃 {int(result['rows_discard']):,} 行，"
                    f"写入 {args.cleaned_table}/{args.abnormal_table}/{args.discard_table} | {result['seconds']:.1f}s",
                    flush=True,
                )

    total_elapsed = perf_counter() - total_start
    print("\n========== 数据库清洗完成 ==========")
    print(f"原始订单: {total_raw:,}")
    print(f"废弃处理前订单: {total_before_discard:,}")
    print(f"废弃处理后保留: {total_after_discard:,}")
    print(f"废弃缩减订单: {total_discard:,}")
    print(f"短时重启合并前订单: {total_restart_before:,}")
    print(f"短时重启合并后会话: {total_restart_after:,}")
    print(f"短时重启合并减少订单: {total_restart_reduced:,}")
    print(f"短时重启合并组数: {total_restart_groups:,}")
    print(f"短时重启合并后清洗订单: {total_cleaned:,}")
    print(f"异常订单: {total_abnormal:,}")
    print(f"废弃订单: {total_discard:,}")
    print(f"结果表: {args.cleaned_table}, {args.abnormal_table}, {args.discard_table}, {args.overview_table}")
    print(f"总耗时: {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
