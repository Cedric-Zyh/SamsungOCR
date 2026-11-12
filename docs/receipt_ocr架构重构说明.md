# receipt_ocr 架构重构说明

`receipt_ocr` 按应用编排、领域模型、识别阶段、提供方、图像基础设施和持久化边界组织。网页与数据库继续使用兼容的 JSON 结构。本文更新于 2026-11-10，本轮验收记录见《结构优化长任务-20261110》。

## 目录职责

```text
receipt_ocr/
├── application/       识别用例、计划校验、阶段编排
├── domain/            阶段、结果和字段/商品/文档规则
├── stages/            字段、商品、手写、日期、印章阶段
├── recognition/       日期与印章的复杂证据算法
├── providers/         OCR 后端注册、能力校验、Paddle 运行时
├── imaging/           图像预处理、裁剪、方向和证据图
├── persistence/       数据库仓储
├── jobs/              后台任务生命周期
├── samples/           样单业务、独立运行资源、目录与统计
├── storage/           上传文件和运行时文件
├── runtime/           运行时路径、进度、权限范围和安全策略
├── web/               Flask 组合根与 HTTP 路由
└── api/               阶段结果到外部 JSON 的序列化
```

应用层只编排流程。`application/stage_runner.py` 根据领域阶段标识选择实现，阶段返回 `domain/results.py` 中的结果对象；`api/serializers.py` 负责生成网页和数据库继续使用的字段结构。

阶段请求位于 `application/requests.py`，不依赖 Flask 或数据库。字段、商品和文档分类规则分别位于 `domain/fields`、`domain/products` 与 `domain/documents`。OCR 后端由 `providers/registry.py` 声明能力，`providers/selection.py` 只负责识别计划的能力和可用性校验。模型初始化留在 `providers/paddle_runtime.py`，注册表导入不会加载模型。

日期和印章中的复杂算法按证据职责分布在 `recognition/date` 与 `recognition/seal`。它们不负责 HTTP 或数据库写入。图像实现分布于 `imaging/io.py`、`crops.py`、`date.py`、`shapes.py`、`ellipse.py` 等模块。

## 一次识别的路径

上传、同步重试和后台任务共用 `application/document_service.py`，统一原图检查、预览图与中间图位置、原文件名及时间戳。持久化和 HTTP 响应仍归调用方管理。

`RecognitionService` → `ReceiptAnalyzer.analyze` → `plans.run_configured` 是统一执行路径。未提供配置时，先通过 `default_plan` 生成计划；显式配置经过同一校验和阶段执行。`assembly.py` 负责多提供方选择、结果合并、核对模式和最终输出；旧 `run_legacy` 已删除。

`ReceiptAnalyzer` 接收 `TextRecognizer`。整页、字段/商品补读、日期行、印章文本和检测框共用这次注入；模型适配在 `providers/text.py`。每次执行的识别器作用域和图像缓存覆盖阶段执行、参考证据补充及预览生成，成功或异常退出均释放。OpenCV 解码缓存按文件路径、修改时间、变更时间、大小失效，默认上限 128 MiB，采用 LRU 淘汰；调用方收到独立副本。

清瞳请求由 `application/qingtong_dispatch.py` 调度和收敛，成功与异常都按本次文档缓存；远程请求仍可与整页 OCR 重叠。`StageRequest` 携带已返回的 `QingTongEvidence`，印章阶段不再处理 Future 或发起上传。本地读取可以复用已选择的清瞳章框，仍保留独立识别来源。

`domain/results.py::StageOutcome` 连接实际的 plans → assembly 以及异步回填路径，明确阶段、来源和完成/失败/等待/跳过状态。供应商载荷保留字典及扩展字段，兼容视图允许领域比较器逐步迁移；本轮未强制重写所有嵌套证据字段。

`application/snapshots.py` 的 `DocumentSnapshot` 负责保存和恢复历史交接数据。未知扩展字段继续保留在 JSON 中，但只能恢复明确允许的文档类型、页脚、主读取来源和观察证据，不能用任意属性覆盖运行上下文。缺少页脚元数据的旧快照沿用已有证据恢复逻辑，不重新 OCR。`document_evidence.py` 统一字段选择与已保存日期/印章证据的重算，同步和异步最终都经 `assemble_result`。

## 复核与持久化

