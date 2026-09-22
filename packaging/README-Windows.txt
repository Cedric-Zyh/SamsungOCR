三星回单核验台 Windows 单机版

使用方法：
1. 双击“启动.bat”。
2. 程序启动后会自动打开浏览器，地址为 http://127.0.0.1:5001。
3. 关闭程序时双击“停止.bat”。

数据位置：
%LOCALAPPDATA%\SamsungReceipt

识别模型第一次使用时会下载到 %LOCALAPPDATA%\SamsungReceipt\models。请预留足够磁盘空间，
并确保第一次启动时可以联网。识别结果、上传图片和中间证据图都保存在数据目录中。

如果需要清瞳印章接口，请通过环境变量配置 SEAL_API_KEY 或 SEAL_API_KEY_FILE；
默认使用本地印章识别，不会上传回单。
