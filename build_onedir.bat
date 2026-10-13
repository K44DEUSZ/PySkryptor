@echo off
setlocal ENABLEEXTENSIONS
powershell.exe -NoProfile -File "%~dp0tools\build_release.ps1" %*
exit /b %ERRORLEVEL%
