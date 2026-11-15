@echo off
setlocal EnableExtensions DisableDelayedExpansion
py -3 -B -X utf8 "%~dp0patch.py" %*
exit /b %errorlevel%
