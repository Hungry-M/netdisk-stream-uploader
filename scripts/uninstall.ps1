# scripts/uninstall.ps1
#
# 安装版的卸载脚本：由安装器复制到安装目录，并写进 HKCU 卸载项。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1            # 预览
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Yes       # 执行
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Yes -PurgeData
#
# 从「设置 → 应用」启动时没有控制台：确认/失败都走对话框，不靠 Write-Host。
# -Yes 模式下不再提问（没有可交互的终端会永久阻塞）。

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$PurgeData,
    [string]$InstallDir = "",
    [switch]$Detach,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$appName = "StreamPack"
$installDir = if ($InstallDir) { $InstallDir } else { $PSScriptRoot }
$exePath = Join-Path $installDir "streampack.exe"

$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appName"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$shortcut = Join-Path $startMenu "$appName.lnk"

$logDir = Join-Path $env:LOCALAPPDATA $appName
$logFile = Join-Path $logDir "uninstall.log"

function Write-Log {
    param([string]$Message)
    Write-Host $Message
    try {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        Add-Content -Path $logFile -Encoding UTF8 `
            -Value ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
    } catch { }
}

$hasConsole = try { -not [Console]::IsInputRedirected } catch { $true }

function Show-Message {
    param([string]$Text)
    if ($Quiet -or $hasConsole) {
        Write-Log $Text
        return
    }
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        [System.Windows.Forms.MessageBox]::Show($Text, "$appName 卸载") | Out-Null
    } catch {
        Write-Log $Text
    }
}

function Get-Instances {
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $path = $null
        try { $path = $_.Path } catch { return $false }
        $path -and $path.StartsWith($installDir, [StringComparison]::OrdinalIgnoreCase)
    }
}

# ---------- 第二阶段：从 TEMP 里执行删除 ----------
#
# 删除进程的 cwd 不能是待删目录（目录会被自己占着），所以复制一份脚本到
# TEMP，由那边删。
if ($Detach) {
    $removed = $false
    for ($i = 0; $i -lt 40; $i++) {
        if (-not (Test-Path $installDir)) { $removed = $true; break }
        try {
            Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction Stop
            $removed = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($removed) {
        Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
        exit 0
    }
    Write-Log "删除安装目录失败: $installDir"
    exit 1
}

Write-Log "$appName 卸载"
Write-Log ""

# 证据链：目录里得有我们的 exe，否则拒绝
if (-not (Test-Path $exePath)) {
    Show-Message ("这里不像安装目录（找不到 streampack.exe）：$installDir`n`n" +
                  "如果程序已被手工删除，请改用便携包里的 cleanup.cmd。")
    exit 1
}

# 正在运行的程序会锁住文件，必须先处理
$instances = @(Get-Instances)
if ($instances.Count -gt 0) {
    $names = ($instances | ForEach-Object { $_.ProcessName } ) -join ", "

    if (-not $Yes) {
        # 预览模式不杀进程，也不阻塞提问（可能没有可交互的控制台）
        Show-Message ("$appName 正在运行（$names）。`n`n" +
                      "请先关闭它，或运行 uninstall.cmd -Yes 让卸载器自动关闭。")
        exit 2
    }

    Write-Log "程序正在运行，先结束："
    foreach ($proc in $instances) {
        try {
            Stop-Process -Id $proc.Id -Force -ErrorAction Stop
            Write-Log "  已结束: $($proc.ProcessName) (PID $($proc.Id))"
        } catch {
            Write-Log "  结束失败: $($proc.ProcessName) (PID $($proc.Id))"
        }
    }
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-Instances)) { break }
        Start-Sleep -Milliseconds 300
    }
    if (Get-Instances) {
        Show-Message "程序仍在运行，无法卸载。请手动退出 $appName 后重试。"
        exit 1
    }
}

$plan = @()
if (Test-Path $shortcut) { $plan += "开始菜单快捷方式: $shortcut" }
if (Test-Path $uninstallKey) { $plan += "卸载注册表项: $uninstallKey" }
$plan += "安装目录: $installDir"
if ($PurgeData) { $plan += "用户数据: $env:USERPROFILE\.netdisk_uploader" }

Write-Log "将要处理："
$plan | ForEach-Object { Write-Log "  - $_" }

if (-not $Yes) {
    if (-not $hasConsole) {
        Show-Message ("检测到 $appName 安装。`n`n要卸载，请运行安装目录里的 uninstall.cmd，" +
                      "或从「设置 → 应用」重新点击卸载。")
        exit 0
    }
    Write-Log ""
    Write-Log "以上为预览。确认后加 -Yes 执行。"
    exit 0
}

$failures = @()

# 1. 快捷方式
if (Test-Path $shortcut) {
    try {
        Remove-Item $shortcut -Force -ErrorAction Stop
        Write-Log "  已删除快捷方式"
    } catch {
        $failures += "删除快捷方式失败: $($_.Exception.Message)"
    }
}

# 2. 卸载项
if (Test-Path $uninstallKey) {
    try {
        Remove-Item $uninstallKey -Recurse -Force -ErrorAction Stop
        Write-Log "  已删除卸载注册表项"
    } catch {
        $failures += "删除卸载项失败: $($_.Exception.Message)"
    }
}

# 3. 本机证书（先子后根）。必须在删目录之前做：trust-sign.ps1 就在目录里。
$trustSign = Join-Path $installDir "trust-sign.ps1"
if (Test-Path $trustSign) {
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $trustSign -RemoveCert | Out-Null
        Write-Log "  已清理本机信任证书"
    } catch {
        $failures += "清理证书失败: $($_.Exception.Message)"
    }
} else {
    Write-Log "  没找到 trust-sign.ps1，跳过证书清理"
}

# 4. 用户数据（可选）
if ($PurgeData) {
    $cleanup = Join-Path $installDir "cleanup.ps1"
    try {
        if (Test-Path $cleanup) {
            & powershell -NoProfile -ExecutionPolicy Bypass -File $cleanup -Yes
        } else {
            $dataDir = if ($env:STREAMPACK_HOME) { $env:STREAMPACK_HOME }
                       else { Join-Path $env:USERPROFILE ".netdisk_uploader" }
            if (Test-Path $dataDir) {
                Remove-Item $dataDir -Recurse -Force -ErrorAction Stop
                Write-Log "  已删除用户数据: $dataDir"
            }
        }
    } catch {
        $failures += "删除用户数据失败: $($_.Exception.Message)"
    }
} else {
    Write-Log "  保留用户数据（加 -PurgeData 可一并删除）"
}

# 5. 安装目录
$cwdInside = $false
try {
    $cwdInside = (Get-Location).Path.StartsWith($installDir, [StringComparison]::OrdinalIgnoreCase)
} catch {
    $cwdInside = $false
}

if (-not $cwdInside) {
    $removed = $false
    for ($i = 0; $i -lt 20; $i++) {
        try {
            Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction Stop
            $removed = $true
            break
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
    if ($removed) {
        Write-Log "  已删除安装目录"
    } elseif (Test-Path $installDir) {
        $failures += "删除安装目录失败: $installDir（可能仍有文件被占用）"
    }
} else {
    $staged = Join-Path $env:TEMP ("streampack-uninstall-" + [guid]::NewGuid().ToString("N") + ".ps1")
    Copy-Item -LiteralPath $PSCommandPath -Destination $staged -Force
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $staged, "-Detach", "-Yes", "-InstallDir", $installDir
    ) -WindowStyle Hidden -WorkingDirectory $env:TEMP
    Write-Log "  已安排后台删除（当前目录在安装目录内，需退出后完成）"
}

if ($failures.Count -gt 0) {
    $text = "卸载未完全成功：`n`n" + ($failures -join "`n") + "`n`n日志：$logFile"
    Show-Message $text
    exit 1
}

Show-Message "$appName 已卸载。`n`n用户数据默认保留，需要清理请运行 cleanup.cmd。"
exit 0
