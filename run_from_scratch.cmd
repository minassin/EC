@echo off
setlocal
chcp 65001 >nul
rem ===================================================================
rem  EC 项目：从零跑通全链路（9 步）
rem  导入 -> 清洗 -> 导出 -> 分析 -> 特征 -> 可视化 -> 滑窗 -> 名单 -> 落桶
rem  滑动窗口固定：窗口 90 天 / 步长 1 天 / 回看 180 天（共 91 个窗口）
rem
rem  用法：
rem    run_from_scratch.cmd                    默认 data\dataset + mysql_run
rem    run_from_scratch.cmd [Excel根目录] [批次名]
rem
rem  复用已有清洗数据、只补第 5~9 步：在下面命令末尾追加
rem    --skip-import --skip-clean --skip-export --skip-analysis
rem  只重跑第 7~9 步（滑窗/名单/落桶）：再加 --skip-feature --skip-visualize
rem ===================================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "INPUT_DIR=%~1"
if "%INPUT_DIR%"=="" set "INPUT_DIR=%ROOT%data\dataset"
set "RUN_NAME=%~2"
if "%RUN_NAME%"=="" set "RUN_NAME=mysql_run"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo ============================================================
echo  批次名   : %RUN_NAME%
echo  输入目录 : %INPUT_DIR%
echo  滑窗参数 : 窗口 90 天 / 步长 1 天 / 回看 180 天
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 找不到 .venv\Scripts\python.exe，请先在项目根目录建好虚拟环境。
  exit /b 1
)

".venv\Scripts\python.exe" "scripts\run_mysql_full_pipeline.py" ^
  --input-dir "%INPUT_DIR%" ^
  --run-name "%RUN_NAME%" ^
  --clean-scope full ^
  --workers 2 ^
  --recent-window-days 90 ^
  --sliding-window-days 90 ^
  --sliding-step-days 1 ^
  --sliding-horizon-days 180 ^
  --recent-horizon-days 180
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [失败] 退出码 %RC%
  exit /b %RC%
)

echo.
echo [完成] 产物落点：
echo   清洗与分析 : outputs\runs\%RUN_NAME%\
echo   特征与分群 : outputs\features\archive\%RUN_NAME%\
echo     - 滑动窗口 : outputs\features\archive\%RUN_NAME%\sliding_windows\
echo     - 三份名单 : outputs\features\archive\%RUN_NAME%\recent_user_segments\
echo     - 最终分群 : outputs\features\archive\%RUN_NAME%\segments\
exit /b 0
