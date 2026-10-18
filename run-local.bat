@echo off
REM Этот батник запускается скрыто через run-local.vbs (без своего окна).
REM Он открывает 3 окна (webapp, ngrok, backend) и прописывает ngrok-URL в .env.
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- проверки (окна нет, поэтому об ошибке сообщаем диалогом) ---
where ngrok >nul 2>nul || (mshta "javascript:alert('ngrok not found in PATH. Install from ngrok.com/download, then run: ngrok config add-authtoken YOUR_TOKEN');close()" & exit /b 1)
if not exist "venv\Scripts\python.exe" (mshta "javascript:alert('Python venv not found. Create venv and install backend requirements first.');close()" & exit /b 1)
if not exist "webapp\node_modules" (pushd webapp & call npm install & popd)
if not exist "backend\.env" (copy "backend\.env.example" "backend\.env" >nul)

REM --- frontend (Vite :5173) + ngrok ---
start "InSync webapp" cmd /k "cd /d "%~dp0webapp" && npm run dev"
start "InSync ngrok" cmd /k "ngrok http 5173"

REM --- поймать ngrok-URL и записать его в backend\.env ---
set "NGROK_URL="
for /l %%i in (1,1,40) do (
  if not defined NGROK_URL (
    for /f "usebackq delims=" %%u in (`powershell -NoProfile -Command "$u=(Invoke-RestMethod http://127.0.0.1:4040/api/tunnels -ErrorAction SilentlyContinue).tunnels ^| ? {$_.public_url -like 'https*'} ^| select -First 1 -Expand public_url; if($u){$u}"`) do set "NGROK_URL=%%u"
    if not defined NGROK_URL (timeout /t 1 /nobreak >nul)
  )
)
if defined NGROK_URL powershell -NoProfile -Command "$p='backend\.env';$u='!NGROK_URL!';$c=Get-Content $p;if($c -match '^WEBAPP_URL='){$c=$c -replace '^WEBAPP_URL=.*',('WEBAPP_URL='+$u)}else{$c+=('WEBAPP_URL='+$u)};[IO.File]::WriteAllText((Resolve-Path $p).Path,(($c -join [Environment]::NewLine)+[Environment]::NewLine),(New-Object Text.UTF8Encoding($false)))"

REM --- backend (API + бот), уже с актуальным WEBAPP_URL ---
start "InSync backend" cmd /k "cd /d "%~dp0" && venv\Scripts\python.exe -m backend.main"
exit