`domain/issues.py` 定义 `ReviewIssue` 和关键核验结果类型。问题包含 `code`、`scope`、`message`、`blocking`、`provider`；通过规则读取代码和所属项目，提示文字只用于显示。日期/印章禁用、低置信度忽略和提供方失败不再依赖中文正则。已有数据库文本由 `persistence/issue_migration.py` 单向转换，机器原始快照不覆盖；这是数据迁移，不是旧识别入口。

`persistence/read_model.py` 在结果表维护 `summary_json`，由触发器随原结果在同一事务更新。列表用轻量字段筛选、关联首页和续页、排序分页，再读取当前页的完整 OCR 证据。复核读取、版本检查和写入共用事务。当前仍会扫描筛选范围内的摘要；这不是完全由 SQL 完成的逻辑回单分页。

## Web 与前端

`web/dependencies.py` 为上传、任务、结果和复核定义独立依赖对象，处理模块不再接收整个应用模块。`web/application.py` 仍是进程级 Flask 组合根，承担初始化、部分路由和服务装配；本轮没有将其改为支持多实例的应用工厂。

记录页由 `records/query.mjs` 管查询和过期请求保护，`table.mjs` 管列表渲染和行交互，`controller.mjs` 管筛选与事件协调。复核页由 `view.mjs` 渲染表单，`submission.mjs` 管保存、锁定和冲突处理。工作台由 `query.mjs` 管请求与缓存，`summary.mjs` 管汇总，控制器协调筛选、选择和操作。拆分复用原状态对象，不复制第二套状态。

`core/state.mjs` 的 `createResultChanges` 是跨页面结果和队列变更入口，由 shell 注入功能控制器。工作台报告变化，不直接修改报表或结果页状态；它仍可以读取跨页操作需要的批次与选择范围。页面自身保留查询序号、筛选和选择状态。

`samples/data_session.mjs` 管理列表 revision、请求合并、写操作后的强制刷新及活跃/空闲刷新节奏；成功应用快照后才推进 revision。`samples/center.mjs` 负责操作和展示。定时器继续归 shell 所有，会话不创建第二套定时器。

## 样单业务与运行资源

`web/samples.py` 只承担 HTTP 输入、错误映射和响应转换。`samples/service.py` 提供运行、查询与结果投影；`listing.py` 管列表读模型与缓存；`jobs.py` 管样单原图来源、配置快照、入队及识别后改名。目录、标注、统计、导出等模块位于同一个业务包，不依赖 Flask 或 Web 组合根。

组合根通过 `SampleDependencies` 明确提供生产标准库、路径、分析函数、结果投影及日志。`SampleRuntime` 延迟创建资源，并在数据库或数据集路径切换时关闭旧中心；关闭入口可重复调用。业务层以 `SampleNotFound` 表达不存在的原图/结果，HTTP 层将其映射为 404。

样单仍使用独立数据库和产物目录。领取配置由 `JobStore.resolve_claim_options` 在同一领取事务内决定，样单子类应用排队时的快照；通用队列不再识别 `sample_options`，也不需要领取完成后的第二次写回。

## 结果写入边界

`persistence/job_views.py` 提供远程任务状态的纯投影，`result_versions.py` 提供结果版本计算，持久化读模型无需导入任务存储类。

`Database.result_writes` 组合 `ResultWrites`，负责识别创建、重试、人工复核、远程回填、历史修复和保留结果恢复的实际写入。旧 `insert_result`、`review_result`、`replace_after_retry` 是兼容入口。各操作明确是否变更机器原值、尝试次数和审计；共享收尾维护待复核数量及分页关联。任务领取、完成和分支快照仍归任务事务所有，写入入口加入调用方连接，不提前提交。

人工复核与重试保留首份机器原值；异步机器证据完成时更新该原值；历史修复只有显式提供修正后的原值才更新它。保留结果恢复不增加识别次数或伪造人工审计。相关区别由专门入口表达。

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

`PreparedSeal.tasks` 是预处理与识别循环的实际交接对象。每个 `SealInputTask` 明确输入 ID、印章 ID、图片、渠道、读取方式及可复用检测结果；`None` 表示需要 OCR，空观察列表表示已经检测过但没有文字。`input_evidence.py` 按输入 ID 更新展示证据，主识别循环不再从可变展示字典读取执行指令。

