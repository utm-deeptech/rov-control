@echo off
rem Windows launcher: rov setup | test | run | run-sim | gamepad-test | network | shortcut
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\rov.ps1" %*
