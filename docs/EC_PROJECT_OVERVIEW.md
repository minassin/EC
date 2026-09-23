# EC Project 项目综述

## 1. 项目概述

本项目围绕充电订单数据开展数据清洗、订单分析、用户画像、行为偏好识别和优惠券投放支持分析。项目以大规模充电订单数据为基础，通过统一的数据处理流程，将原始订单逐步转化为：

- 可用于统计分析的标准订单数据；
- 订单和场站维度的年度分析结果；
- 用户 RFM 分层及行为特征；
- 用户站点偏好和充电时段偏好；
- 新用户、回流用户和异常终止用户等特殊群体；
- 高价值活跃用户的谷段偏好和优惠券使用分析；
- 基于滑动窗口的用户状态变化和 RFM 类型迁移结果。

项目当前主要服务于两类目标：

1. 了解不同场站、用户和订单的整体运行特征；
2. 为后续用户召回、精准发券和优惠券使用率提升提供数据基础。

## 2. 项目总体流程

项目总控流程主要分为九步：

1. 数据导入：将目录中的订单文件导入数据库；
2. 数据清洗：按照统一废弃规则、异常规则和字段规则处理订单；
3. 数据导出：输出标准订单与异常订单 CSV，并生成场站类别分布图和站点分类明细；
4. 订单分析与可视化：生成订单、场站、时段、电量和充电时长等分析结果；
5. 用户行为特征构建：基于有效订单生成 RFM 和用户行为特征；
6. 用户行为可视化：对 RFM、站点偏好、时段偏好等用户特征进行图表展示；
7. 滑动窗口 RFM 监测：按 90 天窗口 / 1 天步长 / 180 天回看滚动出高价值活跃轨迹与四态；
8. 近 7 / 14 / 180 天名单：识别新用户、回流用户（含「180 天内是否出现高价值活跃」标记）与近期异常终止用户；
9. 最终分群落桶：按固定优先级链输出 11 类互斥分群（口径见 `docs/segment_definition.md`）。

第 7~9 步依赖第 5 步的 `user_behavior_rfm_segments.csv`，必须按 7 → 8 → 9 的顺序执行——第 8 步回流表要读第 7 步的 `high_value_active_periods.csv` 才能打上高价值活跃标记。九步都可单独用 `--skip-*` 开关跳过。

产物落点：

- 第 1~4 步（清洗与分析）：`outputs/runs/<批次名>/`；
- 第 5~9 步（特征与分群）：`outputs/features/archive/<批次名>/`。

此外，项目还包括与主流程相对独立的扩展分析：

- 高价值活跃用户价格敏感度分析（含谷段电量占比、优惠券使用率）；
- 高价值活跃用户轨迹可视化（`visualize_rfm_sliding_windows.py`）。

## 3. 数据导入与数据库处理

### 3.1 功能

数据导入阶段负责读取数据目录中的原始订单文件，并写入 MySQL 数据库，为后续清洗和分析提供统一数据源。

当前流程支持：

- 目录级批量读取；
- 多个文件或数据表导入；
- 按运行名称区分不同批次；
- 数据库连接参数配置；
- 强制重新导入；
- 跳过导入步骤，直接使用已有数据。

### 3.2 主要脚本

```text
scripts/run_mysql_full_pipeline.py
```

### 3.3 主要输入

```text
D:\EC-project\data\dataset
```

### 3.4 主要输出

数据导入后的结果保存在数据库中，后续清洗脚本从数据库读取原始订单。

## 4. 数据清洗与订单分类

### 4.1 功能

数据清洗阶段负责对原始订单进行统一判定和分类，形成标准订单、异常订单和废弃订单。

清洗内容包括：

- 时间字段解析；
- 无效日期处理；
- 订单状态判断；
- 电量和费用字段检查；
- 充电时长检查；
- 计量电量与交易电量一致性检查；
- 分时电量合计检查；
- 超长充电订单识别；
- 废弃订单和异常订单分类；
- 短时重启订单合并。

### 4.2 时间范围规则

订单时间范围按照项目当前规则执行：

```text
开始时间和结束时间原则上应处于有效分析范围内；
结束时间截止到 2026-07-31 23:59:59。
```

对于无效时间、时间顺序错误或超出有效范围的订单，按照清洗规则进入异常或废弃结果。

### 4.3 异常判定规则

