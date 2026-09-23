# 用户订单数据筛选清洗方案

本阶段只做第 1 项任务：用户行为数据提取与清洗。目标是把原始订单表整理成标准化订单级数据，为后续行为分析准备底表；暂不做用户画像、RFM、沉默用户识别等第二阶段特征工程。

## 目录与输出

- 默认输入订单表：`data/充电数据-60维.xlsx`
- 可切换旧输入表：`data/国网常州沪武高速滆湖服务区(武汉方向）充电站订单管理20260101-0423.xls`
- 字段映射表：`data/订单映射.xlsx`
- 主脚本：`scripts/cleaning/clean_user_orders.py`
- 可视化脚本：`scripts/analysis/analyze_user_orders.py`
- 清洗后主表：`outputs/cleaned/charge_60d_standard_user_orders.csv`
- 异常订单表：`outputs/abnormal/charge_60d_abnormal_orders.csv`
- 清洗日志：`outputs/logs/charge_60d_cleaning_log.xlsx`
- 可视化图片目录：`outputs/analysis/images/charge_60d/`
- 可视化分析说明汇总：`outputs/analysis/behavior_analysis_summary.md`

60 维表没有提供原始交易流水号，脚本不会再虚拟生成订单编号；旧表原本存在真实交易流水号，因此旧表输出会继续保留该字段。

当前脚本支持两个数据集：

- `charge_60d`：`充电数据-60维.xlsx`，默认处理。
- `gehu_wuhan`：`国网常州沪武高速滆湖服务区(武汉方向）充电站订单管理20260101-0423.xls`。

## 运行命令

以下命令均在 PowerShell 中执行。

1. 进入项目目录

```powershell
cd D:\EC-project
```

2. 创建虚拟环境

如果 `.venv` 已经存在，可以跳过这一步。

```powershell
python -m venv .venv
```

3. 安装依赖

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

4. 执行订单清洗

默认处理 `charge_60d`：

```powershell
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py
```

也可以显式指定：

```powershell
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py --dataset charge_60d
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py --dataset gehu_wuhan
```

查看当前可选数据集：

```powershell
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py --list-datasets
```

该脚本会生成：

```text
outputs\cleaned\<dataset>_standard_user_orders.csv
outputs\abnormal\<dataset>_abnormal_orders.csv
outputs\logs\<dataset>_cleaning_log.xlsx
```

主表和异常表都会保持原始订单表中的相对行顺序。脚本只筛选/删除不需要的行列，不按用户或时间重新排序。

主表和异常表输出为 CSV；清洗日志仍为 Excel。若某个结果文件正在被 Excel 或 WPS 打开，脚本会自动另存为 `_new.csv` 或 `_new_2.csv`，避免覆盖失败。

5. 执行可视化分析

```powershell
.\.venv\Scripts\python.exe scripts\analysis\analyze_user_orders.py
.\.venv\Scripts\python.exe scripts\analysis\analyze_user_orders.py --dataset gehu_wuhan
```

该脚本会读取清洗后的主表：

```text
outputs\cleaned\<dataset>_standard_user_orders.csv
```

并生成：

```text
outputs\analysis\images\<dataset>\*.svg
outputs\analysis\behavior_analysis_summary.md
```

6. 完整流程

如果依赖已经安装好，可以直接按顺序运行：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py
.\.venv\Scripts\python.exe scripts\analysis\analyze_user_orders.py
```

处理旧表时：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\cleaning\clean_user_orders.py --dataset gehu_wuhan
.\.venv\Scripts\python.exe scripts\analysis\analyze_user_orders.py --dataset gehu_wuhan
```

7. 年度订单表（`XX年.xlsx`）清洗与分析

`data` 目录下形如 `22年.xlsx`、`23年.xlsx`、`24年.xlsx`、`25年.xlsx`、`26年.xlsx` 的年度订单表，使用年度脚本处理。运行时可以写 `26`、`26年`、`26年.xlsx` 或 `2026`，脚本会自动定位到 `data\26年.xlsx`，并按完整年份命名输出。

