# 三星回单核验台

本地 Web 工具，支持多图拖拽、文件夹导入和人工复核闭环，并给出：

- 表单字段提取与 SQLite 记录；
- 商品明细按固定表头逐列归位、按纵坐标聚合行，并对每个单元格显示置信度和支持逐格复核；
- 自动区分标准出库回单、商品明细续页、仓库货物接收委托书和未知文档；无表头商品续页会用“行号 + 物料编码 + EAN”几何证据识别并按固定列提取，非标准页不会硬套日期/印章模板；
- 模板固定的“签收说明”在 OCR 文本足够相似时校正为标准短语，同时保留 OCR 原文和规则相似度；
- 收货日期识别，并与“要求到货”日期比对；
- 红色或蓝色客户印章检测、文字识别，并与“签章要求”比对；
- 对重复出现的客户章，可与同签章要求、人工确认应匹配的本地真值章做 SIFT/RANSAC 大面积几何复核；当前章和参考章会并排展示，文字 OCR 原值仍完整保留；
- OCR 后端可选择：macOS 支持系统 Vision、PaddleOCR Mobile、PaddleOCR Server 大模型，以及两档“Paddle 表单/商品 + Vision 日期/印章”的混合模式；Windows 默认使用 PaddleOCR Mobile；
- 可选调用《印章识别 API 接口文档 1.0.1》中的远程接口交叉验证。
- 每个字段的识别置信度，低于 72% 的字段在复核界面自动标黄；
- 手写日期和印章的原始裁剪图、颜色清理图及增强/展开图；
- 日期或印章证据不足时自动进入“待复核”，不会强行判定通过；
- 原始机器值、人工修改值、修改时间、最终状态和复核历史；
- 批量进度、失败原因、单张重试、筛选、批量确认与重新识别；
- 每张回单一行的 Excel 导出及准确率/错误类型统计。

当前日期识别、印章识别及匹配评分的完整规则见 [`docs/日期识别、印章识别与匹配比较逻辑说明.md`](docs/日期识别、印章识别与匹配比较逻辑说明.md)。

## 最新验收快照（2026-08-16）

当前 301 张标准回单的最新 Hybrid 机器原始口径为：表单字段
`3010/3010`、商品单元格 `2817/2817`、商品整行 `313/313`；日期值
`220/301`（73.09%；有日期样本检出 `222/300`，74.00%），其中可靠自动日期
`204/301`（67.77%）且 `204/204` 正确；印章严格结论和可靠自动印章分别为
`235/301`（78.07%）和 `234/301`（77.74%），且可靠自动结论 `234/234`
正确。完整测试为 `487 passed`。仓库货物接收委托书已按当前使用范围排除，既不
参与标准回单准确率分母，也不作为后续优化或验收阻断项。

第七十五梯队新增“跨模型月日一致 + 三业务日期年份共识”。它不从要求到货复制
月日：全部日期 OCR 原文中可明确解析的月日必须只有一个值，并同时覆盖 Mobile、
Server、紧裁和宽裁；全部完整日期又必须只有一个，且其月日相同、年份早于制单
日期至少一年。只有制单日期、运单号日期、要求到货三项独立业务日期年份一致，
重建日期处于 7 天业务窗口且不晚于要求日期时，才只修正年份。301 张规则矩阵只
接受 `7311852241.jpg`，正确 1、误接受 0；真实 Hybrid 重跑将实际日期
`2025-10-03` 从 68% 提升为 82% 可靠匹配，印章 100%，整体自动通过。界面显示
候选、被排除的 `2021-10-03` 及逐路 OCR 原文，日期 43/43 张、印章 12/12 张
原始/处理图加载正常。报告见
`storage/date-business-year-month-day-matrix-tier75.json`、
`storage/e2e-date-business-year-month-day-tier75.json`、
`storage/accuracy-report-301-hybrid-tier75.json`、
`storage/date-gap-analysis-tier75.json`、`storage/seal-gap-analysis-tier75.json`
和 `storage/windows-smoke-tier75.json`。

第七十四梯队新增“Server 双几何完整日期 + Mobile 残缺组件共识”。它要求
Server 最大通道在紧裁和宽裁只读到同一完整日期；Mobile 又必须在两个区域分别
保留该日期的 OCR 自有年月日组件。只有当 Mobile 最大通道的两个完整冲突值月日
相同、年份却彼此不同，证明 Mobile 自身不稳定，且不存在第四个严格日期时才允许
通过。规则不接收要求到货日期作为输入。301 张矩阵只命中 `7269202808.jpg`，
正确 1、误接受 0；真实 Hybrid 重跑将 `2025-01-23` 从 68% 提升为 82% 可靠
匹配，印章 94%，整体自动通过。界面显示 Server/Mobile 全部采用及冲突原文，日期
43/43 张、印章 7/7 张中间图正常加载。报告见
`storage/date-server-mobile-component-matrix-tier74.json`、
`storage/e2e-date-server-mobile-components-tier74.json` 和
`storage/accuracy-report-301-hybrid-tier74.json`。

第七十三梯队新增“远下方日期行右侧补白 + Mobile/Server 互补共识”。它只在
Mobile 对整块日期区域两种视图重复读到同一完整日期、补白窄行也读到该日期时
进入候选；未补白 Server 必须提供同年同月，补白 Server 必须提供同月同日，三位
年份还只能是四位候选年份的有序子序列。任何其他严格日期都会否决，要求到货日期
不参与字符补全。301 张安全矩阵只命中 `7302125543.jpg`，正确 1、误接受 0；
真实 Hybrid 重跑可靠识别 `2025-08-05`，日期与印章均匹配，整单自动通过。复核
界面会同时显示原日期区域、全部增强图和右侧补白图，并列出 Mobile `2025.8.5`
与 Server `225.8.5` 的互补证据；滚动加载后 77/77 张中间图正常，控制台错误为
0。报告见 `storage/date-far-lower-padding-matrix-tier73.json`、
`storage/e2e-date-far-lower-padding-tier73.json` 和
`storage/accuracy-report-301-hybrid-tier73.json`。

第七十二梯队新增“强文字前缀 + 双参考章色”路线。它只适用于非纯法定公司名的
8–20 字全中文签章要求，并要求独立 OCR 片段逐字等于要求开头至少 7 个连续字；
同一检测章区还必须对两份不同文件的可靠同要求参考章分别通过章色、相关、Dice、
SIFT/RANSAC 及双向覆盖门槛。301 张全矩阵只命中 `7281987478.jpg`，正确 1、
误接受 0；目标两票为 67.91%/64.80%，已知易混淆反例最高 43.37%。真实 Hybrid
重跑后日期 `2025-04-07` 可靠匹配，印章 OCR 原值“太原市伊加壹电”保留并由
视觉参考复核提升为 92.2% 可靠匹配，整体通过。界面中的日期原图/处理图与印章
原图/章色/裁剪/展开/旋转/参考图均已验证加载，控制台错误为 0。Excel 为 1 行、
16 列、2 张工作表，格式、渲染和公式错误检查通过。报告见
`storage/seal-strong-prefix-color-consensus-matrix-tier72.json`、
`storage/e2e-strong-prefix-color-consensus-tier72.json`、
`storage/accuracy-report-301-hybrid-tier72.json`、
`storage/date-gap-analysis-tier72.json`、`storage/seal-gap-analysis-tier72.json`
和 `storage/windows-smoke-tier72.json`；Excel 见
`outputs/019ff6b3-50ea-7a63-a9e6-22472e8db17d/三星回单-强文字前缀章色共识-tier72.xlsx`。

第七十一梯队新增纯公司章的双参考整体墨迹共识。该路线不降低既有单参考门槛，
只在无公司冲突、签章要求为纯法定公司名、独立 OCR 行仍保留 2–6 字公司片段时
启用；同一检测章区必须分别与两份不同文件的可靠同要求参考章达到章色相关、Dice
和最低局部 SIFT 支持，首个参考还必须达到更高墨迹分数。

301 张全矩阵只命中 `7295579989.jpg`，正确 1、误接受 0；两个已知错章继续拒绝。
真实 Hybrid 重跑中，目标日期 `2025-07-03` 可靠匹配，印章从 OCR-only 45%
不匹配提升为 92.1% 可靠匹配，整体自动通过；原始噪声 OCR 串、两份参考文件及
75.59%/68.65% 墨迹分数完整留痕。界面已验证日期/印章原图与处理图、参考章和
多参考说明均可见，控制台错误为 0。命令行批量验证也补上与正式界面相同的参考章
后处理。Excel 为 1 行 16 列、2 张工作表，文本、日期、百分比、状态着色、渲染和
公式错误检查全部通过。报告见
`storage/seal-color-mask-consensus-matrix-tier71.json`、
`storage/e2e-seal-color-mask-consensus-tier71.json`、
`storage/accuracy-report-301-hybrid-tier71.json`、
`storage/date-gap-analysis-tier71.json`、
`storage/seal-gap-analysis-tier71.json` 和 `storage/windows-smoke-tier71.json`；
Excel 见 `outputs/三星回单-双参考章色共识-tier71.xlsx`。

第七十梯队修复手写两位日整体左移导致固定日位窄槽只截到末位或印刷“日”字的
问题。候选不从要求到货日期补全：Server 必须先给出唯一严格完整日期，紧裁与宽裁
的 Server 最大通道行只能是该日期或只漏掉两位日中的一位；Mobile 在紧/宽两区都
必须呈现同样的单字截断；Server 还须在两种独立增强路径保留相同月日。满足这些
前置条件后，才生成自适应日位原图和去章色图，并要求 Paddle Mobile/Server 四个
单元全部读到同一两位日。

301 张最新保存证据只命中 `7306087419.jpg`，正确 1、误接受 0。真实 Hybrid
重跑后其日期 `2025-08-30` 从 25% 待复核提升为可靠匹配并自动通过；模型把末位
“5”读成“3”的 `7302125543.jpg` 和实际早一天的 `7303331190.jpg` 均继续待复核。
三张批次 3/3 成功、字段 30/30、商品单元格 27/27、日期值 3/3、印章结论 3/3；
中间图文件、HTTP 与契约缺失均为 0。Excel 为 3 行、16 列、2 张工作表，中文、
长订单号文本、真实日期/时间、百分比、条件色、两表渲染及公式错误扫描均通过。
Windows 模拟失败项为 0。报告见 `storage/date-adaptive-day-tier70.json`、
`storage/e2e-date-adaptive-day-tier70.json`、
`storage/accuracy-report-301-hybrid-tier70.json`、
`storage/date-gap-analysis-tier70.json` 和 `storage/windows-smoke-tier70.json`；
Excel 见 `outputs/三星回单-日期自适应日位-tier70.xlsx`。

第六十九梯队先审计六张基线中唯一日期漏识别的 `7266227347.jpg`。该样本日期
行被红章和手写笔画覆盖，年份末位、月份窄槽和日数字没有形成独立一致证据，因此
继续保持待复核，没有从“要求到货”复制 `2025-01-04` 强行通过。

随后新增三位年份共识窄规则：仅限 macOS Hybrid，Paddle Mobile 必须在宽裁去
表格线区域读到自包含的 `20x年M月DD`，Paddle Server 必须在同一宽裁至少两种
独立标记的增强路径重复同一三位年份/月/日；日必须为两位数。缺失的年份末位只有
在制单日期、运单号编码日期和要求到货日期三项年份一致、均不晚于候选日期且相距
不超过 7 天时才补全。紧裁/宽裁中出现任何其他合法日期、平台不是 Vision +
Paddle、单模型证据或业务年份不一致都会拒绝。

301 张保存证据只命中 `7272556241.jpg`，正确 1、误接受 0；真实重跑后该样本
日期 `2025-02-11` 由 68% 不可靠提升为 78% 可靠匹配。反例
`7304152279.jpg` 仍为 `2025-08-19`、46% 不可靠不匹配，`7285996828.jpg`
仍为 `2025-05-01`、25% 不可靠匹配，均继续待复核。三张真实批次字段 30/30、
商品单元格 27/27、日期值 3/3；中间图文件、HTTP 和契约缺失均为 0。印章重跑
波动经既有人工真值参考章几何路线复验后恢复，全量印章指标无净回归。Windows
模拟自检失败项为 0。报告见
`storage/date-partial-year-required-tier69-pre.json`、
`storage/e2e-date-partial-year-required-tier69.json`、
`storage/e2e-seal-stability-7304152279-tier69.json`、
`storage/date-partial-year-required-tier69-post.json`、
`storage/accuracy-report-301-hybrid-tier69.json`、
`storage/date-gap-analysis-tier69.json` 和 `storage/windows-smoke-tier69.json`。

第六十八梯队把印章识别改为每批明确选择“本地印章识别”或“本地 + 清瞳印章
API”。默认始终为本地模式且不上传图片；界面、批量任务、单张/批量重试和端到端
工具都会保存并沿用所选模式。清瞳 Key 可放在被 Git 忽略的
`config/seal_api_key`，也可由环境变量提供；未配置 Key 时远程选项不可选择。
界面会明确提示上传的是完整回单，并在新批次、单张重试和批量重试前再次确认。

远程接口严格同时检查 HTTP 状态和文档规定的业务 `code=200`。网络异常、HTTP
错误、业务 `500/503` 或缺少业务状态码均不会中断本地结果，也不会冒充“清瞳 +
本地”成功；错误原因保留在结果中供复核。真实第三方上传尚未执行，需取得对具体
样单的明确授权后再测。

本梯队 6 张真实 Hybrid 本地批次 6/6 成功、0 失败：字段 60/60、商品单元格
81/81、商品整行 9/9；日期值 5/6，5 个可靠自动日期全部正确；印章结论 6/6，
可靠自动结论 6/6 正确，平均相似度 98.7%。`7266227347.jpg` 的机器结果因未可靠
识别收货日期进入待复核，自动化复核闭环随后按真值确认通过；原始机器值保持不变，
历史、六类筛选和批量确认均通过。日期/印章中间图文件、HTTP 与契约缺失均为 0。
Excel 共 2 个工作表、6 行结果，中文正常，运单号/订单号为文本，日期为真实日期且
格式 `yyyy-mm-dd`，无公式错误并完成两表渲染检查。Windows 模拟自检失败项为 0。
报告见 `storage/e2e-six-hybrid-tier68.json` 和
`storage/windows-smoke-tier68.json`；Excel 见
`outputs/三星回单-六张端到端验收-tier68.xlsx`。

第六十七梯队恢复 `7287296284.jpg` 的手写日期 `2025-05-11`。Server 在宽裁
最大通道图读到完整日期；同一裁剪的 Mobile 独立保留完整年份与两位日，只漏掉
月份数字；固定月份窄槽的 Mobile/Server 四个单元中有三个读到 `5`，另一个为空，
没有任何单元读到不同月份。唯一其他日期 `2025-05-01` 只是两位日 `11` 漏掉一位。

新规则不从“要求到货”复制缺失月份：年份和两位日来自 Mobile 原文，完整日期来自
Server 原文，月份来自单独窄槽；要求日期只用于最终比对。规则还要求 macOS Hybrid、
同裁剪双模型一致、两位日、无其他严格完整日期、月份原始图双模型均命中且总共至少
三单元一致。301 张保存证据只命中 1 张、正确 1、误接受 0。

目标和两个错误日期反例真实批次 3/3 成功：目标可靠匹配并自动通过；
`7303331190.jpg` 与 `7304002315.jpg` 分别保留实际 `2025-08-14`、`2025-08-16`
和 35% 置信度，均未被要求日期覆盖，继续待复核。字段 30/30、商品单元格 27/27、
日期值 3/3、印章结论 3/3；中间图文件、HTTP 和契约缺失均为 0。Excel 为 3 行、
16 列、2 个工作表，长订单号文本、真实日期/时间、百分比、中文、条件色、渲染和
公式错误检查全部通过。报告见
`storage/date-missing-month-consensus-tier67-pre.json`、
`storage/e2e-date-missing-month-tier67.json`、
`storage/date-missing-month-consensus-tier67-post.json`、
`storage/accuracy-report-301-hybrid-tier67.json`、
`storage/date-gap-analysis-tier67.json` 和 `storage/windows-smoke-tier67.json`。

第六十六梯队刷新人工真值阳性参考章库，并把当前参考矩阵中 8 张已达到严格视觉
门槛但数据库仍保存旧不可靠结论的样本执行完整 Hybrid 重跑。矩阵刷新前已保存
可靠参考匹配 16 张、误接受 0；剩余候选接受 8 张、误接受 0，两个已知错章
`7273687654.jpg` 和 `7286691343.jpg` 均继续拒绝。

8 张真实批次 8/8 成功、0 失败：字段 80/80、商品单元格 72/72，印章 8/8
可靠且全部与人工真值一致；日期机器值 2/8 正确但都没有达到可靠自动门槛，因此
8 张整单全部继续待复核。恢复路线包括 5 张严格单参考 SIFT/RANSAC、1 张超高支持
局部覆盖、1 张高纯度多参考一致和 1 张整体彩色墨迹几何。以
`7289048988.jpg` 为例，候选与 `7279799198.jpg` 获得 152 个好匹配、107 个
单应内点、70.39% 内点率和 24.40%/51.10% 双向覆盖，另一个独立参考也有
113/79 个匹配/内点；最终 92.1% 可靠匹配。

为后续持续新增真值建立了安全重跑清单：参考矩阵会直接输出候选文件名及可传给
端到端工具的 `--sample` 参数，但只要出现任何人工真值负例被接受，就自动清空清单
并列出阻断负例。批次后矩阵为已保存通过 24、误通过 0、剩余新增 0；安全重跑计划
为空。所有目标/参考章中间图、HTTP 和契约缺失均为 0，Windows 模拟失败项 0。
报告见 `storage/seal-reference-matrix-tier66-pre.json`、
`storage/e2e-seal-reference-refresh-tier66.json`、
`storage/seal-reference-matrix-tier66-post.json`、
`storage/accuracy-report-301-hybrid-tier66.json`、
`storage/date-gap-analysis-tier66.json`、`storage/seal-gap-analysis-tier66.json` 和
`storage/windows-smoke-tier66.json`。

第六十五梯队恢复 `7324339102.jpg` 的“蚌埠天马电子技术服务中心”圆章。该章
红色像素占章区 25.81%，肉眼清晰，但黑色表格线穿章导致常规 OCR 只留下“中”，
得分 15.4%，此前没有进入 Server 复核。新路由只把章色占比至少 20%、本地得分
不高于 20%、纯中文且至少 10 字并以“服务中心”结尾的密集圆章交给一次颜色安全
Server 审计；这只决定是否读取大模型，不降低最终匹配门槛。