当前异常判定主要包括：

- 订单状态为废弃或已取消；
- 交易电量过大；
- 计量电量过大；
- 充电费用或服务费用过大；
- 费用合计异常；
- 交易电量为负；
- 电量费用比例异常；
- 计量电量为负；
- 结束时间早于开始时间；
- 充电时长超过 18 小时；
- 有效充电时间但电量过低；
- 交易电量与计量电量差异过大；
- 分时电量合计与总交易电量差异过大；
- 时间字段为空或超出规定范围。

### 4.4 短时重启订单合并

短时间内发生的重启订单会按照用户、时间和订单关系进行判断并合并。合并后：

- 订单电量按相关订单电量求和；
- 总电量和分时电量同步汇总；
- 合并后的订单作为后续有效订单分析对象。

### 4.5 主要脚本和输出

```text
scripts/clean_mysql_dataset.py
scripts/cleaning/clean.py
```

主要输出目录：

```text
D:\EC-project\outputs\runs\mysql_run\cleaned\
D:\EC-project\outputs\runs\mysql_run\abnormal\
D:\EC-project\outputs\runs\mysql_run\discard\
D:\EC-project\outputs\runs\mysql_run\overview\
```

## 5. 订单与场站分析

### 5.1 功能

订单分析阶段基于清洗后的标准订单，生成订单规模、场站类别、充电时长、充电电量、时间分布和价格相关分析。

主要分析内容包括：

- 日订单量趋势；
- 月订单量趋势；
- 年度订单量和用户量；
- 场站类别分布；
- 场站数量和订单数量占比；
- 充电时长分布；
- 充电电量分布；
- 开始充电小时分布；
- 谷段电量占比分布；
- 充电时长与电量组合矩阵；
- 短时重启合并前后对比；
- 场站类别优惠和谷段电量对比；
- 星期-小时充电订单热力图；
- 核心充电时长结构与工作日/周末对比；
- 充电开始时段与时长结构（含按场站类别拆分）；
- 月度充电时长趋势；
- 充电开始时段占比；
- 场站类别时长与电量、同站复用率。

其中「星期-小时热力图」及之后几项由 `analyze.py` 里的手写 SVG 口径产出，
图型对齐 `analysis/images/<run>/legacy/2026/`（见该目录 `README.md`）。

### 5.2 按年份输出

订单分析图表按订单年份分别保存到子目录：

```text
D:\EC-project\outputs\runs\mysql_run\analysis\images\mysql_run\2020\
D:\EC-project\outputs\runs\mysql_run\analysis\images\mysql_run\2021\
...
D:\EC-project\outputs\runs\mysql_run\analysis\images\mysql_run\2026\
```

### 5.3 主要脚本

```text
scripts/analysis/analyze.py
```

## 6. 用户行为特征与 RFM 分析

### 6.1 功能

用户行为特征阶段以有效订单为基础，按用户聚合订单行为，形成用户画像数据。

主要特征包括：

- 有效订单数；
- 活跃月份数；
- 最近充电时间；
- 距最近充电天数；
- 交易电量；
- 平均单次交易电量；
- 平均充电频率；
- 优惠使用率；
- 谷段电量占比；
- 站点使用情况；
- 充电时段情况；
- 异常风险等级；
- 用户标签。

### 6.2 RFM 规则

#### R：近期性

R 根据距样本末次有效充电的天数划分。当前项目采用低分表示更活跃：

| 距最近充电天数 | R 分数 | 类型 |
|---|---:|---|
| 0-7 天 | 1 | 活跃 |
| 8-14 天 | 2 | 近期活跃 |
| 15-30 天 | 3 | 一般活跃 |
| 31-60 天（90天窗口）或31-90天（180天窗口） | 4 | 沉默风险 |
| 超过窗口内沉默阈值 | 5 | 沉默 |

#### F：平均充电频率

F 使用平均充电间隔表示，数值越小代表充电越频繁。

```text
动态观察天数 = max(末次充电时间 - 首次充电时间, 14)
平均充电频率 = 动态观察天数 / 有效订单数
```

最小观察天数设置为 14 天，目的是避免用户只在很短时间内出现，导致频率被过度高估。

当前固定阈值为：

