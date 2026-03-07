@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

title SlashCo/FISH Legacy Residue Cleanup

if /I not "%~1"=="-elevated" (
  net session >nul 2>&1
  if not "%errorlevel%"=="0" (
    echo Requesting Administrator permission...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '-elevated' -Verb RunAs"
    exit /b
  )
)

echo.
echo === SlashCo/FISH Residue Cleanup ===
echo.

set "INET_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
set "SCRIPT_DIR=%~dp0"


echo [1/8] Kill possible residue processes...
for %%P in (
  mitmdump.exe
  mitmproxy.exe
  mitmweb.exe
  caddy.exe
  SlashFishStandalone.exe
  fish_standalone.exe
  sponsor_standalone.exe
  slashco.exe
) do (
  taskkill /F /T /IM %%P >nul 2>&1
)


echo [2/8] Reset WinINET proxy settings...
reg add "%INET_KEY%" /v ProxyEnable /t REG_DWORD /d 0 /f >nul 2>&1
reg delete "%INET_KEY%" /v AutoConfigURL /f >nul 2>&1
reg delete "%INET_KEY%" /v ProxyServer /f >nul 2>&1
reg delete "%INET_KEY%" /v ProxyOverride /f >nul 2>&1
powershell -NoProfile -Command "$sig='[DllImport(\"wininet.dll\")]public static extern bool InternetSetOption(int h,int o,int b,int l);';$t=Add-Type -MemberDefinition $sig -Name WinInet -Namespace Native -PassThru;[Native.WinInet]::InternetSetOption(0,39,0,0)|Out-Null;[Native.WinInet]::InternetSetOption(0,37,0,0)|Out-Null" >nul 2>&1


echo [3/8] Reset WinHTTP proxy...
netsh winhttp reset proxy >nul 2>&1


echo [4/8] Cleanup old hosts markers...
powershell -NoProfile -Command "$p=Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'; if(Test-Path $p){ $old=Get-Content -Path $p -ErrorAction SilentlyContinue; $new=@(); foreach($l in $old){ $line=$l.ToLowerInvariant(); $drop=$false; if($line -match 'slashcocaddy|slashcosponsorproxy'){ $drop=$true }; if($line -match 'pastebin\.com' -and $line -match '127\.0\.0\.1'){ $drop=$true }; if(-not $drop){ $new += $l } }; if($new.Count -ne $old.Count){ Set-Content -Path $p -Value $new -Encoding UTF8; Write-Host '  - hosts cleaned' } else { Write-Host '  - hosts unchanged' } }" 2>nul


echo [5/8] Cleanup old caddy residue files...
if exist "%SCRIPT_DIR%caddy\caddy.pid" del /f /q "%SCRIPT_DIR%caddy\caddy.pid" >nul 2>&1
if exist "%SCRIPT_DIR%caddy\_dns_stop" del /f /q "%SCRIPT_DIR%caddy\_dns_stop" >nul 2>&1
if exist "%LOCALAPPDATA%\SlashCoSponsor\tools\caddy\caddy.pid" del /f /q "%LOCALAPPDATA%\SlashCoSponsor\tools\caddy\caddy.pid" >nul 2>&1
if exist "%LOCALAPPDATA%\SlashCoSponsor\tools\caddy\_dns_stop" del /f /q "%LOCALAPPDATA%\SlashCoSponsor\tools\caddy\_dns_stop" >nul 2>&1


echo [6/8] Cleanup _MEI temp folders...
for /d %%D in ("%TEMP%\_MEI*") do rd /s /q "%%~fD" >nul 2>&1


echo [7/8] Flush DNS cache...
ipconfig /flushdns >nul 2>&1


echo [8/8] Optional: remove mitmproxy certificates
set "REMOVE_CERT="
set /p REMOVE_CERT=Remove mitmproxy certificates from trust stores? [y/N]: 
if /I "%REMOVE_CERT%"=="Y" (
  powershell -NoProfile -Command "$stores=@(@('Root','CurrentUser'),@('Root','LocalMachine')); foreach($it in $stores){$name=$it[0];$loc=$it[1]; try{$s=New-Object Security.Cryptography.X509Certificates.X509Store($name,$loc); $s.Open('ReadWrite'); $targets=@($s.Certificates | Where-Object { $_.Subject -like '*mitmproxy*' -or $_.Issuer -like '*mitmproxy*' }); foreach($c in $targets){ $s.Remove($c) }; $s.Close(); Write-Host ('  - removed '+$targets.Count+' cert(s) from '+$loc+'\\'+$name) } catch { Write-Host ('  - skip '+$loc+'\\'+$name) }}" 2>nul
) else (
  echo   - skipped cert removal
)

echo.
echo Current proxy status:
reg query "%INET_KEY%" /v ProxyEnable 2>nul
reg query "%INET_KEY%" /v ProxyServer 2>nul
netsh winhttp show proxy

echo.
echo Cleanup finished.
pause
