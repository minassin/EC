from __future__ import annotations

import argparse
import csv
import re
import subprocess
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Iterable

import pandas as pd
from openpyxl import load_workbook
from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MYSQL_EXE = Path(r"D:\mysql-9.7.0-winx64\mysql-9.7.0-winx64\bin\mysql.exe")


BASE_COLUMNS = [
    "交易流水号",
    "第三方交易流水号",
    "充电方式",
    "订单状态",
    "订单渠道",
    "订单来源",
    "业务类型",
    "交易电量（kwh）",
    "电费",
    "服务费",
    "交易金额",
    "实扣金额",
    "实扣电费",
    "实扣服务费",
    "优惠金额",
    "优惠电费",
    "优惠服务费",
    "支付流水号",
    "支付方式",
    "冻结金额",
    "订单支付时间",
    "优惠券类型编号",
    "优惠券优惠金额",
    "立减活动编号",
    "立减优惠金额",
    "其他优惠类型",
    "其他优惠名称",
    "其他优惠金额详情",
    "订单创建时间",
    "充电开始时间",
    "充电结束时间",
    "订单上送时间",
    "交易结束原因",
    "是否后付费",
    "用户类型",
    "产权单位名称",
    "产权单位编码",
    "运营单位编码",
    "监管单位编码",
    "运维单位编码",
    "充电桩编号",
    "充电站ID",
    "充电站",
    "电费计费模型Id",
    "服务费计费模型Id",
    "尖电量（kwh）",
    "峰电量（kwh）",
    "平电量（kwh）",
    "谷电量（kwh）",
    "深谷电量（kwh）",
    "抄表电量（kwh）",
    "电表总起值",
    "电表总止值",
    "用户编码",
    "卡号",
    "手机号",
    "单位用户证件号",
    "VIN码",
    "车牌号",
    "清分状态",
    "清分ID",
    "清分时间",
    "开票状态",
    "发票序列标识",
    "充电枪编号",
]

COLUMN_RENAME_MAP = {
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
    "实扣金额": "A_amount",
    "实扣电费": "A_EF",
    "实扣服务费": "A_SF",
    "优惠金额": "discount",
    "优惠电费": "EF_discount",
    "优惠服务费": "SF_discount",
    "支付流水号": "pay_id",
    "支付方式": "pay_method",
    "冻结金额": "frozen_amount",
    "订单支付时间": "pay_time",
    "优惠券类型编号": "coupon_type_id",
    "优惠券优惠金额": "coupon_discount",
    "立减活动编号": "promotion_id",
    "立减优惠金额": "promotion_discount",
    "其他优惠类型": "other_discount_type",
    "其他优惠名称": "other_discount_name",
    "其他优惠金额详情": "other_discount",
    "订单创建时间": "create_time",
    "充电开始时间": "start_time",
    "充电结束时间": "end_time",
    "订单上送时间": "upload_time",
    "交易结束原因": "end_reason",
    "是否后付费": "is_postpaid",
    "用户类型": "user_type",
    "产权单位名称": "owner_org_name",
    "产权单位编码": "owner_org_id",
    "运营单位编码": "operator_org_id",
    "监管单位编码": "regulator_org_id",
    "运维单位编码": "maintainer_org_id",
    "充电桩编号": "charger_id",
    "充电站ID": "station_id",
    "充电站": "station_name",
    "充电站名称": "station_name",
    "电费计费模型Id": "EF_model_id",
    "服务费计费模型Id": "SF_model_id",
    "尖电量（kwh）": "sharp_kwh",
    "峰电量（kwh）": "peak_kwh",
    "平电量（kwh）": "flat_kwh",
    "谷电量（kwh）": "valley_kwh",
    "深谷电量（kwh）": "deep_valley_kwh",
    "抄表电量（kwh）": "meter_kwh",
    "电表总起值": "meter_start",
    "电表总止值": "meter_end",
    "用户编码": "user_id",
    "卡号": "card_id",
    "卡交易前余额": "card_balance_before",
    "卡交易后余额": "card_balance_after",
    "手机号": "phone",
    "单位用户证件号": "org_user_id_no",
    "VIN码": "vin",
    "车牌号": "plate_no",
    "清分状态": "clearing_status",
    "清分ID": "clearing_id",
    "清分时间": "clearing_time",
    "开票状态": "invoice_status",
    "发票序列标识": "invoice_no",
    "充电枪编号": "gun_id",
    "账款额度ID": "credit_id",
}

