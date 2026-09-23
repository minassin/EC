# 本周用户行为模式识别工作总结

本周工作只围绕“用户行为模式识别”展开，不扩展到资产、预测或规划任务。

## 一、本周工作

- 场站类别统一为 `高速 / 专用场站 / 公共场站 / 其他` 四类。
- 车辆类别统一为 `乘用车 / 商用车 / 未知` 三类。
- 充电时长统一为分钟口径，并按不同区间细化。
- 谷峰时段按江苏分时电价文件统一映射。
- 高频异常用户剔除改为动态阈值，不再固定按 365 条删除。
- 短时重启订单已合并为同一会话，减少重复计数。

## 二、现象与结论

### 1. 场站类别

**现象**
- 高速场站更像“路过型补能”，停留更短，时段更分散。
- 专用场站更像“固定型补能”，重复使用更强，场景更稳定。
- 公共场站更分散，受城市活动和临时出行影响更明显。
- 其他类主要是兜底分类，解释力相对弱。

**结论**
- 场站四分类能够有效区分不同补能场景。
- 后续行为分析应优先比较高速、专用和公共三类，其他类用于兜底，不宜单独过度解读。

**建议插图**
- `../outputs/analysis/images/cleaning/orders_2026_station_category_distribution.svg`
- `../outputs/analysis/images/orders_2026/station_category_duration_energy.svg`

### 2. 车辆类别

**现象**
- 乘用车是绝对主体，商用车占比很小。
- 未知主要来自 VIN 缺失、无效或无法可靠识别。
- 现有样本的主画像应围绕乘用车展开。

**结论**
- VIN 更适合做粗粒度识别，不适合强行区分网约车和私家车。
- 车辆类别在当前阶段足够支撑用户行为模式识别，但不宜继续细抠到过细车型。

**建议插图**
- `../outputs/analysis/images/cleaning/gehu_wuhan_vehicle_group_distribution.svg`
- `../outputs/analysis/images/cleaning/gehu_wuhan_vehicle_category_distribution.svg`

### 3. 充电时间时长细化

**现象**
- 单次充电时长整体集中在 30-40 分钟附近，长尾明显。
- 2022-2026 的时长中位数整体呈下降趋势，说明补能效率在提高。
- 细分区间后，短时补能、常规补能和长时停留可以明显区分。

**结论**
- 充电时长是刻画用户补能习惯的核心变量之一。
- 短时重启合并后，时长分布更接近真实会话结构，适合后续画像和行为分层。

**建议插图**
- `../outputs/analysis/images/orders_2026/duration_distribution.svg`
- `../outputs/analysis/images/orders_2026/weekday_weekend_duration_structure.svg`
- `../outputs/analysis/images/orders_yearly/avg_energy_per_order_trend.svg`
- `../outputs/analysis/images/orders_2026/short_restart_merge_donut.svg`

### 4. 谷峰时段定义

**现象**
- 用户充电开始时间长期集中在白天和下午，谷段不是主体。
- 峰平段仍是主场，谷段更多对应夜间补能和跨日长停。
- 统一口径后，不同年份可直接横向比较。

**结论**
- 谷峰时段是解释用户价格敏感性和时段偏好的关键口径。
- 统一规则后，可以直接比较不同年份的充电时段结构变化。

**建议插图**
- `../outputs/analysis/images/orders_yearly/charge_period_share_by_year.svg`
- `../outputs/analysis/images/orders_yearly/channel_share_by_year.svg`
- `../outputs/analysis/images/orders_yearly/discount_usage_rate_trend.svg`

### 5. 高频异常剔除与动态阈值

**现象**
- 固定 365 条对 2026 这种未满年的数据不合适。
- 改成动态阈值后，能避免把正常高频用户误删。
- 2022-2026 合并后，频次分布仍然是典型长尾，`>=500` 次高频用户很少，`=1` 次低频用户占主导。

**结论**
- 动态阈值比固定阈值更合理，尤其适合跨年度、跨完整度不一致的数据。
- 高频异常剔除的目标不是压低总量，而是把真正异常的极端记录从主分析里分离出去。

**建议插图**
- `../outputs/analysis/user_frequency_summary_2022_2026.csv`

## 三、总结

- 这批数据的核心行为特征是：低频、分散、以白天补能为主，少量固定站点偏好明显。
- 场站类别、车辆类别、充电时长和谷峰时段已经足够支撑后续行为模式识别。
- 动态高频剔除比固定阈值更合理，适合多年度、不同完整度的数据。
- 后续若继续做画像，优先围绕 `高速路过型 / 固定专用型 / 公共分散型 / 低频一次型` 四类去看。
