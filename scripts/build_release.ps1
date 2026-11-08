# scripts/build_release.ps1
#
# 打包成可分发的目录并压缩：
#     dist/StreamPack/                <- 直接可运行
#     dist/StreamPack-<版本>-win64.zip  <- 用来发 Release
#
# 用法（项目根目录）：
#     pwsh -File scripts/build_release.ps1
#
# 需要 PyInstaller：pip install pyinstaller

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 版本 =="
$codeVersion = (python -c "from src.version import __version__; print(__version__)").Trim()
# 用 tag --points-at 而不是 describe --exact-match：后者在没 tag 时往 stderr 打字，
# 配合 Stop 策略会直接打断脚本
$exactTag = (git tag --points-at HEAD | Select-Object -First 1)
if ($exactTag) {
    if ($exactTag.TrimStart('v') -ne $codeVersion) {
        throw "版本不一致：tag=$exactTag，src/version.py=$codeVersion"
    }
    $version = $exactTag
} else {
    $short = (git rev-parse --short HEAD).Trim()
    $version = "$codeVersion+$short"
    Write-Host "   不在 tag 上，产物标记为 $version"
}
Write-Host "   $version"

Write-Host "== 清理旧产物 =="
if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }

Write-Host "== PyInstaller =="
python -m PyInstaller packaging/streampack.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（exit $LASTEXITCODE）" }

$outDir = Join-Path $root "dist/StreamPack"
if (-not (Test-Path $outDir)) { throw "没有生成 $outDir" }

Write-Host "== 自检 =="
& "$outDir/streampack.exe" --lang zh --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "streampack.exe --help 执行失败（exit $LASTEXITCODE）" }

& "$outDir/streampack.exe" --lang en config list | Out-Null
if ($LASTEXITCODE -ne 0) { throw "streampack.exe config list 执行失败（exit $LASTEXITCODE）" }
Write-Host "   exe 可执行，翻译目录已进包"

Write-Host "== 附带文件 =="
# 便携包要自带清理脚本和文档，否则用户删不干净也没处查
$extras = @(
    "README.md",
    "README.zh.md",
    "LICENSE",
    "scripts\cleanup.ps1",
    "scripts\cleanup.cmd",
    "scripts\uninstall.ps1",
    "scripts\uninstall.cmd"
)
foreach ($f in $extras) {
    if (Test-Path $f) {
        Copy-Item $f -Destination $outDir -Force
        Write-Host "   + $f"
    } else {
        Write-Host "   ! 缺失: $f"
    }
}

Write-Host "== 安装器 =="
$env:STREAMPACK_VERSION = $version.TrimStart('v')
python -m PyInstaller packaging/installer.spec --noconfirm --clean --distpath dist --workpath build-installer
if ($LASTEXITCODE -ne 0) { throw "安装器构建失败（exit $LASTEXITCODE）" }
$setup = Get-ChildItem "dist/StreamPack-Setup-*.exe" | Select-Object -First 1
if (-not $setup) { throw "没有生成安装器" }
Write-Host "   $($setup.Name) ($([math]::Round($setup.Length / 1MB, 1)) MB)"

Write-Host "== 压缩 =="
$zip = Join-Path $root "dist/StreamPack-$version-win64.zip"
Compress-Archive -Path $outDir -DestinationPath $zip -CompressionLevel Optimal
$sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "   $zip ($sizeMB MB)"

Write-Host ""
Write-Host "完成。分发时让对方解压后运行："
Write-Host "   streampack.exe        命令行"
Write-Host "   streampack-gui.exe    图形界面"
