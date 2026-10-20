@echo off
setlocal
pushd "%~dp0"
if not exist ".venv-core\Scripts\python.exe" (
  echo Core runtime missing. See docs\INSTALL.md.
  popd
  exit /b 2
)
".venv-core\Scripts\python.exe" -m app %*
set "studio_exit=%ERRORLEVEL%"
popd
exit /b %studio_exit%
