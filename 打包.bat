@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    pause
    exit /b 1
)

echo Building SilverStar_GSHC...
python -m PyInstaller --noconfirm --clean "SilverStar_GSHC.spec"
set "BUILD_EXIT_CODE=%ERRORLEVEL%"

if not "%BUILD_EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Packaging failed with exit code %BUILD_EXIT_CODE%.
    pause
    exit /b %BUILD_EXIT_CODE%
)

echo.
echo Packaging completed successfully.
echo Output: %~dp0dist\SilverStar_GSHC\SilverStar_GSHC.exe
pause
exit /b 0
