@echo off
setlocal
if not exist "%~dp0.venv-core\Scripts\python.exe" (
  echo {"status":"BLOCKED_UNSUPPORTED","error_code":"PYTHON_REQUIRED","next_action":"Restore the documented core interpreter; no installation was attempted."}
  exit /b 5
)
"%~dp0.venv-core\Scripts\python.exe" -B -X utf8 "%~dp0scripts\launch_multi_ai_v2.py" %*
exit /b %ERRORLEVEL%
