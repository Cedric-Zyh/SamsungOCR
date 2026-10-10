param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Desktop = Join-Path $Root "desktop"
$ResourceServer = Join-Path $Desktop "resources\SamsungReceipt"

Push-Location $Root
try {
    Write-Host "[1/4] 安装/检查 Python 打包依赖..." -ForegroundColor Cyan
    & $Python -m pip install -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Python 依赖安装失败。" }

    Write-Host "[2/4] 构建本地 Flask/OCR 服务..." -ForegroundColor Cyan
    & $Python -m PyInstaller --clean --noconfirm packaging/SamsungReceipt.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败。" }

    if (Test-Path $ResourceServer) { Remove-Item -Recurse -Force $ResourceServer }
    New-Item -ItemType Directory -Force -Path $ResourceServer | Out-Null
    Copy-Item -Recurse -Force "dist\SamsungReceipt\*" $ResourceServer
    if (-not (Test-Path (Join-Path $ResourceServer "SamsungReceipt.exe") -PathType Leaf)) {
        throw "打包资源中缺少 SamsungReceipt.exe，无法生成安装包。"
    }

    Write-Host "[3/4] 构建桌面前端..." -ForegroundColor Cyan
    Push-Location $Desktop
    try {
        & pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw "前端依赖安装失败。" }
        & pnpm tauri build
        if ($LASTEXITCODE -ne 0) { throw "Tauri 安装包构建失败。" }
    } finally {
        Pop-Location
    }
    Write-Host "[4/4] 安装包已生成在 desktop\src-tauri\target\release\bundle\nsis\" -ForegroundColor Green
} finally {
    Pop-Location
}
