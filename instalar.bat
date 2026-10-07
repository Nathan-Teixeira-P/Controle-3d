@echo off
rem Instalador completo (Windows 10/11): WSL2 + Docker Desktop + .env + sobe o sistema.
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs" & exit /b)
cd /d "%~dp0"

if not exist .env (
  echo Gerando .env com senhas aleatorias...
  powershell -NoProfile -Command "'DB_PASSWORD='+[guid]::NewGuid().ToString('N'); 'SECRET_KEY='+[guid]::NewGuid().ToString('N')+[guid]::NewGuid().ToString('N'); 'OLLAMA_MODEL=qwen2.5:1.5b'" > .env
)

set "PATH=%PATH%;%ProgramFiles%\Docker\Docker\resources\bin"
where docker >nul 2>&1 || (
  echo Instalando WSL2 e Docker Desktop...
  wsl --install --no-distribution
  winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements || goto erro
)

docker info >nul 2>&1 || start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
echo Aguardando o Docker iniciar (ate 5 min)...
for /l %%i in (1,1,60) do (
  docker info >nul 2>&1 && goto pronto
  timeout /t 5 >nul
)
echo.
echo O Docker nao iniciou. Se acabou de instalar: REINICIE o PC, abra o Docker Desktop,
echo aceite os termos, e de dois cliques neste instalar.bat de novo.
pause & exit /b 1

:pronto
call iniciar.bat
start http://localhost:3000
exit /b 0

:erro
echo Falha ao instalar o Docker Desktop (precisa de Windows 10/11 com winget).
pause & exit /b 1
