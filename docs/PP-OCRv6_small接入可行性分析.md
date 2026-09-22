# multi_PP-OCRv6_small 接入可行性分析

> 状态：**已按路线 A 实施完成**（见文末第九节「实施结果」）。
> 日期：2026-09-18

---

## 一、结论

**1. 你想试的模型不需要 cnocr。**

`multi_PP-OCRv6_small` 就是 PaddleOCR 官方的 **`PP-OCRv6_small_rec`**，只是换了个包装。证据链：

| 事实 | 来源 |
| --- | --- |
| CnOCR 文档列 `multi_PP-OCRv6_small` 模型文件 **20 M** | cnocr.readthedocs.io/zh-cn/stable/models |
| PaddleOCR 官方列 `PP-OCRv6_small_rec` 模型存储 **20.4 MB** | paddleocr.ai 文本识别模块文档 |
| CnOCR 模型卡自述："本仓库中的 ONNX 模型文件来自 RapidOCR" | huggingface.co/breezedeus/cnocr-ppocr-multi_PP-OCRv6_small |
| `multi_PP-OCRv6` 只是 `multi_PP-OCRv6_small` 的别名 | CnOCR 2.3.3 Release Notes |

CnOCR 在这里扮演的角色只是 **ONNX 运行时 + 模型下载器**，模型本体是百度的 PP-OCRv6。项目已经在用 PaddleOCR，没有必要为此引入一层额外的工具链。

**2. 推荐路线 A：升级 paddleocr，直接用官方 PP-OCRv6_small。**

**3. 路线 B（引入 cnocr 作为独立后端）不建议**，理由见第六节。

---

## 二、模型调研

### 2.1 官方 PP-OCRv6 三档（都是独立可选的 det / rec 组合）

| 模型 | 模型大小 | 官方精度 | 定位 |
| --- | --- | --- | --- |
| PP-OCRv6_tiny_det / tiny_rec | 1.9 MB / 4.4 MB | 80.6% / 73.5% | 端侧、IoT |
| **PP-OCRv6_small_det / small_rec** | **9.6 MB / 20.4 MB** | **84.1% / 81.3%** | **移动端 / 桌面，本项目目标** |
| PP-OCRv6_medium_det / medium_rec | 59.4 MB / 73.3 MB | 86.2% / 83.2% | 服务端 |

- PP-OCRv6 于 2026-06-11 随 **PaddleOCR 3.7.0** 发布（当前项目是 3.6.0，尚不支持 v6 模型名）。
- 架构：检测 PPLCNetV4 + RepLKFPN；识别 PPLCNetV4 + LightSVTR + CTC/NRTR 多头解码器（推理时去掉 NRTR 分支）。
- 单模型支持 50 种语言，官方称相比 PP-OCRv5 同级"检测 +4.9%、识别 +5.1%"。

### 2.2 依赖兼容性（已实测 PyPI 元数据，**无冲突**）

| 约束 | PP-OCRv6 要求 | 项目当前 | 结论 |
| --- | --- | --- | --- |
| paddleocr | `>=3.7.0,<3.8` | 3.6.0 | 需升级 |
| paddlex | `[ocr-core]>=3.7.0,<3.8.0` | 随 paddleocr | 需升级 |
| numpy | `>=1.24,<2.4` | 1.26.4 | ✅ 满足 |
| opencv-contrib-python | `==4.10.0.84` | 4.10.0.84 | ✅ 完全一致 |
| paddlepaddle | `>=3.2.1` | 3.3.1 | ✅ 满足 |

也就是说升级只动 paddleocr / paddlex 两个包，`requirements.txt` 里其他 pin 全部不用碰。

### 2.3 两个必须知道的陷阱

**陷阱 1：paddleocr>=3.7.0 的默认模型已经切到 v6。**
官方原话："3.7.0 及以上版本的默认 PP-OCR 模型已切换到 v6 版本"。好消息是本项目在 `paddle_ocr.MODEL_VARIANTS` 里显式 pin 了模型名，不会被动跟着变——**现有 v5 行为在升级后保持不变**。但这也意味着升级本身不会带来任何收益，必须显式加新变体。

