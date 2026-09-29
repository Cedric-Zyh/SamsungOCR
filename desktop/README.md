# 三星回单核验台桌面端

这是现有 Flask 页面外面的 Tauri 桌面窗口。业务页面、API、SQLite 数据库和识别队列仍由根目录项目提供；桌面端只负责启动本地服务、等待服务就绪并在窗口中打开页面。

## 本机开发

先确保根目录 Python 环境已经安装 `requirements.txt`，然后执行：

```bash
cd desktop
pnpm install
pnpm tauri:dev:mac
```

`tauri:dev:mac` 默认使用当前开发机的 `/Users/zhuyihao/anaconda3/bin/python`。如果 Python 环境不同，可以先设置 `SAMSUNG_RECEIPT_PYTHON` 再运行。桌面壳会自动从项目根目录启动 `app.py`，并为每次运行选择空闲端口。

浏览器运行 `pnpm dev` 只用于查看启动页，不会启动 Flask 或识别服务。

## Windows 安装包

在 Windows 开发机上执行：

```powershell
desktop\scripts\build_installer.ps1
```

脚本先构建根目录的 PyInstaller 服务程序，再把它放入 Tauri 资源目录，最后生成当前用户安装模式的 NSIS 安装包。目标电脑不需要预装 Python、Node.js 或 Rust。

首次运行仍由现有识别服务负责准备 PaddleOCR 模型；运行数据继续保存在 `%LOCALAPPDATA%\SamsungReceipt`。
