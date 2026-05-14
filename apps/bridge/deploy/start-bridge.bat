@echo off
REM Brikko Bridge — start daemon + SSH reverse tunnel.
REM CEO double-clicks this OR puts it in Startup folder.
REM Logs to %USERPROFILE%\.brikko-bridge\supervisor.log

set "BRIDGE_DIR=%~dp0.."
set "PYTHON=C:\Users\gridc\AppData\Local\Programs\Python\Python312\python.exe"
set "BRIDGE_DAEMON_CLAUDE_BINARY=C:\Users\gridc\AppData\Roaming\Claude\claude-code\2.1.128\claude.exe"
REM Bot username — used by supervisor to print correct https://t.me/<bot>?start=<token> deep-link
set "BRIDGE_BOT_USERNAME=brikkoclaude_bot"

cd /d "%BRIDGE_DIR%"

echo Starting Brikko Bridge supervisor...
echo Logs: %USERPROFILE%\.brikko-bridge\supervisor.log
echo Press Ctrl+C to stop.
echo.

"%PYTHON%" -m daemon.supervisor
