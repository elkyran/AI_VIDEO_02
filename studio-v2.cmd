@echo off
setlocal
set "STUDIO_V2_PYTHON=%~dp0.venv-core\Scripts\python.exe"
if not exist "%STUDIO_V2_PYTHON%" if defined STUDIO_PYTHON set "STUDIO_V2_PYTHON=%STUDIO_PYTHON%"
if not exist "%STUDIO_V2_PYTHON%" (
  echo {"status":"BLOCKED_UNSUPPORTED","error_code":"PYTHON_REQUIRED","next_action":"Restore the documented core interpreter; no installation was attempted."}
  exit /b 5
)
"%STUDIO_V2_PYTHON%" -B -X utf8 "%~dp0scripts\launch_multi_ai_v2.py" %*
exit /b %ERRORLEVEL%
