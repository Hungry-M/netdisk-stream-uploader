@echo off
rem StreamPack 本机自签名的双击入口。
rem 直接运行会给本目录的 exe 签名并建立本机信任；撤销加 -RemoveCert。
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0trust-sign.ps1" %*
echo.
pause
