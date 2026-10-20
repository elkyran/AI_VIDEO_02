@echo off
setlocal
pushd "%~dp0"
if not exist ".venv-core\Scripts\python.exe" (
  echo Core runtime missing. See docs\INSTALL.md.
  popd
  exit /b 2
)
".venv-core\Scripts\python.exe" -X utf8 -m app.multi_ai_ui --open %*
set "multi_ai_exit=%ERRORLEVEL%"
popd
exit /b %multi_ai_exit%