COLUMNS_INFO = {
    "order_id": "VARCHAR(255)",
    "third_party_order_id": "VARCHAR(255)",
    "charge_method": "VARCHAR(255)",
    "status": "VARCHAR(255)",
    "channel": "VARCHAR(255)",
    "source": "VARCHAR(255)",
    "business_type": "VARCHAR(255)",
    "kwh": "DOUBLE",
    "EF": "DOUBLE",
    "SF": "DOUBLE",
    "amount": "DOUBLE",
    "A_amount": "DOUBLE",
    "A_EF": "DOUBLE",
    "A_SF": "DOUBLE",
    "discount": "DOUBLE",
    "EF_discount": "DOUBLE",
    "SF_discount": "DOUBLE",
    "pay_id": "VARCHAR(255)",
    "pay_method": "VARCHAR(255)",
    "frozen_amount": "VARCHAR(255)",
    "pay_time": "DATETIME",
    "coupon_type_id": "VARCHAR(255)",
    "coupon_discount": "DOUBLE",
    "promotion_id": "VARCHAR(255)",
    "promotion_discount": "DOUBLE",
    "other_discount_type": "VARCHAR(255)",
    "other_discount_name": "VARCHAR(255)",
    "other_discount": "DOUBLE",
    "create_time": "DATETIME",
    "start_time": "DATETIME",
    "end_time": "DATETIME",
    "upload_time": "DATETIME",
    "end_reason": "VARCHAR(255)",
    "is_postpaid": "VARCHAR(255)",
    "user_type": "VARCHAR(255)",
    "owner_org_name": "VARCHAR(255)",
    "owner_org_id": "VARCHAR(255)",
    "operator_org_id": "VARCHAR(255)",
    "regulator_org_id": "VARCHAR(255)",
    "maintainer_org_id": "VARCHAR(255)",
    "charger_id": "BIGINT",
    "station_id": "VARCHAR(255)",
    "station_name": "VARCHAR(255)",
    "EF_model_id": "VARCHAR(255)",
    "SF_model_id": "VARCHAR(255)",
    "sharp_kwh": "DOUBLE",
    "peak_kwh": "DOUBLE",
    "flat_kwh": "DOUBLE",
    "valley_kwh": "DOUBLE",
    "deep_valley_kwh": "DOUBLE",
    "meter_kwh": "DOUBLE",
    "meter_start": "DOUBLE",
    "meter_end": "DOUBLE",
    "user_id": "BIGINT",
    "card_id": "VARCHAR(255)",
    "card_balance_before": "DOUBLE",
    "card_balance_after": "DOUBLE",
    "phone": "VARCHAR(255)",
    "org_user_id_no": "VARCHAR(255)",
    "vin": "VARCHAR(255)",
    "plate_no": "VARCHAR(255)",
    "clearing_status": "VARCHAR(255)",
    "clearing_id": "VARCHAR(255)",
    "clearing_time": "DATETIME",
    "invoice_status": "VARCHAR(255)",
    "invoice_no": "VARCHAR(255)",
    "gun_id": "BIGINT",
    "credit_id": "VARCHAR(255)",
}

STANDARD_COLUMNS = list(dict.fromkeys(COLUMN_RENAME_MAP.values()))
ZERO_DATETIME_COLUMNS = ["pay_time", "create_time", "start_time", "end_time", "upload_time", "clearing_time"]


NUMERIC_COLUMNS = {
    "交易电量（kwh）",
    "电费",
    "服务费",
    "交易金额",
    "实扣金额",
    "实扣电费",
    "实扣服务费",
    "优惠金额",
    "优惠电费",
    "优惠服务费",
    "冻结金额",
    "优惠券优惠金额",
    "立减优惠金额",
    "产权单位编码",
    "运营单位编码",
    "监管单位编码",
    "运维单位编码",
    "充电桩编号",
    "电费计费模型Id",
    "服务费计费模型Id",
    "尖电量（kwh）",
    "峰电量（kwh）",
    "平电量（kwh）",
    "谷电量（kwh）",
    "深谷电量（kwh）",
    "抄表电量（kwh）",
    "电表总起值",
    "电表总止值",
    "用户编码",
    "清分ID",
    "充电枪编号",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="递归导入目录下的 Excel 到 MySQL，每个文件一张表。")
    parser.add_argument("--input-dir", required=True, help="包含 xlsx 或其子目录的根目录。")
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-port", default="3306")
    parser.add_argument("--db-user", default="root")
    parser.add_argument("--db-password", default="123456")
    parser.add_argument("--db-name", default="ec_all")
    parser.add_argument("--batch-rows", type=int, default=50000, help="每次导入的行数。")
    parser.add_argument("--mysql-exe", default=str(MYSQL_EXE), help="mysql.exe 路径。")
    parser.add_argument("--dry-run", action="store_true", help="只生成计划，不写库。")
    parser.add_argument("--force-reimport", "--reimport", action="store_true", help="即使表已存在且行数一致，也强制删表重导。")
    return parser.parse_args()


