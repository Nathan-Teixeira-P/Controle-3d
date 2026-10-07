@echo off
rem Inicia o sistema (cria os volumes protegidos na primeira vez).
docker volume create controle_pgdata >nul
docker volume create controle_uploads >nul
docker compose up -d --build
echo.
echo Sistema em http://localhost:3000
pause