Server 整章仍把地名误读成“蚌棒天”，系统没有采用该错误结果。三个圆章展开分带
分别逐字读到“蚌埠天马”和“技术服务中心”，独立旋转对照图逐字读到“电子”；
新重组器只允许 2–3 个长度至少 2 的实际 OCR 片段按要求原顺序、无重叠完整覆盖，
至少一个片段长 4 字、至少来自两种安全处理图且必须包含展开分带。任何缺字、补字、
错序、只来自同一处理图或非纯中文服务中心要求都拒绝。

真实端到端重跑后字段 10/10、商品单元格 9/9，印章由 15.4% 不可靠提升为完整
文本、100% 可靠匹配；日期仍未识别，因此整单继续待人工复核。原章、章色白底图、
圆章展开、三张独立分带及旋转对照图文件和 HTTP 均正常。真实 Web 复核界面已验证
“圆章跨分带精确重组”和三张分带卡片可见。301 张全量印章严格正确和可靠覆盖均
新增 1，日期指标不变；Windows 模拟失败项 0。报告见
`storage/seal-unaudited-four-probe-tier65.json`、
`storage/e2e-seal-dense-service-center-tier65-v2.json`、
`storage/accuracy-report-301-hybrid-tier65.json`、
`storage/date-gap-analysis-tier65.json`、`storage/seal-gap-analysis-tier65.json` 和
`storage/windows-smoke-tier65.json`。

第六十四梯队新增圆章“章类型横向分带”：只在已完成章色隔离的白底图中裁取圆章
下部内圈横带，让 Paddle Server 单行识别公司弧线下方的“手机售后专用章”等横排
章型。分带原始 OCR 仅用于界面审计，不直接进入匹配；只有同一章区已独立逐字读到
完整法定公司名、没有竞争公司全称、分带置信度至少 75%，且章型与要求等长、恰好
一个替换字并仍以“专用章”结尾时，才允许恢复完整要求文本。少字、多字、多处错误、
低置信度或公司冲突全部保持待复核。

真实样单 `7290278299.jpg` 的分带读到“手抗售后专用章”（91.7%），结合同章区
已独立读到的“合肥佳元电子第一分公司”，安全纠正为完整签章要求，印章由不可靠
提升为 100% 可靠匹配；较弱同模板 `7272556241.jpg` 只读到“手机售さて”
（55.2%），不纠正；错章反例 `7286691343.jpg` 仍保持不匹配且不可靠。受影响样本
重跑还让 `7305680939.jpg` 通过既有人工阳性参考章几何路线可靠匹配。全量印章严格
正确及可靠覆盖各净增 2。日期正确数仍为 219；此前数据库中
`7272556241.jpg` 的旧派生证据结果被安全重跑替换，因此可靠日期从 199 调整为
更诚实的 198，且继续保持 198/198 正确。

本轮已在真实 Web 界面核验“圆章章型分带”、92% 置信度、单字纠错说明和中间图均
可见，图片接口无错误；Windows 模拟仍默认 Paddle Mobile，Hybrid 无 Vision 时
安全回退 Paddle，失败项 0。报告见
`storage/e2e-round-type-band-safe-tier64.json`、
`storage/e2e-round-type-band-affected-tier64.json`、
`storage/accuracy-report-301-hybrid-tier64.json`、
`storage/date-gap-analysis-tier64.json`、`storage/seal-gap-analysis-tier64.json` 和
`storage/windows-smoke-tier64.json`。

第六十三梯队修复日期人工候选的同源证据累积。`7272556241.jpg` 原有日期行只以
68% 低置信度读到要求日期；Server 在同一物理行的原图、去线、自动对比等派生图
又重复读到相同字符串时，旧实验代码曾把这些派生结果误当成独立证据并抬到
99.7%。现行逻辑先冻结决策级日期行，只允许显式标记成功的严格槽位路线参与自动
可靠性；其余 Server/Otsu/自动对比结果继续完整展示在中间过程，但不会相互累积。
跨年份、跨几何一致的 Server 候选仍可用两条 35% 封顶行显示给人工复核，明确不
参与自动放行。

真实重跑后目标仍为 `2025-02-11`、68%、不可靠；危险负例
`7284653895.jpg` 的人工真值是 `2025-04-21`，机器虽仍低置信读成要求日期
`2025-04-25`，但同样保持 68%、不可靠并进入待复核，没有误通过。随后对稳定
基线中全部 104 张日期不可靠标准回单执行完整 Hybrid 重跑：104/104 成功、0
失败，102 张继续待复核；`7302601664.jpg` 和 `7303331194.jpg` 依靠既有严格
跨模型×跨几何证据可靠通过，二者均与人工真值一致。最新日期可靠覆盖为 199，
199/199 正确；不可靠 102 张中，17 张没有可解析候选、81 张存在其他候选、42 张
同时含真值与冲突值，故没有整体降阈值。

本轮还通过原图逐字复核纠正两条旧真值：`7315229756.jpg` 的签章要求确实包含
编号 `045153608531`，`7318259458.jpg` 确实包含后缀“业务受理”；两次修改均写入
SQLite 真值历史。`7320775545.jpg` 的物料号 OCR 漏掉颜色字“黑”，现仅在同页
校验位有效 EAN `8806097727347` 与已核验目录一致时恢复为
`SM-W9026AKDCHC玄曜黑512G`，OCR 原值仍保留。真值一致性审计重新达到 3010 个
字段比较、0 不一致。

六张完整端到端验收 6/6 成功、0 失败：字段 60/60、商品单元格 81/81、商品行
9/9，日期机器值 5/6 且可靠结论 5/5 正确，印章结论 6/6 且可靠结论 6/6 正确；
唯一未识别日期按策略先进入待复核，随后通过自动化人工复核流程验证原值不可变、
修改历史和批量确认。日期/印章中间图文件、HTTP 与界面契约缺失均为 0。Excel
为 6 行、16 个必需列和“回单结果/统计摘要”两个工作表，已用独立电子表格运行时
验证订单号文本、真实日期/时间、百分比、中文、人工备注和视觉布局，公式错误 0。
Windows 模拟继续默认 Paddle Mobile，Hybrid 三阶段无 Vision 时全部安全回退，
单一 Paddle 日期/印章不能自我认证，失败项 0。报告见
`storage/e2e-date-component-audit-safe-7272556241-tier63.json`、
`storage/e2e-date-component-audit-safe-7284653895-tier63.json`、
`storage/date-low-confidence-rerun-tier63.json`、
`storage/e2e-product-xuanyao-ean-tier63.json`、
`storage/e2e-six-hybrid-tier63.json`、
`outputs/三星回单-六张端到端验收-tier63.xlsx`、
`storage/accuracy-report-301-hybrid-tier63.json`、
`storage/date-gap-analysis-tier63.json`、`storage/seal-gap-analysis-tier63.json`、
`storage/ground-truth-consistency-tier63.json` 和 `storage/windows-smoke-tier63.json`。

第六十二梯队恢复 `7272556242.jpg` 的纯公司圆章。签章要求为“贵州宏羿科技
有限公司”，常规 OCR 读成等长但一字不同的“贵州宏开科技有限公司”，Server
则读到“贵州宏翼科技有限”；系统没有把同音或形近字当作文本纠错，而是继续保留
公司冲突，并只在同要求人工阳性参考章的三种视觉表示全部一致时越过该冲突。

新路线只允许不带任何章型的纯法定公司名，当前 OCR 必须逐字形成同长度、同法定
后缀、恰好 1 个同位置字符不同的完整公司名，公司相似度至少 83%。普通 SIFT 要求
至少 95/60 个匹配/内点、64% 内点率和双向 60% 覆盖；章色裁剪 SIFT 至少
90/60、64% 和双向 58% 覆盖；同一候选与同一参考的整体彩色墨迹还须同时达到
0.77 综合分、0.78 相关性和 0.77 Dice。三种表示任一不足都继续待复核。

目标与 `7266702320.jpg` 实测普通表示 99/65、65.66%、63.57%/64.25%，章色
表示 94/61、64.89%、58.81%/59.39%，彩色墨迹为 0.7856/0.7897/0.7751，
最终印章 94.3% 可靠匹配。日期 `2025-02-11` 可靠匹配，字段 10/10、商品 9/9，
整单通过。相同要求但几何较弱的 `7282767631.jpg` 真实重跑后仍待复核；目标进入
参考库后的递归矩阵为已保存通过 23、误通过 0、剩余新增 0，两张已知错章也仍拒绝。
目标和控制批次中间图、HTTP 及界面契约缺失均为 0。报告见
`storage/e2e-seal-bare-company-one-glyph-tier62.json`、
`storage/e2e-seal-bare-company-one-glyph-control-tier62.json`、
`storage/seal-reference-matrix-tier62-bare-company-one-glyph.json`、
`storage/seal-reference-matrix-tier62-post-target.json`、
`storage/accuracy-report-301-hybrid-tier62.json`、
`storage/date-gap-analysis-tier62.json`、`storage/seal-gap-analysis-tier62.json`、
`storage/ground-truth-consistency-tier62.json` 和 `storage/windows-smoke-tier62.json`。

第六十一梯队恢复 `7277227160.jpg` 的“上海凝鹏通讯科技有限公司收货专用章”，
但继续保留完整公司名冲突的通用硬拦截。常规 OCR 明确读到完整但一字不同的
“上海海鹏通讯科技有限公司”，另一路独立读到“收货专用章”；新路线只允许要求
逐字以“收货专用章”结尾、当前法定公司名与要求等长且恰好 1 个同位置汉字不同、
公司相似度至少 87%，并且存在同要求、人工真值确认匹配的另一份参考章。

目标与 `7313024141.jpg` 的普通 SIFT 为 130 个好匹配、106 个单应内点、81.54%
内点率、67.60%/64.48% 双向覆盖；独立章色裁剪 SIFT 为 133/102、76.69%、
68.43%/64.60%。两套表示分别受 125/100、80%、60% 和 125/100、75%、60%
的 AND 门槛约束，任一不足都继续待复核。完整 301 张参考矩阵只新增接受该 1 张
真值阳性样本，误接受 0；两个已知错章最高仅 68/56、63/51 和 46/24、46/21，
均拒绝。几何较弱的 `7297139402.jpg` 也继续待复核，没有因同属单字冲突被放行。

真实重跑后目标字段 10/10、商品单元格 9/9，日期 `2025-03-09` 可靠匹配，印章
以 94.3% 置信度可靠匹配，整单通过；原章、颜色分离、章色稳健裁剪、圆章展开和
参考章文件/HTTP/界面契约缺失均为 0。当前全量累计数字与文首相同，参考刷新中
另一个证据不稳定的历史样本被安全降为待复核，因此净可靠覆盖不变。报告见
`storage/e2e-seal-receiving-one-glyph-tier61.json`、
`storage/seal-reference-matrix-tier61-receiving-one-glyph.json`、
`storage/accuracy-report-301-hybrid-tier61.json`、
`storage/date-gap-analysis-tier61.json`、`storage/seal-gap-analysis-tier61.json`、
`storage/ground-truth-consistency-tier61.json` 和 `storage/windows-smoke-tier61.json`。

第六十梯队恢复 `7274693664.jpg` 的分公司圆章。打印要求为“北京亨通达科技
有限公司西城西单分公司北售后服务专用章”，常规 OCR 只保留公司前半段；Paddle
Server 在同一颜色安全章区的圆章校正图逐字读到完整“北京亨通达科技有限公司
西城西单分公司”，在旋转对照图又逐字读到“北售后服务专用章”。

新路线只把这两个原文精确重组，不补写任何缺字。要求必须同时包含“分公司”和
明确章型、常规相似度至少 50%，才额外读取 180°/旋转对照图；最终仍要求同一个
章区分别出现完整法定组织名（包含分公司）和完整章型。章型前最多允许 1–3 个
已经逐字观察到的中文前缀，例如“北”或“三星”；分公司名、前缀、章型或独立编号
缺任一项都拒绝，现有完整公司冲突保护不变。

真实复跑后目标印章从 96% 不可靠变为完整文本、100% 可靠匹配；其日期仍只出现
早于制单日的 `2025-02-02`，被业务时间规则拒绝，整单继续待人工复核。已知错章
`7286691343.jpg` 虽经同样旋转审计，仍缺公司“第一”和章型“手机”的关键字，
保持无法判断。15 张“分公司 + 明确章型”安全图矩阵精确命中 4 张，全部为真值
匹配、误接受 0；其中 3 张原本已可靠，只有目标形成新增覆盖。目标及负例任务文件、
HTTP 和中间图契约缺失均为 0。报告见
`storage/e2e-seal-branch-exact-tier60-target-v2.json`、
`storage/e2e-seal-branch-exact-tier60-target-control.json`、
`storage/seal-branch-safe-variants-tier60.json`、
`storage/accuracy-report-301-hybrid-tier60.json`、
`storage/date-gap-analysis-tier60.json`、`storage/seal-gap-analysis-tier60.json`、
`storage/ground-truth-consistency-tier60.json` 和 `storage/windows-smoke-tier60.json`。

第五十九梯队恢复 `7289834212.jpg` 开头被表格线/裁剪干扰的公司圆章。签章要求
为“乌鲁木齐贵迪电子有限公司”，当前章 OCR 为“齐贵迪电子有限公司”；新路线
不做任意公司名纠错，只允许纯法定公司名要求、识别结果是要求的逐字长后缀、缺失
前缀 1–4 字且识别长度至少 8 字。公司名必须已经触发冲突保护，并与同要求、人工
确认应匹配的本地参考章同时形成普通和彩色章面两套超强 SIFT/RANSAC 一致。

目标与 `7311093592.jpg` 的普通表示为 203 个好匹配、166 个单应内点、81.77%
内点率和 39.37%/58.32% 双向覆盖；章色表示为 189/166、87.83% 和
51.56%/64.71%，最终以 95.3% 的参考章置信度可靠匹配。301 张完整参考矩阵为
已保存通过 21、误放行 0、待新增通过 0；两张已知错章仍只有普通 68/56、46/24
及章色 63/51、46/21，均拒绝。目标实际收货日 `2025-06-01` 与要求
`2025-05-31` 不匹配，因此整单正确保持“不通过”，没有因印章匹配被连带放行。

真实目标任务 1/1 成功、0 失败、0 待复核，字段 10/10、商品单元格 9/9；日期和
印章的原始/处理图、参考章文件、HTTP 访问及界面契约缺失均为 0。目标批次 Excel
已独立验证为 1 行、16 列、订单号文本、真实日期/时间、中文和百分比格式正常，
两个工作表均能渲染且公式错误为 0。报告见
`storage/e2e-seal-clipped-prefix-tier59-target.json`、
`storage/seal-reference-matrix-tier59-final.json`、
`storage/accuracy-report-301-hybrid-tier59.json`、
`storage/date-gap-analysis-tier59.json`、`storage/seal-gap-analysis-tier59.json`、
`storage/ground-truth-consistency-tier59.json` 和 `storage/windows-smoke-tier59.json`；
工作簿位于 `outputs/tier59/三星回单-Tier59-目标验收.xlsx`。

第五十八梯队继续处理 `7304152279.jpg` 的手写日期。紧凑区域最大通道日期行
Mobile/Server 分别读到 `200年8月19日`、`202年8月19日`，宽区域分别读到
`20年8月19日`、`200年8月19日`；四个单元都独立保留显式 8 月 19 日，但年份
仅剩 2–3 位，因此此前只显示被业务时间拒绝的错误 `2025-08-09`。

新路线仅生成低置信度人工建议：必须是 macOS Vision + Paddle Hybrid，Mobile 与
Server 在紧/宽四个最大通道单元都只出现同一显式“残缺年份 + 月 + 日”，不得出现
任何完整四位年份日期；候选必须为两位日、与制单日期同年且不早于制单日，并且
恰为要求到货日前一天。只有缺失的年份后缀使用同页制单/要求年份，其余月日数字
全部来自四个 OCR 单元。Windows 单 Paddle、少一个单元、单元分歧、完整年份冲突、
单数字日或时间顺序异常都会拒绝。

真实复跑后日期恢复为正确的 `2025-08-19`，固定 46% 置信度、标黄、不匹配且
不可靠，整体仍待人工复核；旧 `2025-08-09` 保留为被拒绝证据。301 张矩阵仅命中
该 1 张，正确 1、错误候选 0、自动决策 0。界面展示紧/宽日期行原图、最大通道
处理图、四个 OCR 原文及“残缺年份人工建议”说明。印章仍为 93.3% 可靠匹配，
字段 10/10、商品 9/9，中间图文件、HTTP 和契约缺失均为 0。报告见
`storage/e2e-date-partial-year-day-before-tier58-target.json`、
`storage/date-partial-year-day-before-matrix-tier58-final.json`、
`storage/accuracy-report-301-hybrid-tier58.json`、
`storage/date-gap-analysis-tier58-final.json`、
`storage/seal-gap-analysis-tier58-final.json`、
`storage/ground-truth-consistency-tier58.json` 和 `storage/windows-smoke-tier58.json`。

第五十七梯队恢复 `7304152279.jpg` 的辽宁旭睿售后专用章，但没有放宽“完整
公司名冲突”的通用阻断。当前 OCR 同时出现“辽宁旭谷/辽丁旭省”等错字，只有
“售后专用章”、公司相似度至少 82%、同要求人工阳性参考章，以及普通 SIFT 和
按章色裁剪 SIFT 两套独立几何全部达到门槛时才可越过该单个冲突。目标真实复跑
普通表示为 192 个匹配点、136 个单应内点、70.83% 内点率、双向覆盖
66.82%/53.70%；章色裁剪表示为 180/145、80.56%、71.64%/55.61%，参考样本为
`7275944177.jpg`，最终印章 93.3% 可靠匹配。

全库参考章矩阵为已保存通过 20、误放行 0、待新增通过 0；两张已知错章均拒绝，
其中最近似错章也只有普通 68/56、章色 63/51，远低于本路线的双表示支持度门槛。
目标日期只读到早于制单日的 `2025-08-09`，仍明确保持待人工复核，印章恢复不会
连带放行日期。界面会并排展示当前章、章色裁剪处理图、参考章，并显示两套匹配点、
内点率和覆盖率。报告见
`storage/e2e-seal-company-conflict-reference-tier57-target.json`、
`storage/seal-reference-matrix-tier57-final.json`、
`storage/accuracy-report-301-hybrid-tier57.json`、
`storage/date-gap-analysis-tier57-final.json`、
`storage/seal-gap-analysis-tier57-final.json`、
`storage/ground-truth-consistency-tier57.json` 和 `storage/windows-smoke-tier57.json`。

第五十六梯队处理 `7304857073.jpg` 的手写 `2025年8月22日`。原始日期行肉眼
可见 22 日，但最大通道整行 OCR 在紧凑和宽区域都出现稳定的模型分歧：Mobile
两次读到 `2025年8月22月`，Server 两次读到要求日期 `2025年8月23/月`；旧逻辑
随后又得到不可能的 `2025-08-02` 并因早于制单日安全拒绝，界面只剩空日期。