**陷阱 2：官方那个 81.3% 不能拿来和现在的 81.29% 比。**
官方明确标注："PP-OCRv6 指标基于内部多场景评估集测得，PP-OCRv5/v4 指标基于通用评估集测得，两者评估集不同，指标不可直接对比。"所谓 +5.1% 是换数据集后的数字。**必须用项目自己的真值样本实测**，不能靠发布稿决策。

### 2.4 速度（官方 CPU 数据，单模型推理耗时）

| 模型 | paddle_static | onnxruntime | 加速 |
| --- | --- | --- | --- |
| PP-OCRv5_mobile_rec | 6.69 ms | 2.05 ms | 3.3× |
| PP-OCRv6_small_rec | 4.73 ms | 1.79 ms | 2.6× |
| PP-OCRv5_mobile_det | 13.80 ms | 5.70 ms | 2.4× |
| PP-OCRv6_small_det | 10.97 ms | 7.46 ms | 1.5× |

两个附带收益：
- v6_small 比 v5_mobile **本来就快**（rec 4.73 vs 6.69，det 10.97 vs 13.80）。
- 本机已装 `onnxruntime 1.28.0`，PaddleOCR 3.7 支持 `engine="onnxruntime"`，**不装任何新包**就能再拿 2~3 倍识别加速。（这条对 cnocr 路线是降维打击：cnocr 的主要卖点 onnxruntime 速度，直接设个 engine 参数就有。）

### 2.5 本机现状

- `~/.paddlex/official_models/` 只有 4 个 v5 模型（共 186 MB），**没有 v6**。
- `paddle_model_home()` 的优先路径 `/Volumes/SN770/OCR` 对应外置盘**当前未挂载**，会回落到 `~/.paddlex`。
- → 首次跑 v6 需要联网下载约 **30 MB** 权重（det 9.6 + rec 20.4）。内网/离线环境下这是个前置阻塞项。
- Python 运行时：项目走 `anaconda3/bin/python3`（3.11.5）。

---

## 三、当前架构与耦合点

### 3.1 后端抽象现状

```
backend_catalog()  →  /api/ocr-backends  →  templates/index.html 渲染勾选框
                                            └─ static/modules/recognition_plan.mjs 读 checkbox
        ↓
recognize_text(path, backend, stage) → backend_route()[stage]
        ├─ "vision"                     → vision_ocr.recognize_text
        └─ "paddle" / "paddle_server"   → paddle_ocr.recognize_text(model_variant=...)
```

模型档位只由 `paddle_ocr.MODEL_VARIANTS` 决定：

```python
MODEL_VARIANTS = {
    "mobile": ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"),
    "server": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
}
```

### 3.2 关键耦合点清单

`receipt_ocr` 里写死的后端 id 字面量共 **74 处，分布在 22 个文件**（`"paddle"` / `"paddle_server"` 的 `==`/`in` 判断）。这是本次改造最大的隐性成本。

新增一个后端 id 时，**必须**同步的位置：

| # | 文件:行 | 作用 | 漏改后果 |
| --- | --- | --- | --- |
| 1 | `receipt_ocr/paddle_ocr.py:21` `MODEL_VARIANTS` | 模型档位 → 官方模型名 | 根本没有 v6 |
| 2 | `receipt_ocr/paddle_ocr.py:126,166` `provider_allowed(...)` | 请求级 provider 白名单校验 | **静默返回 `[]`**，识别结果全空但不报错 |
| 3 | `receipt_ocr/ocr_backends.py:11` `BACKEND_LABELS` | 后端中文标签 | 报表显示原始 id |
| 4 | `receipt_ocr/ocr_backends.py:23` `backend_catalog()` | 后端目录 | 界面不出现新选项 |
| 5 | `receipt_ocr/ocr_backends.py:169` `recognize_text` 分支 | id → 模型档位映射 | 走到 `raise ValueError` |
| 6 | `templates/index.html:122-123` | 勾选框白名单 + 标签字典 | **设置页不显示该方式** |
| 7 | `static/modules/recognition_plan.mjs:3,5` | `methodLabels` / `localMethods` | 摘要显示 `undefined`，且提交时被判为"不支持" |
| 8 | `receipt_ocr/recognition_config.py:45` | 识别计划 method 白名单校验 | 提交时抛 "识别方式不可用：xxx" |
| 9 | `receipt_ocr/recognition_safety.py:23` | 单一 Paddle 模型安全策略 | **单模型自证风险**（详见下） |
| 10 | `receipt_ocr/field_rules.py:56` | 字段行重核是否可用 | 回退到 mobile 模型 |
| 11 | `receipt_ocr/date_crop_regions.py:105` | 日期行 backend → 档位映射 | 用错模型重核日期 |
| 12 | `receipt_ocr/evaluation.py:293` | 报表后端排序 | 仅顺序问题，功能不坏 |
| 13 | `tests/test_ocr_backends.py:20,67+` | 既有断言 | 测试红 |

