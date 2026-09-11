@echo off
rem ============================================================
rem  构建 nattunnel-client.exe (PyInstaller, 单文件, 控制台窗口)
rem  在 client/ 目录运行:  build.bat
rem  产物: dist\nattunnel-client.exe (与 config.json 放同一目录)
rem ============================================================
cd /d %~dp0

where python >nul 2>&1 || (echo 未找到 python & exit /b 1)

if not exist .venv python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

rem pyinstaller-hooks-contrib 已自动处理 cryptography 依赖
.venv\Scripts\pyinstaller.exe --noconfirm --onefile --console ^
  --name nattunnel-client ^
  nattunnel_client.py

echo.
echo 构建完成: dist\nattunnel-client.exe
echo 使用方法: 将 config.json(参考 config.example.json)与 exe 放同一目录后运行