def discover_excel_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
            continue
        files.append(path)
    return sorted(files)


def safe_ident(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text, flags=re.UNICODE).strip("_")
    text = re.sub(r"_+", "_", text)
    if not text:
        text = "table"
    if re.match(r"^\d", text):
        text = f"t_{text}"
    return text[:60]


def build_table_name(root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(root).with_suffix("")
    parts = [safe_ident(part) for part in rel.parts]
    return safe_ident("__".join(parts))


def mysql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def mysql_type_for(column: str) -> str:
    return COLUMNS_INFO.get(column, "VARCHAR(255)")


def build_create_table_sql(table_name: str, columns: list[str]) -> str:
    col_defs = ["`id` BIGINT AUTO_INCREMENT PRIMARY KEY"]
    for col in columns:
        col_defs.append(f"`{col}` {mysql_type_for(col)} NULL DEFAULT NULL")
    col_defs.append("`source_file` VARCHAR(500)")
    return "\n".join(
        [
            "SET NAMES utf8mb4;",
            "SET SESSION sql_mode = 'ALLOW_INVALID_DATES';",
            "SET FOREIGN_KEY_CHECKS = 0;",
            f"DROP TABLE IF EXISTS `{table_name}`;",
            f"CREATE TABLE `{table_name}` (",
            "  " + ",\n  ".join(col_defs),
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;",
            "SET FOREIGN_KEY_CHECKS = 1;",
        ]
    )


def run_mysql_sql(mysql_exe: Path, host: str, port: str, user: str, password: str, db_name: str, sql: str) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cnf", encoding="utf-8", newline="\n") as tf:
        defaults_file = Path(tf.name)
        tf.write("[client]\n")
        tf.write(f"host={host}\n")
        tf.write(f"port={port}\n")
        tf.write(f"user={user}\n")
        tf.write(f"password={password}\n")
        tf.write("default-character-set=utf8mb4\n")
        tf.write("local-infile=1\n")

    try:
        cmd = [
            str(mysql_exe),
            f"--defaults-extra-file={defaults_file}",
            db_name,
            "--default-character-set=utf8mb4",
            "--local-infile=1",
            "-e",
            sql,
        ]
        subprocess.run(cmd, check=True)
    finally:
        defaults_file.unlink(missing_ok=True)


def run_mysql_query(mysql_exe: Path, host: str, port: str, user: str, password: str, db_name: str, sql: str) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cnf", encoding="utf-8", newline="\n") as tf:
        defaults_file = Path(tf.name)
        tf.write("[client]\n")
        tf.write(f"host={host}\n")
        tf.write(f"port={port}\n")
        tf.write(f"user={user}\n")
        tf.write(f"password={password}\n")
        tf.write("default-character-set=utf8mb4\n")
        tf.write("local-infile=1\n")

    try:
        cmd = [
            str(mysql_exe),
            f"--defaults-extra-file={defaults_file}",
            db_name,
            "--default-character-set=utf8mb4",
            "--local-infile=1",
            "--batch",
            "--skip-column-names",
            "-e",
            sql,
        ]
        result = subprocess.run(cmd, check=True, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE)
        return result.stdout
    finally:
        defaults_file.unlink(missing_ok=True)


def ensure_database(mysql_exe: Path, host: str, port: str, user: str, password: str, db_name: str) -> None:
    sql = f"CREATE DATABASE IF NOT EXISTS `{db_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
    run_mysql_sql(mysql_exe, host, port, user, password, "mysql", sql)


def ensure_local_infile(mysql_exe: Path, host: str, port: str, user: str, password: str, db_name: str) -> None:
    sql = """
SHOW VARIABLES LIKE 'local_infile';
SET GLOBAL local_infile = 1;
SHOW VARIABLES LIKE 'local_infile';
"""
    try:
        run_mysql_sql(mysql_exe, host, port, user, password, db_name, sql)
        print("local_infile 已尝试开启。")
    except subprocess.CalledProcessError:
        print("local_infile 自动开启失败，请手动在 MySQL 服务器与客户端都启用后重试。")


def table_exists(mysql_exe: Path, host: str, port: str, user: str, password: str, db_name: str, table_name: str) -> bool:
    sql = (
        "SELECT COUNT(*) "
        "FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = {mysql_literal(db_name)} "
        f"  AND TABLE_NAME = {mysql_literal(table_name)};"
    )
    out = run_mysql_query(mysql_exe, host, port, user, password, db_name, sql).strip()
    return out not in {"", "0"}


def get_table_columns(mysql_exe: Path, host: str, port: str, user: str, password: str, db_name: str, table_name: str) -> list[str]:
    sql = (
        "SELECT COLUMN_NAME "
        "FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = {mysql_literal(db_name)} "
        f"  AND TABLE_NAME = {mysql_literal(table_name)} "
        "ORDER BY ORDINAL_POSITION;"
    )
    out = run_mysql_query(mysql_exe, host, port, user, password, db_name, sql)
    return [line.strip() for line in out.splitlines() if line.strip()]


def get_table_row_count(mysql_exe: Path, host: str, port: str, user: str, password: str, db_name: str, table_name: str) -> int:
    sql = f"SELECT COUNT(*) FROM `{table_name}`;"
    out = run_mysql_query(mysql_exe, host, port, user, password, db_name, sql).strip()
    return int(out or 0)


def get_source_row_count(file_path: Path) -> int:
    workbook = load_workbook(file_path, read_only=True, data_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        max_row = int(sheet.max_row or 0)
        return max(0, max_row - 1)
    finally:
        workbook.close()


def normalize_cell(value) -> str:
    if value is None:
        return r"\N"
    try:
        if value != value:  # NaN
            return r"\N"
    except Exception:
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def load_rows_from_sheet(file_path: Path) -> tuple[list[str], Iterable[list[str]]]:
    header_df = pd.read_excel(file_path, nrows=1, header=None, engine="openpyxl", dtype=object)
    if header_df.empty:
        return [], []

    headers = [normalize_cell(v).strip() for v in header_df.iloc[0].tolist()]
    headers = [h for h in headers if h and h != "nan"]
    headers = [COLUMN_RENAME_MAP.get(h, h) for h in headers]
    unique_headers: list[str] = []
    seen_headers: set[str] = set()
    for header in headers:
        if header not in seen_headers:
            unique_headers.append(header)
            seen_headers.add(header)
    headers = unique_headers
    if not headers:
        return [], []

    workbook = load_workbook(file_path, read_only=True, data_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        rows = sheet.iter_rows(min_row=2, min_col=1, max_col=len(headers), values_only=True)

        def row_iter():
            try:
                for row in rows:
                    yield [normalize_cell(v) for v in row[:len(headers)]]
            finally:
                workbook.close()

        return headers, row_iter()
    except Exception:
        workbook.close()
        raise


def build_columns(source_headers: list[str]) -> list[str]:
    return list(STANDARD_COLUMNS)


def report_header_mapping(source_headers: list[str], final_columns: list[str]) -> None:
    source_set = {h for h in source_headers if h}
    base_set = set(STANDARD_COLUMNS)
    matched = [c for c in STANDARD_COLUMNS if c in source_set]
    missing = [c for c in STANDARD_COLUMNS if c not in source_set]
    extras = [c for c in source_headers if c and c not in base_set]
    print(f"  -> 识别到源表头: {len(source_headers)} 列")
    print(f"  -> 基础字段匹配: {len(matched)}/{len(STANDARD_COLUMNS)}")
    if missing:
        preview = "、".join(missing[:12])
        if len(missing) > 12:
            preview += "..."
        print(f"  -> 未匹配基础字段: {preview}")
    if extras:
        preview = "、".join(extras[:8])
        if len(extras) > 8:
            preview += "..."
        print(f"  -> 额外字段: {preview}")


def expected_table_columns() -> set[str]:
    return {"id", *STANDARD_COLUMNS, "source_file"}


def normalize_zero_datetimes(
    mysql_exe: Path,
    host: str,
    port: str,
    user: str,
    password: str,
    db_name: str,
    table_name: str,
) -> None:
    assignments = ", ".join(
        f"`{column}` = NULL"
        for column in ZERO_DATETIME_COLUMNS
    )
    run_mysql_sql(
        mysql_exe,
        host,
        port,
        user,
        password,
        db_name,
        f"SET SESSION sql_mode = 'ALLOW_INVALID_DATES'; "
        f"UPDATE `{table_name}` SET {assignments} "
        "WHERE " + " OR ".join(f"`{column}` = '0000-00-00 00:00:00'" for column in ZERO_DATETIME_COLUMNS) + ";",
    )


def value_sql(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        txt = value.strip()
        return "NULL" if txt == "" else mysql_literal(txt)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return "NULL"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    txt = str(value).strip()
    return "NULL" if txt == "" else mysql_literal(txt)


def import_one_file(
    file_path: Path,
    root: Path,
    mysql_exe: Path,
    host: str,
    port: str,
    user: str,
    password: str,
    db_name: str,
    batch_rows: int,
    dry_run: bool = False,
    force_reimport: bool = False,
) -> dict[str, object]:
    table_name = build_table_name(root, file_path)
    print(f"  -> 目标表: `{table_name}`")
    print(f"  -> 正在读取: {file_path.name}")

    source_rows = get_source_row_count(file_path)
    if not force_reimport and table_exists(mysql_exe, host, port, user, password, db_name, table_name):
        existing_rows = get_table_row_count(mysql_exe, host, port, user, password, db_name, table_name)
        existing_columns = set(get_table_columns(mysql_exe, host, port, user, password, db_name, table_name))
        if existing_rows == source_rows and expected_table_columns().issubset(existing_columns):
            print(f"  -> 已存在且行数一致，跳过导入: `{table_name}` ({existing_rows} 行)")
            return {"file": file_path.name, "table": table_name, "rows": existing_rows, "status": "skipped"}
        if existing_rows == source_rows:
            print(f"  -> 行数一致但表结构不是兼容结构，准备重导: `{table_name}`")
        print(f"  -> 已存在但行数不一致，准备重导: 现有 {existing_rows} 行，源文件 {source_rows} 行")
    elif force_reimport and table_exists(mysql_exe, host, port, user, password, db_name, table_name):
        print(f"  -> 强制重导：将覆盖已有表 `{table_name}`")

    headers, row_iter = load_rows_from_sheet(file_path)
    if not headers:
        return {"file": file_path.name, "table": table_name, "rows": 0, "status": "empty"}

    columns = build_columns(headers)
    report_header_mapping(headers, columns)
    header_index = {name: idx for idx, name in enumerate(headers)}

    if dry_run:
        print(f"[DRY] {file_path} -> `{table_name}`")
        return {"file": file_path.name, "table": table_name, "rows": 0, "status": "dry-run"}

    print(f"  -> 正在建表: `{table_name}`")
    if force_reimport and table_exists(mysql_exe, host, port, user, password, db_name, table_name):
        run_mysql_sql(mysql_exe, host, port, user, password, db_name, f"DROP TABLE IF EXISTS `{table_name}`;")
    run_mysql_sql(mysql_exe, host, port, user, password, db_name, build_create_table_sql(table_name, columns))

    total_rows = 0
    batch: list[list[str]] = []
    batch_index = 0
    tmp_dir = Path(tempfile.gettempdir()) / "ec_mysql_import"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for row in tqdm(row_iter, desc=f"{file_path.name} 导入", unit="行", mininterval=1.0, dynamic_ncols=True):
        mapped: list[str] = []
        for col in columns:
            idx = header_index.get(col)
            mapped.append(row[idx] if idx is not None and idx < len(row) and row[idx] != "" else r"\N")
        batch.append(mapped)
        total_rows += 1

        if len(batch) >= batch_rows:
            batch_index += 1
            print(f"  -> 写入批次 {batch_index}: {len(batch):,} 行")
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", dir=tmp_dir, encoding="utf-8-sig", newline="") as tf:
                temp_csv = Path(tf.name)
                writer = csv.writer(tf, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
                writer.writerow(columns)
                writer.writerows(batch)
            try:
                load_sql = (
                    "SET NAMES utf8mb4;\n"
                    f"LOAD DATA LOCAL INFILE {mysql_literal(str(temp_csv))}\n"
                    f"INTO TABLE `{table_name}`\n"
                    "CHARACTER SET utf8mb4\n"
                    "FIELDS TERMINATED BY '\\t'\n"
                    "OPTIONALLY ENCLOSED BY '\"'\n"
                    "ESCAPED BY '\\\\'\n"
                    "LINES TERMINATED BY '\\n'\n"
                    "IGNORE 1 LINES\n"
                    f"({', '.join(f'`{c}`' for c in columns)})\n"
                    f"SET `source_file` = {mysql_literal(file_path.name)};\n"
                )
                run_mysql_sql(mysql_exe, host, port, user, password, db_name, load_sql)
            finally:
                temp_csv.unlink(missing_ok=True)
            batch.clear()

    if batch:
        batch_index += 1
        print(f"  -> 写入批次 {batch_index}: {len(batch):,} 行")
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", dir=tmp_dir, encoding="utf-8-sig", newline="") as tf:
            temp_csv = Path(tf.name)
            writer = csv.writer(tf, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows(batch)
        try:
            load_sql = (
                "SET NAMES utf8mb4;\n"
                f"LOAD DATA LOCAL INFILE {mysql_literal(str(temp_csv))}\n"
                f"INTO TABLE `{table_name}`\n"
                "CHARACTER SET utf8mb4\n"
                "FIELDS TERMINATED BY '\\t'\n"
                "OPTIONALLY ENCLOSED BY '\"'\n"
                "ESCAPED BY '\\\\'\n"
                "LINES TERMINATED BY '\\n'\n"
                "IGNORE 1 LINES\n"
                f"({', '.join(f'`{c}`' for c in columns)})\n"
                f"SET `source_file` = {mysql_literal(file_path.name)};\n"
            )
            run_mysql_sql(mysql_exe, host, port, user, password, db_name, load_sql)
        finally:
            temp_csv.unlink(missing_ok=True)

    normalize_zero_datetimes(
        mysql_exe,
        host,
        port,
        user,
        password,
        db_name,
        table_name,
    )
    return {"file": file_path.name, "table": table_name, "rows": total_rows, "status": "ok"}


def main() -> None:
    args = parse_args()
    root = Path(args.input_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"输入目录不存在: {root}")

    mysql_exe = Path(args.mysql_exe)
    if not mysql_exe.exists():
        raise SystemExit(f"mysql.exe 不存在: {mysql_exe}")

    files = discover_excel_files(root)
    if not files:
        print(f"未发现 Excel 文件: {root}")
        return

    ensure_database(mysql_exe, args.db_host, args.db_port, args.db_user, args.db_password, args.db_name)
    ensure_local_infile(mysql_exe, args.db_host, args.db_port, args.db_user, args.db_password, args.db_name)

    started = perf_counter()
    results = []
    for file_path in files:
        file_start = perf_counter()
        print(f"\n开始导入: {file_path}")
        try:
            result = import_one_file(
                file_path=file_path,
                root=root,
                mysql_exe=mysql_exe,
                host=args.db_host,
                port=args.db_port,
                user=args.db_user,
                password=args.db_password,
                db_name=args.db_name,
                batch_rows=args.batch_rows,
                dry_run=args.dry_run,
                force_reimport=args.force_reimport,
            )
            elapsed = perf_counter() - file_start
            result["seconds"] = round(elapsed, 1)
            results.append(result)
            print(f"完成: {file_path.name} -> {result['table']} | {result['rows']} 行 | {elapsed:.1f}s")
        except subprocess.CalledProcessError as exc:
            elapsed = perf_counter() - file_start
            results.append({"file": file_path.name, "table": "", "rows": 0, "status": f"failed:{exc.returncode}", "seconds": round(elapsed, 1)})
            print(f"失败: {file_path.name} | Exit code {exc.returncode}")
        except Exception as exc:
            elapsed = perf_counter() - file_start
            results.append({"file": file_path.name, "table": "", "rows": 0, "status": f"error:{exc}", "seconds": round(elapsed, 1)})
            print(f"失败: {file_path.name} | {exc}")

    total = perf_counter() - started
    print("\n========== 导入汇总 ==========")
    for item in results:
        print(f"{item['file']}\t{item['status']}\t{item['rows']} 行\t{item['seconds']:.1f}s")
    print(f"总耗时: {total:.1f}s")


if __name__ == "__main__":
    main()