| 平均充电频率 | F 分数 | 类型 |
|---|---:|---|
| <= 4 天 | 1 | 高频 |
| 5-7 天 | 2 | 中高频 |
| 8-14 天 | 3 | 中频 |
| 15-20 天 | 4 | 低频 |
| > 20 天 | 5 | 极低频 |

#### M：平均单次交易电量

| 平均单次交易电量 | M 分数 | 类型 |
|---|---:|---|
| < 10 kWh | 1 | 极低价值 |
| 10-19 kWh | 2 | 低价值 |
| 20-29 kWh | 3 | 中价值 |
| 30-39 kWh | 4 | 中高价值 |
| >= 40 kWh | 5 | 高价值 |

### 6.3 RFM 用户类型

项目根据 R、F、M 组合识别重点用户类型：

- 高价值活跃用户：`R <= 2 且 F <= 2 且 M >= 4`
- 高价值沉默风险用户：`R >= 4 且 F <= 2 且 M >= 4`
- 新近低频用户：`R <= 2 且 F >= 4`
- 高频低价值用户：`F <= 2 且 M <= 2`
- 低频低价值用户：`F >= 4 且 M <= 2`
- 其他用户归入一般用户。

### 6.4 主要脚本和输出

```text
scripts/user_behavior_features/archive/build_user_behavior_features_pipeline.py
scripts/user_behavior_features/archive/visualize_user_behavior_features.py
```

输出目录：

```text
D:\EC-project\outputs\features\archive\mysql_run\
```

主要文件包括：

```text
final_user_behavior_features.csv
final_user_behavior_labels.csv
user_station_top3_detail.csv
user_time_preference_detail.csv
```

## 7. 站点偏好分析

### 7.1 功能

站点偏好分析用于识别用户是否集中在少数固定场站充电，并输出用户最常使用的站点及其使用次数、占比。

### 7.2 分类规则

| 条件 | 类型 |
|---|---|
| 订单数 <= 5 | 样本不足 |
| 最高频站点占比 >= 0.5 | 单站点固定 |
| 前二站点占比 >= 0.6 且第二站点占比 >= 0.2 | 双站点固定 |
| 前三站点占比 >= 0.7 且第三站点占比 >= 0.1 | 三站点固定 |
| 使用站点数 >= 5 | 多站流动用户 |
| 其他情况 | 一般站点用户 |

站点偏好明细重点输出：

- 第一常用站点；
- 第二常用站点；
- 第三常用站点；
- 各站点使用次数；
- 各站点订单占比；
- 站点偏好类型。

## 8. 时段偏好分析

### 8.1 功能

时段偏好分析用于识别用户是否集中在固定小时充电，为定时发券提供依据。

当前分析粒度为 1 小时，即按订单开始充电时间所在小时统计用户行为。

### 8.2 分类规则

| 条件 | 类型 |
|---|---|
| 订单数 <= 5 | 样本不足 |
| 最高频时段占比 >= 0.5 | 单时段偏好 |
| 前二时段占比 >= 0.6 且第二时段占比 >= 0.15 | 双时段偏好 |
| 前三时段占比 >= 0.7 且第三时段占比 >= 0.1 | 三时段偏好 |
| 最高频时段占比 >= 0.3 | 轻度时段偏好 |
| 其他情况 | 无明显偏好 |

## 9. 滑动窗口 RFM 监测

### 9.1 功能

滑动窗口分析用于连续观察用户在不同时间窗口内的 RFM 类型变化，重点监测：

- 高价值活跃用户是否持续稳定；
- 高价值活跃用户是否流失；
- 用户是否重新回流；
- 用户是否从一般用户转为新近低频用户；
- 用户 RFM 类型是否发生迁移。

### 9.2 当前窗口设置

```text
窗口大小：90 天
总回看跨度：180 天
滑动步长：1 天（共 91 个窗口，与总控 `--sliding-step-days` 默认值一致）
```

窗口按照时间先后进行统计和展示。即使内部计算采用倒序滑动，最终结果仍应按正常时间顺序排列。

### 9.3 高价值活跃用户变化

滑动窗口结果可进一步区分：

- 稳定高价值活跃：只出现过一段高价值活跃，且到最后一个窗口仍保持该状态；
- 回流高价值活跃：此前曾退出高价值活跃，之后重新进入，且当前仍处于高价值活跃状态；
- 历史高价值活跃（当前已退出）：曾经处于高价值活跃状态，最后一个窗口已退出，但近 90 天仍有充电；
- 沉默高价值活跃：最后一个窗口已退出，且近 90 天完全没有充电（不在 90 天主表内）。

