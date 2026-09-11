@echo off
rem ============================================================
rem  构建 nattunnel-client.exe (PyInstaller, 单文件, 控制台窗口)
rem  在 client/ 目录运行:  build.bat
rem  产物: dist\nattunnel-client.exe (与 config.json 放同一目录)
rem  图标:   仓库根 favicon.ico (--icon 打进 exe; 缺失时用默认图标)
rem ============================================================
cd /d %~dp0

where python >nul 2>&1 || (echo 未找到 python & exit /b 1)

if not exist .venv python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

rem exe 图标: 仓库根的 favicon.ico(不存在则跳过, 不中断构建)
set ICONFLAG=
if exist ..\favicon.ico set ICONFLAG=--icon ..\favicon.ico

rem pyinstaller-hooks-contrib 已自动处理依赖
.venv\Scripts\pyinstaller.exe --noconfirm --onefile --console ^
  --name nattunnel-client ^
  %ICONFLAG% ^
  nattunnel_client.py

echo.
echo 构建完成: dist\nattunnel-client.exe
echo 使用方法: 将 config.json(参考 config.example.json)与 exe 放同一目录, 然后运行
echo   nattunnel-client.exe -a ^<authtoken^> [-s http(s)://公网服务器]
echo  authtoken 在网页管理端 "认证令牌" 卡片生成(也可省略 -a 启动后在控制台输入)
