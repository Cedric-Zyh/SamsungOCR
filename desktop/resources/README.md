# Bundled server

Windows release builds copy the PyInstaller output to
`resources/SamsungReceipt/` before running `tauri build`. The desktop shell
looks for `SamsungReceipt.exe` there at runtime.
