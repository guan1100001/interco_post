@echo off
chcp 65001 >nul
title QD Dev 顺序启动：后端就绪后再启动前端

set "BACKEND_DIR=D:\workbuddy\qddev\backend"
set "FRONTEND_DIR=D:\workbuddy\qddev\frontend"
set "BACKEND_URL=http://127.0.0.1:8000"

echo [1] 启动后端服务...
start "backend" cmd /k "cd /d %BACKEND_DIR% && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

echo [2] 等待后端 %BACKEND_URL% 就绪，请稍候...
:wait_loop
curl -s --connect-timeout 1 %BACKEND_URL% >nul 2>&1
if %errorlevel% equ 0 (
    echo ✅ 后端监听成功！准备启动前端
    goto start_frontend
)
echo ⏳ 后端尚未就绪，继续等待...
timeout /t 1 /nobreak >nul
goto wait_loop

:start_frontend
echo [3] 启动前端服务
start "frontend" cmd /k "cd /d %FRONTEND_DIR% && npm run dev"

echo.
echo ==============================================
echo 全部拉起完成
echo 后端：%BACKEND_URL%
echo 前端：通常 http://localhost:5173
echo ==============================================
echo 本窗口为监控窗口，可以直接关闭，不影响服务
pause