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
    [switch]$NoRestart,
    [switch]$Detach
)

$ErrorActionPreference = "Stop"

$appName = "StreamPack"
$selfDir = $PSScriptRoot
$installDir = if ($InstallDir) { $InstallDir } else { $selfDir }
$exePath = Join-Path $installDir "streampack.exe"

$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appName"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcut = Join-Path $startMenu "$appName.lnk"

# ---------- 第二阶段：从 TEMP 里执行真正的删除 ----------
#
# 为什么要复制自己：以安装目录为 cwd 的进程无法删除该目录（目录被占用）。
# 用户双击 uninstall.cmd 时 cwd 正是安装目录，uninstall.cmd 的 pause 还会
# 一直占着它。所以先把脚本复制到 TEMP，由那个副本（cwd=TEMP）完成删除。
if ($Detach) {
    if (-not $Yes) { exit 0 }

    $removed = 0
    for ($i = 0; $i -lt 40; $i++) {
        if (-not (Test-Path $installDir)) { break }
        try {
            Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction Stop
            $removed = 1
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($removed) {
        Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

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
        $dataDir = if ($env:STREAMPACK_HOME) { $env:STREAMPACK_HOME }
                   else { Join-Path $env:USERPROFILE ".netdisk_uploader" }
        if (Test-Path $dataDir) {
            Remove-Item $dataDir -Recurse -Force
            Write-Host "  已删除用户数据: $dataDir"
        }
    }
} else {
    Write-Host "  保留用户数据（加 -PurgeData 可一并删除）"
}

# 4. 安装目录
#
# 能不能就地删，取决于当前工作目录是否在安装目录里：
# - 不在（设置→应用、或 uninstall.cmd 已 cd 到 TEMP）：直接同步删，最可靠；
# - 在（直接跑 ps1、或用户从资源管理器打开该目录的终端）：自己占着目录，
#   删不掉，复制一份脚本到 TEMP 让那边删。
Write-Host "  处理安装目录: $installDir"

$cwdInside = $false
try {
    $cwdInside = (Get-Location).Path.StartsWith($installDir, [StringComparison]::OrdinalIgnoreCase)
} catch {
    $cwdInside = $false
}

if (-not $cwdInside) {
    for ($i = 0; $i -lt 20; $i++) {
        try {
            Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction Stop
            break
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
    Write-Host "  已删除安装目录"
} else {
    $staged = Join-Path $env:TEMP ("streampack-uninstall-" + [guid]::NewGuid().ToString("N") + ".ps1")
    Copy-Item -LiteralPath $PSCommandPath -Destination $staged -Force
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $staged, "-Detach", "-Yes", "-InstallDir", $installDir
    ) -WindowStyle Hidden -WorkingDirectory $env:TEMP
    Write-Host "  已安排后台删除（当前目录在安装目录内，需退出后完成）"
}

Write-Host ""
Write-Host "卸载完成。网盘上已上传的文件不受影响。"
exit 0
