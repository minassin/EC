# 本周用户行为分析周报

时间范围：2026-07-21 至 2026-07-28

本周工作聚焦在“用户行为模式识别”，重点完成了清洗口径统一、特征链路整理和结果输出。

## 一、本周完成

1. 完成 2026 年高频异常阈值的动态化处理，不再固定按 365 单判定。
2. 梳理并固化了用户行为特征链路，形成字段口径、订单级特征、用户级汇总、RFM 分层和标签输出的完整流程。
3. 将 `archive` 与 `xls_task` 两套流程整理成更清晰的单文件主流程，便于按年运行。
4. 补齐了按年份分析和按年份输出能力，方便单年查看与全量对比。
5. 输出了可直接用于后续画像、分群和汇报的结果表与重点图表。

## 二、重点现象与结论

### 1. 场站类别差异明显

- 高速场站更偏路过型补能，短停和偶发特征更明显。
- 专用场站的使用习惯更稳定，站点和桩位偏好更强。
- 公共场站行为相对分散，介于高速和专用之间。
- 其他类主要作为兜底口径，解释力相对弱。

结论：场站类别本身就是重要分层变量，后续做用户行为识别时应优先保留。

可配图：
- `outputs/analysis/images/cleaning/orders_2026_station_category_distribution.svg`

### 2. 车辆类别以辅助解释为主

- 车辆类别已完成基础划分口径，整体可分为乘用车、商用车和未知。
- 可用 VIN 样本里，乘用车是主体，商用车样本较少。
- 未知主要来自 VIN 缺失或无效，不适合直接作为业务判断依据。

结论：车辆类别适合做辅助标签，不建议作为主分群依据。

可配图：
- 本次 2026 清洗样本没有稳定可用的 VIN 车辆分布图，不建议强行配图。

### 3. 充电时长是最有解释力的行为变量之一

- 订单时长分布明显右偏，短时订单占主导，长时订单占比较小。
- 工作日和周末之间存在结构差异，时段峰值也比较集中。
- 时长和电量结合看，比单看时长更容易区分不同补能模式。

结论：充电时长需要按多个维度细化看，单一均值不够解释用户行为。

可配图：
- `outputs/analysis/images/orders_2026/duration_distribution.svg`
- `outputs/analysis/images/orders_2026/weekday_weekend_duration_structure.svg`

### 4. 高频异常用户已按动态阈值剔除

- 2026 年数据只覆盖了部分自然年，因此高频阈值已按覆盖天数动态折算。
- 本次覆盖天数为 154 天，对应动态阈值也是 154 单。
- 超阈值用户已整组剔除，避免少数异常高频用户拉偏整体行为特征。

结论：这一步是必要的，能让后续 RFM 和行为标签更稳定。

可配图：
- `outputs/analysis/cleaning_summary.md`

### 5. 短时重启任务合并后，订单口径更稳定

- 短时重启订单在原始数据中并不少，直接按订单数看会放大重复会话。
- 合并后，主表会话数明显少于原始订单数，说明同一用户同站短间隔重复启动的情况客观存在。
- 这类处理能更接近还原一次真实充电行为，避免后续时长、频次和 RFM 统计被抬高。

结论：短时重启合并是必要的清洗步骤，能让后续用户行为识别更接近真实充电习惯。

可配图：
- `outputs/analysis/images/orders_2026/short_restart_merge_donut.svg`

## 三、图表建议

- 用户整体画像：`outputs/features/archive/all_years/images/user_behavior_patterns/rfm_type_donut.svg`
- 风险等级结构：`outputs/features/archive/all_years/images/user_behavior_patterns/risk_level_donut.svg`
- 价格敏感结构：`outputs/features/archive/all_years/images/user_behavior_patterns/price_sensitivity_donut.svg`

## 四、汇报口径

- 本周重点是把清洗、特征、标签和阈值口径统一起来。
- 当前阶段的结论优先服务用户行为模式识别，不延伸到经营预测。
- 后续汇报建议优先围绕“场站类别、充电时长、短时重启合并、高频异常剔除”四个点展开。

## 四、当前可直接复用的结果

- `outputs/cleaned/orders_2026_standard_user_orders.csv`
- `outputs/abnormal/orders_2026_abnormal_orders.csv`
- `outputs/analysis/cleaning_summary.md`
- `outputs/analysis/behavior_analysis_summary.md`
- `outputs/features/archive/all_years/final_user_behavior_profile.csv`
- `outputs/features/archive/all_years/final_user_behavior_labels.csv`
- `outputs/features/archive/all_years/user_behavior_rfm_segments.csv`
