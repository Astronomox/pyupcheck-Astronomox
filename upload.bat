@echo off
setlocal

:: ── pyupcheck release script ──────────────────────────────────────────────────
:: Usage: upload.bat YOUR_PYPI_TOKEN

if "%~1"=="" (
    echo Usage: upload.bat YOUR_PYPI_TOKEN
    exit /b 1
)

set TOKEN=%~1

echo.
echo Cleaning old builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo Building...
python -m build
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Uploading to PyPI...
twine upload dist\* -u __token__ -p %TOKEN%
if errorlevel 1 (
    echo Upload failed.
    exit /b 1
)

echo.
echo Done. Installing latest version...
for /f "tokens=*" %%v in ('python -c "import tomllib; f=open(\"pyproject.toml\",\"rb\"); d=tomllib.load(f); print(d[\"project\"][\"version\"])"') do set VERSION=%%v

pip install pyupcheck==%VERSION% --force-reinstall --quiet
echo.
echo pyupcheck %VERSION% is live.
pyupcheck banner
endlocal