四态互斥且完备，由 `sliding_windows/high_value_active_periods.csv` 的「高价值活跃状态」列直接给出。

同一用户只归入一种最终状态，按完整时间序列判定；如需区分「新晋」「开窗即高价值」等更细口径，可结合同表输出的「高价值活跃段数」与「上一阶段类型」字段还原。

### 9.4 主要脚本和输出

```text
scripts/user_behavior_features/archive/build_rfm_sliding_windows.py
```

输出目录：

```text
D:\EC-project\outputs\features\archive\mysql_run\sliding_windows\
```

主要结果：

```text
sliding_window_overview.csv
user_rfm_window_snapshots.csv
user_rfm_type_periods.csv
high_value_active_periods.csv
```

## 10. 特殊用户群体分析

### 10.1 新用户

在 180 天观察窗口内，历史上没有更早充电记录，并且在最近 7 天内首次出现的用户。

### 10.2 回流用户

用户历史上存在充电记录，期间连续中断超过 30 天，之后在最近 7 天内重新出现。

对于回流用户，还需要判断其断档前是否曾经属于高价值活跃用户。

回流表同时给出两个标记：`断档前是否高价值活跃用户`（上次充电日期落在某段高价值活跃内，仅回显）与 `180天内是否出现高价值活跃`（180 天内出现过高价值活跃窗口，是 02 桶的落桶依据）。

### 10.3 近期异常终止用户

统计最近 14 天内出现异常终止记录的用户，并输出对应异常原因。

以下情况不归入异常终止：

- 用户主动终止；
- 正常结束；
- 达到金额或 SOC 阈值后的正常结束。

### 10.4 结果

相关输出包括：

- 新用户明细；
- 回流用户明细；
- 历史高价值活跃回流用户；
- 近期异常终止用户及异常原因；
- 用户对应的近期充电订单次数。

## 11. 高价值活跃用户价格敏感度分析

### 11.1 功能

该部分单独针对高价值活跃用户，分析其是否具有：

- 谷段充电偏好；
- 优惠券使用倾向；
- 较强的价格敏感特征。

后续也可将同样的分析扩展到全部 RFM 用户类型。

### 11.2 谷段电量占比

谷段电量占比定义为：

```text
谷段电量占比 = 谷段交易电量 / 交易总电量
```

用户层面的平均谷段电量占比为其有效订单谷段电量占比的平均值，结果保留三位小数。

当订单跨越多个分时段时，按照订单在各时段内的重叠时长比例分摊电量。例如：

```text
订单时间：10:00-12:00
订单总电量：88 kWh
8:00-11:00：谷段
11:00-13:00：峰段
```

订单在谷段和峰段各占 1 小时，因此：

```text
谷段电量：44 kWh
峰段电量：44 kWh
```

### 11.3 优惠券使用率

优惠券使用率用于衡量用户历史订单中使用优惠券的比例。该指标与谷段电量占比结合后，可以区分：

- 更偏好低谷电价的用户；
- 更容易响应优惠券的用户；
- 同时具有谷段偏好和优惠券使用倾向的用户。

### 11.4 主要输出

```text
D:\EC-project\outputs\features\archive\mysql_run\high_value_active_price_analysis\
```

主要文件：

```text
high_value_active_price_behavior_users.csv
high_value_active_price_behavior_overview.csv
high_value_active_price_behavior_cross.csv
high_value_active_valley_ratio_density.png
high_value_active_coupon_usage_density.png
```

## 12. 发券分析思路

项目后续可以按照三层逻辑组织发券：

1. RFM 决定用户价值和当前活跃状态；
2. 谷段偏好、优惠券使用率决定用户是否可能响应价格刺激；
3. 站点偏好和时段偏好决定优惠券投放的目标站点和触达时间。

例如：

| 用户特征组合 | 建议策略 |
|---|---|
| 高价值活跃 + 谷段偏好高 | 谷段电价引导券 |
| 高价值活跃 + 优惠使用率高 | 定向优惠券 |
| 高价值活跃 + 固定站点 | 指定站点券 |
| 高价值活跃 + 固定时段 | 指定时段券 |
| 历史高价值活跃回流用户 | 短期回流券 |
| 新用户 | 新客引导券 |
| 近期异常终止用户 | 风险排查或召回券 |

