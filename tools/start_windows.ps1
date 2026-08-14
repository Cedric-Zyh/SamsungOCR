param(
    [string]$Python = "python",
    [string]$ModelHome = "",
    [string]$Node = ""
)

$ErrorActionPreference = "Stop"

if ($ModelHome) {
    $env:PADDLE_MODEL_HOME = $ModelHome
}
if ($Node) {
    $env:WORKSPACE_NODE = $Node
}

Write-Host "[1/2] 检查 Windows PaddleOCR 路由、依赖和模型目录..." -ForegroundColor Cyan
& $Python -m tools.windows_smoke
if ($LASTEXITCODE -ne 0) {
    throw "Windows OCR 自检未通过，请先根据上方 failures 修复环境。"
}

Write-Host "[2/2] 启动三星回单核验台：http://127.0.0.1:5001" -ForegroundColor Green
& $Python app.py