需要**留意但可暂不改**的位置：

- `receipt_ocr/date_slot_evidence.py:377,688` —— 用**后端标签文本**反推模型族（`"mobile" in label or "paddleocr" in label`）。所以新后端的**标签字符串不能随便起**：如果标签里带 "mobile" 会被误判成 mobile 档。
- `receipt_ocr/date_slot_evidence.py` / `date_business_evidence.py` / `date_strict_evidence.py` 里大量 `"paddle" not in str(secondary_ocr_backend)` 判断 —— 决定跨模型证据是否成立。

### 3.3 一个非常容易踩的坑

`receipt_ocr/recognition_config.py:134,164`：

```python
with provider_scope({method}):
    context.page(method)
```

`method` 就是设置页里那个后端 id，同时 `StageRequest(dict(page=backend, date=backend, seal=backend), ...)` 把它同时当成三个阶段的 backend。

所以 **新后端必须是一条完整的、端到端贯通的后端 id**，不能"只加模型不给 id"。否则 `paddle_ocr` 里的 `provider_allowed()` 会把结果全部拦成 `[]`——**不报错、不抛异常，只是识别结果为空**。这是本项目里最容易诊断出错的失败模式。

### 3.4 安全策略必须同步（最重要的一处）

`recognition_safety.py:23`：

```python
if len(physical) != 1 or not physical.issubset({"paddle", "paddle_server"}):
    return ""
```

这是"单一 Paddle 模型不得自证"的兜底：当 page/date/seal 三个阶段实际由**同一个**物理模型完成时，日期和印章结论置信度被压到 ≤0.68 且强制人工复核。

新增 `paddle_v6` 后如果忘记把它加进 `issubset(...)`，那么选择"仅 v6 单模型"时 **`len(physical)==1` 但 `issubset` 为 False → 直接 `return ""`，安全策略静默失效**，单模型就获得了自证能力。这是不能漏的一处。

---

## 四、路线 A 改造方案（推荐）

### Step 1 — 依赖升级（不碰业务代码）

```bash
/Users/zhuyihao/anaconda3/bin/pip install "paddleocr>=3.7.0,<3.8"
```

只升级 paddleocr + paddlex。验证：

```bash
/Users/zhuyihao/anaconda3/bin/python3 -c "
from paddleocr import PaddleOCR
ocr = PaddleOCR(
    text_detection_model_name='PP-OCRv6_small_det',
    text_recognition_model_name='PP-OCRv6_small_rec',
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    device='cpu',
)
r = list(ocr.predict(input='数据/<任意一张回单>.jpg'))
print(r[0]['rec_texts'][:5], r[0]['rec_scores'][:5])
"
```

同时在 `requirements.txt` 里把 `paddleocr==3.6.0` 更新掉。

> **Step 1 必须先单独验证通过再动 Step 2~7。** 升级后跑一遍现有测试，确认 v5 行为零变化（因为模型名是 pin 死的，预期全绿）。

### Step 2 — `receipt_ocr/paddle_ocr.py`：加档位 + provider 映射

```python
MODEL_VARIANTS = {
    "mobile": ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"),
    "server": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
    "v6":        ("PP-OCRv6_small_det",  "PP-OCRv6_small_rec"),   # 对应 multi_PP-OCRv6_small
    "v6_medium": ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"),  # 备用，可先不加
}

# 请求级 provider id ↔ 模型档位（与 backend_catalog 的 id 必须一致）
VARIANT_PROVIDERS = {
    "mobile": "paddle",
    "server": "paddle_server",
    "v6": "paddle_v6",
    "v6_medium": "paddle_v6_medium",
}
```

把 126 / 166 两行的判断改为查表：

