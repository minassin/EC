# EC

充电订单（EC）数据全链路：**Excel 导入 MySQL → 清洗 → 导出 CSV → 分析出图 → 用户行为特征 → 可视化 → 滑动窗口 → 近期名单 → 最终落桶**，共九步，由一个总控脚本串起来。

- 总控：[`scripts/run_mysql_full_pipeline.py`](scripts/run_mysql_full_pipeline.py)
- 一键入口：[`run_from_scratch.cmd`](run_from_scratch.cmd)

---

## 一、运行环境

### 1. 操作系统

Windows。脚本里有 Windows 绝对路径，启动器是 `.cmd`，没做跨平台适配。

### 2. 软件

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | **3.14.3** | 虚拟环境必须建在项目根的 `.venv`（总控按硬编码路径去找它） |
| MySQL 服务端 | **9.7** | 需要开着 `local_infile=1`（脚本会尝试自动开） |
| MySQL 客户端 | 9.7 | `mysql.exe`，路径写在总控第 21 行，见下方「需要改的本机路径」 |

### 3. Python 依赖

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

九步各用到这些，`requirements.txt` 已按此列全：

| 包 | 实测版本 | 用在哪 |
| --- | --- | --- |
| pandas | 3.0.3 | 全流程 |
| numpy | 2.5.1 | 分析出图 |
| matplotlib | 3.11.1 | 第 4 步的常规图表 |
| seaborn | 0.13.2 | 第 4 步的核密度曲线 |
| duckdb | 1.5.5 | 第 4 步的投影读、第 5/7/8 步的 SQL |
| openpyxl | 3.1.5 | 读 `.xlsx` 原始表、写清洗日志 |
| xlrd | 2.0.2 | 读 `.xls` 原始表 |
| tqdm | 4.70.0 | 第 1 步导入进度条 |

只跑第 1~3 步（不涉及分析和特征）的话，`matplotlib / seaborn / duckdb / tqdm` 可以不装。

### 4. 需要改的本机路径

克隆到别的机器上，这两处要按实际改：

| 位置 | 现值 | 说明 |
| --- | --- | --- |
| `scripts/run_mysql_full_pipeline.py:21` | `D:\mysql-9.7.0-winx64\mysql-9.7.0-winx64\bin\mysql.exe` | MySQL 客户端路径 |
| `scripts/run_mysql_full_pipeline.py:20` | `.venv\Scripts\python.exe` | 找不到时自动退回当前解释器，一般不用改 |

数据库连接默认 `localhost:3306 / root / 123456 / ec_all`，可用 `--db-*` 覆盖。**密码现在是明文默认值，公开仓库里请自行评估。**

### 5. 磁盘

原始 Excel 要进 MySQL，产物体积不小——本项目一次全量跑完，`outputs\` 约 **100 GB**。留足空间。

---

## 二、运行操作步骤

### 1. 准备输入目录

把待导入的 Excel 按年份放进一个目录（默认 `data\dataset`，里面是 `Y20`、`Y21`… 这样的子目录）：

```text
data\dataset\
  ├─ Y20\
  ├─ Y21\
  └─ ...
```

### 2. 确认 MySQL 已启动

```powershell
# 能连上就行
& "D:\mysql-9.7.0-winx64\mysql-9.7.0-winx64\bin\mysql.exe" -u root -p -e "SELECT VERSION();"
```

### 3. 一键跑通九步（推荐）

```powershell
cd D:\EC-project
run_from_scratch.cmd
```

默认参数：输入目录 `data\dataset`、批次名 `mysql_run`、滑窗 90/1/180。

也可以指定：

```powershell
run_from_scratch.cmd "D:\EC-project\data\dataset" "mysql_run"
```

### 4. 只补后半段（复用已有清洗结果，省时间）

> `run_from_scratch.cmd` 只接受**前两个位置参数**（输入目录、批次名），**不转发额外参数**。
> 要带 `--skip-*` 有两条路：直接调总控（推荐，见第 5 节），或者把 `--skip-*` 追加到
> `run_from_scratch.cmd` 末尾那行 python 命令后面（该文件开头的注释就是这么写的）。

第 1~4 步已经跑过、只想重跑特征与分群：

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\run_mysql_full_pipeline.py `
  --input-dir data\dataset --run-name mysql_run `
  --skip-import --skip-clean --skip-export --skip-analysis
```