新路线只在 macOS Hybrid 下生成复核建议：两个模型必须各自在紧/宽区域重复唯一
四位年份日期，Server 值必须等于要求日期、Mobile 值必须是同年同月前一天且双方
都是两位日；随后固定保存完整年份原图/最大通道图、日上下文原图/最大通道图和
新增白边标准化图。完整年份槽位 Mobile/Server 都必须读到 Mobile 候选年份，
两种白边日上下文的四个“模型 × 白边方式”单元必须全部只读到 Mobile 候选日。
日期数字全部来自 OCR，要求日期只参与最后的相邻日边界检查。

真实目标重跑后日期从空值变为正确的 `2025-08-22`，固定置信度 45%、标黄并继续
待人工复核；Server 整行冲突及错误 `2025-08-02` 都保留，自动日期结论覆盖不变。
三个相近负例 `7303331190.jpg`、`7304152279.jpg`、`7293767112.jpg` 均未产生
该标记并保持待复核。301 张最新结果矩阵仅选中目标 1 张，正确 1、错误候选 0、
自动决策 0；日期原始准确率由 219/301 提升为 220/301，可靠日期仍为 196/196
正确。目标字段 10/10、商品单元格 9/9，中间图文件、HTTP 和契约缺失均为 0。

负例重跑还让 `7304152279.jpg` 当前印章 OCR 暴露“辽宁旭香/辽丁旭睿”的完整
公司名冲突；系统没有沿用历史高分，而是按既有规则安全降为待复核。因此最新可靠
印章为 223/223 正确。报告见
`storage/e2e-date-white-day-audit-tier56-target.json`、
`storage/e2e-date-white-day-audit-tier56-controls.json`、
`storage/date-white-day-conflict-matrix-tier56.json`、
`storage/date-gap-analysis-tier56-final.json`、
`storage/seal-gap-analysis-tier56-final.json`、
`storage/accuracy-report-301-hybrid-tier56.json`、
`storage/ground-truth-consistency-tier56.json` 和 `storage/windows-smoke-tier56.json`。

第五十五梯队先对印章差距报表中仅剩的两张未审计样本做离线 Mobile/Server
多表示探针。`7276798821.jpg` 的签章要求为“三星电子服务中心绵阳讯烽维修站”，
Server 只得到零散符号和单字，未形成服务中心或维修站结构；`7273005247.jpg`
要求“深圳市星睿奇光电有限公司仓储部收货章”，大小模型同样没有读到可验证的
公司主体和章型。两张都不满足既有安全门槛，探针没有写回生产结果，也没有新增
放行规则，继续留在人工复核队列。明细见
`storage/seal-unaudited-7276798821-tier55.json` 和
`storage/seal-unaudited-7273005247-tier55.json`。

随后用最初 6 张样单重新执行完整 Hybrid 闭环：任务 `6/6` 成功、`0` 失败；
机器原始字段 `60/60`、商品单元格 `81/81`、商品整行 `9/9`，日期值 `5/6`，
5 个可靠日期结论 `5/5` 正确，印章严格结论 `6/6`，6 个可靠印章结论 `6/6`
正确。`7266227347.jpg` 因机器未可靠识别日期先进入待复核，界面人工补录日期和
印章文字后确认通过；原始机器值保持不可变，修改值、备注、时间和复核历史均已
保存。另一个可靠样本的批量确认、文件名/订单号/客户/日期/整体结论/复核状态
六种筛选也全部通过，最终批次为成功 6、失败 0、待复核 0。

六张 Excel 为 6 行、16 个必需业务列和“回单结果/统计摘要”两张工作表；订单号
保持文本，日期和处理时间为真实日期类型，印章相似度为数值百分比。统计公式得到
总数 6、最终通过 6、待复核 0、已确认通过 2，公式错误扫描为 0；两张工作表均已
实际渲染检查，中文、换行、状态色和布局正常。文件位于
`outputs/tier55/三星回单-六张闭环验收.xlsx`。本轮权威全量口径与文首相同；报告见
`storage/e2e-six-hybrid-tier55.json`、
`storage/accuracy-report-301-hybrid-tier55.json`、
`storage/seal-gap-analysis-tier55-final.json`、
`storage/ground-truth-consistency-tier55.json` 和
`storage/windows-smoke-tier55.json`。

第五十四梯队处理 `7303511731.jpg` 被表格线、签字和日期覆盖的三星取机站号章。
当前章 OCR 仍逐字保留“三星电子服务中心”，打印签章要求为严格的
“三星电子服务中心取机专用章 + 7 位站号”结构；同要求人工真值阳性参考章为
`7283755018.jpg`。新增路线不降低通用参考章门槛，只在这两个语义条件同时满足时，
再要求至少 95 个好匹配、70 个 RANSAC 内点、72% 内点率和当前章/参考章双向
50% 覆盖。

301 张完整参考矩阵仅新增该目标：99 个好匹配、74 个内点、74.75% 内点率、
59.35%/51.58% 双向覆盖；同要求弱章 `7285996828.jpg` 只有 59/31 个
匹配/内点，已知错章 `7286691343.jpg` 只有 46/24 且参考覆盖 8.6%，均拒绝。
真实三图任务 3/3 成功，目标印章 92.4% 可靠匹配、日期 `2025-08-13` 可靠
匹配，整单通过；两个负例继续待复核，中间图文件、HTTP 和契约错误均为 0。
目标成为新参考后再次运行递归矩阵，19 个已存可靠参考无误接受，剩余新增接受 0、
已知负控误接受 0。印章待复核由 78 张降至 77 张。报告见
`storage/e2e-seal-branded-station-tier54.json`、
`storage/seal-reference-matrix-tier54.json`、
`storage/seal-reference-matrix-tier54-post-target.json`、
`storage/seal-gap-analysis-tier54-final.json`、
`storage/accuracy-report-301-hybrid-tier54.json`、
`storage/ground-truth-consistency-tier54.json` 和 `storage/windows-smoke-tier54.json`。

第五十三梯队处理 `7306087413.jpg` 被红章覆盖的手写 `2025年9月1日`。
Paddle Server 在紧裁和宽裁两张 RGB 最大通道去彩色日期行中均逐字得到
`2025-09-01`；随后系统固定保存“完整年份上下文”“月日上下文”和“日上下文”
原图及处理图。只有 macOS Hybrid 下 Mobile/Server 都从同一非破坏性最大通道
图读到完整年份 `2025`，并都从月日上下文读到 `9月1日`，才允许自动核验。
候选年月日全部来自 OCR，要求到货日期只在最后比较，不用于补字。

低置信度 Server 候选和人工审计增强图仍完整保留，但不能单独否决两种几何的
重复主证据；普通原图、去章色图等决策级证据若出现另一个完整日期仍立即拒绝，
残缺冲突最多两个且只能改变月份、年份和日期必须相同。真实目标结果为
`2025-09-01`、可靠匹配、整体通过；三张相近反例 `7293949384.jpg`、
`7288719420.jpg`、`7317213641.jpg` 均因年月日组件不完整或跨模型冲突保持
待复核，任务 3/3 成功且中间图文件、HTTP、契约缺失均为 0。日期待复核由
106 张降至 105 张；Windows 模拟仍回退单一 Paddle，不允许该跨模型路线。
报告见 `storage/e2e-date-server-component-tier53-target-v2.json`、
`storage/e2e-date-server-component-tier53-controls.json`、
`storage/date-gap-analysis-tier53-final.json`、
`storage/accuracy-report-301-hybrid-tier53.json`、
`storage/ground-truth-consistency-tier53.json` 和 `storage/windows-smoke-tier53.json`。

第五十二梯队处理 `7324339105.jpg` 的浅色圆章。相同签章要求的人工阳性
参考章已存在，但原保留章色图上方少量彩色扫描噪点拉高了裁剪框，将
候选覆盖率压到 19.87%。新路线不修改任何既有门槛，而是额外保存一张
“去稀疏噪点章色裁剪图”：只移除每个坐标轴两端最外层 2% 的彩色像素，
然后仍在原灰度纹理上执行 SIFT/RANSAC。单参考自动接受必须同时达到
90 个好匹配、75 个单应性内点、80% 内点率，且当前章/参考章双向覆盖
均不少于 50%；完整公司名冲突和签章要求不同仍在几何比较前直接拒绝。

301 张最新结果的稳定参考矩阵中，25 张待复核图具有同要求阳性参考，
新路线只接受该目标：94 个好匹配、77 个内点、81.91% 内点率、
68.79%/60.28% 双向覆盖。已知错章 `7286691343.jpg` 仅 46/24 个匹配/内点、
参考覆盖 8.6%，仍拒绝。目标、近邻待复核与错章的真实三图任务 3/3 成功、
0 失败；目标印章 92.2% 可靠匹配，日期 `2025-12-20` 与要求一致，整体通过；
其余两张保持待复核。字段 30/30、商品单元格 27/27，中间图文件、HTTP 和
契约缺失均为 0。印章待复核由 79 张降至 78 张；两个无安全证据的未审计章
仍保留队列。Windows 模拟默认 Paddle Mobile，失败项 0；3010 个字段真值
一致性审计为 0 冲突。报告见
`storage/e2e-seal-trimmed-chromatic-tier52-final.json`、
`storage/seal-trimmed-chromatic-matrix-tier52.json`、
`storage/seal-gap-analysis-tier52-final.json`、
`storage/accuracy-report-301-hybrid-tier52.json`、
`storage/ground-truth-consistency-tier52.json` 和 `storage/windows-smoke-tier52.json`。

第五十一梯队处理 `7295138362.jpg` 的手写 `2025年7月1日`。Server 紧裁三种
处理和 Mobile 宽裁最大通道图都给出完整 `2025-07-01`，但 Server 宽裁去表格线
三倍图把细长月份 `7` 破坏成 `1`，生成 `2025-01-01` 并被业务时间规则拒绝。
新路线先要求完整要求日期同时由 Mobile/Server 在相反紧宽裁剪逐字读出，唯一完整
冲突只能来自 Server 去线图、保持相同年份和日期且只改变月份；随后固定提取年/月
之间的月份数字窄槽，Mobile/Server 必须在最大通道去彩色及去横线两种图的四个
单元全部只读出要求月份。要求到货日仅作最后比较，不能补任何 OCR 组件。

301 张旧证据矩阵只有该目标满足槽位前置条件。真实重跑四单元均为 `7`，最终
`2025-07-01`、86% 可靠匹配，原 `2025-01-01` 保留为拒绝候选；字段 10/10、
商品 9/9、印章 97.2% 可靠匹配，整体自动通过。三个负例（真实日期与要求日期
不同、同月不同日、多个不完整月份冲突）3/3 成功且全部保持待复核，新标记 0；
中间图文件、HTTP 和契约缺失均为 0。Windows 模拟仍默认 Paddle Mobile，失败项
0。专项 Excel 为 1 行 16 列、两张工作表，中文、长订单号文本、真日期/时间、
百分比和公式错误扫描均通过。报告见
`storage/e2e-date-month-slot-conflict-tier51-final.json`、
`storage/e2e-date-month-slot-conflict-tier51-controls.json`、
`storage/date-gap-analysis-tier51-final.json`、
`storage/accuracy-report-301-hybrid-tier51.json` 和 `storage/windows-smoke-tier51.json`；
Excel 为 `outputs/tier51/三星回单-月份窄槽冲突验收.xlsx`。

第五十梯队复核 `7319592513.jpg` 时，整页 OCR、打印签章要求和红章章面都明确
包含“云南邮维科技有限公司检测专用章（3）”，原人工真值却漏记了编号“（3）”。
本轮据原图修正真值，没有删除 OCR 正确识别的字符；同时新增只读真值一致性审计，
只有字段置信度至少 95%、机器值与真值不同且同一机器值可在保存的整页原始 OCR
文本中逐字找到时，才列入人工真值复查，程序不会自动改写真值。修正后 3010 个字段
比较为 0 不一致、0 个待复查项。

目标单真实 Hybrid 重跑为 1/1 成功、字段 10/10、商品 9/9；实际收货日
`2025-11-23` 与要求日期一致并可靠通过，错误的 `2025-01-23` 候选因早于制单日被
拒绝。印章文字碎片不足以独立判定，但与人工确认的同要求参考章形成 SIFT/RANSAC
大面积几何一致：160 个好匹配、132 个内点、82.5% 内点率、候选/参考覆盖率
31.86%/59.01%，最终相似度 93.2%、可靠匹配，整体自动通过。79 张其余印章仍因
公司名、章型、编号或无文字证据不足保持待复核。中间图文件、HTTP 和契约缺失均为
0；Windows 模拟默认 Paddle Mobile 且失败项 0。专项 Excel 为 1 行 16 列、两张
工作表，中文、长订单号文本、真日期/时间、百分比格式和零公式错误均通过。报告见
`storage/e2e-seal-truth-correction-tier50-final.json`、
`storage/ground-truth-consistency-tier50-final.json`、
`storage/seal-gap-analysis-tier50-final.json`、
`storage/accuracy-report-301-hybrid-tier50.json` 和 `storage/windows-smoke-tier50.json`；
Excel 为 `outputs/tier50/三星回单-真值编号与参考章验收.xlsx`。

第四十九梯队处理要求日期为 `2025-05-24`、实际手写日期清晰为
`2026-05-24`，但破坏性增强图把年份末位 `6` 误成 `5/0` 的跨年冲突。
新路线不做多数投票：Paddle Mobile 必须在紧裁与宽裁的原始裁剪、去章色裁剪、
日期行原图和日期行去章色图共八个非破坏性单元中全部只读到同一个完整日期；
Server 在紧/宽两种几何至少各生成一个独立复核单元，并且宽裁全部支持候选、
紧裁全部明确保留要求年份冲突。候选先完全由 OCR 组装，之后才检查它是否为要求
日期同月同日的下一年；其他冲突只允许是同十年月日或两位日漏首位。

301 张独立矩阵只命中 `7289097484.jpg`，正确 1、误放行 0。真实重跑先发现
“低置信度 Server 单元”并非每轮生成，规则因此改为每种几何至少一条、所有实际
生成单元必须一致；第二次又发现可靠标记生成后仍被通用候选排序覆盖，现严格共识
在最终日期比较前先明确候选，冲突原文继续保留审计。最终真实结果为
`2026-05-24`、88% 可靠“不匹配（晚 365 天）”；印章 96% 可靠匹配，字段
10/10、商品 9/9。整单仍因签章要求字段低置信度保持待人工复核，没有绕过复核闭环。

两张冲突对照样本 2/2 成功、均未触发新路线；日期/印章中间图文件、HTTP 和契约
缺失均为 0。专项 Excel 为 1 行、16 列、两张工作表，长订单号为文本，要求/实际
日期和处理时间为真日期，印章相似度为百分比，待复核黄色状态、中文渲染及公式
错误扫描均通过。Windows 模拟仍默认 Paddle Mobile，失败项 0。报告见
`storage/date-cross-year-nondestructive-tier49-final.json`、
`storage/e2e-date-cross-year-nondestructive-tier49-target-final.json`、
`storage/e2e-date-cross-year-nondestructive-tier49-controls.json`、
`storage/date-gap-analysis-tier49-final.json`、
`storage/accuracy-report-301-hybrid-tier49.json` 和
`storage/windows-smoke-tier49.json`；Excel 为
`outputs/tier49/三星回单-跨年日期目标验收.xlsx`。

第四十八梯队处理日期 OCR 漏掉打印“年”分隔符，同时两位手写日的首位容易被
表格线吞掉的场景。`7292789196.jpg` 的日期栏原图被 Mobile 读为
`20256月16日`，Server 在独立紧/宽裁剪读为 `2025年6月16日`；系统只在
macOS Hybrid、候选年月日完全来自这些字面 OCR、其他日期至多把同月两位日压成
一位时，才进入新的日数字内槽复核。

内槽固定取日数字中心的 58%–78%，保存原图、最大通道去彩色图和去横线图。
Mobile/Server 在后两张图上的四个单元必须全部只读到同一个两位日；本样本均为
`16`，因此可靠识别 `2025-06-16` 并与要求到货日匹配。301 张独立矩阵只命中
该 1 张，正确 1、误放行 0。真实目标及两张对照样本批量任务 3/3 成功、0 失败、
2 待复核；目标自动通过，两张对照样本均未触发新路线并因印章证据不足继续待复核。
字段 30/30、商品单元格 27/27、日期值 3/3；日期可靠决策 2/2 正确，印章可靠
决策 1/1 正确。中间图接口、文件和契约缺失均为 0；Windows 模拟仍默认 Paddle
Mobile，失败项 0。报告见
`storage/date-missing-year-separator-tier48-final.json`、
`storage/e2e-date-missing-year-separator-tier48-final.json`、
`storage/date-gap-analysis-tier48-final.json`、
`storage/accuracy-report-301-hybrid-tier48.json` 和
`storage/windows-smoke-tier48.json`。

第四十七梯队处理真实签收日与要求到货日不同、但两位手写“日”在部分预处理
中被截成一位的场景。`7278191302.jpg` 的原日期栏为 `2025年3月13日`，要求
到货为 `2025-03-14`；Paddle Mobile/Server 在紧裁、宽裁四个 RGB 最大通道
单元中都读到完整 `2025-03-13`，另一路会把细竖笔 `1` 漏掉并读成 `3 日`。

新路线先要求四个模型×几何单元全部一致、候选位于要求日期前后 3 天且不同于
要求日期；其他字面完整日期至多一个，并且只能是同年同月、两位日减少为其中一位。
随后从固定模板的“月/日”之间保存日数字窄槽原图、最大通道增强图和去横线增强图；
Mobile 与 Server 必须在两种处理上都只读出同一个完整两位日，四个槽位单元任一
缺失或读成单字都拒绝。候选年月日全部来自 OCR，要求日期只用于最后比较。

301 张历史证据预筛只命中该目标；真实 Hybrid 完整重跑得到日期
`2025-03-13`、100% 可靠“不匹配（早 1 天）”，字段 10/10、商品单元格 9/9，
新旧中间图文件、HTTP 和契约错误均为 0。本张印章仍只有 54% 且公司文字不足，
所以整单继续待人工复核，没有因日期可靠而强行关闭。Windows 模拟仍默认 Paddle
Mobile，失败项 0。专项 Excel 为 1 行/16 列与两张工作表，运单号/订单号保持文本，
要求/实际日期为 `yyyy-mm-dd` 真日期，待复核黄色状态、中文渲染和公式错误扫描均
通过。报告见 `storage/e2e-date-day-slot-tier47-target-final.json`、
`storage/date-gap-analysis-tier47-final.json`、
`storage/accuracy-report-301-hybrid-tier47.json` 和
`storage/windows-smoke-tier47.json`；Excel 为
`storage/exports/e2e-date-day-slot-tier47-target.xlsx`。

