@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- checks ---
where ngrok >nul 2>nul
if errorlevel 1 (
  echo [X] ngrok not found in PATH.
  echo     Install https://ngrok.com/download  then run once:  ngrok config add-authtoken YOUR_TOKEN
  timeout /t 10 & exit /b 1
)
if not exist "venv\Scripts\python.exe" (
  echo [X] venv not found:  python -m venv venv  ^&^&  venv\Scripts\pip install -r backend\requirements.txt
  timeout /t 10 & exit /b 1
)
if not exist "webapp\node_modules" (
  echo [i] Installing webapp dependencies...
  pushd webapp & call npm install & popd
)
if not exist "backend\.env" (
  copy "backend\.env.example" "backend\.env" >nul
  echo [i] Created backend\.env - fill BOT_TOKEN and SESSION_KEYS before use.
)

REM --- 1) frontend (Vite :5173) and 2) ngrok tunnel ---
start "InSync webapp" cmd /k "cd /d "%~dp0webapp" && npm run dev"
start "InSync ngrok" cmd /k "ngrok http 5173"

REM --- detect ngrok public URL (poll up to ~40s) ---
echo Waiting for ngrok...
set "NGROK_URL="
for /l %%i in (1,1,40) do (
  if not defined NGROK_URL (
    for /f "usebackq delims=" %%u in (`powershell -NoProfile -Command "$u=(Invoke-RestMethod http://127.0.0.1:4040/api/tunnels -ErrorAction SilentlyContinue).tunnels ^| ? {$_.public_url -like 'https*'} ^| select -First 1 -Expand public_url; if($u){$u}"`) do set "NGROK_URL=%%u"
    if not defined NGROK_URL (timeout /t 1 /nobreak >nul)
  )
)

REM --- write WEBAPP_URL into backend\.env (UTF-8, no BOM) ---
if defined NGROK_URL (
  powershell -NoProfile -Command "$p='backend\.env';$u='!NGROK_URL!';$c=Get-Content $p;if($c -match '^WEBAPP_URL='){$c=$c -replace '^WEBAPP_URL=.*',('WEBAPP_URL='+$u)}else{$c+=('WEBAPP_URL='+$u)};[IO.File]::WriteAllText((Resolve-Path $p).Path,(($c -join [Environment]::NewLine)+[Environment]::NewLine),(New-Object Text.UTF8Encoding($false)))"
)

REM --- 3) backend (after WEBAPP_URL is set) ---
start "InSync backend" cmd /k "cd /d "%~dp0" && venv\Scripts\python.exe -m backend.main"

cls
echo ============================================
echo   InSync is running - three windows opened.
echo.
if defined NGROK_URL (
  echo   Public URL:  !NGROK_URL!
) else (
  echo   URL: see the "InSync ngrok" window, line "Forwarding".
)
echo   Paste this URL into @BotFather - Mini App settings.
echo ============================================
echo   This launcher closes automatically...
timeout /t 8 /nobreak >nul
exit
