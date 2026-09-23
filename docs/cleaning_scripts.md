# 数据清洗脚本总览

本文档汇总当前项目中所有清洗脚本。后续清洗相关运行方式和脚本说明统一维护在本文档中。

## 目录

- 清洗脚本目录：`scripts/cleaning/`
- 输出主目录：`outputs/`
- 清洗后主表：`outputs/cleaned/`
- 异常数据表：`outputs/abnormal/`
- 清洗日志：`outputs/logs/`
- 清洗汇总文档：`outputs/analysis/cleaning_summary.md`

## 1. 通用用户订单清洗

脚本：`scripts/cleaning/clean_user_orders.py`

适用数据：

- `data/充电数据-60维.xlsx`
- `data/国网常州沪武高速滆湖服务区(武汉方向）充电站订单管理20260101-0423.xls`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py --dataset charge_60d
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py --dataset gehu_wuhan
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py --list-datasets
```

主要输出：

```text
outputs\cleaned\<dataset>_standard_user_orders.csv
outputs\abnormal\<dataset>_abnormal_orders.csv
outputs\logs\<dataset>_cleaning_log.xlsx
```

大致伪代码：

```text
读取指定订单数据集
读取字段映射和数据集配置
统一字段名和中文输出表头
识别用户主键，优先用户编码，其次合法 VIN 等兜底字段
清洗时间、电量、金额、渠道、支付状态、结束原因等字段
生成充电时长、充电时段、时长区间、单度价格、平均功率等订单级字段
按支付状态和异常规则拆分主表与异常表
删除不适合第一阶段分析的冗余字段
输出标准订单表、异常订单表和清洗日志
```

## 2. 年度订单清洗

脚本：`scripts/cleaning/clean_2026_orders.py`

适用数据：

- `data/22年.xlsx`
- `data/23年.xlsx`
- `data/24年.xlsx`
- `data/25年.xlsx`
- `data/26年.xlsx`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\cleaning\clean_2026_orders.py --list-datasets
.\.venv\Scripts\python.exe scripts\cleaning\clean_2026_orders.py --dataset 26年
.\.venv\Scripts\python.exe scripts\cleaning\clean_2026_orders.py --dataset 25年
```

主要输出：

```text
outputs\cleaned\orders_<YYYY>_standard_user_orders.csv
outputs\abnormal\orders_<YYYY>_abnormal_orders.csv
outputs\logs\orders_<YYYY>_cleaning_log.xlsx
outputs\analysis\cleaning_summary.md
```

大致伪代码：

```text
根据 --dataset 定位 data\XX年.xlsx
读取年度订单表
统一订单状态、渠道、时间、电量、金额、站点 ID 等字段
筛选已支付订单作为主表，非正常记录进入异常表
填充分类字段、优惠字段、峰平谷电量等缺失值
生成充电时长、粗略时段、星期、单度价格、平均功率、谷段电量占比
按用户和时间排序，生成用户年内序号、月内序号、距上次充电间隔、是否复用上次站点
生成异常原因
输出年度标准订单表、异常订单表、清洗日志和清洗说明
更新清洗汇总文档中的对应年度章节
```

## 3. 资产表清洗

脚本：`scripts/cleaning/clean_assets.py`

适用数据：

- `data/资产表.xlsx`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\cleaning\clean_assets.py
```

主要输出：

```text
outputs\cleaned\assets_standard_asset_table\*.csv
outputs\abnormal\assets_abnormal_records\*.csv
outputs\logs\assets_cleaning_log.xlsx
outputs\analysis\cleaning_summary.md
```

大致伪代码：

```text
读取资产表中各个 sheet
分别处理 UUID 映射、充电站资产、充电桩资产、资产映射、分成资产对照
统一站点 ID 和 UUID 文本格式，删除 station- 前缀
清洗站点状态、桩类型、桩功率、经纬度、投运时间等字段
删除退运站点，保留待投运和停运站点
缺失关键字段的记录进入异常表
生成投运年份、投运月份、映射持续月数、是否最新映射等辅助字段
按 sheet 输出标准资产表、异常记录表和清洗日志
更新清洗汇总文档中的资产表清洗章节
```

说明：资产表清洗目前保留是为了给用户行为解释提供站点和设施背景；后续默认任务仍以用户行为模式识别为主。