第四十六梯队处理颜色分离图中残留黑色表格线/签字噪声导致 SIFT 裁剪边界
虚大的问题。新路线先用红/蓝章色确定稳健裁剪框，再保留原始灰度纹理做
SIFT；原裁剪几何数值仍保存供对照。仅当同一章区与两份不同人工真值
阳性文件同时通过时才自动结论；最强票至少 95 内点/85% 内点率，每票至少
100 匹配、65 内点、68% 内点率和 24% 双向覆盖。

301 张独立章色矩阵和生产矩阵都只新放行 `7290746635.jpg`，已知两张错章
误放行为 0。目标与 `7302721916.jpg`、`7306087418.jpg` 分别形成
112/100 和 102/70 个匹配/内点。真实三张 Hybrid 任务 3/3 成功、字段
30/30、商品 27/27；目标日期 `2025-06-03` 和印章都可靠匹配，整单通过。
复核界面新增“章色稳健裁剪图（SIFT）”，并显示处理前/后几何指标；文件、
HTTP 和中间图契约缺失均为 0。报告见
`storage/seal-chromatic-consensus-matrix-tier46-final.json`、
`storage/seal-visual-reference-matrix-tier46-final.json`、
`storage/e2e-seal-chromatic-consensus-tier46.json`、
`storage/e2e-seal-chromatic-artifact-tier46.json`、
`storage/accuracy-report-301-hybrid-tier46.json` 和
`storage/seal-gap-analysis-tier46-final.json`。

第四十五梯队针对同一章区的多份独立人工真值参考章增加“高纯度多参考
共识”路线。它不放宽单参考门槛；必须有两个不同人工确认文件，每份都要
满足良好匹配数 85、RANSAC 内点 65、内点率 70%、候选/参考双向覆盖
30%，最强参考还要至少 70 个内点。

301 张全矩阵只新放行 `7303954809.jpg`：同一矩形授权章与
`7313375993.jpg` 和 `7317213641.jpg` 分别形成 90/72 和 89/66 个
匹配/内点，内点率 80.0%/74.16%，双向覆盖都高于 33%。已知两张错章
误放行为 0。真实三张 Hybrid 任务 3/3 成功、字段 30/30、商品单元格
27/27；目标章以 91.2% 可靠匹配。其手写日期候选 `2025-08-07` 早于制单日
`2025-08-14`，已被业务时间规则拒绝，整单仍待人工复核。报告见
`storage/seal-high-purity-consensus-matrix-tier45.json`、
`storage/e2e-seal-high-purity-consensus-tier45.json`、
`storage/accuracy-report-301-hybrid-tier45.json`、
`storage/seal-gap-analysis-tier45-final.json` 和 `storage/windows-smoke-tier45.json`。

第四十四梯队对剩余 83 张不可靠印章执行人工真值参考章全矩阵复核。
`7289048988.jpg` 的收货章被表格线和签字局部遮挡，与 `7279799198.jpg`
的同签章要求人工真值阳性章仍形成 152 个良好匹配、107 个 RANSAC
内点、70.39% 内点率和 24.40%/51.10% 双向覆盖。新增“超高支持度局部
覆盖”独立路线，五个边界必须同时满足，不放宽原有任何路线。

301 张全矩阵只新放行该 1 张真阳性，已知两张错章误放行为 0。真实目标/
反例三张端到端任务 3/3 成功、字段 30/30、商品单元格 27/27；目标章
以 92.1% 可靠匹配，但手写日期仍无法可靠识别，所以整单保持待人工复核。
Windows 模拟仍默认 Paddle Mobile，单一 Paddle 不会自我认证日期/印章。报告见
`storage/seal-visual-reference-matrix-tier44-final.json`、
`storage/e2e-seal-ultra-support-tier44.json`、
`storage/accuracy-report-301-hybrid-tier44.json`、
`storage/seal-gap-analysis-tier44-final.json` 和 `storage/windows-smoke-tier44.json`。

第四十三梯队复核月份组件共识的跨平台边界，并审计唯一剩余的无冲突完整年份
日期候选。月份组件路线现硬性要求日期主引擎为 macOS Vision、辅助引擎为 Paddle；
纯 Paddle、Windows Hybrid 回退和缺少任一来源的结果即使保存了相同槽位文本，也
不能触发自动结论。真实 macOS Hybrid 再跑 `7266220440.jpg` 仍以 84% 可靠识别
`2025-01-05`，字段 10/10、商品 9/9、日期 1/1、印章 1/1，整体通过；中间图
文件、HTTP 和契约错误均为 0。

`7302125543.jpg` 的远下方笔迹经七种完整行预处理和独立日数字裁剪复核：Mobile
在原图/去章色图读到 `2025.8.5`，并在两张日数字图读到 `5`；Server 完整行最强
结果却为 `2025.8.3`，日数字图只输出箭头，Vision 对合成窄图也无可靠输出。因此
安全矩阵接受 0，该样本继续待人工复核，没有用要求日期或 Mobile 单模型补日。
Windows 模拟报告确认默认 `paddle`、Hybrid 三阶段均回退 Mobile、Vision 不暴露、
失败项 0；自检新增 `--output` 可将结果保存为 JSON。报告见
`storage/date-preprocessing-tier43-7302125543.json`、
`storage/far-lower-date-component-tier43-7302125543.json`、
`storage/windows-smoke-tier43.json`、
`storage/e2e-date-component-platform-gate-tier43.json`、
`storage/accuracy-report-301-hybrid-tier43.json` 和
`storage/date-gap-analysis-tier43-final.json`。

第四十二梯队处理固定日期模板中“月份数字 1 与印章/表格粘连”的缺口。
`7266220440.jpg` 的日期栏实际为 `2025年1月5日`，原有 Mobile/Server 能分别
读到完整年份和显式 `5日`，但整行 OCR 会把年/月之间的细竖笔吞掉。系统现保存并
展示“月份数字窄槽原图”和“最大通道去彩色增强图”；只有 Mobile、Server 都从
窄槽输出同一个纯数字月份，同时双方完整年份一致、双方显式日一致、其他完整日期
无冲突时，才组合候选。要求到货日期不参与补年、补月或补日，只在候选形成后比较。

对 112 张原日期待复核样本的完整窄槽矩阵只选中 `7266220440.jpg`，1/1 正确、
错误放行 0。真实目标/反例端到端回归 3/3 成功、0 失败：目标以 84% 可靠识别
`2025-01-05`，日期和印章均匹配、整单通过；年份冲突的 `7289097484.jpg` 与年份
证据不足的 `7284653895.jpg` 继续待复核。批次字段 30/30、商品单元格 27/27、
商品行 3/3、印章结论 3/3；可靠日期 1/1 正确；新旧中间图文件、HTTP 和契约
缺失均为 0。报告见 `storage/date-component-consensus-tier42.json`、
`storage/e2e-date-component-consensus-tier42.json`、
`storage/accuracy-report-301-hybrid-tier42.json`、
`storage/date-gap-analysis-tier42-final.json` 和
`storage/seal-gap-analysis-tier42-final.json`。

第四十一梯队修正完整跨年手写日期与缺年份修复候选的证据等级。以往
`7284734613.jpg` 的多个 OCR 引擎都读到字面完整 `2024-04-26`，但“4月26日”
会继承要求到货年份生成 `2025-04-26`，且业务时间规则会把早于制单日期的真实
跨年读数降为人工候选。新路线只在唯一完整四位年份日期与要求年份相邻时启用；
Paddle Mobile 和 Server 必须分别在紧凑/宽区域均读到同一日期，形成双模型×双
几何四个必要单元；所有其他残缺读数还必须保持相同月日，任何完整日期或其他月日
冲突都会否决。Vision 只作为可选支持，避免 macOS 沙箱偶发无输出造成结果波动；
Windows 单 Mobile 路线不能满足该门槛。

实时与全量 301 张矩阵均只接受 `7284734613.jpg`，1/1 正确、误接受 0。生产
重跑稳定识别 `2024-04-26`，置信度 86%，与要求 `2025-04-26` 可靠判为“不匹配”，
整单结论“不通过”；`7289097484.jpg` 和 `7278191302.jpg` 两张完整日期冲突反例
继续待复核。目标/反例批次 3/3 成功、0 失败，字段 30/30、商品单元格 27/27、
商品行 3/3、日期值 3/3；中间图文件、HTTP 和契约缺失均为 0。界面在日期原图及
处理图旁显示跨年共识候选和四单元支持，并把原业务时间异常记录保存为“强共识覆盖”
而不是误列为拒绝项。

同轮还对最后两张未执行 Server 章色复核的样本做了 Mobile/Server、展开图及分带
真实探测；两张均只有无意义碎片，新增可靠章 0，继续待复核。报告见
`storage/seal-server-final-coverage-tier41.json`、
`storage/date-cross-year-consensus-tier41-live.json`、
`storage/e2e-date-cross-year-consensus-tier41-final.json`、
`storage/e2e-date-cross-year-consensus-tier41-target-final.json`、
`storage/accuracy-report-301-hybrid-tier41.json`、
`storage/date-gap-analysis-tier41-final.json` 和
`storage/seal-gap-analysis-tier41-final.json`。

第四十梯队为 70%–80% 的浅色参考章增加“彩色墨迹 + SIFT 同参考联合”路线。
它不降低既有三项 80% 的纯颜色门槛，而是要求同一候选章与同一人工真值阳性参考章
同时满足：彩色综合分/相关度/Dice 不低于 70%/70%/65%，SIFT 至少 60 个匹配、
40 个 RANSAC 内点、65% 内点率，并且候选章和参考章表面覆盖率均不低于 30%。
签章要求必须完全相同，公司名冲突继续在视觉计算前硬阻断。

301 张视觉矩阵仅新增接受 `7298889251.jpg`，对应参考 `7275201025.jpg`；彩色
三项为 72.45%/75.20%/65.39%，SIFT 为 63 个匹配、44 个内点、69.84% 内点率，
双向覆盖 30.81%/40.70%。新增 1/1 正确、错误放行 0；两张已知错误章均未提升。
真实专项回归 3/3 成功，目标章以 91.1% 可靠通过，错误章继续待复核；中间图文件、
HTTP 与契约缺失为 0。原始 6 张基准批量验收 6/6 成功、0 失败、2 张待复核；字段
60/60、商品单元格 81/81、商品整行 9/9、印章 6/6，日期 4/6，另外两张因证据
不足安全进入人工复核。Excel 已按实际任务范围显示上述准确率，订单号保持文本，
日期保持真正日期类型，中文、标黄、两张工作表渲染和公式错误扫描均通过。Windows
模拟失败项为 0。报告见 `storage/seal-color-sift-similarity-tier40.json`、
`storage/seal-visual-reference-matrix-tier40.json`、
`storage/e2e-seal-color-sift-tier40.json`、`storage/e2e-six-hybrid-tier40.json`、
`storage/accuracy-report-301-hybrid-tier40.json`、
`storage/date-gap-analysis-tier40.json` 和 `storage/seal-gap-analysis-tier40.json`；
验收 Excel 为 `storage/exports/e2e-six-hybrid-tier40.xlsx`。

第三十九梯队为浅色、横线干扰严重、SIFT 局部特征不足的参考章增加彩色墨迹
整体几何路线。系统从已展示的章色分离图中只保留红/蓝彩色墨迹，排除黑色表格线
和手写笔迹；将章面居中归一化，抑制不同圆章都具有的外圈边框，再在 ±12° 内做
小角度/平移对齐。候选必须与相同签章要求、人工真值阳性的本地参考章同时满足：
综合分、归一化相关度和 Dice 重合度都不低于 80%。公司名冲突仍在计算前硬阻断。

301 张矩阵新增接受 `7302125543.jpg` 与 `7273697921.jpg`，彩色墨迹三项指标
分别为 `81.58%/80.96%/83.19%` 和 `93.27%/92.83%/94.41%`；2/2 与真值
一致、假接受 0。最强错误章只有 `43.37%/45.35%/38.28%`，另一错误章因完整
公司冲突不进入视觉路线。真实 6 张目标/控制回归 6/6 成功、0 失败、3 张待复核；
两张目标印章以 91.5% 和 96% 可靠匹配，两张错误章均未放行，既有 SIFT 高支持度
和普通 OCR 路线无回退。字段 60/60、商品单元格 54/54、商品整行 6/6、日期值
6/6；日期和印章可靠结论均 100% 正确，中间图文件、HTTP 与契约缺失均为 0。
界面展示彩色墨迹综合分、相关度、Dice、参考文件及并排参考章；Windows 模拟失败
项为 0。报告见 `storage/seal-color-mask-similarity-tier39.json`、
`storage/seal-visual-reference-matrix-tier39-final.json`、
`storage/e2e-seal-color-mask-tier39.json`、
`storage/accuracy-report-301-hybrid-tier39.json`、
`storage/date-gap-analysis-tier39-final.json` 和
`storage/seal-gap-analysis-tier39-final.json`。

第三十八梯队处理“日期被红章和签字覆盖，Mobile 无法读全，但 Server 在同一
区域的多种增强图上稳定读到完整要求日期”的窄场景。路线要求所有紧凑/宽区域内
可解析日期都唯一且等于要求到货日期；Server 必须在同一几何区域至少三种不同
预处理上读到字面完整四位年份日期。两种或更少预处理、残缺年份修复、任何其他
日期、非要求日期以及下方审计区域均不放行。

301 张证据矩阵只接受 `7302125545.jpg` 的 `2025-08-07`，1/1 与人工真值
一致、假接受 0。真实 6 张目标/对照回归 6/6 成功、0 失败、4 张待复核；目标以
74% 可靠日期和可靠印章自动通过，远下方日期仍保持 35% 待复核，三张两位日漏位
反例全部待复核，既有跨区域主证据样本继续通过。字段 60/60、商品单元格 54/54、
商品整行 6/6；日期与印章可靠结论均 100% 正确，中间图文件、HTTP 和契约缺失
均为 0。界面会在原图、去印章色、去表格线和放大图旁显示 Server 三预处理候选
及接受理由；Windows 模拟仍默认 Paddle Mobile 且失败项为 0。报告见
`storage/date-repeated-server-matrix-tier38.json`、
`storage/e2e-date-repeated-server-tier38.json`、
`storage/accuracy-report-301-hybrid-tier38.json`、
`storage/date-gap-analysis-tier38-final.json` 和
`storage/seal-gap-analysis-tier38-final.json`。

第三十七梯队处理“唯一 Server 完整日期与大量 Mobile 证据一致，但另一个
Mobile 预处理产生孤立干扰”的窄场景。第一版全量证据矩阵虽命中 2 张正确样本，
却会误接收 5 张两位日漏掉一位的样本，故未上线。最终路线要求：Server 完整日期
唯一；Mobile 在紧凑和宽区域至少四个几何/预处理单元格重复同日；候选在要求到货日
前后 3 天内；其余最多只有一个 Mobile 非完整日期孤立单元格。完整 Server 冲突、
多日期/多单元格冲突或超出安全窗均保持待复核。

301 张矩阵最终只接受 `7286691342.jpg` 的 `2025-05-10` 和
`7290687504.jpg` 的 `2025-06-02`，2/2 与人工真值一致、假接受 0。真实 7 张
目标/漏位对照批次 7/7 成功、0 失败、5 张待复核；两张目标日期均为 78% 可靠，
前者正确判为比要求日期早 1 天“不通过”，后者正确判为“通过”。5 张两位日漏位
对照仍全部待复核；字段 70/70、商品单元格 63/63、商品整行 7/7，中间图文件、
HTTP 与契约缺失均为 0。界面会在日期原图及处理图旁显示跨区域主证据值与接受依据。
Windows 模拟继续安全回退到 Paddle Mobile。报告见
`storage/date-server-mobile-dominance-matrix-tier37.json`、
`storage/e2e-date-server-mobile-dominance-tier37.json`、
`storage/accuracy-report-301-hybrid-tier37.json`、
`storage/date-gap-analysis-tier37-final.json` 和
`storage/seal-gap-analysis-tier37-final.json`。

第三十六梯队处理“多数最大通道日期证据完整一致，但一个裁剪把两位日截成
单字”的窄场景。新路线必须在 Mobile/Server 与紧/宽几何的 4 个可能单元格中，
至少 3 个不同单元格读到完全相同的字面四位年份日期，并同时覆盖两种模型与
两种几何。唯一允许的其他值必须与共识日期同年同月，且只是两位日丢掉其中
一位；紧凑日期修复、两单元格共识、多个冲突、月份/年份不同均不放行。

301 张已保存证据矩阵仅命中 `7267503639.jpg` 与 `7317213639.jpg`：
两者分别有 3 个单元格完整读到 `2025-01-14` 和 `2025-11-11`，唯一干扰为
`2025-01-04` 和 `2025-11-01`；2/2 与人工真值一致，假接受 0。生产结果为两张
日期均 100% 可靠匹配并自动通过。

6 张目标/反例批次 6/6 成功、0 失败、2 张待复核；字段 60/60、商品单元格
54/54、商品整行 6/6。`7304747896.jpg` 的错误 `8月4日` 增强候选被拒绝，
`7302125545.jpg` 仅有 Server 完整日期也继续待复核；既有不匹配日期路线继续可靠。
界面会在日期原图、去印章色、去表格线和最大通道图旁显示三单元格共识值、逐模型文字
及接受原因。中间图文件、HTTP 和契约缺失均为 0；Windows 模拟仍默认 Paddle Mobile。
报告见 `storage/date-preprocess-tier36.json`、
`storage/date-max-channel-consensus-matrix-tier36.json`、
`storage/e2e-date-three-cell-consensus-tier36.json`、
`storage/accuracy-report-301-hybrid-tier36.json`、
`storage/date-gap-analysis-tier36-final.json` 和
`storage/seal-gap-analysis-tier36-final.json`。

第三十五梯队处理“两枚客户维修章相互重叠，单个检测框都只保留部分公司
名”的窄场景。新路线仅适用于法定公司名后跟“维修专章/维修专用章”的
要求，必须同时满足：两个检测区都是同色“收货客户章”、交集占较小章区
至少 35%、Server 在不同章区读到严格互补的公司名分片，并独立读到完整
“维修专用章”。系统只重组实际 OCR 字符，不从签章要求复制缺字；完整不同
公司名仍为硬否决。

`7281418465.jpg` 的上、下重叠章区分别读到“兰科技有限公司”与
“杭州索”、“维修专用章”，重组后印章可靠匹配，实际日期 `2025-04-03`
也与要求一致，整单自动通过。表单主 OCR 已正确读到印刷原文“维修专章”时，
不再被 Vision 多出的“用”字覆盖；这与印章同义匹配分开处理。界面在原章、
保留章色图、展开图和旋转对照图旁显示重组文字及接受原因。

