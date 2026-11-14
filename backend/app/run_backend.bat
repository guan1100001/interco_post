@echo off
chcp 65001 >nul
setlocal
rem 使用本机 conda 环境 langcag1 (Python 3.14.2) 启动后端
set "UVICORN_EXE=D:\zanaconda\zpy3142\langcag1\Scripts\uvicorn.exe"
rem 兜底：保证 backend.app.* 绝对导入与 app 包在任意目录都可解析
set "PYTHONPATH=D:\workbuddy\qddev;D:\workbuddy\qddev\backend;%PYTHONPATH%"
cd /d %~dp0
echo ============================================================
echo  后端环境 : langcag1 (Python 3.14.2)
echo  启动命令 : uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
echo  服务地址 : http://127.0.0.1:8000    API 文档 /docs
echo ============================================================
"%UVICORN_EXE%" app.main:app --reload --host 0.0.0.0 --port 8000
pause
