# receipt_ocr 架构重构说明

`receipt_ocr` 按应用编排、领域模型、识别阶段、提供方、图像基础设施和持久化边界组织。阶段结果在 API 序列化层转换为字典，由应用层合并为完整结果。本文对应 2026-10-04 的结构。

## 目录职责

```text
receipt_ocr/
├── application/       识别用例、计划校验、阶段编排
├── domain/            阶段、请求、结果和字段/商品/文档规则
├── stages/            字段、商品、手写、日期、印章阶段
├── recognition/       日期与印章的复杂证据算法
├── providers/         OCR 后端注册、能力校验、Paddle 运行时
├── imaging/           图像预处理、裁剪、方向和证据图
├── persistence/       数据库仓储
├── jobs/              后台任务生命周期
├── storage/           上传文件和运行时文件
├── runtime/           运行时路径、进度、权限范围和安全策略
├── web/               Flask 组合根与 HTTP 路由
└── api/               阶段结果到外部 JSON 的序列化
```

应用层只编排流程。`application/stage_runner.py` 根据领域阶段标识选择实现，阶段返回 `domain/results.py` 中的结果对象；`api/serializers.py` 负责生成网页和数据库继续使用的字段结构。

阶段请求位于 `domain/requests.py`，不依赖 Flask、数据库或具体 OCR 引擎。字段、商品和文档分类规则分别位于 `domain/fields`、`domain/products` 与 `domain/documents`。OCR 后端由 `providers/registry.py` 声明能力，`providers/selection.py` 只负责识别计划的能力和可用性校验。模型初始化留在 `providers/paddle_runtime.py`，注册表导入不会加载模型。

日期和印章中的复杂算法按证据职责分布在 `recognition/date` 与 `recognition/seal`。它们不负责 HTTP 或数据库写入。图像实现分布于 `imaging/io.py`、`crops.py`、`date.py`、`shapes.py`、`ellipse.py` 等模块。

## 一次识别的路径

上传、同步重试和后台任务共用 `application/document_service.py`，统一原图检查、预览图与中间图位置、原文件名及时间戳。持久化和 HTTP 响应仍归调用方管理。

`RecognitionService` → `ReceiptAnalyzer.analyze` → `plans.run_configured` 是统一执行路径。未提供配置时，先通过 `default_plan` 生成计划；显式配置经过同一校验和阶段执行。`assembly.py` 负责多提供方选择、结果合并、核对模式和最终输出；旧 `run_legacy` 已删除。

`ReceiptAnalyzer` 接收 `TextRecognizer`。整页、字段/商品补读、日期行、印章文本和检测框共用这次注入；模型适配在 `providers/text.py`。每次执行的识别器作用域和图像缓存覆盖阶段执行、参考证据补充及预览生成，成功或异常退出均释放。OpenCV 解码缓存按文件路径、修改时间、变更时间、大小失效，默认上限 128 MiB，采用 LRU 淘汰；调用方收到独立副本。

## 复核与持久化

`domain/issues.py` 定义 `ReviewIssue` 和关键核验结果类型。问题包含 `code`、`scope`、`message`、`blocking`、`provider`；通过规则读取代码和所属项目，提示文字只用于显示。日期/印章禁用、低置信度忽略和提供方失败不再依赖中文正则。已有数据库文本由 `persistence/issue_migration.py` 单向转换，机器原始快照不覆盖；这是数据迁移，不是旧识别入口。

`persistence/read_model.py` 在结果表维护 `summary_json`，由触发器随原结果在同一事务更新。列表用轻量字段筛选、关联首页和续页、排序分页，再读取当前页的完整 OCR 证据。复核读取、版本检查和写入共用事务。当前仍会扫描筛选范围内的摘要；这不是完全由 SQL 完成的逻辑回单分页。

## Web 与前端

`web/dependencies.py` 为上传、任务、结果和复核定义独立依赖对象，处理模块不再接收整个应用模块。`web/application.py` 仍是进程级 Flask 组合根，承担初始化、部分路由和服务装配；本轮没有将其改为支持多实例的应用工厂。

记录页由 `records/query.mjs` 管查询和过期请求保护，`table.mjs` 管列表渲染和行交互，`controller.mjs` 管筛选与事件协调。复核页由 `view.mjs` 渲染表单，`submission.mjs` 管保存、锁定和冲突处理。工作台由 `query.mjs` 管请求与缓存，`summary.mjs` 管汇总，控制器协调筛选、选择和操作。拆分复用原状态对象，不复制第二套状态。

## 自动检查

`tools/analysis/operations/check_architecture.py` 检查绝对、相对、别名及函数内导入方向；负例测试保证违规导入会被发现。`.github/workflows/checks.yml` 独立运行架构检查与 Python/Node 回归，测试依赖在 `requirements-dev.txt`。Windows 打包流程使用同一测试依赖。

## 维护约束

- 新增识别阶段先在 `domain/stages.py` 注册，再实现阶段结果对象和阶段入口。
- 阶段入口使用 `recognize`，返回领域结果对象；页面字段通过 `api/serializers.py` 暴露。
- 新 OCR 后端先添加 `ProviderDefinition`，再实现适配器和运行时；业务规则不直接判断模型名称。
- 证据生成和业务判定分开保存，低置信度信息不能在序列化时丢失。
- Web、后台任务和离线工具都通过 `application.RecognitionService` 或应用计划服务执行。
- 业务规则变更需要对应的阶段级回放或真值测试；目录整理本身不改变规则阈值。

## 印章识别输入契约

圆章和椭圆章的环形 OCR 固定经过以下顺序：旋正后的保留章色整图 → OCR 返回 `dt_polys` 检测横向章型文字 → 将这些多边形合并成不加边距的检测外接框 → 在整图副本中仅将这个框填白并保存实际输入图 → 用这张整图副本展开圆环 → 环形 OCR。`round_type_band_url` 只保存检测到的横向文字区域供定位和章型 OCR 使用；`ring_input_url` 指向的图片才是环形 OCR 的真实输入，旋正章色图本身保持原样。没有 `dt_polys` 时不猜测覆盖区域。

环形展开的起点可能切断公司名称，因此环形结果还会保留原始 OCR，并生成循环位移候选。候选优先选择完整公司后缀（如“有限公司”）位于末尾的顺序，再用签章要求中的公司名做软匹配排序；签章要求不会补写 OCR 中没有出现的字符。证据中同时保存 `unwrapped_raw_text`、`unwrapped_text`、`ring_reorder.seam_offset` 和排序原因。

印章 OCR 只返回各输入图自己的原始读数。公司环形文字和中心章型文字分别记录，不在识别阶段补字、纠错或拼接；签章要求比对在后续判定层完成。
