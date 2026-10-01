@echo off
rem Pre-flash verifier. Drag a boot.img onto this file, or pass one as the first
rem argument. Exit code 0 means nothing is blocking a dd.

setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo.
  echo  Pakai: verify-boot.bat ^<boot.img^> [Module.symvers]
  echo.
  echo  Contoh:
  echo    verify-boot.bat C:\Users\User\bootwork\new-boot-guard.img
  echo.
  pause
  exit /b 2
)

set SYM=
if not "%~2"=="" set SYM=--symvers "%~2"

python "%~dp0verify_boot.py" "%~1" %SYM%
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
  echo  LOLOS - aman ditulis ke /dev/block/sdc41
) else (
  echo  DITOLAK - jangan tulis ke tablet
)
echo.
pause
exit /b %RC%