```python
if not provider_allowed(VARIANT_PROVIDERS.get(model_variant, "paddle")):
    return []
```

`_pipeline()` / `_line_recognizer()` / `recognize_line()` / `recognize_text()` 其余逻辑**无需改动**——它们本来就是按 `MODEL_VARIANTS` 泛化的，`TextRecognition(model_name=...)` 同样支持 v6 模型名。归一化坐标输出结构完全一致，下游 `TextObservation` 契约零变化。

### Step 3 — `receipt_ocr/ocr_backends.py`：注册后端

```python
BACKEND_LABELS = {
    ...
    "paddle_v6": "PaddleOCR PP-OCRv6 Small",
}
```

`backend_catalog()` 新增条目（照抄 `paddle` 条目的结构）：

```python
{
    "id": "paddle_v6",
    "label": BACKEND_LABELS["paddle_v6"],
    "available": paddle_installed and paddleocr_version >= (3, 7),
    "reason": "" if ... else "需 paddleocr>=3.7.0",
    "model_home": str(paddle_model_home()),
    "model_name": "PP-OCRv6 small",
    "safety_policy": single_model_safety,
    "recommended": False,
    "usage_note": "新一代多语种模型对照；整页/局部与 PP-OCRv5 结果需交叉核对",
}
```

建议**加版本门槛**：`paddleocr<3.7.0` 时 `available=False`，避免升级没做完时界面上出现一个点了会炸的入口。

`recognize_text()` 的分支扩成集合：

```python
elif selected in {"paddle", "paddle_server", "paddle_v6"}:
    from .paddle_ocr import recognize_text as recognize
    kwargs["model_variant"] = {"paddle": "mobile", "paddle_server": "server", "paddle_v6": "v6"}[selected]
```

`backend_route()` 不用改：新 id 不属于 `{"hybrid", "hybrid_server"}`，会自动按"三阶段都用自己"返回。

### Step 4 — `receipt_ocr/recognition_safety.py:23`

```python
if len(physical) != 1 or not physical.issubset(
    {"paddle", "paddle_server", "paddle_v6", "paddle_v6_medium"}
):
    return ""
```

顺手把 `_ocr_model_config()` 里的 `"PP-OCRv5 Server"` 硬编码标签也改成从 catalog 取。

### Step 5 — 计划校验 + 界面

- `receipt_ocr/recognition_config.py:45`：`{"vision", "paddle", "paddle_server", "paddle_v6"}`
- `templates/index.html:122`：`backend.id in ['paddle','paddle_server','paddle_v6','vision']`
- `templates/index.html:123`：标题字典和显示字典各加一条 `'paddle_v6':'Paddle v6 Small'`
- `static/modules/recognition_plan.mjs:3`：`methodLabels` 加 `paddle_v6`
- `static/modules/recognition_plan.mjs:5`：`localMethods` 加 `'paddle_v6'`

> 按项目既有约定：改完 `.mjs` 要 bump `application.mjs` 里的 `?v=`；改完 CSS 要 bump `templates/index.html` 的 `?v=`。（本次不涉及 CSS。）

### Step 6 — 收敛散落的字面量（建议顺做）

`field_rules.py:56`、`date_crop_regions.py:105` 等处的 `in {"paddle","paddle_server"}` / `== "paddle"` 判定，建议在 `paddle_ocr.py` 里加两个语义化 helper：

```python
PADDLE_BACKENDS = {"paddle", "paddle_server", "paddle_v6", "paddle_v6_medium"}

def is_paddle_backend(name: str | None) -> bool: ...
def variant_of(name: str | None) -> str: ...   # -> "mobile" / "server" / "v6"
```

然后把 74 处字面量逐步替换。**不要在这次改造里一次性全改**——先只改必需的（`field_rules.py:56`、`date_crop_regions.py:105`），把 helper 建起来，其余留给后续。否则一次改动面太大，回归风险不可控。

### Step 7 — 测试