6 张目标/反例批次 6/6 成功、0 失败、4 张待复核；字段 60/60、商品单元格
54/54、商品整行 6/6。另两张公司或章型不可读的目标和两张公司冲突/证据
不足控制样本都未误放行。中间图文件、HTTP 和契约缺失均为 0；Windows 模拟默认
Paddle Mobile，Hybrid 无 Vision 时保持 Paddle-only 安全路由。报告见
`storage/e2e-overlapping-repair-stamps-tier35-final.json`、
`storage/accuracy-report-301-hybrid-tier35.json`、
`storage/seal-gap-analysis-tier35-final.json` 和
`storage/date-gap-analysis-tier35-final.json`。Excel 导出逻辑本轮未变。

第三十四梯队处理“几何支持非常强、但检测裁剪使单侧章面覆盖率略低于 30%”的
窄场景。原严格路线、多参考路线和高内点率路线保持不变；新增路线要求同一人工
真值阳性参考章至少形成 130 个 Lowe 候选匹配、100 个 RANSAC 单应性内点、
70% 内点率，并且当前章与参考章的内点覆盖率都不少于 28%。签章要求仍须完全
相同、参考必须来自不同文件、当前 OCR 必须有非空文字，任何完整不同公司名冲突
仍在视觉计算前硬否决。该路线只追加可审计的几何证据，不把签章要求复制成印章
识别文字。

301 张保存证据矩阵中只有 `7293205278.jpg` 满足新增路线：它与
`7302125552.jpg` 的同要求阳性参考章形成 150 个候选、111 个内点、74% 内点率，
当前章/参考章覆盖率为 29.90%/59.42%，可靠印章置信度 92.6%。已知不匹配的
`7289048988.jpg` 因当前章覆盖仅 24.40% 仍拒绝；另一个不匹配控制只有 46 个
候选、24 个内点，也未放行。矩阵新增接受 1、假接受 0。

6 张目标/风险对照真实批次 6/6 成功、0 失败、3 张待复核；字段 60/60、商品
单元格 54/54、商品整行 6/6。目标单实际日期 `2025-06-19` 与要求日期
`2025-06-20` 不匹配，日期和印章现均可靠，所以整体自动判定“不通过”且无需
复核；三个证据不足或公司冲突的控制样本继续待复核。日期/印章中间图文件、HTTP
和契约缺失均为 0。Windows 模拟默认 Paddle Mobile，Hybrid 无 Vision 时三阶段
安全回退；完整测试、Python 编译和前端语法检查均通过。报告见
`storage/e2e-seal-high-support-minor-coverage-tier34.json`、
`storage/seal-visual-reference-matrix-tier34-final.json`、
`storage/accuracy-report-301-hybrid-tier34.json`、
`storage/seal-gap-analysis-tier34-final.json` 和
`storage/date-gap-analysis-tier34-final.json`。Excel 导出逻辑本轮未变，继续使用
已独立验证的
`outputs/019ff6b3-50ea-7a63-a9e6-22472e8db17d/三星回单-Tier33最新全量结果.xlsx`。

第三十三梯队增加“最大通道跨模型不匹配日期”路线。它只在 macOS Hybrid 的
手写日期仍不可靠时运行，把日期行的 RGB 最大通道去彩色图三倍放大，再分别交给
Paddle Mobile 和 Server；两者必须在紧、宽不同几何裁剪上以至少 80% OCR
置信度读到同一个字面完整四位年份日期，而且该日期必须不同于要求到货日期。
所有日期组件均来自 OCR，要求日期只用于最后比较，不能提供年份或月日；同一裁剪
重复、存在第二个可解析日期、候选早于制单日期都会否决。界面在原图、去章色、
去表格线和最大通道图旁显示逐模型文本、接受说明及“未使用要求日期补值”标记。

301 张保存证据矩阵只接受 `7293205278.jpg`，候选 `2025-06-19` 与人工真值
一致，假接受 0。真实复跑中 Mobile/Server 分别在宽/紧最大通道图读到完整
`2025年6月19日`，系统以 100% 可靠度判定实际日期比要求 `2025-06-20` 早一天；
印章证据仍不足，所以整单继续待复核而不是错误关闭队列。4 张完整日期冲突、跨年
或单几何负例均未走新路线；`7273005228.jpg` 由既有严格匹配路线在当前重跑中
恢复。6 张批次 6/6 成功、0 失败，字段 60/60、商品单元格 72/72、商品整行
8/8，可靠日期决定 3/3 正确，日期/印章中间图文件、HTTP 和契约缺失均为 0。

最终可靠日期从 180 增至 182，182/182 正确，未可靠日期由 121 降至 119；印章
权威指标不变。最新全量 Excel 为 310 行、16 列和两张工作表，独立电子表格运行时
确认目标行的文本订单号、真实要求/实际日期、“不匹配 + 待复核”、状态着色、中文
渲染和公式错误 0。Windows 模拟仍默认 Paddle Mobile，Hybrid 无 Vision 时不会
启用这条双模型规则并保持安全待复核。报告见
`storage/date-max-channel-mismatch-matrix-tier33-final.json`、
`storage/e2e-date-max-channel-mismatch-tier33.json`、
`storage/accuracy-report-301-hybrid-tier33.json`、
`storage/date-gap-analysis-tier33-final.json` 和
`storage/seal-gap-analysis-tier33-final.json`；Excel 为
`outputs/019ff6b3-50ea-7a63-a9e6-22472e8db17d/三星回单-Tier33最新全量结果.xlsx`。

第三十二梯队继续保持原有 110 候选匹配、80 单应性内点、65% 内点率和双方
30% 章面覆盖的单参考严格门槛，同时为裁剪轻微抖动增加两条可审计路线。第一条
要求同一检测章区分别与至少两张不同文件的人工真值阳性参考章一致：每份参考至少
98 个候选匹配、65 个内点、65% 内点率和双方 30% 覆盖，且最强参考至少 75 个
内点；不同章区或同一文件的多张中间图不能凑票。第二条仍要求至少 80 个内点，
只把候选匹配下限调整为 105，并把内点率提高到 70%，双方覆盖仍为 30%。完整
301 张当前证据矩阵中仅 `7303604209.jpg` 达到后一条（109 个候选、81 个内点、
74.31% 内点率、当前章/参考章覆盖 57.27%/34.45%）；两张真值不匹配控制仍因
公司冲突硬否决或只有 24 个内点而拒绝，矩阵新增假接受为 0。

真实生产复跑中，`7303604209.jpg` 的印章由不可靠提升为 91% 可靠匹配，但界面
和数据库保留原 OCR“中心”、原状态和全部中间图，不把签章要求复制成识别文字；
其日期虽识别为 `2025-08-14`，只有 68% 且未形成可靠跨模型证据，因此整单仍在
待复核队列。目标、两个不匹配控制及三张回归样本的首轮批次 6/6 成功、0 失败，
字段 60/60、商品单元格 54/54；目标规则固化后的单张生产复跑字段 10/10、商品
单元格 9/9、印章可靠决定 1/1 正确。最终权威口径新增 1 个可靠印章，达到
213/301 且 213/213 正确；仍有 88 张印章和 121 张日期需要继续优化或人工复核。

最新全量 Excel 导出 310 行、16 个必需列和两张工作表，独立电子表格运行时确认
订单号文本、真实日期/时间、百分比、待复核黄色、通过绿色、中文渲染和公式错误
0；日期/印章中间图文件、HTTP 与契约缺失均为 0。Windows 模拟继续默认 Paddle
Mobile，Hybrid 在无 Vision 时安全回退且冒烟失败项为 0。报告见
`storage/e2e-seal-multi-reference-tier32.json`、
`storage/e2e-seal-high-ratio-tier32-target.json`、
`storage/seal-visual-reference-matrix-tier32-final.json`、
`storage/accuracy-report-301-hybrid-tier32.json`、
`storage/seal-gap-analysis-tier32-final.json` 和
`storage/date-gap-analysis-tier32-final.json`；Excel 为
`outputs/三星回单-Tier32最新全量结果.xlsx`。

第三十一梯队没有继续放宽印章 OCR 文字阈值。对 98 张不可靠印章按相同签章要求
查找人工真值阳性参考章后，新增本地 SIFT/RANSAC 复核：候选与参考必须来自不同
文件、签章要求标准化后完全相同、当前 OCR 至少保留一段印章文字且没有完整公司
冲突；特征还必须同时达到 110 个候选匹配、80 个单应性内点、65% 内点率，并在
候选和参考两张章面各覆盖至少 30%。参考库只收录人工真值明确为应匹配、当前
Hybrid 机器结论也可靠的样本；局部五角星、边框或表格线无法单独通过。系统保留
原始 OCR 文字、原 OCR 分数与状态，只把参考文件、当前/参考章色图、内点数、
内点率和覆盖率追加为可审计证据。两张真值不匹配控制分别只有 24 个内点，或因
“大连允华/大连北华”公司冲突在视觉计算前即被否决。

6 张真实端到端安全批次 6/6 成功、0 失败、字段 60/60、商品单元格 54/54；
南昌永航、大连北华、北京华康君泰三张印章获得可靠参考章匹配，两张不匹配控制
和杭州索兰重叠章均未误放行。后一张的大模型逐角度探针最多读到“杭州蒙/修专用
章”，Mobile 未复现完整“杭州索”，所以继续待复核。额外 7 张真实增益回归
7/7 成功、字段 70/70、商品单元格 63/63、日期值 7/7；6 张印章可靠决定全部
正确，另 1 张只有 77 个内点而保留复核。两批合计使权威可靠印章由 203 提升到
212，212/212 正确。第一批 Excel 为 6 行 16 列，独立电子表格运行时确认中文、
文本订单号、真实日期/时间、百分比、黄色待复核、双工作表渲染及公式错误 0；
日期/印章中间图文件、HTTP 和契约也均无缺失。Windows 模拟冒烟通过。报告见
`storage/e2e-seal-visual-reference-tier31.json`、
`storage/e2e-seal-visual-reference-tier31-extra7.json`、
`storage/seal-visual-reference-matrix-tier31.json`、
`storage/accuracy-report-301-hybrid-tier31.json`、
`storage/seal-gap-analysis-tier31-final.json` 和
`storage/date-gap-analysis-tier31-final.json`；Excel 为
`outputs/三星回单-Tier31视觉参考章验收.xlsx`。

第三十梯队把固定日期行拆成“完整年份”和“月日联合”两个重叠槽位，并为每个
槽位保存最大通道去彩色图与进一步去横线图。自动接受只限 macOS Hybrid：
Mobile 与 Server 必须在这两种受控预处理里各自得到唯一且相同的完整四位年份、
明确月份和日期；所有组件均来自 OCR，要求到货日期只用于最后比对，不能补值；
候选还必须等于要求日期且整条日期行没有任何其他可解析日期。44 张矩阵包含
13 张无冲突候选、常见漏位负例及全部 24 张“实际日期与要求日期不同”样本，
严格规则只接受 `7281418465.jpg`、`7316132080.jpg` 两张真匹配，假通过为 0；
`7293205278.jpg` 虽正确识别出提前一天到货，仍以 67% 进入复核，不会被改写成
匹配。5 张真实生产回归全部成功、字段 50/50、商品单元格 45/45，新增两张可靠
日期；其中后一张整单自动通过，前一张因印章不足继续待复核。两张槽位原图、
去彩色图、去横线图和逐模型文本均写入数据库并在界面显示，中间图契约无缺失。
Excel 5 行 16 列经独立表格运行时确认文本订单号、真实日期/时间、百分比、状态
着色、双工作表和零公式错误。Windows 仍回退单一 Paddle 并保持安全待复核。
报告见 `storage/date-slots-matrix-tier30.json`、
`storage/date-slots-mismatch-tier30.json`、
`storage/e2e-date-slot-consensus-tier30.json`、
`storage/accuracy-report-301-hybrid-tier30.json` 和
`storage/date-gap-analysis-tier30-final.json`。

第二十九梯队聚焦 123 张不可靠日期中的“远下方手写日期”。全量缺口审计确认，
当前 301 张证据中只有 `7302125543.jpg` 和 `7303331200.jpg` 在该区域出现严格
四位年份日期，且都命中真值；但前者只有 Mobile 证据，另一个预处理还误读为
`2035-08-05`，所以继续待人工复核。新生产规则不使用要求到货日期补值，只在
macOS Hybrid 下启用：Paddle Mobile 必须在完整远下方区域的至少两种预处理上
重复同一严格日期，Paddle Server 大模型还必须在独立窄日期行的至少两种预处理
上重复同一日期；任何其他严格日期立即否决。`7303331200.jpg` 的完整区域和窄行
均稳定读到 `2025-08-12`，真实重跑后日期 100% 可靠匹配、整单自动通过；
`7302125543.jpg` 仍以 35% 显示 `2025-08-05` 并待复核。原始完整区域、去印章色、
窄日期行及两模型逐变体文本均写入数据库并在界面展示；纯 Paddle 和 Windows
单模型不生成可靠结论。两张回归 2/2 成功、字段 20/20、商品单元格 18/18、
中间图契约无缺失；Excel 2 行 16 列经独立表格运行时验证。报告见
`storage/date-gap-analysis-tier29-current.json`、
`storage/date-far-lower-cross-model-probe-tier29.json`、
`storage/e2e-date-far-lower-cross-geometry-tier29.json`、
`storage/accuracy-report-301-hybrid-tier29.json` 和
`storage/date-gap-analysis-tier29-final.json`。

第二十八梯队把剩余 3 张未执行当前 Server 章区复核的低分样本同时交给
Paddle Mobile/Server。极浅蓝章和被表格线遮挡的绵阳服务中心章均无可靠增益；
牡丹江商店章的大模型只能在旧展开图读到后半段“器材商店”。图像检查发现不足
1% 的远端彩色噪点把真实约 `473×474` 的圆章主体拉成约 `741×478`，导致极坐标
圆心偏移。新增稳健边界只有在原始章色框明显非圆、裁掉 0.5% 坐标尾部后恢复
近正方形且面积显著缩小时才启用。生产规则仅限 macOS Hybrid、低分纯商店组织
名称和最佳非矩形章区；稳健展开的 Server 分带只供审计，必须与 Mobile 分带
共同包含要求中的同一长后缀才可参与匹配，并只保存双方实际观察到的后缀，不从
签章要求补写缺失地名。`7306175811.jpg` 真实重跑输出“江市万邦通讯器材商店”，
字段 10/10、商品 9/9、日期和印章均可靠匹配，整单自动通过。原章、稳健展开和
三张独立分带均在界面展示并通过 HTTP 契约；Excel 的 16 列、中文、文本订单号、
真实日期/时间、百分比、状态着色、双工作表和零公式错误均已独立验证。Windows
仍回退单一 Paddle 并保持待复核策略。报告见
`storage/seal-unaudited-three-probe-tier28.json`、
`storage/seal-robust-bounds-probe-tier28.json`、
`storage/e2e-seal-robust-round-bounds-tier28.json`、
`storage/accuracy-report-301-hybrid-tier28.json` 和
`storage/seal-gap-analysis-tier28-final.json`。

第二十七梯队没有整体放宽“完整近似公司冲突”的硬阻断。对 15 张冲突样本及
已知不匹配负例运行 Paddle Mobile 独立章区分带矩阵后，仅
`7311558827.jpg` 的矩形章横向第二带完整读到“郑州广利达电子技术有限公司”，
第三带读到同章区“业务受理”。新规则只允许 macOS Hybrid 在 Mobile 独立分带
完整命中要求中的法定公司、Mobile 没有另一完整公司、且现有同章区也有明确
“业务受理”时解决常规模型的单字公司冲突；输出只保留实际观察到的“公司名 +
业务受理”，不会从签章要求抄入未识别的“专用章”。真实重跑后该样本印章
100% 可靠匹配、整单自动通过；纯 Paddle/Windows 单模型仍保持冲突并进入人工
复核。三张分带图、逐带 OCR 和采用说明均保存到数据库并在界面展示。报告见
`storage/seal-conflict-mobile-band-region-matrix-tier27.json`、
`storage/e2e-seal-mobile-rectangular-conflict-resolution-tier27.json`、
`storage/accuracy-report-301-hybrid-tier27.json` 和
`storage/seal-gap-analysis-tier27-final.json`。

第二十六梯队发现两个强公司候选的大模型其实已在同一客户章区分别读出完整
公司名和完整章型，只是旧逻辑没有安全重组。新规则必须同时具备独立完整法定
公司名、独立完整特定章型；要求含 1–3 位编号时还必须有完全相同的独立编号。
公司名后只允许跟 6–16 位纯数字章码，分支文字、缺字章型和完整近似公司冲突
均禁止重组。`7302778200.jpg` 重组为“北京华康君泰贸易有限公司售后业务
专用章”并自动通过；`7275201025.jpg` 输出干净的“云南邮维科技有限公司检测
专用章3”，但日期仍未识别，所以整单继续待复核。全量扫描另有两张片段齐全
但存在完整近似公司冲突，保持硬阻断。报告见
`storage/e2e-seal-exact-fragment-reconstruction-tier26.json`、
`storage/e2e-seal-local-fragment-clean-display-tier26.json`、
`storage/accuracy-report-301-hybrid-tier26.json` 和
`storage/seal-gap-analysis-tier26-final.json`。

第二十五梯队只对“签章要求是纯公司名、常规模型已有 72%–80% 非冲突强公司
证据”的最佳圆章候选，把三条圆周展开带独立交给 Paddle Server 大模型。
`7286691342.jpg` 因此从“湖南和联电子科”补到“湖南和联电子科技 + 公司”，
由 73.7% 待复核提升为 80.0% 可靠匹配；两张已知不匹配对照均未误通过。
原章、整张展开图和三张独立分带图都写入数据库并在复核界面展示。其手写日期
仍只有 67% 且可能比要求日期早一天，所以整单继续进入待复核。探针、真实回归、
全量报表和剩余缺口分别见 `storage/seal-unwrapped-band-probe-tier25.json`、
`storage/e2e-seal-unwrapped-band-tier25.json`、
`storage/accuracy-report-301-hybrid-tier25.json` 和
`storage/seal-gap-analysis-tier25-final.json`。

日期差距报表现保留严格的“实际年份与要求年份不同”证据。当前 126 张不可靠
日期中，60 张保存过真实日期候选，42 张还同时存在冲突日期，21 张仍没有任何
可解析候选。第二梯队 6 张旧中间图样本全部处理成功、字段 `60/60`、商品
`54/54`，但没有新增可靠日期。Server 大模型对
`7284734613.jpg` 的紧裁和宽裁均读出 `2024-04-26`；系统仅以 35% 人工证据
保存，并因其早于 `2025-04-23` 制单日期而拒绝自动判定。相关结果见
`storage/e2e-date-legacy-preprocessing-tier2.json`、
`storage/e2e-date-cross-year-audit-7284734613.json` 和
`storage/date-gap-analysis-301-hybrid.json`。

