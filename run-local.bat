@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   InSync - local run via ngrok
echo ============================================
echo.

REM --- checks ---
where ngrok >nul 2>nul
if errorlevel 1 (
  echo [X] ngrok not found in PATH.
  echo     Install from https://ngrok.com/download and run once:
  echo         ngrok config add-authtoken YOUR_TOKEN
  pause & exit /b 1
)
if not exist "venv\Scripts\python.exe" (
  echo [X] venv not found. Create it first:
  echo         python -m venv venv
  echo         venv\Scripts\pip install -r backend\requirements.txt
  pause & exit /b 1
)
if not exist "webapp\node_modules" (
  echo [i] Installing webapp dependencies...
  pushd webapp & call npm install & popd
)
if not exist "backend\.env" (
  copy "backend\.env.example" "backend\.env" >nul
  echo [i] Created backend\.env - fill in BOT_TOKEN and SESSION_KEYS before use.
)

REM --- 1) frontend: Vite dev server on :5173 ---
start "InSync webapp" cmd /k "cd /d "%~dp0webapp" && npm run dev"

REM --- 2) ngrok tunnel to the frontend ---
start "InSync ngrok" cmd /k "ngrok http 5173"

echo Waiting for ngrok to come up...
set "NGROK_URL="
for /l %%i in (1,1,30) do (
  if not defined NGROK_URL (
    for /f "usebackq delims=" %%u in (`powershell -NoProfile -Command "try{((Invoke-RestMethod http://127.0.0.1:4040/api/tunnels).tunnels ^| Where-Object {$_.proto -eq 'https'})[0].public_url}catch{''}"`) do set "NGROK_URL=%%u"
    if not defined NGROK_URL (timeout /t 1 /nobreak >nul)
  )
)

if defined NGROK_URL (
  echo ngrok URL: !NGROK_URL!
  REM write WEBAPP_URL into backend\.env without BOM
  powershell -NoProfile -Command "$p='backend\.env'; $u='!NGROK_URL!'; $c=Get-Content $p; if($c -match '^WEBAPP_URL='){$c=$c -replace '^WEBAPP_URL=.*',('WEBAPP_URL='+$u)}else{$c+=('WEBAPP_URL='+$u)}; [IO.File]::WriteAllText((Resolve-Path $p),(($c -join [Environment]::NewLine)+[Environment]::NewLine),(New-Object Text.UTF8Encoding($false)))"
  echo WEBAPP_URL written to backend\.env
) else (
  echo [X] Could not detect ngrok URL automatically.
  echo     Open the ngrok window, copy the https address and put it into backend\.env as WEBAPP_URL.
)

REM --- 3) backend: FastAPI API + Telegram bot ---
start "InSync backend" cmd /k "cd /d "%~dp0" && venv\Scripts\python.exe -m backend.main"

echo.
echo ============================================
if defined NGROK_URL echo   Mini App URL: !NGROK_URL!
echo   Paste this URL into @BotFather -^> Mini App settings.
echo ============================================
echo Three windows opened: webapp, ngrok, backend.
echo To stop everything - just close those three windows.
echo.
pause
