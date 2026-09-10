@echo off
chcp 65001 >nul
title 安装环境 - yt-dlp 下载器
echo.
echo  ================================================
echo    yt-dlp 下载器 - 环境安装向导
echo  ================================================
echo.
echo  本脚本将自动安装以下内容：
echo    1. Python 3
echo    2. Node.js
echo    3. Flask（Python 依赖）
echo.
echo  全程联网，预计需要 3-10 分钟，请耐心等待。
echo  安装过程中请勿关闭任何弹出的安装窗口。
echo.
pause

:: ============================================================
:: 步骤 1：检查并安装 Python
:: ============================================================
echo.
echo  [1/3] 检查 Python...
python --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  ✓ 已安装 %%v，跳过
    goto check_node
)

echo  未检测到 Python，开始下载安装...
echo  下载中，请稍候...

:: 下载 Python 安装包（3.11 稳定版）
curl -L --progress-bar -o "%TEMP%\python_installer.exe" "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
if errorlevel 1 (
    echo  [错误] Python 下载失败，请检查网络后重试
    pause & exit /b 1
)

echo  正在安装 Python（自动添加到系统路径）...
"%TEMP%\python_installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
if errorlevel 1 (
    echo  [错误] Python 安装失败
    del "%TEMP%\python_installer.exe" >nul 2>&1
    pause & exit /b 1
)
del "%TEMP%\python_installer.exe" >nul 2>&1

:: 刷新环境变量
call :refresh_path
python --version >nul 2>&1
if errorlevel 1 (
    echo  [提示] Python 已安装，但需要重启命令行才能生效
    echo  请关闭本窗口后重新双击本脚本继续安装
    pause & exit /b 0
)
echo  ✓ Python 安装完成

:: ============================================================
:: 步骤 2：检查并安装 Node.js
:: ============================================================
:check_node
echo.
echo  [2/3] 检查 Node.js...
node --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo  ✓ 已安装 Node.js %%v，跳过
    goto install_flask
)

echo  未检测到 Node.js，开始下载安装...
echo  下载中，请稍候（约 30MB）...

curl -L --progress-bar -o "%TEMP%\node_installer.msi" "https://nodejs.org/dist/v20.18.0/node-v20.18.0-x64.msi"
if errorlevel 1 (
    echo  [错误] Node.js 下载失败，请检查网络后重试
    pause & exit /b 1
)

echo  正在安装 Node.js...
msiexec /i "%TEMP%\node_installer.msi" /quiet /norestart
if errorlevel 1 (
    echo  [错误] Node.js 安装失败
    del "%TEMP%\node_installer.msi" >nul 2>&1
    pause & exit /b 1
)
del "%TEMP%\node_installer.msi" >nul 2>&1

call :refresh_path
node --version >nul 2>&1
if errorlevel 1 (
    echo  [提示] Node.js 已安装，但需要重启命令行才能生效
    echo  请关闭本窗口后重新双击本脚本继续安装
    pause & exit /b 0
)
echo  ✓ Node.js 安装完成

:: ============================================================
:: 步骤 3：安装 Python 依赖
:: ============================================================
:install_flask
echo.
echo  [3/3] 安装 Python 依赖（Flask / edge-tts / openai-whisper / deep-translator）...

python -c "import flask" >nul 2>&1
if not errorlevel 1 (
    echo  ✓ Flask 已安装
) else (
    pip install flask
    if errorlevel 1 ( echo  [错误] Flask 安装失败 & pause & exit /b 1 )
    echo  ✓ Flask 安装完成
)

python -c "import edge_tts" >nul 2>&1
if not errorlevel 1 (
    echo  ✓ edge-tts 已安装
) else (
    pip install edge-tts
    if errorlevel 1 ( echo  [错误] edge-tts 安装失败 & pause & exit /b 1 )
    echo  ✓ edge-tts 安装完成
)

python -c "import whisper" >nul 2>&1
if not errorlevel 1 (
    echo  ✓ openai-whisper 已安装
) else (
    pip install openai-whisper
    echo  ✓ openai-whisper 安装完成
)

python -c "from deep_translator import GoogleTranslator" >nul 2>&1
if not errorlevel 1 (
    echo  ✓ deep-translator 已安装
) else (
    pip install deep-translator
    echo  ✓ deep-translator 安装完成
)

:: ============================================================
:: 完成
:: ============================================================
:done
echo.
echo  ================================================
echo    ✓ 所有环境安装完毕！
echo  ================================================
echo.
echo  现在可以双击【启动.bat】运行程序了。
echo.
pause
exit /b 0

:: 刷新 PATH 函数
:refresh_path
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "user_path=%%b"
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul') do set "sys_path=%%b"
set "PATH=%sys_path%;%user_path%"
exit /b 0
