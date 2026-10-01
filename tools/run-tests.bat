@echo off
rem Runs both test files. They need no phone and no GUI, so this is the check to
rem run after editing anything in here.

setlocal
cd /d "%~dp0"
set FAILED=0

for %%t in (test_ksu_inspector.py test_ksu_ops.py test_verify_boot.py) do (
  echo ==== %%t ====
  python "%~dp0%%t"
  if errorlevel 1 set FAILED=1
  echo.
)

if "%FAILED%"=="1" (
  echo GAGAL: ada tes yang tidak lulus.
) else (
  echo Semua tes lulus.
)
exit /b %FAILED%