@echo off
REM Ewinet Route Monitor - Diagnostico Local (Windows)
REM Uso: run.bat [--once] [--daemon] [--debug]

cd /d "%~dp0"

REM Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python no encontrado. Instala Python 3.10+ desde python.org
    pause
    exit /b 1
)

REM Check mtr (requires WinMTR or similar on Windows)
where mtr >nul 2>&1
if %errorlevel% neq 0 (
    echo ADVERTENCIA: mtr no encontrado.
    echo En Windows necesitas una de estas opciones:
    echo   1. Instalar WinMTR y agregar al PATH
    echo   2. Usar WSL (recomendado): wsl --install
    echo   3. Usar traceroute como fallback
    echo.
    echo El monitor funcionara con traceroute como fallback.
    echo.
)

REM Install Python deps if needed
if not exist "venv" (
    echo Creando entorno virtual...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

REM Default to --once mode
set MODE=--once
set EXTRA_ARGS=

:parse_args
if "%~1"=="" goto run
if "%~1"=="--once" set MODE=--once
if "%~1"=="--daemon" set MODE=
if "%~1"=="--debug" set EXTRA_ARGS=%EXTRA_ARGS% --log-level DEBUG
shift
goto parse_args

:run
echo.
echo Ejecutando diagnostico de rutas...
echo.

python main.py %MODE% %EXTRA_ARGS%
pause
