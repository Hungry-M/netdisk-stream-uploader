@echo off
rem StreamPack 卸载的双击入口。不带参数只预览；真删加 -Yes。
rem     uninstall.cmd -Yes
rem     uninstall.cmd -Yes -PurgeData
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
echo.
pause
