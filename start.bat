@echo off
rem Double-click to start DFIR. Needs Docker Desktop (https://www.docker.com/products/docker-desktop/).
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is not running. Install and start Docker Desktop, then double-click this file again.
  pause
  exit /b 1
)
echo Starting DFIR. The first start builds the app and downloads about 3 GB of models - please wait.
start "" /min cmd /c "timeout /t 20 >nul & start http://localhost:8000"
docker compose up --build
pause
