# scripts/trust-sign.ps1
#
# 本机自签名 helper：给 StreamPack 的 exe 打 Authenticode 签名，
# 只在当前 Windows 用户下建立信任（Root + TrustedPublisher），不影响其他机器。
#
# 典型调用（由 GUI 菜单发起）：
#   powershell -File trust-sign.ps1 -AppPath <exe> -WaitPid <gui pid> -Relaunch
#
# 撤销信任：
#   powershell -File trust-sign.ps1 -RemoveCert
#
# 设计依据（dev-notes/docs/windows/local-self-signing.md）：
# - 只信任公钥证书，私钥留在 CurrentUser\My，不导出；
# - 根证书可复用于其他项目，子证书按项目签发；
# - 撤销时先删子证书，根下没有其他子证书才删根；
# - PowerShell 删不掉 CurrentUser\Root 的项，用 certutil -user -delstore；
# - 签名前必须等目标进程退出、文件解锁。

[CmdletBinding()]
param(
    [string]$AppPath = "",
    [string]$RootSubject = "CN=Hungry-M",
    [string]$ChildSubject = "CN=StreamPack",
    [int]$WaitPid = 0,
    [switch]$Relaunch,
    [switch]$RemoveCert,
    [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"

if (-not $LogFile) {
    $logDir = Join-Path $env:LOCALAPPDATA "StreamPack"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $LogFile = Join-Path $logDir "trust-sign.log"
}

function Write-Log {
    param([string]$Message)
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

# ---------- 撤销 ----------

if ($RemoveCert) {
    Write-Log "开始撤销信任：$ChildSubject"

    foreach ($store in @("My", "TrustedPublisher")) {
        Get-ChildItem "Cert:\CurrentUser\$store" -ErrorAction SilentlyContinue |
            Where-Object { $_.Subject -eq $ChildSubject } |
            ForEach-Object {
                certutil -user -delstore $store $_.Thumbprint | Out-Null
                Write-Log "  已删除子证书（$store）: $($_.Thumbprint)"
            }
    }

    $others = @()
    foreach ($store in @("My", "TrustedPublisher")) {
        $others += Get-ChildItem "Cert:\CurrentUser\$store" -ErrorAction SilentlyContinue |
            Where-Object { $_.Issuer -eq $RootSubject -and $_.Subject -ne $RootSubject }
    }

    if ($others.Count -gt 0) {
        Write-Log "  根下还有 $($others.Count) 个其他项目的子证书，保留根：$RootSubject"
    } else {
        foreach ($store in @("My", "Root", "TrustedPublisher")) {
            Get-ChildItem "Cert:\CurrentUser\$store" -ErrorAction SilentlyContinue |
                Where-Object { $_.Subject -eq $RootSubject } |
                ForEach-Object {
                    certutil -user -delstore $store $_.Thumbprint | Out-Null
                    Write-Log "  已删除根证书（$store）: $($_.Thumbprint)"
                }
        }
    }

    Write-Log "撤销完成。"
    exit 0
}

# ---------- 确定目标 ----------

if (-not $AppPath) {
    foreach ($name in @("streampack-gui.exe", "streampack.exe")) {
        $candidate = Join-Path $PSScriptRoot $name
        if (Test-Path $candidate) { $AppPath = $candidate; break }
    }
}

if (-not $AppPath -or -not (Test-Path $AppPath)) {
    Write-Log "找不到要签名的文件：$AppPath"
    exit 1
}

Write-Log "目标：$AppPath"

# ---------- 等进程退出、文件解锁 ----------

if ($WaitPid -gt 0) {
    Write-Log "等待进程 $WaitPid 退出"
    try { Wait-Process -Id $WaitPid -Timeout 120 -ErrorAction SilentlyContinue } catch { }

    $unlocked = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $stream = [System.IO.File]::Open($AppPath, "Open", "ReadWrite", "None")
            $stream.Close()
            $unlocked = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $unlocked) {
        Write-Log "文件仍被占用，放弃签名：$AppPath"
        exit 1
    }
}

# ---------- 证书 ----------

function Test-IsCa {
    param($Cert)
    $ext = $Cert.Extensions | Where-Object { $_.Oid.Value -eq "2.5.29.19" }
    if (-not $ext) { return $false }

    # RawData 是 BasicConstraints 的 DER：SEQUENCE { BOOLEAN cA }
    #   30 <len> 01 01 <ff|00>
    # 不能直接看 RawData[0]（那是 SEQUENCE 的 tag 0x30）。
    $raw = $ext.RawData
    if ($raw.Length -lt 5 -or $raw[0] -ne 0x30) { return $false }

    $pos = 2
    if ($raw[1] -band 0x80) { $pos = 2 + ($raw[1] -band 0x7F) }   # 长格式长度
    if ($pos + 2 -ge $raw.Length) { return $false }
    if ($raw[$pos] -ne 0x01 -or $raw[$pos + 1] -ne 0x01) { return $false }
    return ($raw[$pos + 2] -ne 0x00)
}

$root = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq $RootSubject } |
    Sort-Object NotAfter -Descending | Select-Object -First 1

# 早期版本用 -Type CodeSigningCert 建根，没有 CA 标记，签出来的子证书链
# 验证不过（Set-AuthenticodeSignature 报 UnknownError）。发现这种根就清掉重建。
if ($root -and -not (Test-IsCa $root)) {
    Write-Log "现有根证书缺少 CA 标记，删除后重建（$($root.Thumbprint)）"
    foreach ($store in @("My", "Root", "TrustedPublisher")) {
        Get-ChildItem "Cert:\CurrentUser\$store" -ErrorAction SilentlyContinue |
            Where-Object { $_.Subject -eq $ChildSubject -or $_.Subject -eq $RootSubject } |
            ForEach-Object {
                certutil -user -delstore $store $_.Thumbprint | Out-Null
            }
    }
    $root = $null
}

if (-not $root) {
    Write-Log "创建根 CA：$RootSubject"
    $root = New-SelfSignedCertificate `
        -Type Custom `
        -Subject $RootSubject `
        -KeyUsage CertSign, CRLSign `
        -KeyUsageProperty Sign `
        -KeyExportPolicy Exportable `
        -KeyLength 4096 `
        -HashAlgorithm SHA256 `
        -CertStoreLocation Cert:\CurrentUser\My `
        -NotAfter (Get-Date).AddYears(10) `
        -TextExtension @("2.5.29.19={text}ca=1&pathlength=1")
} else {
    Write-Log "复用根 CA：$($root.Thumbprint)"
}

$child = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq $ChildSubject -and $_.Issuer -eq $RootSubject } |
    Sort-Object NotAfter -Descending | Select-Object -First 1

