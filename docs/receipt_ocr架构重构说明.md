# receipt_ocr 架构重构说明

`receipt_ocr` 现在按应用编排、领域模型、识别阶段、提供方、图像基础设施和持久化边界组织。模块之间通过阶段请求和阶段结果传递数据，识别结果的 JSON 结构只在 API 序列化层组装。

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

日期和印章中的复杂算法仍按证据职责分布在 `recognition/date` 与 `recognition/seal`。它们不负责 HTTP、数据库写入或最终页面 JSON。图像实现位于 `imaging/processing.py`，阶段通过导入的图像能力使用它。

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
