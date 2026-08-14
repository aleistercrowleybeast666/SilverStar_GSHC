@echo off
setlocal
cd /d "%~dp0\.."

echo [1/3] Installing Python dependencies...
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo [2/3] Cleaning old build...
rmdir /s /q build 2>nul
rmdir /s /q dist\SilverStar_GSHC 2>nul

echo [3/3] Building onedir package...
python -m PyInstaller --noconfirm --clean SilverStar_GSHC.spec

echo.
echo Done. Output:
echo dist\SilverStar_GSHC\SilverStar_GSHC.exe
pause
