@echo off
rem One-click install for Windows: Python (if missing) + packages + Desktop shortcut.
rem Double-click this file. Needs internet the first time (about 500 MB).
cd /d "%~dp0"
where py >/dev/null 2>/dev/null || where python >/dev/null 2>/dev/null || (
  echo Python not found - installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python installed. Close this window and double-click Install.bat again.
  pause
  exit /b
)
call "%~dp0rov.cmd" setup || (echo. & echo Setup failed - see the messages above. & pause & exit /b 1)
call "%~dp0rov.cmd" shortcut
echo.
echo Done. Use the "ROV Control" icon on your Desktop.
echo Practice without the ROV:  rov.cmd run-sim
pause
