@echo off
chcp 65001 >nul
echo 正在修复网络设置 (Python 深度清理)...
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "tools\reset_network.py"
) else (
    echo 未找到 Python 环境，尝试直接清理注册表...
    rem 手动清理作为备用
    reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v AutoConfigURL /f >nul 2>&1
    reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer /f >nul 2>&1
    reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyOverride /f >nul 2>&1
    reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable /t REG_DWORD /d 0 /f >nul 2>&1
    bitsadmin /util /setieproxy localsystem NO_PROXY >nul 2>&1
)

echo.
echo 如果仍无法上网，请尝试重启电脑。
echo.
pause