`preprocess/inputs.py` 按原有执行顺序协调公共图片准备、优先版式、方向与增强、矩形/分区任务、环形遮罩、展开及编号恢复。方向/版式判断所需 OCR 仍在相应步骤执行。早退与异常恢复保留已生成的任务和定位信息，不能机械地把全部 OCR 推迟到最后。

圆章和椭圆章的环形 OCR 固定经过以下顺序：旋正后的保留章色整图 → OCR 返回 `dt_polys` 检测横向章型文字 → 将这些多边形合并成不加边距的检测外接框 → 在整图副本中仅将这个框填白并保存实际输入图 → 用这张整图副本展开圆环 → 环形 OCR。`round_type_band_url` 只保存检测到的横向文字区域供定位和章型 OCR 使用；`ring_input_url` 指向的图片才是环形 OCR 的真实输入，旋正章色图本身保持原样。没有 `dt_polys` 时不猜测覆盖区域。

环形展开的起点可能切断公司名称，因此环形结果还会保留原始 OCR，并生成循环位移候选。候选优先选择完整公司后缀（如“有限公司”）位于末尾的顺序，再用签章要求中的公司名做软匹配排序；签章要求不会补写 OCR 中没有出现的字符。证据中同时保存 `unwrapped_raw_text`、`unwrapped_text`、`ring_reorder.seam_offset` 和排序原因。

印章 OCR 只返回各输入图自己的原始读数。公司环形文字和中心章型文字分别记录，不在识别阶段补字、纠错或拼接；签章要求比对在后续判定层完成。


## 2026-11-11：判定、运行与迁移所有权

机器回放和历史刷新统一经过 `application/saved_checks.py`，基础日期／严格印章规则位于 `domain/check_comparison.py`，人工确认保留在 `review.py`。`domain/saved_evidence.py` 集中处理来源选择、typed Mapping 和旧 JSON 格式；独立印章的空／失败来源不能回退为整页成功。

远程状态规则位于 `domain/remote_tasks.py`，任务关联位于 `domain/remote_runs.py`。Worker 处理网络请求，Store 处理租约与事务；`seal_requests_json.run_link` 明确新任务归属及复用来源。调用记录读取与展示分别由 SQL facade、纯投影和旧数据兼容模块承担。

正式资源由 `web/runtime.ApplicationRuntime` 拥有，`application/configured_recognition.py` 显式组合业务配置与识别服务。Web 保留兼容别名，但资源启动／停止由 runtime 管理；停止超时不释放所有权，关闭中拒绝 HTTP 新请求。`application/retries.py` 共用重试准备与执行流程。

`schema_migrations` 按组件记录连续版本。receipt、local queue、remote queue 分别定义升级步骤，所有版本登记和结构更改处于同一事务。变更触发器时追加迁移并显式重建；不可只修改 `CREATE ... IF NOT EXISTS`。历史导入格式的兼容修复与每次启动任务恢复不混入版本判断。

前端导入通过事件交给工作台命令；复核导航由 `shell/review_dialog.mjs` 统一管理，复核功能自己负责编辑、请求代次和证据清理。详见 `docs/结构优化长任务-20261111.md`。

## 2026-11-11 第三轮：结果边界与来源上下文

内部阶段结果和公开 JSON 的转换集中在 `application/variant_projection.py`。执行状态在组装、保存、回放和刷新之间保留，失效结果保留为证据但不参与判定。历史合并按每个供应商的最新结果占位，即使该结果失败或等待，也不能回退到更旧的成功读数。

`domain/decision_provenance.py` 管理规则版本和判定来源。记录中的规则属于操作上下文，不证明每条证据都重新执行过全部规则。历史来源未知时明确保留未知；标准库指纹和实际应用的标准章版本分开记录。查询投影不写入新的判定来源，规则修复的无变化调用不追加历史。调整业务规则时，需要同步递增该模块中对应的版本号。

Web 只从 `ApplicationRuntime` 构造依赖，延迟回调和请求各自固定资源所有者。旧模块资源名只提供集中读取兼容，测试和工具若需更换依赖，应构造并显式安装新的运行环境。工作台的查询、缓存和请求顺序属于 `query.mjs`，DOM 展示属于 `query_view.mjs`，日期及导航由控制器连接。实施边界和验证记录见 `docs/结构优化长任务-20261111-第三轮.md`。
