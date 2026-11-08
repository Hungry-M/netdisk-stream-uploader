# scripts/uninstall.ps1
#
# 安装版的卸载脚本：由安装器复制到安装目录，并写进 HKCU 卸载项。
#
# 默认只列清单；加 -Yes 执行。用户数据（凭证/任务库）默认保留，
# 要一起清掉需显式加 -PurgeData（内部转调 cleanup.ps1）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Yes
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Yes -PurgeData

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$PurgeData,
    [string]$InstallDir = "",
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"

$appName = "StreamPack"
$selfDir = $PSScriptRoot
$installDir = if ($InstallDir) { $InstallDir } else { $selfDir }
$exePath = Join-Path $installDir "streampack.exe"

$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appName"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcut = Join-Path $startMenu "$appName.lnk"

Write-Host "$appName 卸载"
Write-Host ""

# 证据链：目录里得有我们的 exe，否则拒绝
if (-not (Test-Path $exePath)) {
    Write-Host "  这里不像安装目录（找不到 streampack.exe）: $installDir"
    Write-Host "  如果程序已被手工删除，请改用便携包里的 cleanup.cmd"
    exit 1
}

$plan = @()
if (Test-Path $shortcut) { $plan += "开始菜单快捷方式: $shortcut" }
if (Test-Path $uninstallKey) { $plan += "卸载注册表项: $uninstallKey" }
$plan += "安装目录: $installDir"
if ($PurgeData) { $plan += "用户数据: $env:USERPROFILE\.netdisk_uploader" }

Write-Host "将要处理："
$plan | ForEach-Object { Write-Host "  - $_" }

if (-not $Yes) {
    Write-Host ""
    Write-Host "以上为预览。确认后加 -Yes 执行。"
    exit 0
}

# 1. 快捷方式
if (Test-Path $shortcut) {
    Remove-Item $shortcut -Force
    Write-Host "  已删除快捷方式"
}

# 2. 卸载项
if (Test-Path $uninstallKey) {
    Remove-Item $uninstallKey -Recurse -Force
    Write-Host "  已删除卸载注册表项"
}

# 3. 用户数据（可选）
if ($PurgeData) {
    $cleanup = Join-Path $installDir "cleanup.ps1"
    if (Test-Path $cleanup) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $cleanup -Yes
    } else {
        $dataDir = Join-Path $env:USERPROFILE ".netdisk_uploader"
        if (Test-Path $dataDir) {
            Remove-Item $dataDir -Recurse -Force
            Write-Host "  已删除用户数据: $dataDir"
        }
    }
} else {
    Write-Host "  保留用户数据（加 -PurgeData 可一并删除）"
}

# 4. 安装目录：脚本自己就在里面，交回 cmd 延迟删除
Write-Host "  安排删除安装目录: $installDir"
$cmd = "timeout /t 2 /nobreak >nul & rmdir /s /q `"$installDir`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmd -WindowStyle Hidden

Write-Host ""
Write-Host "卸载完成。网盘上已上传的文件不受影响。"
exit 0
