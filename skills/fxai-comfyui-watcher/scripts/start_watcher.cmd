rem 双击启动全局广播监听（最小化运行，无控制台窗口残留）。
rem 监听地址自动解析：--url 参数 > COMFY_URL 环境变量 > 探活本机 > fxai_global.json 的 comfy_url。
rem 停止：start_watcher.cmd stop
rem 查状态：start_watcher.cmd top
@echo off
rem Python 解释器：优先 PY 环境变量，其次 PATH 里的 python
if "%PY%"=="" set "PY=python"
if "%1"=="stop" start "" /min "%PY%" -u "%~dp0comfy_watch.py" stop & exit /b
if "%1"=="top"  start "" /min "%PY%" -u "%~dp0comfy_watch.py" top  & exit /b
start "" /min "%PY%" -u "%~dp0comfy_watch.py" watch
