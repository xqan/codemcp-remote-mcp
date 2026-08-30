@echo off
setlocal
cd /d "%~dp0"

"%~dp0codemcp-remote.exe" start
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo codemcp-remote failed to start. Exit code: %EXIT_CODE%
    echo Run codemcp-remote.exe doctor for details.
    pause >nul
    exit /b %EXIT_CODE%
)

echo codemcp-remote started.
timeout /t 2 /nobreak >nul
exit /b 0