同步既有断言，新增：
- `MODEL_VARIANTS["v6"] == ("PP-OCRv6_small_det", "PP-OCRv6_small_rec")`
- `VARIANT_PROVIDERS` 与 `backend_catalog()` 的 id 集合完全一致（防止 Step 2/3 两侧 id 打错从而静默返回空）
- `backend_route("paddle_v6")` 三阶段都指向 `paddle_v6`
- 单模型安全策略对 `{paddle_v6}` 生效（`_apply_single_paddle_safety` 返回非空 policy）
- `paddleocr<3.7.0` 时 catalog 里 `paddle_v6.available is False`

---

## 五、试点与验证方案（不动默认行为）

新后端**只作为可选项**加入，`default_backend()` 保持不动，直到真值对照证明不退化。

项目已经现成的离线对照工具链，零新增代码：

```bash
# 1) 同一批样本跑两遍
/Users/zhuyihao/anaconda3/bin/python3 -m tools.batch_validate ... --backend paddle
/Users/zhuyihao/anaconda3/bin/python3 -m tools.batch_validate ... --backend paddle_v6
#   （单图先看一眼：python -m tools.inspect_ocr）

# 2) 出对照报告
/Users/zhuyihao/anaconda3/bin/python3 -m tools.compare_backend_runs \
    --run paddle=storage/xxx-paddle.json \
    --run paddle_v6=storage/xxx-paddle-v6.json \
    --reference-backend paddle \
    --output storage/ppocrv6-small-vs-v5-mobile.json
```

重点看这几个指标（`backend_benchmark.py` 已全部输出，含与参考后端的跨后端一致率）：

- `field_accuracy`、`product_cell_accuracy` / `product_row_accuracy`
- `date_accuracy`、`date_decision_accuracy` / `date_decision_coverage`
- `seal_conclusion_accuracy`、`seal_decision_accuracy` / `seal_decision_coverage`
- `failures`、`average_seconds`

**关注优先级**：
1. **日期**——手写 + 相邻重复数字，项目里 `date_crop_*`、`far_lower`、`server_component`、`slot_consensus` 等大量逻辑是针对 v5 的误差特征调出来的，换模型后这些"经验补丁"可能失配（也可能变得更准）。
2. **商品表格**——密集区、按列识别稳定性。
3. **印章**——文字圈、旋转、彩色印。

建议先跑 30 张覆盖失败案例的真值样本集（`数据/ground_truth.json` 已有），而不是全量。

---

## 六、为什么不建议路线 B（引入 cnocr）

| 维度 | 路线 A：paddleocr 3.7 | 路线 B：cnocr |
| --- | --- | --- |
| 新增依赖 | 无（只是版本升级） | cnocr + cnstd（onnxruntime 本机已有） |
| 模型 | PP-OCRv6_small（同一份权重） | PP-OCRv6_small 的 ONNX 转换版 |
| 输出结构 | 与现有 PaddleOCR 完全一致 | CnOcr 返回 `position` 四点 + `score`，需要写适配层转 `TextObservation` 归一化左上角坐标 |
| 单行识别路径 | `TextRecognition` 已有，`date_crop_*` 重度依赖 | 需改用 `ocr_for_single_line`，语义/行为要重新验证 |
| 串联与缓存 | 复用现有 `_PIPELINE_LOCK` / `_PREDICT_LOCK` / 模型缓存 | 需另建一套 |
| 安全策略 | 复用 | 需新增跨模型安全判定 |
| onnxruntime 提速 | `engine="onnxruntime"` 一行参数 | 这是 cnocr 的默认，但没有独占优势 |
| 多语种/繁简 | 支持 | 支持 |

**结论**：cnocr 在这个项目里是纯粹的额外一层，唯一的差异化能力（ONNX 运行时）用 PaddleOCR 的原生 `engine` 参数就能拿到。不建议引入。

---

## 七、风险清单

1. **精度不可比**：官方的 +5.1% 换了评估集，不可作为决策依据。必须真值实测。
2. **首次下载需联网**：约 30 MB 权重；`/Volumes/SN770` 未挂载 → 会落到 `~/.paddlex`。若要放外置盘，先挂载并设 `PADDLE_PDX_CACHE_HOME`。
3. **静默空结果**：`provider_allowed` 的 id 若与 catalog id 不一致，识别结果全空且不报错（见 3.3）。务必加 Step 7 那条"两侧 id 集合一致"的断言。
4. **安全策略失效**：`recognition_safety.py` 未同步则单模型自证（见 3.4）。这是本项目最不能漏的一处。
5. **标签文案有语义**：`date_slot_evidence.py` 靠标签文本反推模型族，新标签不要含 "mobile"。
6. **报表口径**：`ocr_backend` 是存量字段（12 万+ 结果记录）。引入新 id 后 `evaluate_backends` 会多出一组，"全部后端最新结果"视图的统计口径会变化，需要跟使用方说明。
7. **组合爆炸**：现在是"一档模型 = 一个后端 id"。如果以后要支持"v6 small det + v5 server rec"这类混搭，`MODEL_VARIANTS` 的 `(det, rec)` 二元组结构够用，但 UI 侧会膨胀。**本次先只上一个组合。**

