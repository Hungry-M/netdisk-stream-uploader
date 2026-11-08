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
$version = (git describe --tags --always --dirty) 2>$null
if (-not $version) { $version = "dev" }
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

Write-Host "== 压缩 =="
$zip = Join-Path $root "dist/StreamPack-$version-win64.zip"
Compress-Archive -Path $outDir -DestinationPath $zip -CompressionLevel Optimal
$sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "   $zip ($sizeMB MB)"

Write-Host ""
Write-Host "完成。分发时让对方解压后运行："
Write-Host "   streampack.exe        命令行"
Write-Host "   streampack-gui.exe    图形界面"