if (-not $child) {
    Write-Log "签发子证书：$ChildSubject"
    $child = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $ChildSubject `
        -Signer $root `
        -CertStoreLocation Cert:\CurrentUser\My `
        -KeyExportPolicy Exportable `
        -NotAfter (Get-Date).AddYears(3)
} else {
    Write-Log "复用子证书：$($child.Thumbprint)"
}

# ---------- 信任（幂等） ----------

function Ensure-Trusted {
    param($Cert, [string]$Store)

    $exists = Get-ChildItem "Cert:\CurrentUser\$Store" -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $Cert.Thumbprint }
    if ($exists) { return }

    $cer = Join-Path $env:TEMP ("streampack-" + $Cert.Thumbprint + ".cer")
    Export-Certificate -Cert $Cert -FilePath $cer | Out-Null

    # 不能用 Import-Certificate：导入 CA 根到 Root 会弹"安全警告"确认框，
    # 而本脚本通常在隐藏窗口里跑，没人点就永远卡住（实测卡死 5 分钟）。
    # certutil 直接调证书 API，不弹 UI。
    $out = certutil -user -addstore -f $Store $cer 2>&1
    Remove-Item $cer -Force -ErrorAction SilentlyContinue

    $exists = Get-ChildItem "Cert:\CurrentUser\$Store" -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $Cert.Thumbprint }
    if (-not $exists) {
        throw "导入 $Store 失败: $($out -join ' ')"
    }
    Write-Log "  已信任到 $Store"
}

Ensure-Trusted -Cert $root -Store "Root"
Ensure-Trusted -Cert $child -Store "TrustedPublisher"

# ---------- 签名 ----------
#
# 无论成功失败都要把程序重新打开：用户点的是"信任并签名"，
# 结果程序再也不出现会被当成崩溃。

$signatureOk = $false
try {
    Write-Log "签名中…"
    Set-AuthenticodeSignature -FilePath $AppPath -Certificate $child `
        -HashAlgorithm SHA256 | Out-Null

    $check = Get-AuthenticodeSignature $AppPath
    Write-Log "签名状态：$($check.Status)"
    if ($check.Status -eq "Valid") {
        $signatureOk = $true
    } elseif (-not (Test-IsCa $root)) {
        Write-Log "提示：根证书不是 CA，子证书链无法验证"
    }
} catch {
    Write-Log "签名异常：$($_.Exception.Message)"
}

if (-not $signatureOk) {
    Write-Log "签名未成功，程序仍会重新打开（签名可稍后重试）"
}

if ($Relaunch) {
    Write-Log "重新启动程序"
    Start-Process -FilePath $AppPath
}

if ($signatureOk) {
    Write-Log "完成。仅本机当前用户生效，其他电脑不信任。"
    exit 0
}
exit 1