日期冲突现进一步拆分为：21 张单引擎或残缺证据冲突、9 张完整日期相互冲突、
2 张跨年份冲突、4 张多引擎多日期冲突、5 张跨引擎真值与单引擎干扰冲突。
第三梯队 6/6 处理成功、字段 `60/60`、商品 `54/54`。该批一度让
`7295149808.jpg` 以 Mobile/Server 同日证据成为可靠日期，但审计同时发现同一
区域多次读到 7月2日；现行规则只要存在任何其他可解析日期，就拒绝这类同裁剪
月日一致自动放行。真实单张复跑后候选仍显示为 `2025-07-03`，置信度 68%，
状态安全回到“待复核”。报告见
`storage/e2e-date-cross-engine-conflict-tier3.json` 和
`storage/e2e-date-conflict-safe-7295149808.json`。

第四梯队对 6 张“字迹清楚但无候选”的当前中间图比较三倍放大、自动对比、
CLAHE、Otsu 和自适应二值化。只有“去表格线图三倍放大 + Server”在两张样本
形成有用候选；生产流程现把该处理图加入复核界面，并把单模型证据固定封顶为
25%。真实批次 2/2 成功，`7275944177.jpg` 显示 `2025-03-02`，
`7302125549.jpg` 显示 `2025-08-09`，两张均继续待复核。日期值由
202/301 提升到 204/301，但可靠覆盖保持 175/301。无可解析候选由31张降到
29张。报告见 `storage/date-preprocessing-probe-tier4-combined.json` 和
`storage/e2e-date-upscaled-audit-tier4.json`。

第五梯队用当前 Hybrid 流程重新处理 6 张旧版无候选困难样本，任务 6/6 成功、
字段 `60/60`、商品 `54/54`、中间图契约无缺失。Server 三倍放大候选虽然读到
`202年3月`、`207530`、`202年月日`、`202年8月0`、`1月5日` 等残缺或冲突
文本，但没有任何一条达到自动日期门槛，6 张均保持待复核。月份安全规则现会
拒绝把 OCR 明确读到的其他月份替换成要求到货月份；例如 `7302174290.jpg` 的
`1月5日` 不会被补成 `2025-08-05`，机器日期保持空白。该梯队结束时 126 张
不可靠日期中有 55 张真实日期候选、41 张冲突、28 张无可解析候选。批次和
安全单测报告见
`storage/e2e-date-legacy-upscaled-tier5-final.json`、
`storage/e2e-date-server-audit-month-safety-7302174290.json` 和
`storage/date-gap-analysis-301-hybrid.json`。

第六梯队从剩余无候选样本中挑选 6 张人工可见笔迹，比较 Mobile/Server 在
原日期行、去章色行、去表格线行上的三倍放大、自动对比、CLAHE、Otsu 和自适应
二值化。普通解析没有安全新增，但 `7324045142.jpg` 的 Mobile 在灰度自动对比
图上读到 `2025年1218日`，完整保留八位日期数字，仅漏掉“月”分隔字。生产流程
现保存并展示该自动对比图，只解析带终止“日”的合法 `YYYYMMDD`，不从要求日期
补任何年月日，证据置信度封顶且继续待复核。六张真实批次 6/6 成功、字段
`60/60`、商品 `54/54`，其余五张没有生成错误日期。日期值提升到 205/301，
可靠覆盖不变；当前真值候选 56 张、冲突 41 张、无可解析候选 26 张。报告见
`storage/date-preprocessing-probe-tier6.json` 和
`storage/e2e-date-compact-autocontrast-tier6.json`。

第七梯队继续审计 6 张红/蓝章覆盖日期样本。逐像素取 RGB 最大通道以压低彩色
印章、以及在模板匹配日期行后向下扩展裁剪，都没有形成可稳定复现的真值候选。
扩展窗口的 Server 整行检测曾在探针图上把 `7305677830.jpg` 读成 `08月28`，
但同一真实流程的宽/紧裁分别读成 `08月2` 和 `08月29`；月份/日期完整性与冲突
安全规则均正确拒绝。该候选已从生产流程移除，仅保留实验图和报告供后续模型
比较。稳定版本重跑后该样本表单 10/10、商品 9/9、印章 84.7% 可靠匹配，日期
仍为空并进入待复核；全量指标不变。报告见
`storage/date-color-channel-probe-tier7.json`、
`storage/date-template-padding-probe-tier7.json`、
`storage/date-template-padding-detection-probe-tier7.json` 和
`storage/e2e-date-color-probe-final-7305677830.json`。

第八梯队不再扩大整行裁剪，而是利用固定 `20 年 月 日` 模板分别生成“完整年份”
与“月日联合”槽位原图和最大通道增强图。只有 Mobile/Server 整行识别对明确月日
一致，且 Mobile/Server 检测识别又对完整四位年份一致时，才组合一个最高 25%
的人工候选；年月日均来自 OCR，不从要求到货日期补值，同一物理日期行也绝不
自动放行。13 张当前无候选样本统一探针只形成一个候选：`7305364816.jpg` 的
四路证据分别为 `2025年`、`2025年8`、`年8月27日`、`年8月27日`。真实重跑
恢复 `2025-08-27`，综合置信度 45%、继续待复核；表单 10/10、商品 9/9、印章
79.4% 可靠匹配。日期值升至 206/301，无候选降至 25，可靠覆盖保持 175/301。
报告见 `storage/date-slot-probe-tier8-detection.json` 和
`storage/e2e-date-slot-tier8-7305364816.json`。

第九梯队先对 11 张具备旧版日期行的无候选样本运行同一槽位探针，又增加“纯日
数字槽”和“最大通道去横线”对照；两种处理均未形成完整日数字。另下载并缓存
`en_PP-OCRv5_mobile_rec` 到 `/Volumes/SN770/OCR`，它在 11 张日期数字槽上真值
命中为 0，常把变形数字读成 `D/A/E`，因此只保留实验报告、不进入生产路由。
随后用当前 Hybrid 完整重跑 6 张人工可见旧样本：任务 6/6 成功、字段 60/60、
商品 54/54、待复核 6、日期新增 0；所有样本获得当前日期/槽位中间图，两个早于
制单日期的错误候选被安全拒绝。印章严格及可靠结论净增 1，达到 186/301 与
185/301，185 个可靠结论全部正确。报告见
`storage/date-slot-probe-tier9-legacy.json`、
`storage/date-digit-english-model-probe-tier9.json` 和
`storage/e2e-date-legacy-current-tier9.json`。

第十梯队针对两位手写日经常只剩个位的问题，比较了原色白底、整行先去章色白底
和“先裁月日槽位、再取 RGB 最大通道并自动对比”的白底标准化图。24 张原本无
候选样本的正式规则审计只形成 2 个低置信度建议，2/2 命中真值、0 误候选：
`7304747896.jpg` 为 `2025-08-24`，`7317213639.jpg` 为 `2025-11-11`。
生产路径只在既有流程完全没有任何可解析日期证据时启用；Paddle Mobile/Server
必须对白底年份槽位的完整四位年份一致，macOS Vision 才可提供一个自包含月日。
候选置信度封顶并强制标黄、待人工复核，Windows 或 Vision 不可用时自动跳过。
真实端到端批次 2/2 成功、字段 20/20、商品单元格 18/18、两张日期均正确检出，
但可靠日期新增 0；日期值因此升至 208/301，无候选降至 22。界面新增年份白底图
和月日裁后去章色白底图。报告见 `storage/date-vision-crop-first-probe-tier10.json`
和 `storage/e2e-date-vision-slot-tier10.json`，Excel 为
`outputs/三星回单-日期Vision槽位第十梯队验收.xlsx`。

第十一梯队继续审计剩余 22 张无候选样本，对原日期行三倍放大、原色白底、灰度
自动对比、最大通道白底、去章色和去表格线六种视图分别运行四种 Vision 语言
配置。跨视图严格共识没有候选；原色白底视图在三种中文相关配置下形成 1 个稳定
真值、0 个错误，最大通道视图则把另一张的 `24` 丢成 `04`，因此生产流程明确
排除最大通道整行候选。`7276798821.jpg` 最终恢复 `2025-03-07`，综合置信度
40%、继续待复核；`7275201025.jpg` 没有输出错误的 `2025-02-04`。当已有候选
全部早于制单/运单出库日期时，系统仍允许原色白底提供独立人工建议，同时把
`2025-03-02`、原始 OCR“2025年3月2”和拒绝原因保存到结果并在复核界面显示。
两张批次 2/2 成功、字段 20/20、商品单元格 18/18、0 失败、2 待复核，日期值
升至 209/301，可靠日期保持 175/301。最新真实重跑中一张历史印章证据不足，
系统安全降为待复核，因此可靠印章为 184/301 且 184/184 正确。报告见
`storage/date-vision-config-probe-tier11.json`、
`storage/e2e-date-vision-white-line-tier11-final.json` 和
`storage/e2e-date-vision-white-line-tier11-audit.json`；Excel 为
`outputs/三星回单-日期Vision白底第十一梯队最终验收.xlsx`。

印章第十二梯队用 6 张“章区有效但未经过当前 Server 路由”的样本做真实复跑，
6/6 成功、日期 6/6、商品单元格 54/54，所有证据不足样本均继续待复核。审计
发现“服务总汇”和无编号“客户服务中心”这两类长组织要求没有进入大模型；现仅在
常规模型相似度至少 50%、组织名至少 10 字且仍不可靠时，把颜色隔离图和圆章展开
图交给 PP-OCRv5 Server，最终阈值和公司冲突硬阻断不变。三张受影响圆章重跑后，
`7293844749.jpg` 从待复核提升为 91.7% 可靠匹配；`7281987478.jpg` 与
`7306187162.jpg` 因公司主体/服务中心仍残缺继续待复核。另修复郑州广利达两种
并存表单模板：字段原文“业务受理”不再被签章主数据改写为“业务受理专用章”，
章面匹配仍独立处理。最新差距为 116 张不可靠印章，其中 78 张已执行 Server 章色
复核、38 张尚不满足安全路由；报告见
`storage/e2e-seal-server-unaudited-tier12.json`、
`storage/e2e-seal-service-org-server-tier12.json`、
`storage/e2e-business-acceptance-boundary-tier12.json` 和
`storage/seal-gap-analysis-301-hybrid.json`。Excel
`outputs/三星回单-服务组织章Server第十二梯队验收.xlsx` 已验证中文、长订单号、
日期/时间、百分比、状态样式、双工作表及公式错误扫描。

印章第十三梯队把本地已经读到完整“业务受理”的圆章纳入一次颜色隔离 Server
复核。真实 4 张正例加 1 张已知不匹配负例批次 5/5 成功、0 失败；大模型没有
凭通用章型强行放行任何样本。负例测试同时发现“完整公司 + 章型尾缀”会绕过旧
公司冲突长度限制，现改为分别限制法定公司名前后的短文本，一字不同的完整公司
即使带“业务受理（2）”也硬阻断。`7279511621.jpg` 的同一颜色章区由 Server
分别读到“郑州广”“利达电子技术有限公司”“业务受理专用章”；生产只在两个
独立 OCR 行完全等于公司前后段、另有业务受理文字且无完整公司冲突时，记录
“同章区公司片段重组（无字符补写）”，由待复核提升为 100% 可靠匹配。分支编号
要求也必须在章文中一致。当前可靠印章为 186/301 且 186/186 正确；115 张仍
不可靠，81 张已做 Server 章色复核、34 张尚不满足安全路由。报告见
`storage/e2e-business-acceptance-server-tier13.json`、
`storage/e2e-business-acceptance-fragment-tier13.json` 和
`storage/seal-gap-analysis-301-hybrid.json`；Excel
`outputs/三星回单-业务受理片段重组第十三梯队验收.xlsx` 已通过 16 列类型、
中文、长订单号、日期/时间、百分比、状态样式、双工作表和公式错误检查。

印章第十四梯队复跑 6 张当前安全路由候选及 1 张已知不匹配负例，批量任务
`7/7` 成功、0 失败；公司主体、章型或日期证据不足的样本仍进入待复核。站点号
现被视为业务主键：要求中的 6–12 位“站号/代码”必须在章区 OCR 中逐位出现，
少一位或错一位时，即使“三星售后”等周边文字相似度很高也不能自动通过。
`7291554401.jpg` 的真实复跑由 Vision 在独立章区读到完整
“三星售后6183342站”，因此是精确证据通过；报告同时保留其他错读候选供人工
追溯。商品真值复核还发现同一 EAN 的两张样单都印为“玄曜黑”，已修正真值并
重新识别 `7317053633.jpg`，全量商品恢复为 `2817/2817`。当前可靠印章为
`187/301` 且 `187/187` 正确；114 张仍不可靠，其中 86 张已执行 Server 章色
复核、28 张不满足当前安全路由。报告见
`storage/e2e-seal-current-route-tier14.json`、
`storage/e2e-station-exact-safety-tier14.json`、
`storage/e2e-product-truth-correction-tier14.json` 和
`storage/seal-gap-analysis-301-hybrid.json`。三份第十四梯队 Excel 已验证
16 列、中文、长订单号文本、真实日期/时间、百分比、状态样式、双工作表及零
公式错误。

印章第十五梯队对业务章、双语品牌服务章、维修章及 3 张椭圆代码章执行真实
大模型对照，批次 `6/6` 成功、商品 `54/54`。双语品牌章在同一章区形成
`SAMSUNG + 服务专用章` 严格证据，新增 1 个可靠正确印章结论；该样本日期仍
不可靠，所以整体继续待复核。业务章只重复读到“业务章”，3 张椭圆代码章均未
稳定读到带标签的 `代码：6092851`，因此没有凭错误数字放行；这些无收益路由已
从默认 Hybrid 回退，实验 OCR 和中间图继续保留。审计同时发现
`7281418465.jpg` 原单明确打印“杭州索兰科技有限公司维修专章”，现只对带
6–12 位编号的已核验模板补“用”字，无编号清晰原文不再改写；单张复跑恢复字段
`10/10`、商品 `9/9`，日期和印章证据不足时仍待复核。当前可靠印章
`188/301` 且 `188/188` 正确，113 张不可靠；可靠日期因该样本本轮只有 67%
单次证据安全降为 `174/301`，`174/174` 正确。Windows 模拟确认 Hybrid 在
Windows 自动改为 Paddle-only，日期和印章继续执行单模型待复核策略。报告见
`storage/e2e-seal-structural-route-tier15.json`、
`storage/e2e-maintenance-two-region-tier15.json`、
`storage/seal-gap-analysis-301-hybrid.json` 和
`storage/date-gap-analysis-301-hybrid.json`；第十五梯队 Excel 已通过 16 列、
文本订单号、日期/时间、百分比、状态着色、双工作表和零公式错误检查。

日期第十六梯队审计了 16 张“至少两个 OCR 引擎出现过真值”的不可靠样本：12 张
存在其他日期冲突，1 张已经在前序单张重跑中证明仍只有残缺年份，只有
`7305364816.jpg` 适合新增整行增强。对原日期行取 RGB 最大通道可压低红/蓝章、
保留三通道均为深色的黑色笔迹，再三倍放大；生产只在常规日期仍不可靠时运行，
且必须由 Mobile 与 Server 在紧裁/宽裁不同几何上分别以至少 80% 置信度读到
同一完整四位年份日期，同时所有既有和增强证据不得出现其他合法日期。该样本
真实重跑由宽裁 Mobile `81.1%` 与紧裁 Server `87.2%` 同时读到
`2025年8月27日`，日期升为 100% 可靠，整体自动通过。7/9 歧义、7月2/3冲突和
多日期冲突三类负例探针均未触发；`7295149808.jpg` 完整负例重跑仍为 68% 待
复核。新增处理图已在界面以“日期行最大通道去彩色三倍放大”展示。报告见
`storage/date-preprocessing-probe-tier16-7305364816.json`、
`storage/date-max-channel-negative-probe-tier16.json`、
`storage/e2e-date-max-channel-cross-geometry-tier16.json` 和
`storage/e2e-date-max-channel-conflict-negative-tier16.json`；两份 Excel 均通过
16 列、中文、长订单号、日期/时间、百分比、状态样式、双工作表及零公式错误检查。

印章第十七梯队先复跑两张尚未经过当前公司章 Server 路由的样本，任务 `2/2`
成功、0 失败。`7280749135.jpg` 的章区补齐“南京万泓电子有限公司第二”主体，
与要求“南京万泓电子有限公司第二分公司”形成 100% 可靠匹配，新增 1 个可靠
正确印章结论；`7290278296.jpg` 仍只读到“西安”，继续待复核。随后用 2 张蓝色
仓储部收货章、1 张业务专用章和 1 张一字不同公司负例评估低分大模型路由，
`4/4` 均正确保持待复核：蓝章缺公司主体，杭州样单把“松峰”读成“松雄”，深圳
负例把“星睿”读成“星资”。该路由没有安全增益且增加耗时，已从生产代码撤回；
完整近似公司名冲突仍硬阻断。全量刷新后可靠印章为 `189/301` 且 `189/189`
正确，112 张不可靠；其中 94 张已执行 Server 章色复核，18 张不满足安全路由。
权威数据库可靠日期仍为 `174/301`，第十六梯队的 `7305364816.jpg` 是单张验收
结果，未把局部结果冒充全量状态。报告见
`storage/e2e-seal-company-current-route-tier17.json`、
`storage/e2e-seal-reviewed-specific-route-tier17.json`、
`storage/seal-gap-analysis-301-hybrid.json` 和
`storage/date-gap-analysis-301-hybrid.json`。两份第十七梯队 Excel 均已验证
16 列、中文、文本订单号、真实日期/时间、百分比、待复核标黄、双工作表、视觉
布局及零公式错误。

