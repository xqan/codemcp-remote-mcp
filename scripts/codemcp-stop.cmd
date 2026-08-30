@echo off
setlocal
cd /d "%~dp0"

"%~dp0codemcp-remote.exe" stop
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo codemcp-remote failed to stop cleanly. Exit code: %EXIT_CODE%
    echo Run codemcp-remote.exe status for details.
    pause >nul
    exit /b %EXIT_CODE%
)

echo codemcp-remote stopped.
timeout /t 2 /nobreak >nul
exit /b 0