再跳过特征与可视化，只重跑第 7~9 步（滑窗 / 名单 / 落桶）：

```powershell
.\.venv\Scripts\python.exe scripts\run_mysql_full_pipeline.py `
  --input-dir data\dataset --run-name mysql_run `
  --skip-import --skip-clean --skip-export --skip-analysis `
  --skip-feature --skip-visualize
```

只想验证第 2 步（清洗）而不动后面的产物，就 `--skip-import --skip-export --skip-analysis --skip-feature --skip-visualize --skip-sliding-windows --skip-recent-segments --skip-segmentation`。

### 5. 直接调总控（需要更细的控制时）

```powershell
cd D:\EC-project
.\.venv\Scripts\python.exe scripts\run_mysql_full_pipeline.py `
  --input-dir data\dataset `
  --run-name mysql_run `
  --clean-scope full `
  --workers 2 `
  --recent-window-days 90 `
  --sliding-window-days 90 --sliding-step-days 1 --sliding-horizon-days 180 `
  --recent-horizon-days 180
```

常用参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--input-dir` | 必填 | 待导入的 Excel 根目录 |
| `--run-name` | `mysql_run` | 输出批次名，决定产物目录 |
| `--source-run-name` | 同 `--run-name` | 复用另一个批次的清洗 CSV |
| `--table-like` | `%` | 原始表匹配规则，如 `Y20\_%%` |
| `--clean-scope` | `full` | `full` 全量清洗；`incremental` 只清洗新增的表 |
| `--workers` | `2` | 清洗并行进程数 |
| `--force-reimport` | 关 | 表已存在也强制重新导入 |
| `--skip-*` | 关 | 九步各有 `--skip-import / -clean / -export / -analysis / -feature / -visualize / -sliding-windows / -recent-segments / -segmentation` |
| `--recent-window-days` | `90` | 用户行为特征的分析窗口 |
| `--sliding-window-days` | `90` | 滑动窗口大小 |
| `--sliding-step-days` | `1` | 滑动步长（90/1/180 = 91 个窗口，**这步耗时随窗口数线性增长**） |
| `--sliding-horizon-days` | `180` | 滑动窗口回看跨度 |
| `--recent-horizon-days` | `180` | 新用户/回流名单回看跨度 |

### 6. 九步明细

| 步 | 做什么 | 脚本 |
| --- | --- | --- |
| 1 | 导入原始数据到数据库 | [`scripts/import_dataset_to_mysql.py`](scripts/import_dataset_to_mysql.py) |
| 2 | 数据库清洗 | [`scripts/clean_mysql_dataset.py`](scripts/clean_mysql_dataset.py) + [`scripts/cleaning/clean.py`](scripts/cleaning/clean.py) |
| 3 | 导出分析 CSV | 总控内置（`export_table`） |
| 4 | 生成分析图表 | [`scripts/analysis/analyze.py`](scripts/analysis/analyze.py) |
| 5 | 构建用户行为特征 | [`scripts/user_behavior_features/archive/build_user_behavior_features_pipeline.py`](scripts/user_behavior_features/archive/build_user_behavior_features_pipeline.py) |
| 6 | 用户行为可视化 | [`scripts/user_behavior_features/archive/visualize_user_behavior_features.py`](scripts/user_behavior_features/archive/visualize_user_behavior_features.py) |
| 7 | 滑动窗口高价值活跃轨迹 | [`scripts/user_behavior_features/archive/build_rfm_sliding_windows.py`](scripts/user_behavior_features/archive/build_rfm_sliding_windows.py) |
| 8 | 近 7/14/180 天名单 | [`scripts/user_behavior_features/archive/build_recent_user_segments.py`](scripts/user_behavior_features/archive/build_recent_user_segments.py) |
| 9 | 最终分群落桶 | [`scripts/user_behavior_features/archive/build_user_segmentation.py`](scripts/user_behavior_features/archive/build_user_segmentation.py) |

### 7. 产物落点

```text
outputs\runs\<批次名>\
  ├─ cleaned\     清洗后标准订单 CSV + 快照 meta
  ├─ abnormal\    异常订单
  ├─ discard\     废弃订单
  ├─ overview\    清洗概览
  └─ analysis\    图表（含 images\<批次名>\，其中年份子目录按订单年份分）

