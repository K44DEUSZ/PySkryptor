@echo off
setlocal ENABLEEXTENSIONS

set "APP_NAME=PySkryptor"
set "ENGINE_HOST_NAME=AIModelHost"
set "ROOT=%~dp0"
set "SPEC_PATH=%ROOT%%APP_NAME%.spec"
set "DIST_DIR=%ROOT%dist\%APP_NAME%"
set "PYINSTALLER=pyinstaller"
pushd "%ROOT%"

if exist "%ROOT%.venv\Scripts\pyinstaller.exe" (
    set "PYINSTALLER=%ROOT%.venv\Scripts\pyinstaller.exe"
)

if not exist "%ROOT%app\main.py" (
    echo [ERROR] Run this script from the project root ^(where the app and assets folders exist^).
    goto :fail_build
)

"%PYINSTALLER%" --noconfirm --clean "%SPEC_PATH%"
if errorlevel 1 (
    echo [ERROR] PyInstaller failed.
    goto :fail_build
)

if not exist "%DIST_DIR%\%APP_NAME%.exe" (
    echo [ERROR] Missing build output "%DIST_DIR%\%APP_NAME%.exe".
    goto :fail_build
)

if not exist "%DIST_DIR%\%ENGINE_HOST_NAME%.exe" (
    echo [ERROR] Missing build output "%DIST_DIR%\%ENGINE_HOST_NAME%.exe".
    goto :fail_build
)

call :copy_dir "assets" "%DIST_DIR%\assets"
if errorlevel 8 goto :copy_fail

call :copy_dir "bin" "%DIST_DIR%\bin"
if errorlevel 8 goto :copy_fail

call :copy_dir "models" "%DIST_DIR%\models"
if errorlevel 8 goto :copy_fail

call :copy_dir "userdata" "%DIST_DIR%\userdata"
if errorlevel 8 goto :copy_fail

call :ensure_dir "%DIST_DIR%\userdata\config"
call :ensure_dir "%DIST_DIR%\userdata\downloads"
call :ensure_dir "%DIST_DIR%\userdata\transcriptions"
call :ensure_dir "%DIST_DIR%\userdata\logs"

for /d %%D in ("%DIST_DIR%\_internal\pytest-*.dist-info") do (
    if exist "%%~fD" rd /s /q "%%~fD"
)

call :promote_runtime_file "LICENSE"
if errorlevel 1 goto :copy_fail

call :promote_runtime_file "THIRD_PARTY_NOTICES.txt"
if errorlevel 1 goto :copy_fail

echo.
echo [OK] Build completed:
echo     %DIST_DIR%\%APP_NAME%.exe
echo     %DIST_DIR%\%ENGINE_HOST_NAME%.exe
echo.
echo Important: assets/, bin/, models/ and userdata/ must remain next to the EXE files.
goto :success

:copy_dir
if not exist "%~1" exit /b 0
robocopy "%~1" "%~2" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
exit /b %ERRORLEVEL%

:ensure_dir
if exist "%~1" exit /b 0
mkdir "%~1"
exit /b %ERRORLEVEL%

:promote_runtime_file
if exist "%DIST_DIR%\%~1" exit /b 0
if exist "%DIST_DIR%\_internal\%~1" (
    copy /Y "%DIST_DIR%\_internal\%~1" "%DIST_DIR%\%~1" >nul
    exit /b 0
)
if exist "%~1" (
    copy /Y "%~1" "%DIST_DIR%\%~1" >nul
    exit /b 0
)
echo [ERROR] Missing runtime file "%~1".
exit /b 1

:copy_fail
echo [ERROR] Runtime file copy failed.
goto :fail_build

:success
popd
exit /b 0

:fail_build
popd
exit /b 1