---

## 八、工作量与顺序建议

```
Step 1  依赖升级 + 单图验证           ← 先做，独立可回滚
Step 2  paddle_ocr.py 加档位/映射
Step 3  ocr_backends.py 注册后端
Step 4  recognition_safety.py 同步    ← 与 Step 3 同批，不可拆
Step 5  计划校验 + 模板 + 前端标签     ← 与 Step 3/4 同批，否则界面不可用
Step 7  测试同步与新增断言
Step 6  字面量收敛                    ← 建 helper，只改必需的两处，其余后续
---- 以上为一个可交付单元，新后端仅作为可选项 ----
Step 8  真值样本离线 A/B（tools/ 现成）
Step 9  视结果决定是否调整默认后端
```

Step 1~5 + 7 是"能跑起来"的最小闭环，建议作为一个改动单元；Step 6 和 Step 8/9 分离，降低回归面。

---

## 九、实施结果（2026-09-18 完成）

### 9.1 环境变更

| 项目 | 变更前 | 变更后 |
| --- | --- | --- |
| paddleocr | 3.6.0 | **3.7.0** |
| paddlex | 3.6.1 | **3.7.2** |
| 其他依赖 | — | 全部未变（numpy 1.26.4 / opencv 4.10.0.84 / paddlepaddle 3.3.1 均满足） |
| 新增模型权重 | — | `~/.paddlex/official_models/PP-OCRv6_small_det`(9.6MB) + `PP-OCRv6_small_rec`(20.4MB) |

`requirements.txt` 已更新 `paddleocr==3.7.0`。

> 遗留小问题：`site-packages/` 下有一个空的 `~addlex-3.6.1.dist-info`（pip 中断卸载的残留），会让 pip 打印 "invalid metadata entry" 警告，不影响运行。可手动删除。

### 9.2 代码变更清单

| 文件 | 变更 |
| --- | --- |
| `receipt_ocr/paddle_ocr.py` | `MODEL_VARIANTS` 新增 `"v6"`；新增 `VARIANT_PROVIDERS` / `PADDLE_BACKENDS` / `LIGHTWEIGHT_VARIANTS` 与 `is_paddle_backend()` / `provider_of()` / `variant_of()` / `is_lightweight_backend()`；两处 `provider_allowed` 硬编码判断改为查表 |
| `receipt_ocr/ocr_backends.py` | `BACKEND_LABELS` 加 `paddle_v6`；新增 `PADDLE_V6_MIN_VERSION` / `paddle_v6_supported()` 版本门槛；`backend_catalog()` 新增条目；`recognize_text()` 分支改为 `is_paddle_backend()` + `variant_of()` |
| `receipt_ocr/recognition_safety.py` | 单模型安全策略的 `issubset({"paddle","paddle_server"})` 改为 `all(is_paddle_backend(...))` |
| `receipt_ocr/recognition_config.py` | 计划校验白名单改用 `PADDLE_BACKENDS \| {"vision"}` |
| `receipt_ocr/field_rules.py` | 签章要求行重核的 backend 判断与档位映射改为 helper |
| `receipt_ocr/date_crop_regions.py` | 日期行绕过检测的路径改用 `is_lightweight_backend()`，并按实际后端 id 记录 `line_backend` |
| `receipt_ocr/date_business_evidence.py` | 修复"靠标签文本反推模型族"——改用 `is_lightweight_backend()`，避免 v6 标签不含 "mobile" 导致的家族误判 |
| `templates/index.html` | 勾选框白名单与标签字典加入 `paddle_v6`（显示名 `Paddle v6 Small`） |
| `static/modules/recognition_plan.mjs` | `methodLabels` / `localMethods` 加入 `paddle_v6` |
| `static/modules/application.mjs` | 给 `recognition_plan.mjs` 补上 `?v=20260918-paddle-v6` 缓存穿透版本号 |
| `tests/test_ocr_backends.py` | 新增 11 个用例（v6 模型名、id↔provider 双向一致、scoped 白名单、路由、档位选择、版本门槛、标签不含 mobile、轻量档判定、三档安全策略、计划可校验） |
| `tests/settings_ui.cjs` | 夹具加入 `paddle_v6`；新增 1 个前端用例 |
| `requirements.txt` | `paddleocr==3.7.0` |