outputs\features\archive\<批次名>\
  ├─ order_behavior_base.csv          订单行为基表
  ├─ user_behavior_features.csv       用户行为特征
  ├─ user_behavior_rfm_segments.csv   RFM 分层
  ├─ final_user_behavior_profile.csv  最终画像
  ├─ final_user_behavior_labels.csv   最终标签
  ├─ sliding_windows\                 第 7 步
  ├─ recent_user_segments\            第 8 步
  └─ segments\                        第 9 步
```

### 8. 每步耗时怎么看

总控每步结束时打印 `[X/9] 完成，用时 Ns`。**目前不落盘**，要留痕请自行重定向：

```powershell
run_from_scratch.cmd > run.log 2>&1
```

---

## 三、已知问题

### 1. `reporting_utils.py` 源码缺失 —— 第 2 步跑不起来 ⚠️

[`scripts/cleaning/clean.py:50`](scripts/cleaning/clean.py#L50) 依赖 `from reporting_utils import ...`，但 `reporting_utils.py` **在仓库里不存在**，只剩 `scripts/__pycache__/reporting_utils.cpython-314.pyc`。Python 不会从 `__pycache__` 里无源码导入，因此：

```text
ModuleNotFoundError: No module named 'reporting_utils'
```

`clean_mysql_dataset.py` 也 `from cleaning.clean import ...`，所以第 2 步（数据库清洗）依赖链是断的。**在新克隆的环境上跑不到第 2 步。**

修法二选一：补回 `reporting_utils.py`（需提供 `CLEANING_SUMMARY_MD`、`path_for_markdown`、`update_summary_section` 三个符号），或把 `.pyc` 反编译复原。

### 2. 明文数据库密码

`scripts/run_mysql_full_pipeline.py:44` 的 `--db-password` 默认值是明文 `123456`。建议改为从环境变量读。

### 4. 图表两套口径并存

第 4 步的 `analyze.py` 会同时产出两套图：

- **matplotlib 那套**：走 `setup_plotting()` 的 rcParams；
- **手写 SVG 那套**：直接拼 SVG 字符串，共 17 张（15 类分析图 + 2 张场站类别环形图），图型对齐 `analysis\images\<批次名>\legacy\<年>\`，不读 rcParams。

手写 SVG 那套要再投影读一遍清洗表（11 列），耗时和内存都明显更高。

`--skip-legacy-svg` 是 **`analyze.py` 自己的参数，总控没有透出**。想只刷新 matplotlib 那套，就直接调 `analyze.py`：

```powershell
.\.venv\Scripts\python.exe scripts\analysis\analyze.py `
  --cleaned-file outputs\runs\mysql_run\cleaned\mysql_run_standard_user_orders.csv `
  --image-prefix mysql_run --label mysql_run --split-by-year --skip-legacy-svg
```

另外一个口径细节：数据只覆盖单一年份时，手写 SVG 那套直接对整帧出图（保留「充电开始时间」为空的行），这是为了对齐历史图型，改动会让图对不上。
