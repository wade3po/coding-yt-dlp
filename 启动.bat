@echo off
chcp 65001 >nul
title yt-dlp 下载器

:: 快速检查环境
python --version >nul 2>&1
if errorlevel 1 (
    echo  [错误] 未检测到 Python
    echo  请先双击【一键安装环境.bat】完成安装
    pause & exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 (
    echo  [错误] 未检测到 Node.js
    echo  请先双击【一键安装环境.bat】完成安装
    pause & exit /b 1
)

python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  正在安装 Flask...
    pip install flask
)

:: 启动
echo.
echo  ================================================
echo    yt-dlp 下载器 正在启动...
echo  ================================================
echo.
echo  浏览器地址：http://127.0.0.1:5000
echo  关闭此窗口即停止服务
echo.

:: 延迟 1 秒后打开浏览器（等服务启动）
ping 127.0.0.1 -n 2 >nul
start "" "http://127.0.0.1:5000"
python app.py
pause