**未改动（有意保留）**：`recognition_safety._ocr_model_config()` 里 `"PP-OCRv5 Server"` 字符串保持原样——它被 `tests/test_analyzer.py` 断言、且已写进存量导出 JSON，改字符串会造成兼容性破坏。v6 走该函数时返回 `{}`，与 Mobile 一致，语义正确。

### 9.3 关键设计点

- **后端 id = provider id**：`recognition_config` 用 `provider_scope({method})` 且 `StageRequest` 把三阶段都设为同一 id，所以 `VARIANT_PROVIDERS` 的取值必须与 `backend_catalog()` 的 id 完全一致。已加断言锁死这条不变量。
- **标签文案**：`PaddleOCR PP-OCRv6 Small` 刻意不含 "mobile" / "v5"，避免 `date_slot_evidence` 的标签反推误判；同时 `is_lightweight_backend()` 让 `date_business_evidence` 仍能把它认作轻量档。
- **默认行为零变化**：`default_backend()` 未改（macOS 仍为 `hybrid`），`paddle_v6` 只是设置页里多出来的可选项。

### 9.4 验证结果

**单元测试**：`pytest tests` → **2 failed, 916 passed**。
两个失败（`test_settings_plan_presets_validation_and_persistence`、`test_workbench_todos_pagination_and_record_navigation`）经 `git stash` 对照确认**改动前后完全一致**，属于存在已久的测试与代码漂移，与本次改造无关。

**5 张样本离线 A/B**（`tools/batch_validate.py` × `tools/compare_backend_runs.py`，样本 7266220437 / 439 / 440 / 7347 / 7266274552）：

| 指标 | v5 Mobile | v6 Small | 结论 |
| --- | --- | --- | --- |
| 字段准确率 | 1.0 | 1.0 | 持平 |
| 商品单元格准确率 | 1.0 | 1.0 | 持平 |
| 商品行准确率 | 1.0 | 1.0 | 持平 |
| 商品跨后端一致率 | — | 1.0 | 完全一致 |
| 印章结论准确率 | 1.0 | 1.0 | 持平 |
| **日期准确率** | **0.8 (4/5)** | **0.6 (3/5)** | **v6 退步** |
| 处理失败数 | 0 | 0 | 持平 |
| **平均耗时** | **37.74s** | **27.45s** | **v6 快 27.3%** |

**唯一分歧点（确定性回归，可复现）**：

```
7266220440.jpg  required=2025-01-05
  v5 Mobile: actual=2025-01-05  → 匹配
  v6 Small:  actual=2025-04-05  → 不匹配   （月 01 被读成 04）
```

**其他观察**：
- 印章原始文本质量互有胜负：439 上 v6 明显更干净（v5 混入大量重复噪音文本，96% → 100%）；74552 上 v6 反而更乱（96% → 85%）。
- 结算层面三者（字段/商品/印章）没有差异，说明差异集中在**日期这条最敏感链路上**，与调研预判一致。

### 9.5 结论与建议

1. **后端已可用**：设置页「识别内容与方式」现在可勾选 `Paddle v6 Small`，可与其他方式多选做结果对照。
2. **暂不建议设为默认**：5 张样本虽小，但 440 的日期错误是确定的单点回归；而 v6 的主要收益（27% 提速）在结算口径上尚未转化为准确率提升。
3. **下一步**：若要继续推进，用 `tools/batch_validate.py` 把样本扩到 30~50 张（优先覆盖日期识别失败的案例集），重点看 `date_accuracy` 与 `date_decision_accuracy`，再决定是否扩大默认使用范围。

