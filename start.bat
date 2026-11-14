@echo off
cd /d %~dp0
echo 启动全栈服务: http://127.0.0.1:8000
echo (前端开发模式另开终端: cd frontend ^&^& npm run dev)
python backend/run.py
pause
