# 三星回单核验台

三星回单核验台是一个本地 Web 工具，用于批量识别、核验和人工复核三星电子回单。它把 OCR 结果、日期和印章证据、复核记录以及质量统计放在同一个工作流中，适合在本机处理一批图片，也支持按需接入远程识别服务。

## 能做什么

- 导入多张图片或整个文件夹，后台排队识别并显示每张单据的处理进度。
- 识别回单字段、商品明细、签收日期和客户印章，并保存原图、裁剪图和处理后的证据图。
- 自动区分标准回单、商品明细续页、仓库货物接收委托书和未知文档；续页不会被当作一张完整回单重复计算。
- 对日期和印章执行保守匹配。证据不足、存在冲突或时间关系不成立时，记录会进入“待复核”，不会为了提高通过率强行补值。
- 在复核页查看各路原始 OCR、处理图和判定依据，修改字段、日期、印章结论，填写错误类型和备注。
- 支持单张或批量确认、重新识别、筛选、历史记录和人工真值维护。首次机器识别值会保留，人工修改不会覆盖评测原值。
- 提供质量概览、字段和商品准确率、错误类型以及不同识别方式的对比。

日期、印章和匹配规则的详细说明见 [`docs/日期识别、印章识别与匹配比较逻辑说明.md`](docs/日期识别、印章识别与匹配比较逻辑说明.md)。项目结构和模块边界见 [`docs/receipt_ocr架构重构说明.md`](docs/receipt_ocr架构重构说明.md)。

## 快速开始

项目使用 Python 运行。当前开发机推荐使用以下 Conda 解释器：

```text
/Users/zhuyihao/anaconda3/bin/python
```

安装依赖：

```bash
python -m pip install -r requirements.txt
```

源码调试时可以启动本地服务：

```bash
python app.py
```

