# 数据分析脚本总览

本文档汇总当前项目中所有分析、制图和报告辅助脚本。后续分析相关运行方式和脚本说明统一维护在本文档中。

默认分析范围以用户行为模式识别为主；资产相关分析只作为辅助背景，不主动扩展为设施规划任务。

## 目录

- 分析脚本目录：`scripts/analysis/`
- 用户行为特征构建目录：`scripts/user_behavior_features/`
- 图片输出目录：`outputs/analysis/images/`
- 统一分析汇总文档：`outputs/analysis/behavior_analysis_summary.md`

## 1. 通用用户订单分析

脚本：`scripts/analysis/analyze_user_orders.py`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\analyze_user_orders.py
.\.venv\Scripts\python.exe scripts\analysis\analyze_user_orders.py --dataset gehu_wuhan
.\.venv\Scripts\python.exe scripts\analysis\analyze_user_orders.py --list-datasets
```

主要输出：

```text
outputs\analysis\images\<dataset>\*.svg
outputs\analysis\behavior_analysis_summary.md
```

大致伪代码：

```text
读取 outputs\cleaned\<dataset>_standard_user_orders.csv
检查订单数、用户数、总电量、平均电量、平均充电时长
统计每日订单趋势、每日电量趋势、充电时段分布、时长分布、渠道分布
如数据包含结束原因，则统计结束原因分类
输出 SVG 图表和 Markdown 分析说明
更新统一用户行为分析汇总中的对应数据集章节
```

## 2. 年度订单行为分析

脚本：`scripts/analysis/analyze.py`

> 原先这个位置是 `scripts/analysis/analyze_2026_orders.py`。该脚本源码已丢失，
> 2026-09-22 已把它的 18 类图型移植进 `analyze.py`，随后删除其编译缓存。
> 现行入口是总控第 4 步，也可以单独调：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\analyze.py `
  --cleaned-file outputs\runs\mysql_run\cleaned\mysql_run_standard_user_orders.csv `
  --image-prefix mysql_run --label mysql_run --split-by-year
```

主要输出：

```text
outputs\runs\<run>\analysis\images\<run>\*.svg / *.png
outputs\runs\<run>\analysis\images\<run>\<YYYY>\*
outputs\runs\<run>\analysis\images\<run>\behavior_analysis_summary.md
```

大致伪代码：

```text
流式扫描清洗表，按年份累加日/月订单量、时长与小时直方、场站类别计数、谷段占比样本
逐年生成订单趋势、场站类别分布、充电时长与小时分布、谷段占比曲线（matplotlib 一套）
再一次性投影读入 11 列，按 _2026_orders 的旧口径产出 15 类手写 SVG 分析图
外加 2 张场站类别环形图（订单占比含场站数 / 场站数量占比）
写年度行为分析概览
```

口径与注意点：

- 两套产物互不影响：matplotlib 那一套走 `setup_plotting()` 的 rcParams，
  手写 SVG 那一套直接拼字符串、不读 rcParams，因此字体设置对后者无效；
- 手写 SVG 那套要另读一遍清洗表（投影 11 列），耗时和内存都明显更高，
  只刷新 matplotlib 那一套时加 `--skip-legacy-svg`；
- 数据只覆盖单一年份时，手写 SVG 那套直接对整帧出图（保留「充电开始时间」为空的行），
  这是为了对齐 `legacy/` 的历史图型，改动它会让图对不上；
- 默认剔除「充电开始年份 ≠ 来源表年份」的跨年订单，`--keep-cross-year` 可保留。

## 3. 年度用户行为趋势图

脚本：`scripts/analysis/generate_yearly_behavior_charts.py`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\generate_yearly_behavior_charts.py
```

主要输出：

```text
outputs\analysis\images\orders_yearly\charge_period_share_by_year.svg
outputs\analysis\images\orders_yearly\avg_energy_per_order_trend.svg
outputs\analysis\images\orders_yearly\station_reuse_rate_trend.svg
outputs\analysis\images\orders_yearly\channel_share_by_year.svg
outputs\analysis\images\orders_yearly\discount_usage_rate_trend.svg
outputs\analysis\images\orders_yearly\abnormal_reason_top.svg
```

大致伪代码：

```text
读取 2022-2026 年度清洗后订单 CSV
统一充电粗略时段标签
按年份统计充电时段结构、渠道结构、优惠使用率、同站复用率
读取年度异常订单表，统计异常原因 Top
根据年度汇总结果生成跨年份 SVG 图表
```

## 4. 资产表辅助分析

脚本：`scripts/analysis/analyze_assets.py`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\analyze_assets.py
```

主要输出：

```text
outputs\analysis\images\assets\*.svg
outputs\analysis\behavior_analysis_summary.md
```

大致伪代码：

```text
读取 outputs\cleaned\assets_standard_asset_table\*.csv
统计站点城市分布、站点运营状态、充电桩功率分布、投运年份趋势
统计归属单位功率和站点类型状态结构
生成资产辅助图表和 Markdown 分析说明
更新统一用户行为分析汇总中的资产辅助章节
```

说明：该脚本属于辅助分析，不作为用户行为模式识别的主线任务。

## 5. 检查与报告辅助脚本

### 5.1 检查年度时段分布

脚本：`scripts/analysis/check_period_distribution.py`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\check_period_distribution.py
```

大致伪代码：

```text
读取各年度清洗后订单 CSV
统一粗略时段标签
统计每年凌晨、上午、下午、晚上占比
检查四个时段是否完整覆盖有效订单
```

### 5.2 检查站点复用分布

脚本：`scripts/analysis/check_reuse_distribution.py`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\check_reuse_distribution.py
```

大致伪代码：

```text
读取各年度清洗后订单 CSV
统计是否复用上次站点字段的取值分布
分别输出可比较订单口径和全有效订单口径下的复用率
```

### 5.3 Markdown 转 Word

脚本：`scripts/analysis/build_progress_report_docx.py`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\build_progress_report_docx.py
```

大致伪代码：

```text
读取 docs\data_cleaning_progress_report.md
解析标题、段落、编号、代码和图片引用
生成不嵌入图片的 Word 文档
输出 docs\data_cleaning_progress_report.docx
```

### 5.4 抽取 Word 正文

脚本：`scripts/analysis/extract_docx_text.py`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\extract_docx_text.py "C:\path\to\file.docx"
```

大致伪代码：

```text
读取 docx 压缩包中的 word/document.xml
按正文段落和表格顺序抽取文本
打印段落内容和表格单元格内容
```

## 6. 第二阶段用户行为特征目录

## 7. 2022-2026 用户频次统计

脚本：`scripts/analysis/summarize_user_frequency.py`

运行方式：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\analysis\summarize_user_frequency.py
```

主要输出：

```text
outputs/analysis/user_frequency_summary_2022_2026.csv
outputs/analysis/behavior_analysis_summary.md
```

大致伪代码：

```text
读取 outputs/cleaned/orders_2022-2026_standard_user_orders.csv 系列文件
优先使用 用户识别主键，缺失时回退到 用户编码
按年统计每个用户的订单频次
找出年度最高频用户、最低频用户示例
统计频次>=500的高频用户数及占比，频次=1的低频用户数及占比
把 2022-2026 合并后再统计一遍总体结果
输出 CSV，并更新统一用户行为分析汇总
```

目录：`scripts/user_behavior_features/`

用途：

```text
后续用于多维行为特征构建。
默认只围绕用户行为模式识别：
从订单级数据聚合用户级特征，生成用户标签，识别高价值、低频、沉默风险、价格敏感等用户。
```
