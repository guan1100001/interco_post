@echo off
chcp 65001 >nul
cd /d %~dp0
echo 开发模式（热重载）启动: http://127.0.0.1:8000
echo 应用导入路径: backend.app.main:app   （必须在项目根目录执行）
echo.
python -m uvicorn backend.app.main:app --reload --reload-dir backend --host 0.0.0.0 --port 8000
pause
