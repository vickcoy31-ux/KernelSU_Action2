@echo off
rem KSU Inspector - click this file to open it.
rem Kept as a launcher rather than a build step on purpose: the app is one Python
rem file using nothing outside the standard library, and turning that into an exe
rem would add a packer, a virus-scan warning and a stale copy nobody rebuilds.
rem The tests still run the same way, so there is one source of truth.

setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Python tidak ditemukan di PATH.
  echo  Pasang Python dari https://python.org/downloads/
  echo  Centang "Add python.exe to PATH" saat memasang.
  echo.
  pause
  exit /b 1
)

start "KSU Inspector" pythonw "%~dp0ksu_inspector.py"
if errorlevel 1 (
  echo.
  echo  Gagal membuka aplikasi.
  echo  Jalankan manual untuk melihat pesan error:
  echo      python "%~dp0ksu_inspector.py"
  echo.
  pause
)
endlocal