@echo off
rem StreamPack 便携清理的双击入口。
rem 不带参数时只预览；要真删，加 -Yes：
rem     cleanup.cmd -Yes
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup.ps1" %*
echo.
pause