查看可选年度数据集：

```powershell
.\.venv\Scripts\python.exe scripts\cleaning\clean_2026_orders.py --list-datasets
```

处理指定年份：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\cleaning\clean_2026_orders.py --dataset 26年
.\.venv\Scripts\python.exe scripts\analysis\analyze_2026_orders.py --dataset 26年
```

换其他年份只需要改 `--dataset`，例如：

```powershell
.\.venv\Scripts\python.exe scripts\cleaning\clean_2026_orders.py --dataset 25年
.\.venv\Scripts\python.exe scripts\analysis\analyze_2026_orders.py --dataset 25年
```

年度脚本会生成：

```text
outputs\cleaned\orders_<YYYY>_standard_user_orders.csv
outputs\abnormal\orders_<YYYY>_abnormal_orders.csv
outputs\logs\orders_<YYYY>_cleaning_log.xlsx
outputs\analysis\cleaning_summary.md
outputs\analysis\behavior_analysis_summary.md
outputs\analysis\images\orders_<YYYY>\*.svg
```

年度清洗主表不导出 `月份`、`开始小时`、`开始小时区间`，只保留 `充电粗略时段`；脚本内部仍会临时计算月份和开始小时，用于生成月内订单序号和星期-小时热力图。

## 主表保留逻辑

第一阶段主表只保留与订单级用户行为分析直接相关、且口径相对稳定的字段。不同来源表不强制保持同一列数：原表没有且没有业务必要的字段不虚拟生成；原表存在且重要的字段会保留，能够可靠计算的重要字段会清洗生成。表头统一为中文，不导出原始 VIN 列，也不导出“是否周末”列。

- 用户识别：用户编码、用户识别主键、清洗后 VIN；旧表额外保留真实交易流水号。
- 订单行为：订单状态、充电方式、订单渠道、订单来源、支付方式、是否后付费、用户类型。
- 时间：充电开始时间、充电结束时间、充电时长、充电时长分布、充电粗略时段/充电起止粗略时段、星期；年度订单表不再导出月份和开始小时。
- 设备：充电站ID、充电桩编号、充电枪编号；充电站ID 会统一去掉 `station-` 前缀，不额外导出充电站名称。
- 电量金额：清洗后交易电量、清洗后实扣金额、单度价格、平均充电功率、是否使用优惠。
- 结束结果：异常原因；仅在对应数据集导出字段中保留结束原因时，额外输出结束原因分类。

下列字段不进入第一阶段主表：

- 全空或近乎全空字段，例如深谷电量、低谷电量、卡号、卡余额、发票序列标识。
- 站点/单位类低区分度字段，例如产权单位、运营单位、监管单位、运维单位、充电站名称。
- 财务结算和票据字段，例如清分状态、清分 ID、清分时间、开票状态。
- 已被清洗后口径替代的原始金额和电量字段，例如原始交易电量、抄表电量、电费、服务费、交易金额、原始实扣金额。
- 主表筛选后信息重复或暂不直接使用的字段，例如订单创建时间、订单支付时间、单独的开始/结束粗略时段、星期序号、是否周末。
- 只用于识别兜底或人工追溯的敏感字段，例如原始 VIN、手机号、车牌号、单位用户证件号。

这些字段不会从原始文件中删除；清洗日志会记录它们未进入主表的原因。

## 缺失值与异常值处理

不是所有字段都适合均值填充，本项目采用分类型策略。

1. 分类字段

字段：渠道、来源、支付方式、用户类型、充电方式、是否后付费。

方法：缺失填充为“未知...”。分类字段没有均值意义，保留未知类别更适合统计。

2. 优惠字段

字段：优惠金额、优惠券优惠金额、立减优惠金额。

方法：缺失填充为 0。优惠字段为空通常表示未使用优惠。

3. 分时电量字段

字段：峰电量、平电量、谷电量。

方法：缺失填充为 0。分时电量缺失更接近该时段无电量，不适合均值填充。

4. 核心电量字段

字段：交易电量。

方法：若交易电量缺失，则用抄表电量兜底。电量是核心行为变量，不能用均值填充，否则会扭曲真实充电行为。

5. 核心金额字段

字段：实扣金额。

方法：若实扣金额缺失，则用交易金额兜底。金额字段应保持可解释性，不使用均值填充。

6. 充电时长

字段：充电结束时间 - 充电开始时间。

方法：正常计算；缺失或异常时，内部用同一充电方式的中位数填充，再用全局中位数兜底，用于平均功率等计算。最终导出仍展示原始起止时间计算出的充电时长。

7. VIN

方法：合法 VIN 写入 `vin_clean`，非法或缺失 VIN 不强行补值。用户识别优先使用用户编码，只有用户编码缺失时才用合法 VIN、手机号、车牌号兜底。

## 新增字段

最终导出的主表包含以下新增或清洗后的重要字段：

- `user_key`：用户识别主键，优先用户编码，其次合法 VIN、手机号、车牌号。
- `vin_clean`：清洗后 VIN。非法 VIN 和缺失 VIN 不补值，导出为“无有效VIN”。
- `charge_duration_min`：充电时长，单位 min。
- `charge_duration_bucket`：充电时长分布，首小时按 10 min 划分为 `0-10min`、`10-20min` 等，1 小时后按 `60-90min`、`90-120min`、`120-180min`、`180-240min`、`240min以上` 划分。
- `charge_period`：充电起止粗略时段，例如 `上午（06:00-11:59）`、`上午（06:00-11:59）-下午（12:00-17:59）`。
- `weekday_name`：星期。已不再单独导出“是否周末”。
- `month`：月份。仅在部分旧数据集导出；年度订单表中只用于内部计算月内订单序号，不进入最终清洗表。
- `kwh_clean`：清洗后交易电量。
- `actual_amount_clean`：清洗后实扣金额。
- `unit_price`：单度价格。
- `avg_power_kw`：平均充电功率，约等于电量 / 时长。
- `has_discount_flag`：是否使用优惠。
- `end_reason_type`：结束原因归类。该列不强制所有表导出；例如 60 维表当前不导出这一列。用户主动结束、车辆充满、`022AH`/BMS 正常终止统一归为 `正常结束`；`0253H`、`024EH`、`0241H` 等归为 `设备故障`；`022CH`、`024BH` 等归为 `异常中断`。
- `abnormal_reason`：异常订单原因，所有异常判断合并到这一列。

`充电时长(min)`、`单度价格`、`平均充电功率(kW)` 导出时保留 3 位小数。

脚本内部还会计算一些辅助字段，例如合法 VIN 标记、零电量标记、负金额标记、填充后时长、是否适合后续行为分析等；这些字段只用于生成异常原因和清洗日志，不导出到最终 CSV 主表。

## 异常订单标记

异常订单不会直接删除，而是进入 `outputs/abnormal/<dataset>_abnormal_orders.csv`。异常表与对应数据集主表使用同一套精简字段，并只保留一列 `异常原因` 来说明问题，不再单独展开“是否零电量、是否负金额、是否时间异常”等辅助判断列。

异常原因包括：

- 非支付完成
- 缺少用户识别
- 零电量
- 负电量
- 超大电量
- 负金额
- 时间异常
- 充电时长超过 4 小时
- 故障或异常结束
- VIN异常

主表只保留支付完成订单，同时保留 `异常原因` 字段，方便后续按研究目标筛选。

## 表头命名

脚本内部仍使用英文标准字段名，便于代码维护；导出的 CSV 文件会统一转换为中文表头。例如：

- `order_id` 导出为 `交易流水号`；仅在原表真实存在该字段时导出
- `vin_clean` 导出为 `VIN码（清洗后）`
- `charge_duration_min` 导出为 `充电时长(min)`
- `charge_duration_bucket` 导出为 `充电时长分布`
- `charge_period` 导出为 `充电起止粗略时段`
- `unit_price` 导出为 `单度价格`
- `avg_power_kw` 导出为 `平均充电功率(kW)`