## 13. 常用运行命令

### 13.1 总控运行（九步）

从零跑通（导入 → 清洗 → 导出 → 分析 → 特征 → 可视化 → 滑窗 → 名单 → 落桶）：

```powershell
.\run_from_scratch.cmd D:\EC-project\data\dataset mysql_run
```

等价的手写命令（滑动步长 1 天）：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_mysql_full_pipeline.py `
  --input-dir D:\EC-project\data\dataset `
  --run-name mysql_run `
  --clean-scope full `
  --recent-window-days 90 `
  --sliding-window-days 90 `
  --sliding-step-days 1 `
  --sliding-horizon-days 180 `
  --recent-horizon-days 180
```

只补第 5~9 步（复用已有清洗 CSV）：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_mysql_full_pipeline.py `
  --input-dir D:\EC-project\data\dataset `
  --run-name mysql_run `
  --skip-import `
  --skip-clean `
  --skip-export `
  --skip-analysis
```

只重跑第 7~9 步（滑窗 / 名单 / 落桶）：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_mysql_full_pipeline.py `
  --input-dir D:\EC-project\data\dataset `
  --run-name mysql_run `
  --skip-import --skip-clean --skip-export --skip-analysis --skip-feature --skip-visualize
```

单跑某一步：用 `--skip-*` 跳过其余步骤，例如只跑滑窗再加 `--skip-recent-segments --skip-segmentation`。

### 13.2 滑动窗口分析

```powershell
.\.venv\Scripts\python.exe `
  .\scripts\user_behavior_features\archive\build_rfm_sliding_windows.py `
  --cleaned-file D:\EC-project\outputs\runs\mysql_run\cleaned\mysql_run_standard_user_orders.csv `
  --label mysql_run `
  --window-days 90 `
  --step-days 1 `
  --horizon-days 180
```

### 13.3 高价值活跃用户价格分析

```powershell
.\.venv\Scripts\python.exe `
  .\scripts\user_behavior_features\archive\build_high_value_active_price_analysis.py
```

### 13.4 回填谷段电量占比

```powershell
.\.venv\Scripts\python.exe `
  .\scripts\user_behavior_features\archive\fill_high_value_active_valley_ratio.py `
  D:\EC-project\outputs\features\archive\mysql_run\high_value_active_price_analysis\high_value_active_price_behavior_users.csv `
  D:\EC-project\outputs\runs\mysql_run\cleaned\mysql_run_standard_user_orders.csv
```

### 13.5 绘制谷段偏好分布图

```powershell
.\.venv\Scripts\python.exe `
  .\scripts\user_behavior_features\archive\render_high_value_active_price_density.py `
  D:\EC-project\outputs\features\archive\mysql_run\high_value_active_price_analysis\high_value_active_price_behavior_users.csv `
  D:\EC-project\outputs\features\archive\mysql_run\high_value_active_price_analysis\high_value_active_valley_ratio_density.png `
  D:\EC-project\outputs\runs\mysql_run\cleaned\mysql_run_standard_user_orders.csv
```

### 13.6 绘制优惠使用率分布图

```powershell
.\.venv\Scripts\python.exe `
  .\scripts\user_behavior_features\archive\render_high_value_active_coupon_density.py `
  D:\EC-project\outputs\features\archive\mysql_run\high_value_active_price_analysis\high_value_active_price_behavior_users.csv `
  D:\EC-project\outputs\features\archive\mysql_run\high_value_active_price_analysis\high_value_active_coupon_usage_density.png
```

## 14. 当前项目特点

- 面向大规模订单数据，支持目录级批处理；
- 数据清洗、分析和用户画像流程相互衔接；
- RFM 规则采用可扩展的固定阈值；
- F 指标采用动态观察窗口并设置 14 天最小观察天数；
- 站点和时段偏好能够输出具体次数与占比；
- 滑动窗口支持连续监测用户状态变化；
- 对新用户、回流用户和异常终止用户进行独立识别；
- 滑动窗口监测、名单识别与最终落桶已并入总控第 7~9 步，可一键跑通或按需跳过；
- 高价值活跃用户分析逐步向价格敏感和发券策略延伸；
- 主流程与扩展分析脚本相对独立，便于单独运行和维护。
