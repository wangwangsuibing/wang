@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   自动驾驶数据采集平台 - 启动中...
echo ============================================
python -m pip install -r backend\requirements.txt -q
echo 启动服务: http://127.0.0.1:8080
start "" http://127.0.0.1:8080
python backend\main.py
pause
