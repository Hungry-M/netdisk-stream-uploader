# scripts/cleanup.ps1
#
# StreamPack 的便携清理。
#
# 默认只列清单（DryRun），加 -Yes 才真删。只删"能证明属于本应用"的内容：
#   - 数据目录里存在 credentials.json / token.json / tasks.db / config.json 之一
#   - 程序目录里存在 streampack.exe
# 不按目录名扫、不杀进程、不碰证书（证书由 trust-sign.ps1 -RemoveCert 负责）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File cleanup.ps1                 # 只看会删什么
#   powershell -ExecutionPolicy Bypass -File cleanup.ps1 -Yes            # 删数据目录
#   powershell -ExecutionPolicy Bypass -File cleanup.ps1 -Yes -RemoveFolder
#   powershell -ExecutionPolicy Bypass -File cleanup.ps1 -Yes -KeepBackups

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$KeepBackups,
    [switch]$RemoveFolder,
    [string]$DataDir = "",
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

$dataDir = if ($DataDir) {
    $DataDir
} elseif ($env:STREAMPACK_HOME) {
    # 自动化测试用 STREAMPACK_HOME 把数据目录关进沙箱
    $env:STREAMPACK_HOME
} else {
    Join-Path $env:USERPROFILE ".netdisk_uploader"
}
$selfDir = $PSScriptRoot
$installDir = if ($InstallDir) { $InstallDir } else { Split-Path -Parent $selfDir }

$dataMarkers = @("credentials.json", "token.json", "tasks.db", "config.json")

function Test-OurDataDir {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    foreach ($m in $dataMarkers) {
        if (Test-Path (Join-Path $Path $m)) { return $true }
    }
    return $false
}

Write-Host "StreamPack 清理"
Write-Host ""

$targets = @()

if (Test-OurDataDir $dataDir) {
    $found = @($dataMarkers | Where-Object { Test-Path (Join-Path $dataDir $_) })
    $targets += [pscustomobject]@{
        Kind   = "数据目录"
        Path   = $dataDir
        Reason = "含 $($found -join ', ')"
    }
} else {
    Write-Host "  跳过数据目录（不存在或不含本应用的文件）: $dataDir"
}

$exePath = Join-Path $installDir "streampack.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "  跳过程序目录（没找到 streampack.exe）: $installDir"
} elseif (-not $RemoveFolder) {
    $targets += [pscustomobject]@{
        Kind   = "程序目录（需 -RemoveFolder）"
        Path   = $installDir
        Reason = "含 streampack.exe；当前只提示，不删除"
    }
} elseif ($selfDir -like "$installDir*") {
    # 脚本自己在这个目录里，删不掉含自身的目录；交给用户手动删
    $targets += [pscustomobject]@{
        Kind   = "程序目录（脚本在目录内）"
        Path   = $installDir
        Reason = "脚本正在其中运行，请手动删除该文件夹"
    }
} else {
    $targets += [pscustomobject]@{
        Kind   = "程序目录"
        Path   = $installDir
        Reason = "含 streampack.exe"
    }
}

if ($targets.Count -eq 0) {
    Write-Host ""
    Write-Host "没有可清理的内容。"
    exit 0
}

Write-Host ""
Write-Host "将要处理："
$targets | Format-Table Kind, Path, Reason -AutoSize | Out-String | Write-Host

if (-not $Yes) {
    Write-Host "以上为预览。确认无误后加 -Yes 执行："
    Write-Host "   powershell -ExecutionPolicy Bypass -File cleanup.ps1 -Yes"
    exit 0
}

# ---- 真正删除 ----

$removed = 0
$failed = 0

foreach ($t in $targets) {
    if ($t.Kind -eq "程序目录（需 -RemoveFolder）" -or $t.Kind -eq "程序目录（脚本在目录内）") {
        Write-Host "  保留（需手动删除）: $($t.Path)"
        continue
    }

    try {
        if ($t.Kind -eq "数据目录") {
            # 只删本应用知道的那几项，其余不动
            foreach ($m in $dataMarkers) {
                $f = Join-Path $t.Path $m
                if (Test-Path $f) {
                    Remove-Item $f -Force
                    Write-Host "  已删除: $f"
                    $removed++
                }
            }
            $left = @(Get-ChildItem $t.Path -Force -ErrorAction SilentlyContinue)
            if ($left.Count -eq 0) {
                Remove-Item $t.Path -Force
                Write-Host "  已删除空目录: $($t.Path)"
            } else {
                Write-Host "  目录里还有其它文件，保留: $($t.Path)"
            }
        } else {
            Remove-Item $t.Path -Recurse -Force
            Write-Host "  已删除: $($t.Path)"
            $removed++
        }
    } catch {
        Write-Host "  删除失败: $($t.Path) — $($_.Exception.Message)"
        $failed++
    }
}

if ($KeepBackups) {
    Write-Host "  -KeepBackups 已指定；本版本没有备份目录，无操作"
}

Write-Host ""
Write-Host "完成：删除 $removed 项，失败 $failed 项。"
Write-Host "网盘上已上传的文件不受影响；证书请用 trust-sign.ps1 -RemoveCert 清理。"

if ($failed -gt 0) { exit 1 }
exit 0