开发调试时可以在浏览器打开 [http://127.0.0.1:5001](http://127.0.0.1:5001)。如果使用推荐解释器，也可以直接运行：

```bash
/Users/zhuyihao/anaconda3/bin/python app.py
```

默认服务只监听本机。可通过环境变量修改监听地址和端口：

```bash
export APP_HOST=127.0.0.1
export APP_PORT=5001
export FLASK_DEBUG=0
```

首次启动会按需准备 PaddleOCR 模型。模型文件较大时，可以把缓存放在容量更大的磁盘：

```bash
export PADDLE_MODEL_HOME="/你的磁盘/OCR"
python app.py
```

## 识别方式

设置页可以分别为印刷字段、商品明细、手写内容、日期和印章选择识别方式。当前支持：

| 方式 | 用途 |
| --- | --- |
| `paddle_v6` | 默认本地识别方式，使用 PP-OCRv6 Small，覆盖字段、商品、日期和印章 |
| `paddle_seal` | 仅用于印章检测和印章区域处理，不负责整页或日期识别 |
| `qingtong` | 可选的远程印章服务 |
| `danzhengtong` | 生产环境远程单证识别，逐张提交并轮询结果 |

旧配置中的 `paddle`、`paddle_server`、`hybrid`、`hybrid_server` 和 `vision` 会兼容映射到 `paddle_v6`。macOS Vision 和旧版 Paddle 后端不再作为当前识别方式提供。

本地 PP-OCR 默认使用 ONNX Runtime；没有安装或无法使用时会回落到 Paddle 默认 CPU 推理：

```bash
export PADDLE_OCR_ENGINE=onnxruntime
# export PADDLE_OCR_ENGINE=off
```

印章补充识别可通过 `SEAL_SECONDARY_READ_MODE` 控制：

```bash
export SEAL_SECONDARY_READ_MODE=auto   # 默认，仅在安全且有授权时补充读取
# export SEAL_SECONDARY_READ_MODE=local
# export SEAL_SECONDARY_READ_MODE=off
```

补充识别只读取已经生成的印章处理图和展开图，不改变最终匹配门槛。印章公司主体、地名和章型必须来自同一章区的有效证据；文字不完整或出现近似冲突时仍需人工确认。

## 远程服务配置

本地识别默认不会上传图片。只有在设置页明确选择远程方式时，才会向对应服务发送图片。

### 清瞳印章 API

把密钥放在 Git 忽略的 `config/seal_api_key` 中，文件只写密钥本身；也可以使用环境变量：

```bash
export SEAL_API_KEY="你的密钥"
export SEAL_API_URL="https://seal.qingtong.cn"
export SEAL_API_TIMEOUT=120
```

未配置密钥时，清瞳选项会显示为不可用。请求同时检查 HTTP 状态和业务状态，失败会保留在记录中，不会覆盖本地结果。

### 单证通

单证通配置位于 Git 忽略的 `config/danzhengtong.local.json`，也可以用 `.env.example` 中的 `DZT_*` 环境变量覆盖。当前流程是逐张上传、提交并轮询结果；真实接口返回空结果、超时或错误时，该张会明确标记为失败，不会用模拟数据替代。

常用配置项包括：

```bash
export DZT_BASE_URL="https://api.sinotrans.com"
export DZT_APP_ID=""
export DZT_APP_KEY=""
export DZT_APP_SECRET=""
export DZT_KEY_ID=""
export DZT_POLL_TIMEOUT_SECONDS=60
```

详细的接口字段和失败处理见 [`docs/单证通真实接入.md`](docs/单证通真实接入.md)。不要把真实密钥提交到仓库。

## 日常使用流程

1. 在“导入”页拖入图片或选择文件夹，确认识别方式后开始处理。
2. 在工作台查看批次进度、失败原因和待复核数量。
3. 在记录页按整体结论、日期、印章、文档类型或搜索条件筛选。
4. 打开复核页查看日期和印章的原始/处理图及各路 OCR，必要时修改字段并保存备注。
5. 只有实际日期和印章证据都满足当前通过规则时，才能确认通过；不完整记录继续保持待复核。
6. 在质量概览查看准确率和错误类型。评测只使用数据库保存的机器原值和已维护真值。

系统会保留每次人工修改的前后值、时间和备注。删除记录前会同步清理不再被引用的上传文件、预览图和中间证据图。

## 数据与目录

| 路径 | 内容 |
| --- | --- |
| `数据/` | 样单图片、`ground_truth.json` 和文档分流真值 |
| `storage/results.db` | 识别结果、任务、复核历史和运行信息 |
| `storage/uploads/` | 上传图片的本地副本 |
| `storage/previews/` | 复核页使用的预览图 |
| `storage/artifacts/` | 日期、印章和其他阶段的证据图 |
| `storage/exports/` | 运行时生成的导出文件 |
| `config/` | 本机配置和密钥文件；真实密钥不应提交 |

源码运行时数据保存在项目的 `storage/`。Windows 打包程序默认把运行数据放在 `%LOCALAPPDATA%\\SamsungReceipt`，也可以用 `SAMSUNG_RECEIPT_DATA_DIR` 指定目录。

## 离线验收与开发检查

对已维护真值的样单运行端到端验收：

```bash
python -m tools.analysis.operations.e2e_six --backend paddle_v6
```

只验证复核闭环时，可运行六张基线并模拟人工操作：

```bash
python -m tools.analysis.operations.e2e_six \
  --backend paddle_v6 \
  --limit 6 \
  --exercise-review \
  --report-path storage/e2e-six-acceptance.json
```

不启动 Web 服务生成质量报表：

```bash
python -m tools.analysis.operations.generate_report \
  --backend paddle_v6 \
  --output storage/accuracy-report-paddle-v6.json
```

新增样单并写入数据库：

```bash
python -m tools.analysis.operations.batch_validate \
  --backend paddle_v6 \
  --filename 你的文件名.jpg \
  --save-db \
  --output storage/new-sample.json
```

文档分流和后端对比使用独立命令，避免把续页或委托书混入标准回单准确率：

```bash
python -m tools.analysis.operations.evaluate_document_routing \
  --task-id <批次 ID> \
  --output storage/document-routing-report.json

python -m tools.analysis.operations.compare_backend_runs \
  --run local=storage/local-run.json \
  --reference-backend local \
  --output storage/backend-comparison.json
```

运行完整测试：

```bash
python -m pytest -q
```

Windows 首次运行建议先做环境自检：

```powershell
python -m tools.analysis.operations.windows_smoke --output storage\\windows-smoke.json
python -m pytest tests/test_ocr_backends.py tests/test_platform_runtime.py -q
```

## 代码结构

```text
receipt_ocr/
├── application/       用例编排和识别计划
├── domain/             阶段、字段、商品和文档规则
├── stages/             字段、商品、日期、手写和印章阶段
├── recognition/        日期与印章证据算法
├── providers/          OCR 后端和远程服务适配
├── imaging/            裁剪、预处理和证据图
├── persistence/        SQLite 仓储
├── jobs/               后台任务和队列
├── runtime/            路径、进度和运行策略
├── web/                Flask 应用与 HTTP 路由
└── api/                对外结果序列化
```

新增识别阶段时，先在 `domain/stages.py` 注册阶段，再实现领域结果和阶段入口；新增 OCR 后端时，先在 provider registry 声明能力，再接入具体运行时。业务规则不应直接依赖模型名称，证据生成和最终判定应分开保存。

相关设计文档：

- [`docs/receipt_ocr架构重构说明.md`](docs/receipt_ocr架构重构说明.md)
- [`docs/日期识别、印章识别与匹配比较逻辑说明.md`](docs/日期识别、印章识别与匹配比较逻辑说明.md)
- [`docs/清瞳印章双路判定.md`](docs/清瞳印章双路判定.md)
- [`docs/工作台与复核区改版.md`](docs/工作台与复核区改版.md)

## Windows 桌面安装包

Windows 用户使用 Tauri 桌面安装包。GitHub Actions 在 Windows 服务器上完成 Python/OCR 服务打包、Tauri 构建和 NSIS 安装包生成；推送 `v*` 标签后，安装包会作为 GitHub Release 附件发布，也可以在 Actions 页面手动运行工作流。

开发机可以在 `desktop/` 下执行：

```bash
cd desktop
pnpm install
pnpm tauri:dev:mac
```

Windows 本地构建脚本为 `desktop/scripts/build_installer.ps1`。它会先构建内部识别服务，再将服务作为桌面端资源打入 NSIS 安装包。用户只需要安装生成的桌面软件，窗口关闭时会同步结束本次启动的本地服务。