印章第十八梯队把“强章色但本地 OCR 零分”与普通低分样本分开处理。生产仅在
签章要求属于服务中心、维修中心/维修部、服务总汇、授权体验店或商店等组织，
当前匹配分为 0、要求至少 8 个规范化字符、实体章色像素占比至少 6% 时，读取
一次颜色隔离 Server 图；弱章色、已有冲突文本、普通收货章和无章区页面均不
触发，最终匹配阈值与公司冲突硬阻断不变。5 张目标加 1 张已知不匹配负例真实
Hybrid 批次 `6/6` 成功、0 失败，新增青岛维修中心、太原服务总汇和牡丹江商店
3 个可靠正确结论；东城星河城只恢复“授权体验店”，蚌埠样本只恢复“技术服务
中心”，继续待复核，负例也未放行。单独 Server 主后端对 4 张困难圆章虽然提高
字符覆盖，但 `4/4` 仍按单模型安全策略待复核，证明默认 Hybrid 仍优于全页大
模型。当前可靠印章 `192/301` 且 `192/192` 正确，109 张不可靠；其中 96 张已
执行 Server 章色复核，13 张未满足安全路由。报告见
`storage/e2e-seal-server-primary-probe-tier18.json`、
`storage/e2e-seal-strong-unread-color-tier18.json`、
`storage/seal-gap-analysis-301-hybrid.json`；Excel
`outputs/三星回单-强章色零分组织章第十八梯队验收.xlsx` 已通过 16 列、中文、
文本订单号、真实日期/时间、百分比、状态着色、零公式错误与双工作表视觉检查。

印章第十九梯队先用当前 Hybrid 重跑 7 张历史无章区/低分样本。最新状态中
`7273225680.jpg`、`7301486650.jpg`、`7302125548.jpg`、`7304152280.jpg`
新增 4 个可靠正确结论；`7289834212.jpg` 与 `7304857073.jpg` 仍因公司主体或
授权结构不足待复核。安全复查发现 `7279957554.jpg` 的要求为“十堰市万盛达…”，
实体章完整读成“十堰盛瑞…”，旧逻辑只检查等长公司名而误放行。现对公司全称
长度差异 ±4 字的候选也检查完整法律后缀；不等长时须有至少 4 个变更字符才认定
冲突，因此保留既有单字 OCR 容错，同时把该真实样本稳定改为“无法判断/待复核”。
绵阳困难章另以 Server 全页主后端对照，单张约 94 秒仍只恢复“服务2”，字段
`9/10`、商品 `8/9`，所以没有扩大生产路由。全量刷新后 105 张印章不可靠样本中
99 张已做 Server 章色审计，只剩牡丹江 1 张、孝感 3 张、绵阳 1 张和深圳仓储章
1 张因证据过弱继续人工复核。

全量 Excel 验收同时修复了“历史重跑重复占行”：导出现在先按文件名保留最新
状态，再合并分页，旧运行仍完整保存在数据库复核历史中。当前 311 个物理页面
导出为 310 张逻辑回单、每张一行；`outputs/三星回单-第十九梯队全量核验.xlsx`
已验证 16 个业务列、中文、长订单号文本、真实日期/时间、百分比、待复核黄色、
双工作表可读及零公式错误。报告见
`storage/e2e-seal-current-rerun-tier19.json`、
`storage/e2e-seal-company-length-conflict-tier19.json`、
`storage/e2e-seal-server-primary-probe-tier19-7276798821.json`、
`storage/accuracy-report-301-hybrid.json`、`storage/seal-gap-analysis-301-hybrid.json`
和 `storage/date-gap-analysis-301-hybrid.json`。

印章第二十梯队专门处理浅色“`三星电子 + 地名 + 服务中心`”圆章。规则只决定
是否调用颜色隔离 PP-OCRv5 Server，并不降低最终匹配门槛；只有同一章区的聚合
证据同时包含三星电子品牌、要求中的地名以及“服务中心”结构才可靠通过，错误
城市、缺品牌或弱章色继续待复核。`7281100567.jpg` 从旋转章色图恢复“星电”、
从圆章展开图恢复“考感/中心/服务”，按已审计的“孝感→考感”单字 OCR 混淆形成
90% 可靠印章，但日期候选早于制单日期，整单仍待复核；`7284653893.jpg` 的三组
独立证据为“孝感”“星电子”“服务中心”，印章与日期均可靠匹配，整单自动通过。
`7303604209.jpg` 的大模型仍无法恢复完整章文，继续待复核。全量刷新后印章严格
结论为 `199/301`，可靠自动印章 `198/301` 且 `198/198` 正确；日期可靠口径保持
`175/301` 且 `175/175` 正确。报告见
`storage/e2e-seal-fragmented-local-service-tier20-7281100567.json`、
`storage/e2e-seal-fragmented-local-service-tier20-7284653893-v2.json`、
`storage/accuracy-report-301-hybrid-tier20.json`、
`storage/seal-gap-analysis-tier20.json` 和 `storage/date-gap-analysis-tier20.json`。

第二十梯队同时把“当前工作台”和“历史运行”彻底分开：记录列表、准确率报表和
Excel 均先按当前选择的 OCR 后端限定结果流，再按文件名保留最新结果；
`/api/history` 单独保留全部旧运行。Paddle Server 对照实验因此不会遮住同名
文件的生产 Hybrid 结果，切换后端时记录与报表也会同步刷新。当前 Hybrid 为
310 张逻辑回单；Server 对照结果为独立的 17 张，不再混入 Hybrid 导出。

日期第二十一梯队先在全部 9 张“机器日期为空、唯一完整日期仅来自 Server”的
真值样本上做统一审计：单一 Server 候选只有 `4/9` 正确，Server 跨紧/宽几何
一致也只有 `2/8` 正确，因此两种规则都没有上线。新增路径只接受唯一 Server
完整日期与 Mobile 在灰度 Otsu 二值三倍放大图上的严格同日，并拒绝任何既有
合法日期冲突；9 张审计中形成 2 个一致、`2/2` 正确、0 错误，其中
`7284734613.jpg` 原本已有候选，`7288719420.jpg` 新增 `2025-05-20`。证据仍
来自同一物理日期行，最终置信度封顶 45%、`reliable=false` 并保持待人工复核；
已知负例 `7267503639.jpg` 的 Server 误读 `2025-01-04` 与 Mobile 不一致，日期
继续为空。界面新增“Otsu 二值三倍放大”中间图及每条 OCR 接受/拒绝说明。全量
日期值提升为 `211/301`，可靠日期仍为 `175/301` 且全部正确；印章指标保持
`199/301` 严格、`198/301` 可靠且全部正确。报告见
`storage/date-preprocessing-probe-tier21.json`、
`storage/date-vision-config-probe-tier21.json`、
`storage/e2e-date-otsu-cross-model-tier21-final.json` 和
`storage/accuracy-report-301-hybrid.json`。

第二十一梯队的端到端中间图契约现强制每个日期裁剪同时具备区域原图、处理后图、
日期行原图、去表格线图和 Otsu 二值三倍放大图；每个印章裁剪继续要求原章及
增强/展开图。真实样本 `7288719420.jpg` 重跑后字段 `10/10`、商品 `9/9`、
日期 `2025-05-20` 正确检出但置信度仅 45% 并保持待复核；印章公司或章型证据
不足，也保持“无法判断”。中间图缺失、HTTP 访问失败和契约失败均为 0，Excel
接口返回 200。Paddle 与 Excel 的原生运行时环境已隔离：受限 macOS 启动时
Paddle 禁用 OpenMP 共享内存，Excel `artifact-tool` 子进程不会继承该专属设置。
报告见 `storage/e2e-date-otsu-artifact-contract-tier21.json`。

第二十二梯队把 6 张旧版日期证据样本和仅剩 3 张尚未执行当前 Server 章色复核
的样本统一重跑：任务 `9/9` 成功、字段 `90/90`、商品单元格 `81/81`、中间图
缺失/HTTP 失败/契约失败均为 0。6 张旧日期样本全部获得当前 Otsu、去线、白底
和槽位图，但没有新增可安全日期；3 张印章在安全章色衍生图上额外比较 Mobile
与 Server，Server 最好只把牡丹江商店章恢复到“器材商店”（50%），另外两张
没有有效主体文字，因此生产路由不放宽。`7305098427.jpg` 虽肉眼可见日期，
Mobile/Server 只稳定读到 8 月和日首位 2，Vision 多配置及英文数字模型均未
恢复末位 5，系统没有从要求日期补字。`7276798821.jpg` 的旧 Vision 日期候选
在本轮未复现，权威低置信度日期值按当前证据由 211 降为 210；可靠日期和印章
覆盖及准确率不变。专项探针可用 `--skip-excel` 避免生成无意义工作簿，正式验收
仍默认导出。报告见 `storage/e2e-date-legacy-seal-unaudited-tier22.json`、
`storage/seal-mobile-server-probe-tier22.json`、
`storage/date-slot-probe-tier22-7305098427.json`、
`storage/date-vision-probe-tier22-7305098427.json` 和
`storage/date-digit-probe-tier22-7305098427.json`。

第二十三梯队用同一批已标注回单比较 Mobile 与 Server 大模型。最初 6 张标准
样单中，Server 的商品单元格由 Mobile 的 `78/81` 提升为 `81/81`，但字段、
日期和印章没有同步提升，历史实测平均耗时也明显更高，因此没有把 Server 改成
整批默认。随后增加 1 张 5 行标准回单和一组 `33 + 34` 行双页密集商品表：两种
Hybrid 后端均正确分类 `3/3` 个页面、找齐 `5/5`、`33/33`、`34/34` 行，并正确
合并为 1 张 67 行逻辑回单。针对 Server 在物料编号中系统性混淆 `O/0`、在合法
EAN 后粘连汉字的问题，解析器现仅对具有三星物料号结构和已知市场后缀的代码
执行 `O→0`，并只在唯一 13 位数字通过 EAN-13 校验时清理边缘噪声；未知代码不
改写。真实重跑后 Server 与已核验 Hybrid 参考结果的商品逐单元格一致率由
`616/648`（95.06%）提升到 `641/648`（98.92%），仍有 7 个商品描述/跨行粘连
差异，因此该数字明确标为“参考后端一致率”，不冒充人工真值准确率。界面现在
按平台标注推荐后端：macOS 默认 Hybrid（Mobile 表单/商品 + Vision 日期/印章），
Windows 默认 Paddle Mobile；Server/Hybrid Server 用于密集表格对照或指定批次
重试，并显示耗时、内存和人工核对提示。报告见
`storage/backend-comparison-dense-tier23-final.json`、
`storage/backend-hybrid-server-dense-tier23-v2.json` 和
`storage/backend-hybrid-dense-tier23.json`。

日期第二十四梯队先对 6 张无冲突完整年份候选和 8 张同类误读负例执行
Mobile/Server/Vision 对照。Vision 在 14 张 × 6 种视图 × 4 种语言配置上没有
形成严格日期；Server 跨几何、多预处理多数票也被负例否定：例如
`7274693664.jpg` 的真实日期为 `2025-02-21`，Mobile 与 Server 却会在两种
裁剪和多种处理图上高频一致误读为 `2025-02-02`，所以没有上线单模型多数票。
新增规则只接受更窄的自包含结构：Mobile 必须在最大通道图保留完整八位
`YYYYMMDD` 和终止“日”（不从要求日期补任何数字），Server 必须在另一几何
裁剪读到同日严格四位年份日期，双方置信度至少 80%，且全部既有/新增证据不得
出现其他合法日期。14 张正负例中只命中 `7324045142.jpg`：Mobile 宽裁原文
`2025年1218日`（85.2%），Server 紧裁为 `2025年12月18日`（89.1%）。真实
Hybrid 重跑后该日期由 45% 人工候选提升为 100% 可靠匹配；日期自动可靠覆盖由
`175/301` 提升为 `176/301`，`176/176` 正确。整单仍因重庆公司章只有部分文字
而待复核，没有强行通过。日期原图、最大通道处理图、两模型原文和接受说明均在
界面保存。Excel 已验证 16 列、文本订单号、真实日期/时间、百分比、待复核黄色、
双工作表及零公式错误。报告见
`storage/date-vision-probe-tier24.json`、
`storage/date-preprocessing-probe-tier24.json`、
`storage/date-vision-slot-tier24.json`、
`storage/e2e-date-compact-cross-model-tier24.json`、
`storage/date-gap-analysis-tier24.json` 和
`storage/accuracy-report-301-hybrid-tier24.json`。

## 指定解释器

本项目按要求使用以下 PyCharm Conda 解释器：

```text
/Users/zhuyihao/anaconda3/bin/python
```

如果环境需要补齐依赖，在 PyCharm Terminal 中运行：

```bash
/Users/zhuyihao/anaconda3/bin/python -m pip install -r requirements.txt
```

## 启动

```bash
/Users/zhuyihao/anaconda3/bin/python app.py
```

浏览器打开 [http://127.0.0.1:5001](http://127.0.0.1:5001)。首页会按 `ground_truth.json` 动态列出当前全部已标注标准回单（目前 301 张），也可以一次拖入多图或选择整个文件夹。另有 10 个委托书/分页页面记录在 `document_ground_truth.json` 中，数据目录 311 张图片已全部按适用评测口径分类。

识别结果保存于 `storage/results.db`。上传图片、标注预览、中间证据图和导出文件分别位于 `storage/uploads/`、`storage/previews/`、`storage/artifacts/` 和 `storage/exports/`。macOS 默认使用“混合 OCR（Paddle Mobile + Vision）”：表单与商品走 Paddle，手写日期与印章走 Vision；未安装 Paddle 时自动回退到系统 Vision。Windows 默认使用 PaddleOCR Mobile。两种平台都可以在上传区选择已安装且适用的其他后端。

## OCR 引擎与模型目录

PaddleOCR 提供两档：

- Mobile：`PP-OCRv5_mobile_det` + `PP-OCRv5_mobile_rec`，适合日常批量处理；
- Server（大模型）：`PP-OCRv5_server_det` + `PP-OCRv5_server_rec`。它保留为用户可选和困难印章/日期的受限审计兜底，不替换默认 Mobile。最新困难样本中 Mobile/Server 都得到 10/10 字段、9/9 商品单元格和相同的安全日期结论，但 Server 用时 119.88 秒、Mobile 59.14 秒；67 行双页长表中 Server 1400/1800 像素配置与已核验 Mobile 商品真值的一致率分别为 94.69%/95.52%，主要错误是物料号 `0/O` 混淆，未显示整体收益。日期行专项中大模型能稳定补出个别完整年份日期，也会把 14 日读成 4 日；因此不同年份的 Server 结果必须在紧/宽裁剪一致后才能作为最高 35% 的人工证据，不能单模型自动放行。

混合模式也提供两档：

- `hybrid`：表单/商品使用 Paddle Mobile，日期/印章使用 macOS Vision；
- `hybrid_server`：表单/商品使用 Paddle Server，日期/印章使用 macOS Vision；
- Windows/Linux 没有 Vision 时不会报错，而是自动将日期/印章回退到 Paddle Mobile。每条结果会记录页面、日期和印章三个阶段实际使用的后端，便于复现与审计。

本机检测到 `/Volumes/SN770/OCR/` 时会自动将 PaddleX 模型缓存写到该目录，避免占用系统盘；其他机器可以配置：

```bash
export PADDLE_MODEL_HOME="/你的大容量磁盘/OCR"
/Users/zhuyihao/anaconda3/bin/python app.py
```

Windows PowerShell 示例：

```powershell
$env:PADDLE_MODEL_HOME = "D:\OCR"
python app.py
```

Server 整页默认在最长边超过 1400 像素时生成临时等比缩放副本，避免高分辨率扫描件把本地进程以退出码 137 终止；所有框坐标仍按归一化比例映射，原图和中间证据图不被覆盖。结果会记录实际上限。内存充足且更看重小字分辨率时可显式调高，例如：

```bash
export PADDLE_SERVER_MAX_SIDE=1800
/Users/zhuyihao/anaconda3/bin/python app.py
```

```powershell
$env:PADDLE_SERVER_MAX_SIDE = "1800"
python app.py
```

本次长表测试中 1800 比 1400 多匹配 5 个商品单元格，但平均耗时从 76.48 秒增加到 154.94 秒，且仍不及 Mobile；因此 1400 保持为低内存安全默认值。

Windows 首次运行建议先执行专项自检；它会核对当前解释器、Paddle/PaddleOCR
版本、默认后端、阶段路由和模型目录写权限：

```powershell
python -m tools.windows_smoke --output storage\windows-smoke.json
python -m pytest tests/test_ocr_backends.py tests/test_platform_runtime.py -q
```

也可以使用项目自带的启动脚本，一次设置模型目录、Node 路径并在自检通过后启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start_windows.ps1 `
  -Python "C:\Users\你的用户名\anaconda3\python.exe" `
  -ModelHome "D:\OCR" `
  -Node "C:\Program Files\nodejs\node.exe"
```

Windows 没有 Vision，默认使用 Paddle Mobile。选择 `hybrid` 时页面、日期和
印章都会落到同一 Mobile 模型，因此原图/去色/日期行等不同预处理只作为候选，
不当作独立模型印证；日期和印章置信度最高显示 68%，自动进入人工复核。若机器
内存足够，可选择 `hybrid_server`，由 Server 识别整页、Mobile 识别日期/印章，
但仍需满足跨模型严格规则。界面会显示当前路由和安全策略。

Excel 导出还需要 Node.js。程序会依次读取 `WORKSPACE_NODE` 和系统 `PATH`；
Windows 可在 Node 未加入 PATH 时显式设置：

```powershell
$env:WORKSPACE_NODE = "C:\Program Files\nodejs\node.exe"
```

缺少 Node 时接口会返回明确的 503 提示，不会生成损坏的空工作簿。
Paddle 的 OpenMP 兼容设置不会传入 Excel 子进程，避免 OCR 成功后导出阶段受
另一套原生运行时影响。

也可以设置 `OCR_BACKEND=vision`、`OCR_BACKEND=paddle`、`OCR_BACKEND=paddle_server`、`OCR_BACKEND=hybrid` 或 `OCR_BACKEND=hybrid_server` 改变默认引擎。任务和每张回单都会保存实际使用的逻辑后端与阶段路由；重新识别时以界面当前选择为准。

应用启动时会关闭上次进程异常退出遗留的批次：状态改为“已中断”，未处理数量
计入失败和待复核，并继续排除在正式准确率分母之外，避免 Server 大模型被系统
终止后任务永久停留在“处理中”。

## 人工复核

在记录列表点击“复核”后可以：

- 修改任意识别字段、实际收货日期和印章内容；
- 切换查看“手写日期区域”和“印章区域”的所有处理阶段；
- 填写错误类型和人工备注；
- 选择“确认通过”“确认不通过”或“保存并待复核”；
- “确认通过”必须同时具备已填写、可靠且匹配的实际日期与印章；单张和批量接口都会拒绝把证据不完整的记录强行改为通过，界面显示具体拒绝原因；
- 回看每次修改前后的差异及时间。
- 在“确认通过/确认不通过”时选择“同时保存为评测真值”，将人工确认后的 10 个关键字段、逐列商品、实际日期和印章结论纳入准确率评测。

准确率报表始终使用数据库中不可变的首次机器识别值计算，因此人工修改不会虚增 OCR 准确率。报表主卡片跟随上传区当前选择的 OCR 后端，并显示所用后端与已覆盖真值数；不同后端只在“后端效果对比”中并列展示，不再混入同一个准确率口径。处理中批次不会提前进入正式准确率分母；只有整批完成后才整体切换到该批次的结果。
真值保存前会检查关键字段、实际日期和商品列是否完整；`数据/ground_truth.json` 使用临时文件原子替换，新增/更新真值的时间、来源结果、前后值和备注另存于 SQLite 的 `ground_truth_history`，页面质量概览会显示已标注图片占“数据”目录图片总数的比例。

## 印章 API（可选）

文档规定 `/api/v1/recognize` 和 `/api/v1/detect` 必须携带 `X-API-Key`。取得密钥后，在启动前配置：

```bash
export SEAL_API_KEY="你的密钥"
/Users/zhuyihao/anaconda3/bin/python app.py
```

也可以配置 `SEAL_API_URL` 和 `SEAL_API_TIMEOUT`。默认选择“本地印章识别”，
不会上传图片；只有用户主动选择“本地 + 清瞳印章 API”才会调用远程接口。
未配置密钥不会报错，清瞳选项会明确标为不可用。

## 标注样单验收（当前 301 张标准回单 + 10 个文档路由页面）

```bash
/Users/zhuyihao/anaconda3/bin/python -m tools.e2e_six --backend vision
/Users/zhuyihao/anaconda3/bin/python -m tools.e2e_six --backend paddle
/Users/zhuyihao/anaconda3/bin/python -m tools.e2e_six --backend paddle_server
/Users/zhuyihao/anaconda3/bin/python -m tools.e2e_six --backend hybrid
/Users/zhuyihao/anaconda3/bin/python -m tools.e2e_six --backend hybrid_server

# 原始六张基线 + 人工修改、不可变机器原值、历史、筛选、证据图、批量安全规则和 Excel
/Users/zhuyihao/anaconda3/bin/python -m tools.e2e_six \
  --backend hybrid --limit 6 --exercise-review \
  --report-path storage/e2e-six-hybrid-acceptance.json \
  --excel-path outputs/三星回单-六张端到端验收.xlsx
```

不启动 Web 服务也可以生成与页面相同口径的离线报表：

```bash
/Users/zhuyihao/anaconda3/bin/python -m tools.generate_report \
  --backend hybrid --output storage/accuracy-report-301-hybrid.json
```

下段保留各次回归和规则演进的历史累计数字；若与文首“最新验收快照”不同，
以文首及 `storage/accuracy-report-301-hybrid.json` 为准。

详细结果写入 `storage/e2e-six-<backend>-report.json`（文件名为兼容最初六张基准而保留），Excel 写入 `storage/exports/` 下对应文件。当前 301 张标准回单人工真值位于 `数据/ground_truth.json`，共含 313 行、2817 个商品单元格；除关键字段、日期和印章结论外，还按 9 个商品列保存 `product_rows`。当前数据库最新 Hybrid 机器原始口径为：字段 3010/3010（100%）、商品单元格 2817/2817（100%）、商品整行 313/313（100%）、日期 202/301（67.11%；有日期样本检出 204/300，68.00%；日期栏为空样本 1/1 正确保持未识别）、印章严格结论 183/301（60.80%）；日期 175 个和印章 182 个可靠自动结论均正确，自动决策准确率都是 100%，覆盖率分别为 58.14% 和 60.47%。日期增量回归扫描了 602 个紧/宽去线日期行，只对 3 个可能改变结果的候选做完整页面重跑；明细保存在 `storage/date-table-clean-regression-summary.json`。新一轮日期差距分析又审计了剩余不可靠样本：126 张中有 51 张能在已保存 OCR 证据里找到真值候选，38 张还同时存在其他冲突日期，因此没有整体放宽阈值；明细保存在 `storage/date-gap-analysis-301-hybrid.json`。第一条窄规则只接受 Mobile 完整日期与 Server 在紧/宽另一裁剪上的同一严格四位年份日期，日期可靠覆盖由 172 提升到 174；新增第二条只处理不匹配日期：Mobile 在一个裁剪明确读到唯一月日、Server 在另一裁剪读到同月日的完整四位年份日期，`7285643413.jpg` 因此可靠识别为 `2025-04-29`，覆盖提升到 175。同一裁剪重复识别、多个 Mobile 月日冲突和 7/9 歧义仍不放行。印章差距报表当前把 119 张未可靠样本分为 8 张“公司名较强但章类型不足”、11 张完整近似公司名冲突、42 张中等相似度、39 张低相似度、11 张有章区无文字和 8 张未检测章区，避免把不同问题混成一个阈值；明细见 `storage/seal-gap-analysis-301-hybrid.json`。`7281100556.jpg` 的“签章要求”标签被客户章完全遮挡，但固定签收说明、实收/拒收数量和签收表格多个锚点仍证明页脚存在；新增多锚点规则恢复日期/印章全套中间图及 96% 可靠印章匹配，同时对 301 份已保存 OCR 扫描只影响这一张。受限 Server 章色复核又在 `7303331190.jpg` 补齐售后服务章类型并形成可靠匹配；同批 `7302778200.jpg` 仍只有公司名、缺少章类型，继续待复核。后续四种强公司名章型探针中，`7293949384.jpg` 与 `7278526751.jpg` 分别补齐长文本售后服务章和仓储部收货章；`7305680939.jpg` 仍缺“售后业务专用章”，继续待复核。`7303331194.jpg` 的常规模型同时给出“济南新字航”错字和缺首字的“南新宇航”，只有颜色隔离 Server 又读出完整“济南新宇航科技发展有限公司”及“业务专用”时，才把末尾漏掉的通用“章”字作为可靠章类型证据；`大连北华/大连允华` 这类中间关键字冲突仍禁止 Server 覆盖。对 12 张尚未执行当前受限 Server 复核的中高证据候选做两梯队真实复跑后，云南检测章、安徽公司章、哈尔滨维修编号章、太原服务总汇、湖南三星服务中心和成都京东维修章等共新增 8 个可靠结论；真值为不匹配的 `7286691343.jpg` 没有被同模板碎片误放行，另有 3 张仍因公司名或章型不足待复核。批次还发现业务边界清理会误删“检测专用章（3）”和维修章 12 位编号，现只保留结构化括号序号及 6–12 位标识，字段全量仍为 100%。第三、第四梯队继续对 12 张尚未执行当前受限 Server 复核的中高证据候选做真实复跑，又新增 6 个可靠印章结论：云南邮维检测章、哈尔滨晨光维修编号章、三星电子维修中心编号章、辽宁旭睿售后章、武汉飞鸿公司章和带 6237143 站点电话的取机专用章。公司名出现关键字冲突或证据不足的样本仍保持待复核，大模型结果不会覆盖完整但不同的公司名。第五梯队再复跑 6 张未审计中等证据困难章，新增合肥佳元手机售后专用章和沈阳辰星公司章 2 个可靠结论；大连北华、兰州悦达、海口群嘉和哈尔滨晨光等公司主体仍不完整，继续待复核。第六梯队新增 5785258 站取机专用章 1 个可靠结论；第七梯队按服务中心和编号结构选样，又新增三星电子孝感服务中心、郑州广利达业务受理专用章 2 个可靠结论。第七梯队同时发现“青岛维修中心单定”的跨栏噪声，现只在完整“三星电子 + 地名 + 服务/维修中心”结构下清理该尾部，真实单张复跑恢复 10/10 字段。当前 119 张不可靠印章中还有 49 张未执行当前 Server 章色复核；70 张已审计，其中 37 张证据参与最终匹配、9 张因公司冲突仅供人工审计。低分章色专项分析表明，39 张低相似度样本中 31 张章色充足、6 张偏弱、2 张极弱，主要瓶颈是公司主体、章型或编号文字碎片化。第八梯队按高章色结构选样新增 2 个可靠结论；第九梯队增加矩形编号章数字行裁剪，Mobile 与 Server 均能从 7273697921.jpg 读到完整 2310637，但因“维修中心”主体不足仍保持待复核。界面会展示原章、增强章和编号行图。站代码联系字段也改为保留“站点号 + 电话 + 公司名”完整结构，7285597191.jpg 不再因字段截短而获得虚高匹配。相关报告见 storage/seal-low-visual-analysis-301-hybrid.json、storage/e2e-seal-server-visual-priority-tier8.json、storage/e2e-rectangular-code-line-tier9.json 和 storage/e2e-station-contact-boundary-fix.json。第十梯队6张低分结构未新增可靠章，但发现行级重试会把“整单签收”噪声粘到本已接近完整的服务中心要求；现优先保留结构更安全的整页读数，只补固定末字，7291212709.jpg 恢复10/10字段并获得可靠印章。第十一梯队在“本地已读到具体章型”时允许一次颜色隔离Server复核，新增大连北华售后章和浙江大唐维修章2个可靠结论，已知不匹配的7286691343.jpg仍待复核。第十二梯队及单张路由复核又新增武汉飞鸿公司章和三星电子孝感服务中心2个可靠结论；原17张大模型优先队列现为0，余下39张低分章均已审计或不满足安全路由条件。报告见 storage/e2e-seal-server-visual-priority-tier10.json、storage/e2e-seal-specific-type-tier11.json、storage/e2e-seal-server-visual-priority-tier12.json 和 storage/e2e-service-center-server-route-7274930780.json。两张历史可靠日期在本轮 Vision 无输出时安全降为不可靠，系统没有沿用历史高分掩盖本次证据不足；随后对仅剩的两张“跨引擎且无其他冲突”候选用当前预处理真实重跑，7302558619.jpg 可靠识别为 2025-08-07，7293205278.jpg 因年份仍被章遮挡继续待复核，当前日期可靠覆盖回升为 175/301。报告见 storage/e2e-date-two-engine-no-conflict-current.json。第三至第七梯队验收依次保存在 storage/e2e-seal-server-unaudited-tier3.json 至 storage/e2e-seal-server-unaudited-tier7.json。圆章增强先对 12 个“公司名证据强但章类型缺失”的候选执行 180° 展开扫描，3 张完整页面重跑后新增可靠印章结论，明细保存在 `storage/seal-rotated-unwrap-regression-summary.json`。后续保留章色 90°/180°/270° 对照图又恢复 2 张章类型，但“一字不同公司”的安全回归同时把 4 张原本依赖单字模糊匹配的样本降为待复核；净覆盖率略降，但已知 `大连允华` 与要求 `大连北华` 的不同公司章不会被 Server 误字强行判定通过，明细见 `storage/seal-rotated-color-sheet-regression-summary.json` 和 `storage/seal-near-company-conflict-final-rerun.json`。另有 10 个非独立标准回单页面位于 `数据/document_ground_truth.json`：8 张委托书、1 张双页回单首页和 1 张商品续页，文档分类 10/10、两页商品行数 33/33 与 34/34、逻辑回单 2 页 67 行和行号顺序均正确。由此 311 张数据图片已 311/311 分类覆盖；委托书和分页页级指标不混入标准回单日期/印章准确率分母。日期可靠性采用严格的跨模型、跨几何复核：常规路径必须 Mobile 与 Server 在紧/宽不同裁剪上读到同一完整日期；不匹配日期的受限补充路径允许 Mobile 提供唯一明确月日，但 Server 必须在另一裁剪读到同月日的严格四位年份完整日期。同一裁剪重复读数或多个有效月日冲突仍不允许自动放行；去线日期行也只有严格完整日期或上述唯一月日证据才可参与。椭圆代码章可以在红章 OCR 明确读到“代码 + 6~10 位数字”且 SoldToCode/ShipToCode 独立一致时按业务编码可靠匹配。

`--exercise-review` 会优先挑选待复核样本填写人工真值，验证 `original_result_json` 不变、复核历史落库、六类筛选命中、可靠样本批量确认成功、证据不足样本批量确认返回 409，以及所有非空日期/印章证据 URL 均能实际读取。六张最终验收保留无法识别日期的 `7266227347.jpg` 为待复核，没有用批量状态掩盖机器不确定性。

字段规则会记录 OCR 原值及校正来源；已复核 EAN 商品目录与客户签章主数据仅在同页重复字段或明确业务编号、站号、章类型提供独立证据时使用。客户名或仓库名只剩孤立表格笔画时，必须由另一行完整公司和一致的 SoldTo/ShipToCode 共同恢复。客户地址与收货地址仅差省级前缀、数字段完全一致且相似度至少 96% 时，才补回至多两个 OCR 漏字。EAN 为空时，只有物料号与已复核商品目录完全一致且目录 EAN 校验位有效才允许回填。Server 大模型的部分日期或印章文本只作为复核候选；证据不足、时间顺序冲突或偏离标准日期栏时继续进入人工复核。圆章展开后的公司名与章类型可能方向相反，只有公司名已经形成强证据、要求含明确章类型、同一颜色隔离圆章的 180° 展开图或保留章色旋转对照图又读出章类型时，才合并为可靠匹配；不同印章区域的公司名和通用“专用章”不得拼接。常规模型若已经读出一个完整但不同的近似公司全称，Server 后续结果只显示在审计界面、不得参与匹配覆盖这条反证。界面会展示日期区域原图、去章色、区域去线、日期行原图、日期行去章色、日期行去表格线，以及印章的颜色分离、圆周展开、圆章展开 180°、保留章色 90°/180°/270° 对照图和必要的矩形章旋转图。文档类型分流覆盖标准回单、商品续页、仓库货物接收委托书和未知页；未补充人工真值的图片不会进入准确率分母。

可使用 `python -m tools.export_task <task_id> --output <文件.xlsx>` 离线复验指定批次导出。132 张阶段对 127/129/130 三张复核结果的工作簿检查确认：订单号列为文本、要求/实际日期列为 `yyyy-mm-dd`、中文和人工备注完整，且无公式错误。

新增标准回单可以限制后端、文件名和数量并把结果写入数据库：

```bash
/Users/zhuyihao/anaconda3/bin/python -m tools.batch_validate \
  --backend hybrid --filename 7266702320.jpg --filename 7266933626.jpg \
  --save-db --output storage/new-sample-pilot-hybrid.json
```

委托书、商品续页和双页回单使用独立真值与评估命令，不混入标准回单分母：

```bash
/Users/zhuyihao/anaconda3/bin/python -m tools.evaluate_document_routing \
  --task-id <委托书批次> --task-id <双页回单批次> \
  --output storage/document-routing-report-hybrid.json
```

离线比较多个后端批次时，可生成不写入 SQLite 的独立基准报告：

```bash
/Users/zhuyihao/anaconda3/bin/python -m tools.compare_backend_runs \
  --run hybrid=storage/mobile-run.json \
  --run hybrid_server=storage/server-run.json \
  --reference-backend hybrid --output storage/backend-comparison.json
```

其中商品“一致率”明确标注为与已核验参考后端逐单元格比较，不冒充人工真值准确率。

该工具同样会保存日期与印章中间图。macOS Vision 必须在普通 PyCharm/Terminal 权限下运行；受限沙箱可能无法创建 Vision 图像缓冲区，因此正式准确率应使用本机原生运行结果。

手写日期会在紧凑区域和宽区域上分别生成五张可查看图：区域原图、去除彩色印章图、去表格线增强图、手写日期行紧裁原图和日期行去印章色图。日期行会额外调用 Paddle 识别模型；区域 OCR 提供印证，或紧裁/宽裁两种不同几何区域均真实读出同一完整日期且至少一组原图/去章色结果一致时，才允许用于可靠自动结论。单一区域即使连续读成要求日期也不足以通过，避免潦草手写数字被要求日期“吸附”。复核弹窗保留所有中间图、后端和各版本 OCR 文本，避免预处理误删浅色或细笔画时失去人工判断依据。

若 Paddle Mobile 只在紧裁、宽裁各一次读出相同的要求月日，系统可按需调用 Server 行识别模型；只有 Server 再独立读出严格四位年/月/日且置信度不低于 82% 才采用。Server 单独读对或只在一个几何区域读对仍不足以通过。该回退让 `7275692497.jpg` 正确检出 2025-03-01，同时已知手写 7 被 Mobile 误吸附为要求 9 的 `7272433470.jpg` 仍保持未识别、待复核。

Hybrid 对“签章要求”会把同一行被 OCR 拆开的相邻文本框拼接、排除右侧实收/拒收数量和电话号码噪声，并对固定签章要求行做 Paddle 行识别复核；当候选更完整且不会偏离客户主体时才替换主值。“要求到货”被拆成标签尾部孤立数字和独立日期框时，会使用同一行的真实日期 OCR 框修复。商品表格按列边界、行号和表头基线识别，估算轻微倾斜并校正纵坐标聚类；缺失等级会对独立等级单元格紧裁复识，不从物料描述末尾猜测。

数据目录还可能包含多页附件。分析器会在整页 OCR 后先做文档类型分流：标准回单继续执行字段流程，但首页没有“签章要求”页脚时不运行日期/印章，等待同任务中的 `_01` 等续页；无表头商品续页通过至少 3 组同行“行号 + 物料编码 + EAN”识别，并沿用固定列坐标提取商品，若续页自身带签收页脚则保留日期/印章证据。数据库保存首页/续页父子关系，Excel 将同一任务中的分页商品按顺序合并为一张回单一行。仓库货物接收委托书和未知页只保存 OCR 证据，日期/印章阶段明确记录为“未执行（文档类型分流）”。复核界面和批量队列都会显示文档类型、类型置信度和分页关系。

印章在 Hybrid 下以 Vision 为主，同时让 Paddle 读取颜色隔离图和圆章展开图；原始彩色裁剪 OCR 仍显示在审计界面，但不再作为匹配证据，避免与红章重叠的黑色“签章要求”打印行形成循环满分。公司主体与章类型必须有同一章区的真实彩色印迹 OCR 证据才能可靠通过。淡红印泥除 HSV 外增加红色通道优势掩码；不同颜色的重叠候选优先保留彩色像素密度更高的真实章框。左右两枚章被浅色印迹连成超宽候选时，会先拆分并按各自中心重新判定发货章/收货章。所有回退和增强仍受置信度及后缀完整性检查约束，遮挡严重的日期或印章继续进入待复核。

运行单元测试：

```bash
/Users/zhuyihao/anaconda3/bin/python -m pytest -q
```

macOS Vision 在某些受限沙箱中可能无法创建图像缓冲区；在本机 PyCharm/Terminal 中使用上述解释器运行即可。

启用“本地 + 清瞳印章 API”选项时，默认将 Key 单独保存在：

```text
config/seal_api_key
```

文件中只放 API Key 本身，不添加引号。真实密钥文件已被 `.gitignore` 排除，
`config/seal_api_key.example` 是可提交的说明文件。也可通过 `SEAL_API_KEY_FILE`
指定其他密钥文件，或用 `SEAL_API_KEY` 环境变量覆盖文件内容。

未配置环境变量或密钥文件时，界面仍保留该选项说明，但不可选择。
