# AI 视频剪辑项目 · 唯一技术执行基线

**文档**：`docs/TECHNICAL_PLAN.md`
**版本**：v1.2（收敛版 + Model Strategy + Interactive Editing Agent）
**生成日期**：2026-08-25
**收敛来源**：
- 《AI 视频剪辑项目说明 v0.3》（`docs/source/AI视频剪辑项目说明_v0.3.pdf`）— **产品目标、用户场景、范围、里程碑定义以此为准**
- 《AI 视频剪辑技术执行文档 v1.1》（`docs/source/AI视频剪辑_技术执行文档_v1.1.md`）— **工程架构、数据结构、MVP 实现细节以此为准**
- `docs/MODEL_SELECTION.md` v1.0（**已产出**）— **具体模型候选、bake-off 方案与 provisional baseline stack 以此为准**；其 `FD-M01`–`FD-M10` 已收录为 §0.8 的架构决策，未决项见附录 D

---

## 使用说明（给 Coding Agent）

**本文件是唯一执行基线。开始开发时只需阅读本文件，不需要再读 v0.3 PDF 或 v1.1 MD。**

三条硬规则：

1. **本文档与任何旧文档冲突时，以本文档为准。** 旧文档仅作历史记录。
2. **第 2 章「冲突登记与决策」中的每一条决策都已写入本文档正文。** 如果人类决策者不同意某条，只需修改第 2 章对应条目并同步正文，不需要重读原始文档。
3. **第 13 章是要做的事情清单，第 2 章是框架总览。** 想直接开工就从这两章开始，它们不依赖前面任何内容。第 14 章是环境、仓库结构和验收测试。
4. **硬门禁**：**在模型 bake-off（§0）完成、baseline model stack 冻结之前，不要大规模实现任何依赖具体模型行为的 pipeline。** 但**块 A 整块 + 块 B 的确定性部分不受影响，随时可以开始**——specs、校验、DB、确定性 CV、FFmpeg renderer、CLI 骨架都不需要知道最终用哪个模型。

当前阶段是**单人开发**。任何"看起来更完整但会延后第一个可播放 MP4"的方案，一律否决。

---

## 0. Model Strategy

本项目不是"先把前后端架起来、最后接个模型"的 SaaS。**模型承担哪些认知任务，决定 pipeline 怎么设计、数据结构长什么样、评测怎么做。** 因此模型选型发生在 M0 之前，而不是之后。

同时要区分两件事：现在需要选的是**模型栈和 baseline**，不是把所有模型永久选死。M-1 的产出叫 **Freeze MVP baseline**，替换流程见 §0.7。

### 0.1 Model Roles

pipeline 里至少有五个模型位置。它们的要求、替换成本和影响面都不同，**必须分别选型，不能"全都用同一个最强模型"**。

| # | 角色 | 输入 → 输出 | 关键要求 | 替换成本 | 影响的假设 |
|---|---|---|---|---|---|
| ① | **Material Understanding VLM** | segment → `semantic / shot_type / semantic_role / camera_motion / people / emotion / interestingness` | 语义准确 + 结构化输出稳定 | 中 | **H1**（候选池质量） |
| ② | **Reference Understanding VLM** | 参考视频 → `ReferenceAnalysis` | **精确时间理解**；结构/节奏/镜头模式抽取 | **高（决定 pipeline 形态，见 §0.5）** | **H3** |
| ③ | **Editing Planner LLM** | 结构化上下文 → `EditPlanSpec` | 长 JSON schema 遵从；计划连贯 | **低** | H2 |
| ④ | **Embedding / Retrieval** | 帧 / 文本 → 向量 | 检索相关性；可本地跑、零边际成本 | 低 | Slot Matching |
| ⑤ | **Taste Critic** | (A, B, 上下文) → 偏好 + 理由 | **editing reasoning** + **可微调** | 高 | **H4** |
| ⑥ | **Interactive Editing Agent** | 用户连续指令 + Project State → `EditOperation[]` | **tool-calling 准确性、多轮状态跟踪、scope 控制、结构化命令稳定性** | 中 | **H5**（§11） |

辅助能力（用成熟服务，不进 bake-off 主流程）：ASR、OCR、Beat detection、Audio understanding。

**角色 ⑥ 与 ①② 是完全不同的任务，必须单独选型。** ①② 回答"**从视频里读出什么**"；⑥ 回答"**用户这句话要改哪里、改多少、不能碰什么**"。⑥ 的评测维度和视频理解几乎不重叠：

```
Tool-calling accuracy          Scope-control accuracy
Structured-output stability    Instruction following
Multi-turn state tracking      中文自然表达理解
（"这里""刚才那版""后半段"的指代消解）    Latency / Cost
```

⑥ 进一步拆成 **⑥-Fast**（快速小模型，处理确定性局部修改）与 **⑥-Deep**（强模型，处理需要重新规划审美与叙事的修改），见 §11.9。**用户说"字幕小一点"不应该触发最昂贵的视频模型。**

**三条来自角色差异的结论**：

1. **精力优先给 Video VLM。** 它同时决定 ①②，而且 ⑤ 的 zero-shot baseline 也用它——一个模型影响三个位置。③ Planner 反而最容易换，因为它的输入到那时已经是结构化文本（segments JSON + Reference EditPlanSpec + prompt），任何一个强 LLM 都能先顶上。**真正难的是"从视频里读出什么"。** 建议 M-1 的精力分配：Video VLM 60%，editing reasoning 25%，Planner + Embedding 15%。

2. **⑥ 不能与 ①② 共用一个模型。** ⑥-Fast 需要的是低延迟 + 稳定 tool calling，⑥-Deep 需要的是规划能力。把它们绑定到最强的视频理解模型，会让每一次"删掉这段"都付出视频模型的延迟和成本。合理分工是：视频模型只在**需要重新看素材**时才被调用。

3. **只有 ⑤ 需要"可微调"。** M3 要做 LoRA / preference fine-tuning，而 ①②③④ 只要 API 可用即可。因此完全可能出现最优解是"Material/Reference 用最强的闭源模型，Critic 用一个稍弱但能微调的开源 Video-VLM"。**Scorecard 里"可微调性"这一行只对 ⑤ 是必选项**——否则会因为一个只对 Critic 成立的约束，把最好的 Material VLM 误杀掉。

#### 能力分层：不要什么都交给大模型

很多 AI 产品的失败模式是 `Everything → 一个大模型`，所有任务往同一个 prompt 里塞。本项目按判断类型分层路由：

| 要判断什么 | 交给谁 | 为什么 |
|---|---|---|
| 镜头边界在哪 | 确定性 CV（PySceneDetect） | 可确定计算 |
| 是否模糊 / 抖动 / 过曝 | OpenCV（§4.5 公式） | 免费、可复现、可解释 |
| 音乐 beat 在 3.72 秒 | librosa / aubio beat tracking | 同上 |
| 时长、速率、时间线运算 | 代码 | **绝不让 LLM 算数** |
| 这段是什么内容 / 情绪 / 值不值得留 | **VLM** | 需要语义理解 |
| 景别 / 运镜 | VLM（或轻量分类器） | 语义 |
| 应该怎么组织成一个故事 | **LLM** | 规划 |
| slot ↔ segment 谁更匹配 | **Embedding** | 检索 |
| A / B 哪个剪得更好 | **Taste Critic** | 偏好判断 |

> **硬规则：任何可以用确定性方法算出准确答案的量，禁止交给 LLM/VLM。**
> "这段是不是朋友第一次看到富士山时很开心的 moment"才应该给 VLM；"这一帧模糊不模糊""beat 在第几秒"不应该。这让系统更稳定、更便宜、更可解释。

### 0.2 Candidate Models

**已由 `MODEL_SELECTION.md` v1.0 §0.2 / §4 / §13 填入**（provisional stack + mandatory challengers）。**Coding Agent 不得自行增删候选或提前冻结。** 尚未补齐的项见附录 D。 模型选型是核心技术决策，需要结合当下实际可用的模型、API、视频输入限制、价格、context、微调能力和部署/合规条件做一轮真实比较。

每个候选模型**必须记录以下属性**（缺一项就无法判断它能不能进 §0.5 的路径 A）：

```
供应商 / 模型 ID / 版本或快照
视频输入方式：接受视频文件 还是 只接受抽帧序列
最大时长 / 最大帧数 / 采样 fps
是否返回可靠时间戳（关键）
context 窗口
结构化输出：JSON schema 约束 / function calling / 仅自由文本
计价：输入、输出、每秒视频（若按时长计）
典型延迟 p50 / p95
区域与合规：可用性、数据出境、部署方式
微调能力：LoRA / 全参 / 仅 API 级 / 不支持
速率限制
```

> **其中「视频输入方式 + 最大时长 + 时间戳是否可靠」三项必须实测，不能只读文档。** 它们直接决定 §0.5 的架构分支。

### 0.3 Model Evaluation Dataset

三个子集，版本化冻结在 `data/model_eval/`，各自附人工 GT。**这套数据的人工标注才是 bake-off 的主要成本，不是 API 费用**——其中 E2 的逐镜头标注最贵。

**E1 · Material Understanding**
10–20 条自有/授权 vlog，抽 60–100 个 segment，人工标注：`semantic` 描述要点、`shot_type`、`camera_motion`、`people`、情绪、以及**「值不值得用」的二值判断**。
评分：shot_type 准确率、camera_motion 准确率、semantic_role 准确率、描述要点召回（人工 3 分制）、「值得用」与人工的 agreement、JSON 合规率。

**E2 · Reference Understanding**
10 条你认为剪得好的 vlog，人工 GT：shot boundaries（时间戳）、每镜头时长、景别序列、大致结构、节奏、字幕密度、transition、音乐关系。
评分：**边界 F1（±0.25s 容差）**、镜头数误差、景别序列编辑距离、**时长序列 MAE / DTW**、结构一致性（人工）。
**这一项同时是 §0.5 分支判据的来源。**

**E3 · Editing Reasoning**
30–50 组 A/B，用「人工剪的好版本 + §10.7 的合成破坏」构造，每组由 ≥3 位懂剪辑的人投票，**只保留一致率 ≥80% 的高共识对**进入 bake-off。
评分：与人类多数票的 agreement、**理由是否命中真实缺陷**（重复镜头 / 节奏 / 故事断裂 / 音乐 mismatch / 过度转场 / shot diversity）、Tie 滥用率、位置偏置。
这就是未来 Taste Critic 的 zero-shot baseline。

**E4 · Slot Retrieval**、**E5 · Editing Planner**、**E6 · Interactive Agent**
见 `MODEL_SELECTION.md` §5.6 / §5.7。E6 的评测集需要构造 **30–50 条多轮编辑指令**（含"这里""刚才那版""后半段"这类指代，以及"整体快一点但开头别动"这类带 preserve 的复合指令），人工标注**正确的 scope 与期望的 operation 序列**，评分 = scope 解析准确率 + operation 序列匹配率 + **越界修改次数**。

**两条方法论红线**：

1. **E3 必须做 A/B 位置交换的双向测试**——同一对正反各问一次，两次一致才计分。VLM 在 pairwise judging 上的位置偏置非常常见；不控制它，M3 那个「55% → 75%」的提升可能有相当一部分是噪声。
2. **M3 的 Taste Critic held-out 评测集必须与 E3 完全不相交，且不复用 E3 的素材组。** E3 是模型**选择**用的 dev set；如果 M3 又拿它做评测，"这个模型在 E3 上分高"会被误读成"Critic 学到了审美"——这是选择偏置，会让 §13.6 的量化门槛失去意义。

### 0.4 Bake-off Protocol

1. **每个角色一份固定 prompt 模板，所有候选模型共用同一份。** 不为某个模型做特化调优——否则比较的是 prompt 工程，不是模型能力。
2. 每项测 3 次取中位数；单独记录 **JSON 解析失败率与重试次数**（结构化输出稳定性和准确性同等重要，一个 90 分但 15% 输出不合法的模型不能用）。
3. 统一记录：准确性、p50/p95 延迟、单位成本、失败率。
4. **成本必须换算到「每 project」，不比 per-token 单价。** 口径：50 条素材 × 平均 6 segment ≈ 300 次 ① 调用；② 1 次长视频或 N 次逐镜头调用；③ 6 次 Planner 调用；⑤ 3 个候选两两比较 × 双向 = 6 次。算出「一个 project 多少钱」再对照 §9 的预算。

**Model Scorecard 模板**（`MODEL_SELECTION.md` 输出这张表）：

| 能力 | Model A | Model B | Model C |
|---|---:|---:|---:|
| Material 语义准确（E1） | | | |
| shot / motion 分类（E1） | | | |
| 时间戳可靠性（E2） | | | |
| 长视频处理（E2） | | | |
| **Editing reasoning（E3）** | | | |
| 结构化输出稳定性 | | | |
| 可微调性（**仅 ⑤ 必选**） | | | |
| 每 project 成本 | | | |
| p95 延迟 | | | |
| 区域 / 合规 / 部署 | | | |

> **选法：按角色分别选各自最高分，不选「综合最强」。**
> 一个模型在通用 video QA 上很强，不代表它能判断为什么 `wide → close-up → detail → face` 的视觉节奏优于 `wide → wide → wide → wide`。如果 Model X 的 benchmark 更高但 editing reasoning 更差，而 Model Y 反之，本项目应该选 Y。

### 0.5 Pipeline 分支：模型能力决定架构

**本章最重要的一条。** Reference Understanding 有两种 pipeline 形态，选哪一种**不由偏好决定，由 E2 的实测结果决定**：

**路径 A —— 模型能可靠理解整段视频并返回准确时间戳**
```
reference.mp4 → VLM → ReferenceAnalysis
```

**路径 B —— 时间定位不可靠**
```
reference.mp4
  → 确定性 Shot Detector（PySceneDetect）→ shot 1..N
  → VLM 逐 shot 分析（只问语义 / 景别 / 运动，不问时间）
  → 代码做 temporal aggregation
  → ReferenceAnalysis
```

**判据**：E2 的边界 F1 ≥ 0.85 **且** 时长序列 MAE ≤ 0.15s → 路径 A；否则路径 B。

> **provisional 决策：走路径 B（shot-first pipeline）。** 依据 `MODEL_SELECTION.md` 的 **FD-M04** 与其 baseline stack，且其调研记录了一条实证理由——主流视频理解默认以约 1 FPS 采样，官方即提醒快速运动与快速切镜可能丢失信息，这与本项目的 vlog 快剪场景直接冲突。
> E2 仍然要跑，但它的作用从"决定走哪条路"变成"**检验是否有模型好到值得升级为路径 A**"。

**默认假设是路径 B。** 它把"精确时间"交给确定性工具、只把"语义"交给模型，与 §0.1 的分层路由原则一致，更稳定也更便宜；路径 A 只有在 E2 明确证明可行时才采用。

Material Understanding 同理：§5.1 已按路径 B 形态定为「确定性切分 + 逐 segment 图像级理解」。若 M-1 证明视频级理解显著更好，再升级。

> 一句话：**不是把模型塞进现成 pipeline，而是模型能力决定 pipeline 怎么设计。** 这也是 M-1 必须在 M0 之前的根本原因——先写完 pipeline 再发现模型读不准时间，返工的是整条 Reference 链路。

### 0.6 Provider Abstraction（按角色，不按厂商）

> **模型决定系统能力，但模型不能决定系统架构。**

业务层只认**角色接口**，不认厂商：

```python
class MaterialUnderstandingProvider(Protocol):
    def analyze_segment(self, seg: RawSegment) -> SegmentAnnotation: ...

class ReferenceUnderstandingProvider(Protocol):
    def analyze(self, video: Path,
                shots: list[ShotBoundary] | None) -> ReferenceAnalysis: ...
    # shots 为 None → 路径 A；传入确定性边界 → 路径 B

class PlannerProvider(Protocol):
    def plan(self, ctx: PlanningContext, n: int, seed: int) -> list[EditPlanSpec]: ...

class EmbeddingProvider(Protocol):
    def embed_images(self, images: list[Path]) -> np.ndarray: ...
    def embed_texts(self, texts: list[str]) -> np.ndarray: ...

class RerankProvider(Protocol):
    def rerank(self, query, candidates: list) -> list[float]: ...
    # 召回与精排分开：换 reranker 不应该动业务代码

class TasteCriticProvider(Protocol):
    def compare(self, a: Candidate, b: Candidate,
                ctx: CriticContext) -> PairwiseVerdict: ...

class ASRProvider(Protocol):
    def transcribe(self, audio: Path) -> list[TranscriptSegment]: ...

class RendererProvider(Protocol):
    def render(self, tl: TimelineSpec, quality: str, out_path: Path) -> RenderResult: ...
```

实现层按 `<Vendor><Role>Provider` 命名，一个角色可以有多个实现并存。

> **接口全部为 `async`。**上面的签名省略了 `async` 关键字以保持可读，实现时全部是 `async def`。

**四条强制约定**：

1. **禁止** `def analyze_video_with_<vendor>(...)` 这类函数出现在业务层，禁止业务代码 import 任何厂商 SDK。
2. **每个角色独立配置模型**（完整变量表见 §14.1）。**不允许存在一个全局 `AIVE_MODEL`**——那会在架构里固化"所有角色同一个模型"这个错误。
3. **每个角色都必须有 `Mock*Provider`**（确定性假数据，不联网不花钱）。CI 与全部单元测试只用 Mock，这是 M0 能被自动化验收、也是 M-1 期间就能并行开发 pipeline 骨架的前提。
4. **成本与延迟记录在角色 Provider 基类统一实现**，写入 `api_calls`（含 `role` 列）。重试（指数退避，最多 3 次）、超时、结构化输出校验失败重试一次并降级，同样在基类。

### 0.7 Model Replacement Policy

冻结 baseline 不是永久决定。替换流程：

1. 新候选先在 **E1/E2/E3 上跑同一套 bake-off**，产出可比 scorecard。
2. 只有**目标角色**分数提升，且不劣化结构化输出稳定性与每-project 成本预算，才允许替换。
3. 替换后必须在 §12.2 的**固定评测集（5 组素材）**上重跑，人工确认成片质量无回退。
4. 记录到 `docs/model_changelog.md`：日期、角色、旧→新、scorecard diff、回归结论。
5. **Critic 一旦训练完成，更换 backbone 需要重新训练并重跑 §13.6 的门槛**，成本远高于换其他角色。因此 ⑤ 的选型要比其他四个角色谨慎得多。

**版本钉死**：所有模型配置必须写明确的版本或快照 ID，**禁止使用会滚动更新的 alias**。否则某天成片质量变化时无法归因——分不清是自己的改动还是模型静默升级。

### 0.8 Frozen Model Decisions

来自 `MODEL_SELECTION.md` §0.3，已作为本项目的架构决策生效。Coding Agent 可以直接依赖这些结论，不需要重新论证：

| ID | 决策 |
|---|---|
| **FD-M01** | 不寻找"一个模型包办全部任务"；按角色选择模型 |
| **FD-M02** | 中国区 MVP 采用 **China-first hybrid stack**，不是单一供应商 |
| **FD-M03** | Raw Video VLM 必须完成 M-1 Bake-off 后才能冻结 |
| **FD-M04** | **默认时间精度来自 shot detector / ASR / audio DSP，不来自 VLM 自报时间戳** |
| **FD-M05** | provisional planner 不是永久绑定 |
| **FD-M06** | `embedding + rerank` 两段式是 provisional matcher baseline |
| **FD-M07** | Taste Critic 先 zero-shot → SFT/LoRA → Best-of-N；不直接做 RL |
| **FD-M08** | 不把任何单一供应商作为 Raw Video Parser 或长期可训练 Critic 的唯一依赖 |
| **FD-M09** | Workflow / Skills 只用于原型和工具封装；**核心 `EditPlanSpec`、`TimelineSpec`、ranking、feedback log、版本图与实验逻辑必须留在自有代码库** |
| **FD-M10** | 所有生产模型必须锁定 snapshot / model ID，并进入 Model Registry |
| **FD-M11** | **Interactive Editing Agent（角色 ⑥）单独选型，不与 Video VLM 共用**；⑥-Fast / ⑥-Deep 分离 |
| **FD-M12** | **Fast / Deep 路由由 operation 类型静态决定**（§11.3 表），不由模型临时判断 |

FD-M11 / FD-M12 是本轮新增，需要同步回写 `MODEL_SELECTION.md`。

**工程门槛（来自 `MODEL_SELECTION.md` §5.9，对所有角色生效）**：

```
First-pass JSON schema validity  >= 95%
One-repair schema validity       >= 99.5%
生产候选必须支持：timeout / retry / idempotency / token-cost logging
                  / prompt version / model snapshot
```

任何模型若经常拒绝普通生活素材、无法锁定版本、输出结构极不稳定、快剪漏检严重或成本无法满足消费产品——**即使 benchmark 很高也不能入选**。

---

## 1. 产品目标与 MVP 范围（以 v0.3 为准）

### 1.1 一句话

做一个"像豆包一样简单"的 AI 视频剪辑产品：用户把素材丢进来，告诉 AI 想要什么，或者再给一条喜欢的参考视频，AI 直接剪出有审美的成片；默认可直接发布，也支持在产品内继续修改。

更准确的产品定义（见 §11.1）：**Conversational, stateful, taste-aware video editing agent** —— 用户先让 AI 完成大部分剪辑，再通过对话或直接操作不断修正；AI、编辑器和用户共同维护同一个可编辑项目，直到用户满意。

项目真正要降低的不是"软件操作成本"，而是**"剪辑判断成本"**。普通用户不是不会点按钮，而是不知道：哪些镜头该留、顺序怎么排、什么时候切、音乐怎么配、转场该不该加。

### 1.2 三条产品原则

| 原则 | 含义 | 对工程的约束 |
|---|---|---|
| **AI First** | 默认路径是 `Idea → Video`，不是 `AI → Timeline → 用户继续干活` | Lite 主流程不出现时间线 UI；渲染完成即可播放可导出 |
| **Taste First** | 核心命题是"AI 能不能做出像有审美的剪辑师那样的判断" | Critic 与 Preference 数据是第一类工程资产，不是附属实验 |
| **Editor Second** | 编辑器是 Escape Hatch，不是门槛 | 编辑器只做语义级操作，且与对话共用同一套 `EditOperation`（§11.2） |
| **Stateful & Conversational** | AI 出片不是终点，而是迭代起点 | 项目有持续状态与版本图；对话与编辑器是**两个平等入口**（§11） |

### 1.3 MVP 只验证四个假设

- **H1**：AI 能否从大量原始素材中挑出"值得用"的镜头？（Material Understanding + Shot Selection）
- **H2**：AI 能否把这些镜头组织成"明显比机械模板更好"的短视频？（Story + Rhythm + Taste）
- **H3**：Reference Remake 是否成立？能否抽取可迁移的结构/节奏/景别/语义序列/转场密度/音乐关系/字幕风格，并用用户素材重新实现？
- **H4**：加入 Taste Critic 后，用户是否明显更喜欢 AI 输出？

- **H5**：用户是否真的愿意**通过对话**把一个"不完全满意"的 AI 成片改成满意版本？（Interactive Editing Agent，§11）

H3/H4 不成立时，不应急于做跨 NLE 导出或 RL。H5 由 M2.5 验证——它决定产品是"一次性生成工具"还是"长期 Agent"。

### 1.4 Lite 的两个一级核心场景

**Mode A — AI 自动成片**
```
20–100 条用户素材（视频 + 照片混合）+ 一句话 prompt (+ 可选背景音乐)
→ AI 自动挑片 / 排序 / 卡节奏 / 配音乐
→ 可直接发布的成片
```

**照片是一等输入，不是附加项。** 手机相册里照片和视频本来就是混在一起的，要求用户只传视频等于要求他们先做一次筛选——而"不用做筛选"正是这个产品的卖点。技术含义见 §4.5.1。

**Mode B — 参考视频（两种，不是一种）**

这两种在产品上是不同的请求，在技术上是不同的管线，**不能用一个参数区分**：

**B1 · 风格参考（多条参考视频）**
```
20–100 条用户素材 + N 条参考视频
→ 聚合出这类视频的剪辑语法（分布，不是序列）
→ 作为先验塑造 Planner 的规划
→ "剪成这一类的感觉"
```
用户想要的是"这个博主的风格""这类视频的感觉"。**必须允许多条**——单条视频可能是这个创作者的异常样本，五条才看得出他真正的语法。

**B2 · 模板复刻（一条参考视频）**
```
20–100 条用户素材 + 1 条参考视频
→ 拆成逐镜头的槽位序列
→ 用户素材填进槽位
→ "这一条的我的版本"
```
用户想要的是"这一条，我也要一个"。**只能是一条**——五条视频之间没有"第 3 个镜头"的对应关系，槽位序列无法对齐。

**为什么不是同一条管线**：B2 产出的是**序列**（slots[]），B1 产出的是**分布**（StylePrior）。B2 直接替换 Planner 的 slot 生成；B1 只是塑造它。两者的产出类型不同，下游用法也不同。

**Reference Remake 的定位（v0.3 已明确，必须准确执行）**：它是 Lite 的一级核心产品入口和最强 Demo / 获客入口，**但"能参考视频剪"本身不是长期护城河**——百炼的"视频模仿"实践与千问"万镜一刻"已经公开具备类似能力。长期壁垒在于**复刻质量、Reference-to-Edit Grammar、Material Matching、Taste Critic 与 Preference Data**。

工程含义：Mode B 要做，但不要为了"更像参考"去做 pixel-level 逆向、LUT 反推、复杂 motion graphics 还原——那些既不构成壁垒也会拖垮进度。

### 1.5 MVP 范围边界

**内容品类**：只支持 Travel vlog 与 Daily life vlog。
不支持：talking head、podcast、游戏、专业广告、documentary、婚礼长片、多机位、影视级叙事、大量特效型短视频。

**成片时长**：系统支持 20–90 秒；MVP 默认与 Demo 使用 30–60 秒。

**画幅**：MVP 仅 9:16。数据结构保留 `aspect_ratio` 字段但 M0–M4 不实现其他比例。

**支持的剪辑维度**（只做这些）：shot selection、trim、ordering、duration、基础变速、基础 transition、字幕、音乐/beat、crop。

**MVP 明确不做**：
```
自训 foundation video model      从零自研媒体 renderer
复杂 mask / blend mode / 特效逆向  Premiere / DaVinci / 剪映无损 round-trip
未授权大规模社交平台爬取           一上来做 RL
完整专业 timeline editor          复杂关键帧 / 精确滤镜逆向
个人 taste embedding             原生 iOS / Android
多人协作                          完整素材管理 DAM
```

注意区分：**"不自研 renderer" 指不自己造媒体引擎，不是不用 FFmpeg。** MVP 用 FFmpeg 作为默认渲染实现，见第 6.4 节。

### 1.6 Pro

Pro 在 MVP 阶段不实现（M6+）。唯一约束：数据结构不得堵死通向 Pro 的路——因此 `TimelineSpec` 保持 vendor-neutral、保留 `render_constraints` 与多轨结构。

---

## 2. 框架总览

系统由十个模块组成。**每个模块的输入输出都是 §4 定义的结构，模块之间只通过这些结构通信。**

| 模块 | 职责 | 输入 → 输出 | 定义在 |
|---|---|---|---|
| `specs/` | 领域模型与校验 | — | §4、§11.3 |
| `providers/` | 六个模型角色的接口与实现 | — | §0.6 |
| `material/` | 素材切分、质量、语义、向量 | 视频文件 → `MaterialSegment[]` | §5.1 |
| `reference/` | 参考视频拆解 | 视频文件 → `ReferenceAnalysis` → Reference `EditPlanSpec` | §4.6 |
| `planner/` | 剪辑规划（**不碰具体素材**） | intent + 素材池摘要 → `EditPlanSpec[]` | §5.3 |
| `matcher/` | 素材绑定与适配 | `EditPlanSpec` + 素材池 → `TimelineSpec` | §5.4 |
| `critic/` | 候选排序 | `TimelineSpec[]` → 排序 | §5.5 |
| `renderer/` | 执行渲染（**纯机械翻译**） | `TimelineSpec` → MP4 | §5.6 |
| `agent/` | 交互式修改 | 指令 + 项目状态 → `EditOperation[]` → 新版本 | §11 |
| `db/` `api/` `cli/` | 项目状态、任务、成本 | — | §7、§8 |

**一句话数据流**：

> 素材和参考视频先变成结构化的**素材池**和**剪辑计划**；计划再和素材匹配成**时间线**；时间线才渲染成 MP4。之后所有修改都是对计划或时间线的**增量操作**，不是重新生成。

**三条分界线**（违反任何一条都会让后续替换成本急剧上升）：

1. **`planner/` 不知道有哪些具体素材**，只知道素材池的聚合摘要。这让 Reference 学到的"风格"可以脱离素材独立存在。
2. **`renderer/` 只读 `TimelineSpec`**，不读 `EditPlanSpec`。这让换渲染后端或导出 NLE 工程时不需要理解剪辑意图。
3. **`matcher/` 做判断，`renderer/compiler/` 做翻译。** 任何需要判断"哪个镜头更合适""这段该多长"的代码属于 matcher；compiler 只把 `TimelineSpec` 翻译成 FFmpeg 命令。

**自己写的和买来的**：`material/` 的质量指标、`planner/`、`matcher/`、`critic/`、`agent/` 是自研核心；视频理解、ASR、OCR、模型推理是买来的能力（经 `providers/` 接入）；渲染用 FFmpeg。详见 §3.2。

## 3. 系统架构

### 3.1 总数据流

```
                      USER MATERIALS            REFERENCE VIDEO (可选, 用户上传)
                            │                             │
                            ▼                             ▼
                  Material Understanding        Reference Analysis
              (shot split / VLM / quality        (shot boundary / semantics /
               / motion / embedding)              rhythm / music / text)
                            │                             │
                            ▼                             ▼
                  Candidate Segment Pool        ReferenceAnalysis
                            │                             │
                            │                             ▼
   USER PROMPT ──► Intent Parser ──────────►  Reference EditPlanSpec
                            │                             │
                            └──────────┬──────────────────┘
                                       ▼
                              EDITING PLANNER
                                       │
                            N × EditPlanSpec candidates
                                       │
                                       ▼
                        Material Matcher / Adaptation  ◄── Candidate Segment Pool
                                       │
                            N × TimelineSpec candidates
                                       │
                                       ▼
                     Stage 1: Structural Critic (不渲染)
                                       │
                                  Top-K plans
                                       ▼
                     Stage 2: Proxy Render (360p/540p)
                                       │
                                       ▼
                            Video Taste Critic
                                       │
                              Best TimelineSpec
                                       │
                    ┌──────────────────┴──────────────────┐
                    ▼                                     ▼
        Stage 3: HQ Render (1080p)          Interactive Editing Agent
                                            + Lite Editor (M2.5)
                    │                                     │
                    ▼                                     ▼
                   MP4                            User Corrections
                                                          │
                                                          ▼
                                                  Preference Data ──► Taste Critic
```

**但成片不是终点。** 第一版输出之后进入持续迭代闭环（§11）：

```
        Best TimelineSpec ──► 第一版成片
                 │
                 ▼
        ┌────────────────────────────────────────┐
        │            PROJECT STATE               │
        │  EditPlanSpec / TimelineSpec / Locks   │
        │  Version DAG / Candidate Alternatives  │
        └────────────────────────────────────────┘
             ▲                          ▲
             │  EditOperation[]         │  EditOperation[]
             │                          │
     Interactive Editing Agent    Lite Editor (UI)
             ▲                          ▲
             │  自然语言指令             │  点击 / 拖动 / 锁定
             └──────────┬───────────────┘
                        用户
                        │
                        ▼
             局部 proxy 预览 ──► 确认 / 继续修改 / undo
                        │
                        ▼
              导出 MP4  +  Preference & Correction Data
```

**两个入口产生同一种 `EditOperation`，修改同一份项目状态。** 这是 §11.2 的硬规则，也是 §10.10 数据飞轮成立的前提。

### 3.2 自研 vs 复用

| 模块 | 策略 | 是否核心壁垒 |
|---|---|---|
| Agent / Workflow 编排 | 复用（或自己写薄编排层） | 否 |
| 视频理解 / ASR / OCR / Scene Split | 复用 + 轻量自研特征 | 否 |
| Renderer / 媒体处理 | **FFmpeg-first + 可替换 Provider** | 否 |
| Editing Planner | 自研编排 + 现成 LLM/VLM backbone | **是** |
| Reference → EditPlanSpec | 自研 | **是** |
| Material Matching / Moment Selection | 自研 | **是** |
| Taste Critic / Preference Model | 自研、重点训练 | **核心** |
| Cross-NLE Export | 后置（M6+） | 潜在 |

**单人阶段铁律**：用户感知不到的基础设施优先买/调 API/用开源；决定"剪得好不好"的能力自己掌握数据、评测和核心逻辑。

必须自己积累的资产只有五项：`EditPlanSpec + TimelineSpec`、Preference Dataset、Editing Evaluation、Taste Critic、User Correction Data。

---

## 4. 数据结构（规范定义）

本章是实现的唯一依据。所有结构用 Pydantic v2 模型定义，落在 `packages/specs/`。

### 4.1 受控枚举

```python
# semantic_role —— slot 与 segment 共用
SemanticRole = Literal[
    "opening_environment",  # 开场环境/建立镜头
    "environment",          # 一般环境
    "transit",              # 移动/路途
    "person_activity",      # 人在做事
    "action",               # 明确动作
    "interaction",          # 人与人/人与物互动
    "detail",               # 细节特写
    "food",                 # 食物
    "reaction",             # 表情/反应
    "text_card",            # 文字卡
    "ending_environment",   # 收尾环境
    "ending",               # 结束镜头
    "unknown",
]

ShotType = Literal[
    "extreme_wide", "wide", "medium", "close_up", "extreme_close_up",
    "pov", "unknown",
]

CameraMotion = Literal[
    "static", "handheld", "slow_pan", "fast_pan", "tilt",
    "zoom_in", "zoom_out", "tracking", "drone", "unknown",
]

# MVP 只实现前三种；其余枚举值保留但 renderer 拒绝
Transition = Literal[
    "hard_cut", "dissolve", "fade_to_black",
    "whip_pan", "zoom_blur",   # M4+ , M0-M3 不实现
]

Pace = Literal["slow", "medium", "medium_fast", "fast"]
BeatAlignment = Literal["none", "soft", "strict"]
SubtitleDensity = Literal["none", "low", "medium", "high"]
EditMode = Literal["auto", "reference_remake"]
```

**约定**：所有时间单位一律为**秒（float）**，不使用帧号或时间码。所有 0–1 的评分字段闭区间 `[0.0, 1.0]`。

### 4.2 EditPlanSpec —— "应该怎么剪"

不绑定具体素材。是 Planner、Reference Analysis、风格控制与语义编辑的主要表示。

```json
{
  "spec_version": "1.0",
  "plan_id": "plan_004",
  "project_id": "proj_001",
  "mode": "auto",

  "intent": {
    "target_duration": 45.0,
    "duration_tolerance": 0.15,
    "aspect_ratio": "9:16",
    "platform": null,
    "style": ["travel", "relaxed", "minimal"],
    "user_constraints": ["少一点字幕", "不要太多转场"],
    "language": "zh"
  },

  "story": {
    "sections": [
      {"name": "opening",     "target_duration": 6.0,  "slot_ids": ["slot_01","slot_02"]},
      {"name": "exploration", "target_duration": 20.0, "slot_ids": ["slot_03","slot_04","slot_05"]},
      {"name": "food",        "target_duration": 12.0, "slot_ids": ["slot_06","slot_07"]},
      {"name": "ending",      "target_duration": 7.0,  "slot_ids": ["slot_08"]}
    ],
    "narrative_arc": "arrival -> wandering -> food highlight -> quiet ending"
  },

  "slots": [
    {
      "slot_id": "slot_01",
      "order": 0,
      "section": "opening",
      "semantic_role": "opening_environment",
      "shot_type": "wide",
      "target_duration": 1.6,
      "duration_range": [1.0, 2.4],
      "motion": "slow_pan",
      "energy": 0.4,
      "transition_out": "hard_cut",
      "speed_intent": 1.0,
      "required": true,
      "notes": "城市全景，建立地点"
    }
  ],

  "rhythm": {
    "pace": "medium_fast",
    "shot_duration_pattern": [1.6, 1.2, 0.9, 0.8, 1.4, 0.7, 0.7, 2.0],
    "transition_density": 0.15,
    "music_sync_strength": 0.7,
    "beat_alignment": "soft"
  },

  "text": {
    "subtitle_density": "low",
    "style": "minimal",
    "source": "none"
  },

  "audio": {
    "mood": "warm",
    "music_id": "music_01",
    "keep_original_sound": true,
    "duck_music_under_speech": true
  },

  "provenance": {
    "reference_id": null,
    "planner_model": "<provider:model>",
    "candidate_index": 0,
    "seed": 41
  }
}
```

**字段说明（重点）**

- `rhythm` 的五个字段分两类：`pace / transition_density / music_sync_strength` 是暴露给用户的控制旋钮；`shot_duration_pattern`（逐 slot 的目标时长序列，长度必须等于 `len(slots)`）与 `beat_alignment` 是复刻保真与 rhythm similarity 计算所需的具体量。
- `slots[].duration_range` 给 Adaptation 留出伸缩空间。Matcher 只允许在此区间内 trim/extend，超出必须记录为 `adaptation_log` 中的显式动作。
- `slots[].required=false` 表示该 slot 在素材不足时可被跳过而不算失败。Reference 模式下由 Reference Analysis 按角色重要性标注。
- `text.source`：`none`（不加字幕）/ `asr`（从原声转写）/ `generated`（LLM 生成短标题）。M0–M1 固定 `none`。

**Reference 模式下的层级控制映射**（Step 8 的产品按钮直接映射到这里）：

| 用户操作 | 对 EditPlanSpec 的操作 |
|---|---|
| 只保留它的节奏 | 锁定 `rhythm`（含 `shot_duration_pattern`），放松 `story` 与 `slots[].semantic_role` |
| 只保留它的结构 | 锁定 `story` 与 slots 的角色序列，放松 `rhythm` |
| 不要它的字幕 | `text.subtitle_density = "none"`，`text.source = "none"` |
| 更像参考 | 收紧 `slots[].duration_range`，提高 Critic 的 `λ_reference` |
| 自由一点 | 放宽 `duration_range`，允许 Matcher 做 Replace Semantic Role |

### 4.3 TimelineSpec —— "最终怎么执行"

Material Matcher 的输出。**Renderer 只消费 TimelineSpec，不得读取 EditPlanSpec。**

```json
{
  "spec_version": "1.0",
  "timeline_id": "tl_011",
  "plan_id": "plan_004",
  "project_id": "proj_001",
  "duration": 42.3,
  "aspect_ratio": "9:16",

  "tracks": [
    {
      "type": "video",
      "clips": [
        {
          "clip_id": "c_001",
          "slot_id": "slot_01",
          "source_id": "asset_017",
          "source_in": 3.21,
          "source_out": 4.81,
          "timeline_start": 0.0,
          "timeline_end": 1.60,
          "speed": 1.0,
          "crop": null,
          "reframe": null,
          "transition_out": "hard_cut",
          "transition_duration": 0.0,
          "source_audio_gain": 0.0
        }
      ]
    },
    {
      "type": "subtitle",
      "clips": [
        {
          "clip_id": "s_001",
          "text": "第一天 · 东京",
          "timeline_start": 0.4,
          "timeline_end": 2.2,
          "style": "minimal"
        }
      ]
    }
  ],

  "audio": {
    "music_id": "music_01",
    "music_in": 0.0,
    "volume": 0.8,
    "fade_in": 0.5,
    "fade_out": 1.2,
    "ducking": true
  },

  "render_constraints": {
    "resolution": "1080x1920",
    "fps": 30,
    "video_codec": "h264",
    "crf": 20,
    "audio_codec": "aac",
    "audio_bitrate": "128k"
  },

  "adaptation_log": [
    {"slot_id": "slot_03", "action": "speed",   "detail": "1.0 -> 1.25 以适配 0.8s 目标时长"},
    {"slot_id": "slot_06", "action": "skip",    "detail": "无 food 类候选，required=false"},
    {"slot_id": "slot_07", "action": "replace_role", "detail": "food -> detail"}
  ],

  "provenance": {
    "matcher_version": "0.1.0",
    "candidate_index": 0
  }
}
```

**`crop` / `reframe` 结构**：
```json
"crop":    {"x": 0.1, "y": 0.0, "w": 0.8, "h": 1.0}   // 归一化到源画面 [0,1]
"reframe": {"mode": "center" | "face" | "saliency"}     // M2+ 才实现，M0 恒为 null
```

**`adaptation_log[].action` 取值**：`trim | speed | crop | reframe | skip | merge | replace_role | extend | shorten`。

### 4.4 TimelineSpec 不变量（必须实现为校验函数）

`packages/specs/validate.py` 中的 `validate_timeline(spec) -> list[Violation]`，在 Matcher 输出后、Renderer 输入前各调用一次。M0 起即生效：

1. **单调且连续**：video track 内 `clips` 按 `timeline_start` 升序；对 `hard_cut`，`clips[i].timeline_end == clips[i+1].timeline_start`（容差 1e-3 秒）。
2. **无空洞**：`clips[0].timeline_start == 0.0`，`clips[-1].timeline_end == duration`（容差 1e-3）。
3. **速度一致性**：`(source_out - source_in) == (timeline_end - timeline_start) * speed`（容差 1e-3）。
4. **源范围合法**：`0 <= source_in < source_out <= asset.duration`。
5. **最短镜头**：`timeline_end - timeline_start >= 0.30` 秒（低于此值人眼无法识别内容，属技术错误）。
6. **速度范围**：`0.25 <= speed <= 4.0`。
7. **时长符合意图**：`abs(duration - intent.target_duration) <= intent.target_duration * intent.duration_tolerance`。
8. **转场合法**：`transition_out` 属于 MVP 已实现集合；`dissolve` 时 `transition_duration ∈ [0.1, 1.0]` 且两侧 clip 有足够余量。
9. **字幕不越界**：subtitle clip 的时间范围落在 `[0, duration]` 内，且不与其他字幕 clip 重叠。
10. **slot 可追溯**：每个 video clip 的 `slot_id` 必须存在于对应 EditPlanSpec，且未被 `adaptation_log` 标记为 `skip`。

违反 1–6 视为**硬错误**（拒绝渲染）；7–10 视为**软错误**（记录并允许渲染，但计入 Structural Critic 扣分）。

### 4.5 MaterialSegment —— 素材候选池

```json
{
  "segment_id": "asset017_seg03",
  "project_id": "proj_001",
  "source_id": "asset_017",
  "start": 3.2,
  "end": 6.7,
  "duration": 3.5,

  "semantic": "close-up of ramen being served on a wooden table",
  "semantic_role": "food",
  "shot_type": "close_up",
  "camera_motion": "slow_pan",

  "people": 0,
  "face_quality": null,

  "technical_quality": 0.88,
  "sharpness": 0.91,
  "exposure_score": 0.84,
  "stability_score": 0.85,
  "motion_quality": 0.74,
  "composition_score": null,

  "emotion": ["warm", "cozy"],
  "interestingness": 0.82,

  "has_speech": false,
  "asr_text": null,
  "audio_rms": 0.12,

  "keyframe_paths": ["...seg03_a.jpg", "...seg03_b.jpg", "...seg03_c.jpg"],
  "embedding_dim": 512,

  "is_usable": true,
  "reject_reason": null
}
```

**质量指标的具体算法（M0 即实现，纯 OpenCV，无 API 成本）**：

| 字段 | 算法 | 归一化 |
|---|---|---|
| `sharpness` | 采样帧的 Laplacian 方差，取中位数 | `clip(log10(var) / 3.5, 0, 1)` |
| `exposure_score` | 灰度均值偏离 0.5 的程度 + 过曝/欠曝像素占比 | `1 - (2*abs(mean-0.5) + clipped_ratio)`，截断到 [0,1] |
| `stability_score` | 相邻帧稀疏光流位移中位数 `d`（归一化到画面宽度） | `clip(1 - d/0.05, 0, 1)` |
| `motion_quality` | 是否存在过快摇晃：光流方差 | 方差过高判为低分 |
| `technical_quality` | `0.35*sharpness + 0.25*exposure + 0.30*stability + 0.10*motion_quality` | 已在 [0,1] |
| `composition_score` | **M0 为 `null`**；M1 起由 VLM 打 1–5 分再归一化 | — |
| `interestingness` | **M0 用 `technical_quality`**；M1 起由 VLM 输出 | — |

**`is_usable=false` 的判定（M0 即生效，直接剔除出候选池）**：`duration < 0.6s`、`technical_quality < 0.35`、`sharpness < 0.2`（严重虚焦）、纯黑/纯白帧占比 > 0.6。`reject_reason` 记录原因，便于调试"为什么这条素材没被用"。

### 4.5.1 照片：哪些能复用，哪些必须新写

照片走同一条 `MaterialSegment` 结构，但有三处不能照搬：

| 处理 | 视频 | 照片 |
|---|---|---|
| 切分 | PySceneDetect | **不切**，一张照片 = 一个 segment |
| 时长 | 由切分给定 | **没有时长，必须分配**（slot 说多长就多长） |
| `sharpness` / `exposure_score` | 逐帧采样取中位 | 单帧，直接算 |
| `stability_score` / `motion_quality` | 逐帧光流 | **不适用** |
| `camera_motion` | 从光流判 | **恒为 static**，除非我们自己加运镜 |
| embedding / VLM 语义 | 关键帧 | 照片本身，完全一样 |
| 渲染 | trim + speed | `-loop 1 -t <duration>`，可选 Ken Burns |

**`stability_score` 必须显式标为"不适用"，不能给 0。** §4.5 的加权公式里稳定性占 0.30，照片拿 0 分会让每一张照片的 `technical_quality` 掉到 0.4 以下、然后被 `is_usable` 规则整批剔除——**一个正常的产品行为会因为一个指标不适用而变成静默丢弃用户素材**。正确做法是照片走一套只含 sharpness 与 exposure 的权重。

**Ken Burns 是可选的，不是必须的。** 静止照片夹在动态素材中间会显得"卡住"，加缓慢推拉能缓解；但那属于 `EditPlanSpec` 该表达的意图（`slot.motion`），不是渲染器自作主张。

**未实现，两处有 TODO**：`renderer/compiler.py`（执行侧，zoompan 滤镜）与 `planner/plan.py`（决策侧，何时该给照片加运动）。建议的第一版**只做居中缩放**——平移需要构图信息才能不把主体推出画外，而 `composition_score` 目前是 null。

### 4.6 ReferenceAnalysis —— 参考视频的中间产物（M2）

**这不是 EditPlanSpec**，而是编译成 Reference EditPlanSpec 的输入。

**它由哪条 pipeline 产出，取决于 M-1 的 E2 结果**（§0.5）：路径 A 由 VLM 直接产出整个结构；路径 B 由 PySceneDetect 产出 `shots[]` 的时间字段、VLM 只填每个 shot 的语义字段、再由代码聚合出 `shot_duration_stats / pace / semantic_structure`。**两条路径产出同一个结构**，因此下游的编译规则与 `R_reference` 计算完全不受影响——这正是把 ReferenceAnalysis 独立成一层的价值。

```json
{
  "reference_id": "ref_03",
  "duration": 37.8,
  "shot_count": 34,
  "shot_duration_stats": {"mean": 1.15, "median": 0.93, "p10": 0.5, "p90": 2.1, "std": 0.62},
  "shots": [
    {"index": 0, "start": 0.0, "end": 1.4, "shot_type": "wide",
     "semantic_role": "opening_environment", "motion": "slow_pan",
     "energy": 0.4, "transition_out": "hard_cut", "has_text": false}
  ],
  "pace": "fast",
  "transition_density": 0.08,
  "music": {"bpm": 128.0, "beat_times": [0.0, 0.47], "beat_sync_strength": 0.82,
            "cut_to_beat_offset_ms": {"mean": 42, "std": 55}},
  "semantic_structure": ["opening_environment","person_activity","detail","food","ending_environment"],
  "text_pattern": {"subtitle_density": "medium", "avg_chars": 8, "style": "bold_center"},
  "style_tags": ["travel","clean","energetic","minimal_transition"]
}
```

### 4.6.1 StylePrior —— B1 的产出（N 条参考视频聚合）

`ReferenceAnalysis` 是**单条**参考视频的分析单元，B1 和 B2 共用它。区别在下一步：

```
B2:  1 × ReferenceAnalysis  →  slots[]        序列，直接当模板
B1:  N × ReferenceAnalysis  →  StylePrior     分布，当先验
```

```json
{
  "prior_id": "sp_01",
  "source_reference_ids": ["ref_01", "ref_02", "ref_03"],
  "shot_duration": {"mean": 1.2, "median": 0.95, "p10": 0.5, "p90": 2.4, "std": 0.7},
  "shot_scale_distribution": {"wide": 0.22, "medium": 0.31, "close_up": 0.35, "extreme_close_up": 0.12},
  "shot_scale_bigrams": {"wide->medium": 0.18, "medium->close_up": 0.24, "close_up->wide": 0.11},
  "role_bigrams": {"opening_environment->person_activity": 0.15, "detail->food": 0.09},
  "transition_density": {"mean": 0.09, "std": 0.04},
  "subtitle_density": "medium",
  "beat_sync_strength": {"mean": 0.78, "std": 0.11},
  "section_count": {"mean": 4.2, "std": 0.8},
  "style_tags": ["travel", "clean", "energetic"],
  "consistency": 0.81
}
```

**为什么是 bigram 而不是完整序列**：五条视频的镜头序列长度不同、内容不同，没法逐位平均。但"全景之后倾向接中景"这种**转移概率**是可以聚合的，而且那正是"剪辑语法"的实质。

**`consistency` 是必需的**：N 条参考视频如果风格差异很大（用户传了五条毫无共同点的视频），聚合出的先验是无意义的平均值。这个字段量化分歧，**低于阈值时应该告诉用户而不是硬套**。

**编译规则 `ReferenceAnalysis → Reference EditPlanSpec`**（仅 B2）：
- `shots[]` → `slots[]`，逐一映射 `shot_type / semantic_role / motion / energy / transition_out`；`duration` → `target_duration`，`duration_range = [0.7*d, 1.4*d]`。
- 参考视频镜头数远多于用户素材可支撑的数量时，按 `energy` 与角色重要性合并/裁剪 slot，并把次要 slot 标为 `required=false`。
- `shot_duration_stats` + 逐 shot 时长 → `rhythm.shot_duration_pattern`；`music.beat_sync_strength` → `rhythm.music_sync_strength`。
- `text_pattern.subtitle_density` → `text.subtitle_density`（文字**内容**不迁移，只迁移密度与风格）。
- **`music` 只贡献 BPM/beat 特征，参考视频的音轨绝不进入输出**（§16.1）。

### 4.7 交互层数据结构

`EditOperation`、`EditScope`、`ProjectVersion` 的定义见 **§11.3 / §11.6 / §11.7**，同样落在 `packages/aive/specs/`，同样受 `spec_version` 管理。它们与 `EditPlanSpec` / `TimelineSpec` 是同一等级的领域模型，不是 UI 层的临时结构。

---

## 5. 模块契约

每个模块是纯函数式的一步，输入输出都是上一章定义的结构。这是 M0 的实现骨架。

### 5.1 Material Understanding

```python
def preprocess_asset(path: Path, project_id: str) -> Asset:
    """探测时长/分辨率/fps/旋转/是否有音轨；生成统一代理文件（540p, 30fps）用于分析。"""

def split_segments(asset: Asset) -> list[RawSegment]:
    """PySceneDetect ContentDetector(threshold=27, min_scene_len=15帧)。
    额外规则：单段 > 6.0s 时按 4.0s 等分切开；单段 < 0.6s 时并入相邻段。"""

def analyze_segment(seg: RawSegment, vlm: VideoUnderstandingProvider,
                    emb: EmbeddingProvider) -> MaterialSegment:
    """OpenCV 质量指标（4.5 节表格）+ VLM 语义描述 + 三帧平均 embedding。"""
```

**M0 的 VLM 调用粒度**：每 segment 取中间帧一张图 → 图像理解，返回 `semantic / shot_type / semantic_role / camera_motion / people`。**不要在 M0 做逐 segment 的视频级理解**（成本高一个数量级）。

这是 §0.5 路径 B 形态在 Material 侧的对应选择：时间由确定性切分决定，模型只回答语义。M-1 的 E1 若证明视频级理解在 `camera_motion` 与「值得用」判断上显著更好（这两项恰好是单帧最弱的），再评估升级为多帧或视频级——升级只影响 `MaterialUnderstandingProvider` 的实现，不影响 `MaterialSegment` 结构。

### 5.2 Intent Parser

```python
def parse_intent(prompt: str | None, defaults: IntentDefaults) -> Intent:
    """LLM 结构化输出。prompt 为空时返回 defaults。
    抽取：target_duration / style[] / user_constraints[] / 隐含的 pace 与 subtitle 倾向。
    LLM 不可靠时降级为规则：正则提取"NN 秒"，风格词表匹配。"""
```

默认值：`target_duration=45.0`、`aspect_ratio="9:16"`、`style=["travel"]`、`duration_tolerance=0.15`。

### 5.3 Editing Planner

```python
def plan(intent: Intent, pool: SegmentPool, n: int,
         reference_plan: EditPlanSpec | None,
         llm: LLMProvider) -> list[EditPlanSpec]:
    """产出 n 个 EditPlanSpec 候选。"""
```

**两阶段，不允许一步到位**（v1.1 §9）：

1. **Story / Rhythm Planning**：输入是**素材池的聚合摘要**（各 `semantic_role` 的数量与总时长、整体风格标签、可用素材总时长），**不是 80 条素材的完整清单**。输出 `story.sections` 与 `rhythm`。
2. **Slot Definition**：按 section 展开为 `slots[]`，逐 slot 指定 `semantic_role / shot_type / target_duration / energy`。

约束：`sum(slots[].target_duration)` 必须落在 `target_duration ± tolerance` 内；生成后由代码强制归一化（等比缩放各 slot 时长），不依赖 LLM 自己算对。

**候选多样性**：n 个候选不能靠调 temperature 随机撞。M0–M1 用显式扰动策略生成：
```
候选 0: 基准
候选 1: pace 提高一档（slot 数 +25%，单镜头更短）
候选 2: pace 降低一档
候选 3: 不同 story 结构（换 section 顺序或数量）
候选 4: 更高 transition_density
候选 5: 更低 transition_density + 更长开场
```

Reference 模式下，`reference_plan` 作为基准，扰动只在 `duration_range` 与非 `required` slot 上进行。

### 5.4 Material Matcher / Adaptation

```python
def match(plan: EditPlanSpec, pool: SegmentPool) -> TimelineSpec:
    """为每个 slot 选择 segment 并做适配，产出可执行时间线。"""
```

**打分函数**（v1.1 §8.1 Step 4）：

```
S(i,j) = w_s·S_semantic + w_v·S_visual + w_m·S_motion + w_q·S_quality + w_e·S_emotion
```

M0–M2 权重初值：`w_s=0.35, w_v=0.20, w_m=0.15, w_q=0.20, w_e=0.10`（写成配置常量，M4 做 ablation）。

- `S_semantic`：`semantic_role` 精确匹配得 1.0，同组近似角色 0.6，否则 0.0
- `S_visual`：slot 的 `shot_type` 与 segment 一致得 1.0，相邻档（wide↔medium）0.5
- `S_motion`：`motion` 一致 1.0，`static` 与任意 0.5
- `S_quality`：直接取 `technical_quality`
- `S_emotion`：intent 的 style 词与 segment `emotion` 的重合度
- **M2 起追加**：slot 的目标 embedding（由 reference shot 或 slot 描述编码）与 segment embedding 的余弦相似度，并入 `S_visual`

**分配算法**：M0 用贪心（按 slot 顺序取最高分且未被占用的 segment）。**M1 起改为匈牙利算法/最小费用最大流**做全局最优分配——贪心会导致后段 slot 只能捡剩下的差素材，这是"AI 剪辑后半段变差"的典型成因。

**去重硬约束（M0 即实现）**：同一 `source_id` 在整条时间线上最多出现 3 次，且**不得连续出现**；相邻两个 clip 的 embedding 余弦相似度 > 0.95 时强制替换（避免 §10.7 列出的 Repetition 缺陷）。

**Adaptation 决策顺序**（依次尝试，每步都写 `adaptation_log`）：
```
1. Trim      —— 在 segment 内选取最佳子区间使时长命中 target_duration
2. Extend/Shorten —— 在 duration_range 内调整目标时长
3. Speed     —— 变速命中时长（限 0.5x–2.0x，超出则放弃）
4. Crop/Reframe —— 画幅不符时（M0 只做居中裁切）
5. Replace Role —— 该角色无候选时，替换为近似角色
6. Skip      —— required=false 且无任何候选时跳过，并把时长按比例分给相邻 slot
7. Merge     —— 相邻同角色 slot 合并为一个更长镜头
```
若某个 `required=true` 的 slot 走完 1–7 仍无候选，则整个候选标记为 `infeasible`，不进入渲染（Structural Critic 直接给最低分）。

### 5.5 Critic

```python
class Critic(Protocol):
    def score_structural(self, plan: EditPlanSpec, tl: TimelineSpec) -> StructuralScore: ...
    def compare_video(self, a: RenderedCandidate, b: RenderedCandidate,
                      ctx: CriticContext) -> PairwiseVerdict: ...

class PassthroughCritic(Critic):
    """M0 占位：structural 恒返回 0.5，compare 恒返回 tie。"""
```

`StructuralScore`（M1 起为纯规则，无模型调用，因此免费且可解释）：

| 维度 | 计算 |
|---|---|
| `slot_coverage` | 非 skip 的 slot 占比 |
| `shot_diversity` | 使用的不同 `source_id` 数 / clip 数；`shot_type` 分布熵 |
| `rhythm_fidelity` | 实际时长序列与 `rhythm.shot_duration_pattern` 的 1 - 归一化 MAE |
| `semantic_match` | 各 clip 的 `S_semantic` 均值 |
| `duration_error` | 相对 `target_duration` 的偏差 |
| `repetition_penalty` | 相邻相似镜头、同源重复的扣分 |
| `violation_penalty` | 4.4 节软错误计数 |
| `reference_similarity` | **仅 Mode B**：结构/节奏/shot pattern/角色序列相似度（见 10.3） |

`overall = 加权和`，权重写成配置。M4 之前 Stage 1 全靠这个规则分，**不需要模型**。

`compare_video` 是真正的 Taste Critic（M3 训练，M4 接入），输入是两个 proxy 渲染结果 + 上下文，输出 `A / B / Tie` + 六维理由。

### 5.6 Renderer

```python
class RendererProvider(Protocol):
    def render(self, tl: TimelineSpec, quality: Literal["proxy","hq"],
               out_path: Path) -> RenderResult: ...

class FFmpegRenderer(RendererProvider): ...
```

**FFmpeg 实现策略（M0）**：两步法，优先鲁棒而非极致效率。
1. 逐 clip 抽取并归一化为统一中间文件（同分辨率/fps/像素格式/编码），处理 `source_in/out`、`speed`（`setpts` + `atempo`）、`crop`。
2. 用 concat demuxer 拼接，叠加音乐轨（`amix`，含 `ducking` 时用 `sidechaincompress`），最后统一编码输出。

理由：单条 `filter_complex` 处理 30+ clip 的命令行既难调试也容易在参数微小变化时整体失败。转场（`xfade`）在 M1 引入时只影响相邻两段的合成，不改变整体结构。

**proxy vs hq**：仅 `render_constraints` 不同（proxy = `608x1080, crf 30, preset veryfast`；hq = `1080x1920, crf 20, preset medium`）。**两档必须走同一份代码路径**，否则 Critic 在 proxy 上的判断无法迁移到最终成片。

**局部渲染**（M2.5 起）：`render(tl, quality, out_path, time_range=(a, b))`。交互式修改只重渲染受影响时间段 + 前后各 1 秒，这是 §11.9 Fast path 能在 5 秒内响应的前提。

### 5.7 Interactive Editing Agent

```python
def resolve(instruction: str, state: ProjectState,
            conv: ConversationMemory, agent: AgentProvider) -> EditCommandPlan:
    """自然语言 -> EditOperation[]。必须先读 state，再做指代消解。"""

def precheck(plan: EditCommandPlan, state: ProjectState) -> list[Violation]:
    """scope 合法性 + lock 命中 + params 引用存在性。代码层硬约束。"""

def apply(plan: EditCommandPlan, state: ProjectState) -> ProjectVersion:
    """执行 operations -> 新版本。任一 operation 失败则整轮回滚。"""
```

完整设计见 §11。**`apply` 是 Agent 与编辑器 UI 的唯一共同入口**——UI 不得绕过它直接改 `TimelineSpec`。

---

## 6. Provider 抽象

**接口定义见 §0.6**（按角色而非按厂商）。本节只补充落地细节，不重复接口声明。

- 目录：`packages/aive/providers/`，每个角色一个模块：`material.py / reference.py / planner.py / embedding.py / rerank.py / critic.py / agent.py / asr.py / renderer.py`。
- 每个模块至少两个实现：`Mock<Role>Provider`（确定性、离线、免费）与由 `MODEL_SELECTION.md` 冻结的真实实现。
- 装配在 `providers/registry.py`：按 §0.6 的**每角色环境变量**实例化，业务层通过 `get_provider(Role.MATERIAL)` 取用，**不允许业务层构造具体实现类**。
- 基类 `BaseProvider` 统一实现：重试（指数退避，最多 3 次）、超时、结构化输出校验失败重试一次并降级、**成本与延迟写入 `api_calls`（含 `role` 列）**。
- **bake-off 期间**：所有角色跑 Mock 实现，pipeline 骨架照常开发。冻结后只改配置，不改业务代码——**如果这时需要改业务代码，说明抽象漏了，必须先修抽象。**

**成本可观测性从第一行代码就要成立**（§16.2），不能事后补。

---

## 7. 数据库

用 SQLite + SQLAlchemy（closed beta 前再迁 PostgreSQL）。以下为逻辑 schema，JSON 列存完整 spec，同时把查询/排序要用的字段提取为独立列。

```
users             (id, created_at, kind)                      -- kind: dev|expert|consumer
projects          (id, user_id, mode, prompt, intent_json, status, created_at)
assets            (id, project_id, path, duration, width, height, fps, has_audio,
                   proxy_path, created_at)
segments          (id, project_id, asset_id, start, end, duration,
                   semantic, semantic_role, shot_type, camera_motion, people,
                   technical_quality, sharpness, exposure_score, stability_score,
                   motion_quality, composition_score, interestingness,
                   emotion_json, has_speech, asr_text, keyframe_paths_json,
                   embedding BLOB, embedding_dim, is_usable, reject_reason)
references        (id, project_id, path, duration, analysis_json, plan_id, created_at)
music_tracks      (id, path, title, license, bpm, beat_times_json, mood_tags_json, duration)
edit_plan_specs   (id, project_id, mode, candidate_index, reference_id,
                   spec_json, created_at)
timeline_specs    (id, project_id, plan_id, duration, spec_json,
                   is_feasible, created_at)
candidate_scores  (id, timeline_id, stage, scorer, overall,
                   dimensions_json, created_at)        -- stage: structural|video
renders           (id, timeline_id, quality, path, duration_sec,
                   render_time_ms, file_size, status, error, created_at)
jobs              (id, project_id, kind, status, progress, stage,
                   error, result_json, created_at, updated_at)
api_calls         (id, project_id, role, provider, model, tokens_in, tokens_out,
                   seconds_processed, cost_estimate, latency_ms, retries,
                   created_at)        -- role: material|reference|planner|retrieval|critic|asr
preference_pairs  (id, project_id, mode, reference_id,
                   timeline_a, timeline_b, render_a, render_b,
                   source, created_at)                 -- source: synthetic|human|product
preference_votes  (id, pair_id, annotator_id, annotator_type,
                   choice, reasons_json, would_post, latency_ms, created_at)
user_corrections  (id, project_id, timeline_id, action, target_slot_id,
                   before_json, after_json, created_at)

-- 交互层（M2.5 起）
project_versions  (id, project_id, parent_version_id, turn_id,
                   user_instruction, plan_snapshot_id, timeline_snapshot_id,
                   preview_render_id, accepted, created_at)
edit_operations   (id, version_id, turn_id, seq, operation, params_json,
                   scope_json, origin, applied, error, created_at)
                   -- origin: chat|editor|agent_plan|system
agent_turns       (id, project_id, version_before, version_after,
                   instruction, route, resolved_scope_json, model, latency_ms,
                   op_count, precheck_violations_json, outcome, created_at)
                   -- route: fast|deep ; outcome: applied|rejected|rolled_back|undone
element_locks     (id, project_id, element_type, element_id,
                   locked_by, created_at, released_at)
                   -- element_type: clip|section|music|subtitle_style
```

**交互层的三条存储规则**：

1. **`project_versions` 存完整 spec 快照，不存增量 diff**（理由见 §11.7）。`plan_snapshot_id` / `timeline_snapshot_id` 指向 `edit_plan_specs` / `timeline_specs` 里的完整记录。
2. **`agent_turns` 记录每一轮的完整审计**：解析出的 scope、走了 fast 还是 deep、预检拦截了什么、最终是 applied 还是 rolled_back。**§12.1 的 Agent 指标全部从这张表统计**，没有它就无法测量 Unintended Change Rate。
3. **`edit_operations.origin` 必须如实记录**。`chat` 与 `editor` 的比例就是 Manual-vs-Agent Edit Ratio，直接回答"用户更信任哪个入口"。

```text
```

**三条关于 preference 存储的硬规则**：

1. **`preference_votes` 一行 = 一个标注者的一次判断，是唯一真相源。** 绝不允许只保存聚合后的 `{A_votes, B_votes}`——那会永久丢失 disagreement 信息，而 disagreement 是 taste cluster 的信号而非噪声。聚合值一律实时计算或存物化视图。
2. **`preference_pairs` 同时记录 `timeline_a/b` 与 `render_a/b`。** 人类看到的是渲染结果（`render_id`），模型的结构化特征来自 `timeline_id`，两者都必须可追溯。v1.1 §18 示例里含义不明的 `edit_04` 由这两个字段替代。
3. **`annotator_type`（expert / consumer）必须逐票记录**，评测时分开统计。专业剪辑质量 ≠ 普通用户愿不愿意发。

---

## 8. 接口

### 8.1 CLI（M0 的主要入口）

```bash
# 一条命令跑通全链路
python -m aive.cli run \
    --materials ./samples/tokyo/ \
    --prompt "帮我剪一个 45 秒的东京旅行 vlog，轻松、有氛围感，不要太多转场" \
    --duration 45 \
    --music music_01 \
    --candidates 6 \
    --out ./out/tokyo.mp4

# 分步执行，便于调试
python -m aive.cli ingest   --materials ./samples/tokyo/ --project proj_001
python -m aive.cli analyze  --project proj_001
python -m aive.cli plan     --project proj_001 --prompt "..." --candidates 6
python -m aive.cli match    --project proj_001
python -m aive.cli render   --project proj_001 --timeline tl_011 --quality hq

# 检查中间产物
python -m aive.cli inspect  --project proj_001 --stage segments|plans|timelines
```

### 8.2 HTTP API（M0 提供，M1 由 Web UI 消费）

```
POST   /projects                       -> {project_id}
POST   /projects/{id}/assets           multipart, 支持多文件 -> {asset_ids[]}
POST   /projects/{id}/reference        multipart, 单文件（M2）-> {reference_id}
POST   /projects/{id}/generate         {prompt, mode, target_duration, n_candidates}
                                       -> {job_id}
GET    /jobs/{job_id}                  -> {status, progress, stage, result}
GET    /projects/{id}/candidates       -> [{timeline_id, scores, proxy_url}]
GET    /projects/{id}/result           -> {timeline_id, render_url, plan_json}
GET    /renders/{render_id}/file       -> 视频文件流
POST   /projects/{id}/corrections      {action, slot_id, params}（M5）
```

`jobs.stage` 取值供前端展示进度：`ingesting | analyzing | planning | matching | scoring | rendering_proxy | rendering_hq | done | failed`。

---

## 9. 分层 Best-of-N 与成本控制

**绝不做 N 次高清渲染。** 三级筛选（v0.3 §14 + v1.1 §15）：

```
Planner 生成 N=6 个 EditPlanSpec
        ↓
Material Matcher 生成 6 个 TimelineSpec
        ↓
Stage 1  Structural Critic —— 不渲染，纯规则打分（免费）
         淘汰 infeasible 与低分候选
        ↓  保留 Top K=3
Stage 2  360p/540p Proxy Render（快，便宜）
         Video Taste Critic 两两比较排序
        ↓  保留 Top 1
Stage 3  1080p High-quality Final Render
```

即 `6 plans → 3 cheap previews → 1 HQ render`，而不是 `6 plans → 6 HQ renders`。

这一条同时解决三件事：验证 Critic 是否有效、控制推理与渲染成本、降低用户等待时间。

**成本与延迟预算（M0 起每个 project 记录实测值，超标即报警）**：

| 阶段 | 目标 | 记录字段 |
|---|---|---|
| 素材分析（50 条素材） | < 5 分钟，< ¥2 | `api_calls` 汇总 + `seconds_processed` |
| 规划 + 匹配（6 候选） | < 60 秒 | `jobs` 各 stage 时间戳 |
| Proxy 渲染（3 个 × 45 秒） | < 90 秒 | `renders.render_time_ms` |
| HQ 渲染（1 个 × 45 秒） | < 120 秒 | 同上 |
| **端到端** | **< 10 分钟** | `jobs.created_at → updated_at` |

**M4 的核心实验**（v1.1 §15）：
```
Baseline: Planner single output
   vs.
Planner + hierarchical Best-of-N + Taste Critic
```
盲测 preference。后者显著更好 → 核心技术假设成立。

---

## 10. Taste Critic 与 Preference 数据

### 10.1 核心技术判断：不要学习"统一审美"

审美不是单一标量。用户 A 喜欢 `slow / cinematic / minimal / long shot`，用户 B 喜欢 `fast / energetic / beat-sync / short shot`，**两者都可能是好剪辑**。

因此系统**不是**：
```
Video → Universal Aesthetic Score
```
而是条件化偏好：
```
R(E | M, P, R_f, U)
```
`E` 候选剪辑，`M` 原始素材，`P` 文字意图，`R_f` 参考视频，`U` 用户偏好表示。

**MVP 不建模 `U`**，只做 `R(E | M, P, R_f)`。但数据结构必须为 `U` 留好位置（`preference_votes.annotator_id/type` + `user_corrections`）。

问的问题永远是"**对于这些素材和这个用户想要的风格，这是不是一个更好的剪辑**"，而不是"这个视频是否 universally 好看"。

### 10.2 Critic 的形式

```
P(E_A > E_B | M, P, R_f)
```
第一版做 **pairwise classification**，不做 scalar reward。

- 输入：Raw Material Summary、Prompt、Reference Summary / EditPlanSpec、Candidate A、Candidate B（EditPlanSpec 结构化特征 + proxy 渲染视频）
- 主输出：`A better / B better / Tie`
- 辅助输出（六维理由，用于可解释性与失败分析）：`shot selection / rhythm / story / music matching / transition / overall aesthetics`

### 10.3 Reference 模式的 reward 分解

```
R(E | M, P, R_f) = λ_a·R_aesthetic + λ_r·R_reference + λ_i·R_intent + λ_t·R_technical
```

`R_reference` **不是像素相似度**，而是：结构相似度、节奏相似度、shot-pattern 相似度、语义角色序列相似度、音乐-剪辑关系、转场密度、字幕风格相似度。

M2 的可计算实现（无需模型，进入 Structural Critic）：

| 分量 | 计算 |
|---|---|
| 结构相似度 | section 序列的编辑距离（归一化） |
| 节奏相似度 | 时长序列 DTW 距离；均值/中位数/标准差的相对偏差 |
| shot-pattern 相似度 | `shot_type` 序列的 n-gram 重合度（n=1,2） |
| 语义角色序列相似度 | `semantic_role` 序列的编辑距离 |
| 转场密度 | `abs(实际 - 参考) / 参考` |
| 音乐-剪辑关系 | 切点相对 beat 的偏移分布与参考的 KL 距离 |

### 10.4 为什么先做 Critic 而不是直接 RL

```
Human Preference Data → Taste Critic → Best-of-N → DPO → RL（必要时）
```

第一阶段只验证一件事：**一个经过 preference fine-tuning 的 Critic，能不能比基础 VLM 更接近人类审美判断。** 如果不能，问题在数据、任务定义或表示上，而不是 RL 算法不够高级。

不是"先训练一个巨型 Agent，然后希望它有审美"。

### 10.5 Human Preference 数据设计

**标注对象**不是"哪个视频更漂亮"，而是"给定原始素材 + 用户要求 + 可选 reference，A 和 B 哪个剪得更好"。

**标注者**：8–12 人 = 4–6 个懂剪辑的人 + 4–6 个普通目标用户。`expert_preference` 与 `consumer_preference` 分别保存，**不混在一起**。

**第一版数据量**：500–1000 preference pairs，分配为：
```
~70%  单标注者
~20%  3 人重复标注   → inter-rater agreement
~10%  3–5 人标注     → held-out evaluation
```
不要把所有样本都做 5 人投票，成本太高。

**标注 UI**：
```
主问题：Which edit is better?   [A] [B] [Tie]
第二层：Why?  shot selection / rhythm / storytelling /
              music matching / transition / overall aesthetics
Lite 用户追加：Would you post this?  [Yes] [Maybe after editing] [No]
```
最后一问未来可能成为真正的产品 KPI。

### 10.6 审美不一致必须保留

5 人投票结果 `A A B B B`，**不要只保存 `B = GT`**。保存原始票 + `annotator_type` + vote distribution：
```
P(B > A) = 0.6,  P(A > B) = 0.4
```
训练时不要机械地把 3:2 多数票变成硬标签。至少在评测阶段保留 soft distribution，并分别统计 expert 与 consumer 的分布。

**如果两类人稳定出现相反选择，这不是脏数据，而可能代表真实的 taste cluster**（cinematic/slow/minimal vs fast/energetic/beat-driven），是未来 personalization 的基础。

标注者 metadata 只保存真正需要的非敏感信息：`expert/consumer`、风格偏好、年龄区间、内容使用频率。注意隐私。

### 10.7 Synthetic Preference Data

从一个"较好 timeline"自动制造坏版本（`scripts/generate_corruptions.py`），得到 `Good Edit > Corrupted Edit`：

| 破坏类型 | 实现 |
|---|---|
| Bad Rhythm | 随机改变各 clip 时长（保持总时长） |
| Bad Ordering | 打乱局部顺序（section 内洗牌） |
| Bad Shot Selection | 换成语义相近但 `technical_quality` 显著更低的候选 |
| Repetition | 连续插入相似镜头（embedding 相似度 > 0.9） |
| Music Misalignment | 所有切点整体偏移 200–400ms |
| Over-editing | 把 `transition_density` 拉到 0.8 |
| Under-editing | 所有镜头长度固定为均值 |

**这类数据只适合 bootstrap。** 最终仍需要真实人类 A/B 数据，否则 Critic 很可能只学会识别"人工破坏"而非真实审美。因此：合成对不得超过训练集的 50%，且 **held-out 评测集必须 100% 是真人标注**。

### 10.8 第一个真正该做的实验（可在 M2 期间并行启动）

如果只能做一个 AI 实验，不是训练 Planner，而是：**Taste Critic 能否学会剪辑偏好。**

```
准备 20–30 组 travel/daily vlog 素材
每组：1 个较好版本 + 3–5 个 AI/corrupted 版本
→ 100–300 个初始 pair
→ 先用现成 Video-VLM 做 zero-shot pairwise judging
→ 测与专业剪辑者 preference 的一致率
→ 再做 LoRA / preference fine-tuning，看是否明显提高
```

目标结果：`Base VLM 55–60% → Fine-tuned 75–80%`。这就是非常强的 MVP research result。

### 10.9 数据来源边界（硬约束）

优先级：
```
1. Creator / 剪辑师授权数据（同一组 raw footage + 专业成片，天然强监督）
2. 许可清晰的公开数据集
3. Synthetic corruption / synthetic preference
4. 产品内真实 preference 与用户修改
5. 社交平台 engagement —— 仅在合法来源前提下作为 weak signal
```

**不把"未经授权大规模爬取抖音/TikTok 用于商业训练"作为数据战略。** 爆款受 creator、热点、题材、账号体量、发布时间和推荐分发影响，engagement 本身也不是 Editing Taste 的 GT。

### 10.10 数据飞轮

产品上线后最有价值的数据不是公开视频，而是：

| 用户行为 | 信号 |
|---|---|
| 直接导出 | positive preference |
| 删除 shot 7 | shot-selection negative signal |
| 把 1.8s 改成 0.7s | rhythm correction |
| 系统给 A/B/C，用户选 B | 天然 preference pair |
| 点"重新生成" | 整体 reject signal |

```
AI 生成多个版本 → 用户选择/修改 → Preference Log
   → 更新 Taste Critic → 更新 Planner → 更好的默认成片
```

**"可编辑"不只是产品兜底，也是数据采集器。** 这是 `user_corrections` 表从 M5 第一天就要结构化的原因。

---

## 11. Interactive Editing Agent（核心子系统）

### 11.1 产品定义的修正

本项目**不是**一次性的 AI Video Generator，也**不只是**一个 AI-assisted NLE，而是：

> **Conversational, stateful, taste-aware video editing agent.**
> 用户先让 AI 完成大部分剪辑，再通过对话或直接操作不断修正；**AI、编辑器和用户共同维护同一个可编辑项目**，直到用户满意。

这不推翻 §1.2 的 "AI First, Editor Second"——默认入口仍然是 AI 先给成片。它补上的是缺失的另一半：**AI 出片不是流程结束，而是迭代的开始**，编辑器不只是"AI 不完美时的兜底"，还是与对话等价的第二个入口。

```
上传素材 / 参考视频
        ↓
AI 生成第一版成片
        ↓
用户可以：
├── 直接导出
├── 在内置编辑器中手动修改
└── 继续对 AI 说怎么改
        ↓
AI 只修改指定部分
        ↓
局部预览
        ↓
用户继续修改或确认  ──┐
        ↑             │
        └─────────────┘
```

因此 **Interactive Editing Agent 是与 Material Understanding、Reference Understanding、Editing Planner、Taste Critic 并列的第五个核心子系统**，不是"最后加一个聊天框"。它的实现从 M2.5 开始（§13.1），不能推迟到 M5。

### 11.2 两个平等入口，一套底层命令

| 用户做了什么 | 入口 | 产生的领域操作 |
|---|---|---|
| 说"这段短一点" | chat | `trim_clip` |
| 把片段右边界向左拖 | editor | `trim_clip` |
| 说"第 8 秒那个食物镜头换掉" | chat | `replace_clip` |
| 点击片段 → 选另一个候选镜头 | editor | `replace_clip` |

> **硬规则：编辑器 UI 不得直接修改 `TimelineSpec`。** 它必须构造 `EditOperation` 并走与 Agent 完全相同的执行路径（§11.4）。
> 否则对话修改和手动修改会逐渐产生两套互不兼容的状态，undo、版本、锁定和 preference 数据会同时失效——而 §10.10 的数据飞轮**完全依赖**用户修改能被结构化捕获。

### 11.3 EditOperation：唯一的领域操作

```json
{
  "op_id": "op_017",
  "turn_id": "t_009",
  "operation": "trim_clip",
  "params": { "clip_id": "c_017", "new_duration": 0.8 },
  "scope": { "type": "clip_set", "clip_ids": ["c_017"] },
  "origin": "chat",
  "created_at": "..."
}
```

`origin`：`chat`（Agent 从自然语言生成）｜`editor`（UI 直接构造）｜`agent_plan`（Deep path 多步规划的一步）｜`system`（回滚/修复）。

**Operation Catalog**

| operation | 作用层 | 需重匹配 | 需重渲染 | 路由 |
|---|---|:---:|:---:|---|
| `replace_clip` | Timeline | 是（单 slot） | 局部 | Fast |
| `trim_clip` / `extend_clip` | Timeline | 否 | 局部 | Fast |
| `delete_clip` | Timeline | 是（补空位） | 局部 | Fast |
| `reorder_clips` | Timeline | 否 | 局部 | Fast |
| `set_speed` / `set_crop` | Timeline | 否 | 局部 | Fast |
| `edit_caption` / `move_caption` | Timeline | 否 | 局部 | Fast |
| `remove_subtitles` | Timeline | 否 | 全局 | Fast |
| `replace_music` / `set_audio_mix` | Timeline | 否 | 全局 | Fast |
| `lock_clip` / `unlock_clip` | 元数据 | 否 | 否 | Fast |
| `change_rhythm` | **Plan** | 是 | 全局 | Deep |
| `change_story_structure` | **Plan** | 是 | 全局 | Deep |
| `change_style` / `change_audio_mood` | **Plan** | 是 | 全局 | Deep |
| `change_reference_strength` | **Plan** | 是 | 全局 | Deep |
| `change_subtitle_density` | **Plan** | 否 | 全局 | Fast |
| `regenerate_section` | Plan + Timeline | 是（段内） | 局部 | Deep |
| `regenerate_all` | Plan + Timeline | 是 | 全局 | Deep |
| `inspect_project` / `search_material` / `list_alternatives` | 只读 | — | — | Fast |
| `render_preview` / `compare_versions` | 只读 | — | — | Fast |
| `undo` / `redo` / `revert_to` / `commit_version` | 版本 | — | — | Fast |

这张表**同时是 §11.8 的路由表**：路由由 operation 类型静态决定，不由模型临时判断。

**§6 的双层表示在这里兑现价值**：全局抽象修改落到 `EditPlanSpec`（"整体快一点" → `rhythm.pace`；"更像参考" → 提高 reference constraint strength），局部精确修改落到 `TimelineSpec`（"第 8 秒那个镜头换掉" → 替换具体 clip）。**Agent 每次只产出 Spec Diff，然后局部重编译——而不是让模型重新生成整条视频。** 后者既不稳定也无法保证不动到用户没让改的地方。

### 11.4 Agent 执行环路

```
用户指令
   ↓
① 读取 Project Memory（权威状态，不靠聊天记录记住 timeline）
   ↓
② 解析意图 + 解析 scope + 读取 locks
   ↓
③ 生成 Edit Command Plan（EditOperation[]）
   ↓
④ 预检：scope 合法？是否触碰 locked 元素？params 是否指向存在的 clip？
   ↓  失败 → 拒绝执行并说明原因，不进入⑤
⑤ 调用 Tool/Skill → 产生 EditPlanSpec / TimelineSpec 的 Diff
   ↓
⑥ validate_timeline（§4.4 的 10 条不变量）
   ↓  硬错误 → 整个 turn 回滚，向用户说明，绝不产出坏预览
⑦ 局部 proxy 渲染（只渲染受影响时间段 + 前后各 1 秒）
   ↓
⑧ 写入新版本（§11.6）
   ↓
用户确认 / 继续修改 / undo
```

**三条硬约束**：

1. **Agent 不输出 FFmpeg 命令。** 它只能调用 §11.3 目录里的标准化 operation。
2. **Agent 不允许整体重写 timeline。** 除非用户显式要求 `regenerate_all`。
3. **任何 operation 的结果都必须通过 §4.4 的不变量校验**；校验失败即整轮回滚。这是把 §16.4"AI 输出稳定性"落到交互层的具体防线——生成阶段的规则检查在这里同样适用，而且更重要，因为用户已经有一个"还算满意"的版本可以被弄坏。

### 11.5 Skill / Tool 目录

外层是 Agent，**内层是确定性的剪辑工具和 Workflow**。Skill 按域分组，每个 Skill 暴露 §11.3 中的若干 operation：

```
Editing Agent
├── Material Search Skill      search_material / list_alternatives
├── Timeline Edit Skill        replace/trim/extend/delete/reorder/speed/crop
├── Reference Remake Skill     change_reference_strength / apply_reference_layer
├── Subtitle Skill             edit_caption / move_caption / remove_subtitles / density
├── Audio Skill                replace_music / set_audio_mix
├── Preview Render Skill       render_preview
├── Taste Evaluation Skill     compare_versions / 调用 §10 的 Critic
└── Version Control Skill      undo / redo / revert_to / commit_version
```

**产品不是这些 Skill 本身**——它们都是确定性的、可被任何人实现的。产品是 Agent 能否正确理解意图、保持上下文、**控制修改范围**，并给出审美合格的结果。

呼应 `MODEL_SELECTION.md` 的 **FD-M09**：平台侧的 Workflow / Skills 可以用来减少 plumbing，但 `EditPlanSpec`、`TimelineSpec`、operation 执行器、版本图和 preference log **必须留在自有代码库**，不能只存在于某个平台的黑盒画布里。

### 11.6 Scope 与 Lock：最重要的安全机制

交互式 AI 编辑最容易出现的失败是：**用户只想改一处，AI 却把别的部分也改了。**

```json
{
  "scope": {
    "type": "time_range",
    "start": 7.2,
    "end": 10.5,
    "section_ids": [],
    "clip_ids": []
  },
  "preserve": ["opening_section", "music_track", "subtitle_style"],
  "locked_elements": ["clip_001", "clip_002"]
}
```

`scope.type`：`time_range` ｜ `section` ｜ `clip_set` ｜ `global`。**`global` 必须由用户指令明确支持**（"整体""全部""重新剪"），Agent 不得为了省事默认取 `global`。

**worked example** —— 用户说"整体节奏快一点，但开头别动"：

```
Target:    除 opening 外的全部 section
Change:    降低目标 shot duration + 提高 cut density
Preserve:  opening / story order / music
```
而**不是**从头重新生成一版。

**Lock 是一等产品功能**。Lite 编辑器提供直观的 `🔒 锁定这段`；锁定后无论用户后续怎么要求，Agent 都不得修改该元素，除非显式解锁。

> **实现要求：lock 检查在 operation 执行器里做，是代码层硬约束，不是 prompt 里的一句请求。**
> 不能指望模型自觉遵守。命中锁定元素时**拒绝执行并明确告知**，而不是"尽量少动"——后者会让用户失去对锁的信任，而锁是用户唯一能真正控制 AI 的手段。

### 11.7 版本：Diff 而非覆盖

项目状态像代码一样保留历史，形成版本 DAG（支持从任意版本分叉）：

```json
{
  "version_id": "v4",
  "parent_version": "v3",
  "user_instruction": "最后五秒重新剪，情绪更温暖一点",
  "turn_id": "t_009",
  "operations": [
    { "operation": "regenerate_section", "params": { "section_id": "ending" } },
    { "operation": "set_audio_mix", "params": { "original_audio_gain": 0.6 } }
  ],
  "plan_snapshot_id": "plan_012",
  "timeline_snapshot_id": "tl_031",
  "preview_render_id": "r_044",
  "accepted": true
}
```

> **工程决策：每个版本存完整 spec 快照 + operations 列表，不存增量 diff。**
> 理由：MVP 阶段两个 spec 加起来只有几十 KB，完整快照让"恢复到 v3"是 O(1) 且**不可能因为 diff 应用逻辑的 bug 导致状态损坏**；operations 保留是为了可解释性、undo 语义和 preference 数据。存储空间在这里不是约束，状态正确性才是。

支持的语义：`撤销` / `重做` / `恢复到上一版` / `比较两个版本` / **"还是刚才那个版本好"**——最后一句是用户非常自然的表达，Agent 必须能把它解析成 `revert_to(version_id)`，这依赖 §11.8 的 Conversation Memory 记住"刚才那版"指向谁。

### 11.8 三层记忆

| 记忆 | 内容 | 权威性 | 何时实现 |
|---|---|---|---|
| **Conversation Memory** | 当前对话；"这里""刚才那版""那个食物镜头"分别指向谁 | 仅用于**指代消解** | M2.5 |
| **Project Memory** | Assets、Segments、Reference EditPlanSpec、当前 EditPlanSpec/TimelineSpec、Locked Elements、Version History、Candidate Alternatives | **唯一权威状态** | M2.5 |
| **Taste Memory** | 常删大字幕、偏好慢镜头、不喜欢复杂转场、常选环境镜头、喜欢保留现场声 | 偏好先验 | M5+ |

> **硬规则：Agent 每一轮必须先读 Project Memory，不得依赖对话历史里残留的 timeline 内容。**
> 语言模型"记住"的时间线一定会在多轮后与真实状态漂移。正确顺序是：先读状态 → 再用 Conversation Memory 做指代消解 → 再决策。

Taste Memory 最终让用户可以说"**还是按我平时喜欢的方式剪**"——这时系统才真正是一个长期 Agent，而不是一次性生成工具。它在结构上就是 §10.1 里那个被推迟的 `U`；MVP 不实现个性化，但 `user_corrections` 与 `edit_operations` 从 M2.5 起就要结构化落库，否则将来无从学起。

### 11.9 Fast / Deep 双路

| | Fast Path | Deep Path |
|---|---|---|
| 处理 | 删掉这段、字幕小一点、这段短一点、换音乐、撤销 | 后半段有点无聊重新组织、更像参考但不要那么快、把故事改成先看结果再回顾过程 |
| 模型 | 快速小 LLM（角色 ⑥-Fast） | 强 Planner（角色 ⑥-Deep / ③） |
| 流程 | 意图 → 单个 Tool Call → 局部预览 | 读 EditPlanSpec → 多步规划 → 生成 2–3 候选 → Taste Critic → 预览 |
| 目标延迟 | **< 5 秒** | **< 60 秒** |
| 是否触发 Best-of-N | 否 | 是（N=3，只渲染受影响段落） |

**路由由 §11.3 表格中的 operation 类型静态决定**，不交给模型自行判断。流程是：小模型先做意图分类 → 若落在 Deep 集合则升级到强模型。

> 不需要每次用户说"字幕小一点"都调用最昂贵的视频模型。这与 §0.1 的分层路由和 `MODEL_SELECTION.md` §11.2 的多层模型路由是同一条原则在交互层的应用。

### 11.10 与 Best-of-N 的关系

Deep path 的 `regenerate_section` / `regenerate_all` 复用 §9 的三级筛选，但两点收窄：**N=3 而不是 6**（用户在等），且 **proxy 只渲染受影响段落**。Structural Critic 在这里额外承担一个职责——检查重新生成的段落与未修改段落的**衔接**（节奏突变、相邻镜头重复、景别跳跃），这是整体生成时不存在的问题。

---

## 12. 评测体系

不能只用 render accuracy、shot-boundary accuracy 或 VLM score 评测。north-star metric 必须接近"用户愿不愿意用这个结果"。

| 层级 | 指标 | 何时开始测 |
|---|---|---|
| **Technical** | 黑帧、音频断裂、字幕溢出、重复帧、裁切错误、导出失败率 | M0 |
| **Edit Quality** | shot selection、节奏、故事、音乐、transition、style match | M1 |
| **Human Preference** | Blind A/B：Our vs baseline、Our w/o Critic；**保留 vote distribution** | M3 |
| **Reference Remake** | 结构/节奏/shot pattern/style match + Remake Satisfaction | M2 |
| **Interactive Agent** | Edit Intent Success、**Unintended Change Rate**、Scope Resolution、Undo Rate（§12.1） | **M2.5** |
| **Consumer Product** | Direct-export rate、二次修改率、regenerate rate、time-to-post | M5 |
| **Pro Product** | rough-cut acceptance、节省时间、round-trip 成功率 | M6+ |

**最重要的消费者指标**：**"如果这是你的素材，你愿不愿意直接发？"** 这比 CLIP score 或 aesthetic score 更接近产品价值。

**Reference Remake 的两个指标必须分开记录，绝不合并**：
```
1. 像不像我给的参考视频？
2. 即使不看参考，这个视频本身好不好看 / 愿不愿意发？
```
一个复刻得很像但本身难看的结果，和一个好看但不像参考的结果，是两种完全不同的失败，需要不同的修复方向。

**MVP 成功指标全集**：AI Edit Acceptance Rate、Direct Post/Export Rate、Edit Distance After AI（改得越少越成功）、Pairwise Preference Win Rate、Reference Remake Similarity、Remake Satisfaction（Yes/Partially/No）、**Time to Good Video**（从上传到用户满意的时长——因为产品的核心就是减少普通人从素材到"好视频"的成本）。

### 12.1 Interactive Agent 指标（M2.5 起）

成片好不好看之外，还必须评估 Agent 本身：

| 指标 | 含义 | 数据来源 |
|---|---|---|
| **Edit Intent Success Rate** | AI 是否完成了用户要求 | `agent_turns.outcome` + 人工抽检 |
| **Unintended Change Rate** | 是否修改了不该修改的部分 | 版本 diff 与 `scope` 的比对 |
| **Scope Resolution Accuracy** | "这里/后半段/刚才那段"是否定位正确 | `resolved_scope_json` vs 人工标注 |
| **Average Turns to Satisfaction** | 用户要说几轮才满意 | `agent_turns` 计数至导出 |
| **Undo Rate** | 用户是否频繁撤销 AI 操作 | `outcome = undone` 占比 |
| **Preview Acceptance Rate** | 用户是否接受本轮修改 | `accepted` 占比 |
| **Manual-vs-Agent Edit Ratio** | 更常手动改还是让 AI 改 | `edit_operations.origin` 分布 |
| **Time to Good Video** | 上传到满意成片的总时长 | `jobs` + `project_versions` 时间戳 |
| **Direct Export Rate** | 最终是否愿意导出 | 产品事件 |

> **最危险的指标是 `Unintended Change Rate`。**
> 一个 Agent 即使很有创造力，但用户说改一处却改坏三处，产品体验会非常差——而且比"改得不够好"更伤信任，因为它破坏的是用户已经接受的部分。**M2.5 的准入门槛应该由这个指标把关，而不是由"能听懂多少种指令"把关。**

自动化测量方式：对每个 turn，比较 `version_before` 与 `version_after` 的 TimelineSpec，列出所有实际变化的 clip / 字段，检查它们是否**全部落在 `resolved_scope` 内且未命中 `element_locks`**。落在 scope 外的每一处变化都计入 Unintended Change。**这个检查不需要人工，M2.5 第一天就应该自动跑。**

### 12.2 固定评测集

**固定评测集**：M1 起维护 `data/eval/` 下 5 组固定素材（每组 30–50 条 travel/daily vlog 素材 + 3 条参考视频）。每次改动 Planner / Matcher / Critic 都在这 5 组上重跑，人工快速打分。没有固定评测集，"我觉得这版更好"是不可证伪的。

---

## 13. 构建清单

**没有排期。** 下面是六个能力块，块内任务可以任意顺序做，块之间有依赖。每块给出**完成判据**——判据没达到就不要开始下一块。

全文其他章节用 `M0`–`M5` 作为阶段标签（例如"M0 即实现""M1 起改为"），对应关系如下，只表示**先后**，不表示时间：

| 标签 | 对应 |
|---|---|
| `M-1` | 模型 bake-off（§0、§13.7） |
| `M0` | 块 A + B + C 跑通端到端，通过 §14.3 验收 |
| `M1` | 块 C 达到"像有人剪过"（完成判据二） |
| `M2` | 块 D 参考复刻 |
| `M2.5` | 块 E 交互 Agent |
| `M3` | 块 F 的 Critic 部分 |
| `M4` | 块 F 的 Best-of-N 部分 |
| `M5` | 编辑器打磨 + closed beta |
| `M6+` | Pro（MVP 不做） |

```
A 骨架 ──► B 素材理解 ──► C 自动成片 ──┬──► D 参考复刻
  ✅          🔨            🔨          ├──► E 交互 Agent
                                       └──► F 偏好与 Critic
                            D / E / F 之间无强依赖，可并行
```

**当前进度**：块 A 完成；块 B、块 C 的**代码链路已跑通**，两者的模型相关部分（语义标注、成片质量）都待 M-1 冻结后用真实素材验证。下一步可以是块 D（参考复刻，复用块 C 的 matcher）或 M-1 的 eval harness。

另有两件不写代码但必须做的事，见 §13.7。

### 13.1 块 A · 骨架（不依赖任何模型，随时可以开始）　✅ 已完成

```
[x] specs：EditPlanSpec / TimelineSpec / MaterialSegment / EditOperation /
    EditScope / ProjectVersion 的 Pydantic 模型，全部带 spec_version
    → packages/aive/specs/
[x] validate_timeline：§4.4 的 10 条不变量，硬错误拒绝渲染、软错误记录扣分
    → specs/validate.py，21 个错误码；软错误的"扣分"消费方待块 C 的
      Structural Critic 接入，校验器一侧已就位
[x] DB：§7 全部表 + SQLAlchemy models + 迁移（SQLite，禁止写方言 SQL）
    → db/models.py 19 张表；migrations/ 首个迁移含 19 个 create_table
[x] 任务模型：jobs 表 + 进程内 worker + GET /jobs/{id} 轮询
    （不引入 Redis / Celery，等 closed beta 再说）
    → jobs.py + apps/api/main.py
[x] providers：六个角色的 async 接口 + 全套 Mock 实现 + registry
    + 成本记录基类（写 api_calls，含 role 列）
    → providers/，实际落了 9 个角色（含 ⑥-Fast/⑥-Deep、ASR、Renderer）
[x] renderer：FFmpegRenderer 两步法（逐 clip 归一化 → concat → 音乐轨）
    proxy / hq 双档走同一代码路径；预留 time_range 局部渲染参数
    → renderer/，compiler.py 只做翻译，ffmpeg.py 只做执行
[x] CLI：run / ingest / analyze / plan / match / render / inspect
    → cli.py；db init / validate / render / inspect 可用，
      ingest / analyze / plan / match / run 声明后明确报"属于块 B/C"并退出 2
```

> **完成判据**：手写一份 TimelineSpec JSON → 通过校验 → 渲染出可播放的 MP4；所有测试用 Mock Provider 离线跑通，不联网、不花钱。
>
> **✅ 已达成**：`examples/timeline_demo.json` → `aive validate` 无违规 → `aive render` 产出 6.02s / 1080×1920 / 30fps 可播放 MP4（proxy 档 540×960）；44 个测试全绿，全部使用 Mock Provider，无网络、无 API 花费。

### 13.2 块 B · 素材理解　🔨 确定性部分与照片已完成，语义部分待模型冻结

```
[x] 探测（时长/分辨率/fps/旋转/音轨）+ 代理转码（540p, 30fps）
    → material/probe.py；旋转 tag 与 display matrix 两种来源都读
[x] PySceneDetect 切分（threshold=27, min_scene_len=15帧）
    >6.0s 按 4.0s 等分切开；<0.6s 并入相邻段
    → material/split.py；短段并入邻段而非丢弃，保证 segment 始终铺满整条素材
[x] OpenCV 质量指标四项 + technical_quality 加权（§4.5 公式表）
    → material/quality.py；稀疏光流估抖动，中位数抗单帧异常
[x] is_usable 剔除规则 + reject_reason（回答"这条素材为什么没被用"）
    → material/usability.py；四类原因带具体数值，`aive segments --all` 可逐条看
[~] VLM 语义标注：semantic / shot_type / semantic_role / camera_motion / people
    先做图像级（每 segment 取中间帧），不做视频级
    → 管道已接通并可并发（analyze.py::_semantic_pass），当前走 MockMaterialProvider；
      **真实模型待 M-1 冻结**，届时只改 ACTIVE_STACK 一行，不动管道代码
[x] embedding：首/中/尾三帧平均 + L2 归一化
    先用本地 open_clip ViT-B/32（512 维，零成本、CI 可离线跑）
    生产阶段可切换到云端 embedding + rerank 两段式，接口不变
    → material/embedding.py；torch 惰性导入，确定性部分不因此变重
[x] 照片输入（§4.5.1）：不切分、时长由 slot 分配、
    照片专用的质量权重（**不能让不适用的稳定性指标把照片整批剔除**）、
    渲染走 -loop 1 + 可选 Ken Burns
    → AssetKind 贯穿 probe/split/quality/usability/matcher/renderer；
      不适用的指标报 None 而不是 0；不认识的文件明确报出不静默丢弃；
      Ken Burns 未实现（属于 slot.motion 表达的意图，不是渲染器自作主张）
```

> **完成判据**：30 条素材 → segments 入库；能看到合理的时长、质量分和角色分布；能逐条解释为什么某素材被剔除。
>
> **进度**：`aive ingest` / `aive analyze` / `aive segments` 已可用，4 条素材 → 8 segments 全流程跑通，质量分在同一条素材内可区分（0.54–0.88）。**完整判据要等 30 条真实素材**——合成素材没有真实的虚焦、过曝和抖动，验证不了剔除规则的实际表现。

### 13.3 块 C · 自动成片（Mode A）　🔨 链路已跑通，质量待真实素材验证

```
[x] Intent Parser：prompt → intent（LLM 结构化抽取，失败降级为规则）
    → planner/intent.py；规则层是真降级不是占位，模型缺席/超时/输出不合法都走它
[x] 两阶段 Planner：story/rhythm 规划 → slot 定义
    时长由代码强制归一化，不依赖 LLM 自己算对
    → planner/plan.py；planner 只读素材池聚合摘要，永远看不到具体 clip 列表
[x] 候选多样性：6 个候选用显式扰动策略生成，不靠 temperature 随机撞
    → planner/variants.py；baseline/faster/slower/restructured/rhythmic/long_opening
      实测候选 slot 数 17–34，差异来自结构不是采样噪声
[x] Matcher 打分五项（semantic / visual / motion / quality / emotion）
    → matcher/score.py + affinity.py；角色替代表刻意不对称
      （detail 可以顶 food，反过来不行）
[~] 分配：先贪心跑通，再换全局最优（匈牙利 / 最小费用流）
    → matcher/assign.py；**当前是"稀缺优先 + 交换修复"而非纯时间序贪心**，
      直接针对"后半段捡剩饭"这个失效模式；全局最优仍是后续升级
[x] 去重硬约束：同源最多 3 次、不得连续、相邻 embedding 相似度 > 0.95 强制替换
    → 另加重用衰减（1.0/0.72/0.45）：**光有上限不够，重用必须是兜底不是偏好**
[x] Adaptation 七步 + adaptation_log
    → matcher/adapt.py；trim 从中间取（镜头首尾最可能含转场残留）
[x] Structural Critic（纯规则八维，不调模型，免费）
    → critic/structural.py；硬违规直接清零，坏时间线不可能赢比较
[x] 音乐：内置 8–12 首明确授权曲目 + BPM/beat 预计算 + 用户上传
    → music.py；**无 license sidecar 的音频一律拒绝导入**，不做猜测
[ ] 字幕轨：ASR → subtitle clips（M0 留空，此块后期补）
    → TimelineSpec 的 subtitle 轨已就位但为空；需要 ASR 角色接真实模型后再做
```

> **完成判据一（端到端可用）**：一条命令从 30 条素材产出 45 秒 MP4，**连跑 5 次不崩溃**；播放无黑帧、无音频断裂、无重复帧、无跳切错位；时长落在目标 ±15%。
>
> **✅ 已达成**：`aive run --materials <dir> --prompt "..."` 一条命令 30 条素材 → 45.08s / 1080×1920 MP4；连跑 5 次全部成功且结果完全一致（确定性）；冠军候选 17 clips 用 16 条不同素材，连续同源 0 次，时间线无空洞。
>
> **完成判据二（质量及格）**：把成片给 3 个人看，**至少 2 人认为"像有人剪过"而不是随机拼接**。
>
> **⏳ 未验证**：合成素材（`testsrc2` 图案）没有真实内容，语义标注还走 Mock，**"像不像有人剪过"必须用真实 travel vlog 素材 + 冻结后的 VLM 才能判定**。当前六个候选分数挤在 0.94–0.95，也是因为素材同质——真实素材才会拉开差距。

### 13.4 块 D · 参考视频（Mode B1 风格参考 + B2 模板复刻）

```
共用基础　✅ 已完成
[x] 参考视频切分 + 逐 shot 语义 → ReferenceAnalysis（单条的分析单元）
    → reference/analyze.py，shot-first（§0.5 路径 B）；
      转场类型由帧差分布判定（reference/transitions.py），
      不是把每个边界都当硬切
[x] 参考视频入库：仅接受用户上传文件，不实现 URL 抓取（§16.1）
    → add_reference() 只收 Path，CLI 没有 --url 选项
[x] 参考视频音轨只提 BPM/beat/情绪，绝不进入输出成片
    → reference/audio.py；抽出的音频在 finally 里无条件删除，
      ReferenceAnalysis 结构里没有任何指向像素或音频的字段（有测试守着）
[x] 分析用的关键帧在分析后删除，只留抽象特征（§16.1）

B2 · 模板复刻（一条）
[ ] ReferenceAnalysis → Reference EditPlanSpec 的编译规则（§4.6）
[ ] slot 数量不匹配时的合并 / 裁剪 / required 标注
[ ] slot 目标 embedding 并入 S_visual
[ ] 层级控制映射：只保留节奏 / 只保留结构 / 不要字幕 / 更像 / 自由一点

B1 · 风格参考（多条）
[ ] N × ReferenceAnalysis → StylePrior（§4.6.1）
[ ] StylePrior 塑造 Planner：时长分布、景别 bigram、角色 bigram、
    转场密度、section 数量倾向
[ ] consistency 低于阈值时告知用户，而不是硬套一个无意义的平均值

评估
[ ] R_reference 六个相似度分量（结构 / 节奏 / shot-pattern /
    角色序列 / 转场密度 / 音乐-剪辑关系）—— B2 用逐槽位比对，
    B1 用分布距离（KL / Wasserstein），**两者不是同一个指标**
[ ] 三栏 Demo：左=参考视频，中=我的素材，右=My Version
```

> **完成判据**：测试用户对两个问题分别给出正向评价，且**两个指标分开记录**——「像不像我给的参考视频？」和「即使不看参考，这个视频本身好不好看／愿不愿意发？」
> 复刻得很像但难看，和好看但不像参考，是两种不同的失败，修复方向也不同。

### 13.5 块 E · 交互 Agent

```
[ ] EditOperation 定义 + 执行器（先做 5–8 个核心 operation：
    replace / trim / delete / regenerate_section / change_rhythm /
    remove_subtitles / replace_music / undo）
[ ] Scope 解析 + Lock 检查 —— 在执行器代码里，不是在 prompt 里
[ ] 版本图：完整快照 + operations；undo / redo / revert_to / compare
[ ] 局部 proxy 渲染（受影响时间段 + 前后各 1 秒）
[ ] Fast / Deep 静态路由（由 operation 类型决定，不由模型判断）
[ ] 编辑器 UI —— 必须构造 EditOperation 走同一个 apply()，
    不得直接修改 TimelineSpec
[ ] Unintended Change Rate 自动测量（§12.1）
```

> **完成判据**：连续 8 轮编辑指令后项目状态仍然正确；**简单确定性指令的越界修改次数为 0**；命中锁定元素时明确拒绝而不是"尽量少动"。
> 这一块的门槛由 Unintended Change Rate 把关，不由"能听懂多少种指令"把关。

### 13.6 块 F · 偏好与 Critic

```
[ ] 标注页面：A / B / Tie + 原因六选 + Would you post this?
[ ] 合成破坏脚本七种（§10.7）
[ ] 逐票存储：一行一票，保留 annotator_type 与 vote distribution
    合成对不得超过训练集 50%；held-out 必须 100% 真人标注
[ ] 收集 500–1000 preference pairs（70% 单标注 / 20% 三人 / 10% 三到五人）
[ ] zero-shot baseline → LoRA / preference fine-tuning
[ ] 分层 Best-of-N 接入：PassthroughCritic → 真 Critic
    6 plans → Structural 筛到 3 → proxy 渲染 → Taste Critic → 1 个 HQ
```

> **完成判据一（Critic 有效）**：held-out 上对 **expert 多数票的 agreement ≥ 70%**，且比 prompted base VLM **高 ≥ 10 个百分点**；expert 与 consumer 分开报告。
> **完成判据二（Best-of-N 有效）**：Best-of-N 相对单次生成的 blind A/B win rate **显著 > 50%**，且成本与延迟落在 §9 预算内。

### 13.7 两件不写代码但必须做的事

**模型 bake-off**（§0）：六个角色分别选型，评测集 E1–E6。主要成本是人工标注 GT，不是 API 费用。清单见 `MODEL_SELECTION.md` §5 与附录 D。**在 baseline stack 冻结前，不要大规模实现依赖具体模型行为的代码**——块 A 和块 B 的确定性部分不受影响，可以先做。

**固定评测集**（§12.2）：5 组素材，每组 30–50 条 + 3 条参考视频。每次改 Planner / Matcher / Critic 都在这 5 组上重跑。没有它，"我觉得这版更好"不可证伪。

### 13.8 全程不做的东西

```
完整专业 timeline editor      Premiere XML / DaVinci 工程 / 剪映兼容
复杂 mask / compositing       复杂关键帧 / 精确滤镜逆向
自研媒体引擎                  自训 foundation video model
RL / GRPO                     个人 taste embedding
原生 iOS / Android            多人协作 / 完整 DAM
照片输入（块 C 之后再议）      pixel-level 复刻 / LUT 逆向
```

**判断新功能要不要做的唯一标准**：它是否直接提升「AI 默认成片更好」「复刻更像」「用户更少修改」三个目标之一？否则不做。

## 14. 工程环境与验收

### 14.1 环境与依赖

```
Python 3.11+
ffmpeg / ffprobe（系统安装，命令行调用）
uv 或 poetry 管理依赖

核心依赖：
  fastapi, uvicorn, pydantic>=2, sqlalchemy, alembic
  opencv-python, scenedetect, numpy, pillow, librosa
  open_clip_torch, torch（CPU 即可）
  typer（CLI）, httpx, tenacity（重试）
  pytest, pytest-asyncio
```

配置走环境变量 + `.env`（`pydantic-settings`）。**每个模型角色独立配置，不允许全局 `AIVE_MODEL`**：

```
AIVE_DB_URL=sqlite:///./data/aive.db
AIVE_MEDIA_ROOT=./data/media
AIVE_FFMPEG_BIN=ffmpeg

# 模型选择集中在 packages/aive/model_registry.py：
#   CATALOG        全部候选 + 能力 + 价格 + 待确认项
#   ACTIVE_STACK   当前选择 —— 换模型改这一行
# 下面的变量按角色覆盖 ACTIVE_STACK，值是 catalog key 不是厂商 model id。
AIVE_ROLE_MATERIAL_MODEL=mock
AIVE_ROLE_REFERENCE_MODEL=mock
AIVE_ROLE_PLANNER_MODEL=mock
AIVE_ROLE_EMBEDDING_MODEL=local-openclip-vitb32
AIVE_ROLE_RERANK_MODEL=mock
AIVE_ROLE_CRITIC_MODEL=passthrough
AIVE_ROLE_AGENT_FAST_MODEL=mock
AIVE_ROLE_AGENT_DEEP_MODEL=mock
AIVE_ROLE_ASR_MODEL=mock

# 厂商凭证由各 ModelCard 的 base_url_env / api_key_env 指定，
# 业务代码永远不读 key；model id 一律钉死快照，禁止滚动 alias
```

> **`packages/aive/model_registry.py` 是模型决策的唯一位置。** 业务代码不出现厂商名、不出现 model id、不读 API key，只向 `get_provider(Role.X)` 要角色。换模型 = 改 `ACTIVE_STACK` 一行或设一个环境变量；**如果换模型需要动这个文件之外的代码，说明抽象有洞，必须先补洞再换。**

### 14.2 仓库结构

```
ai-video-editor/
├── apps/
│   ├── api/                 # FastAPI
│   └── web/                 # Next.js（块 C 完成判据二之后再建）
├── packages/aive/
│   ├── specs/               # 全部领域模型 + validate.py
│   ├── providers/           # 六个角色：material / reference / planner /
│   │                        # retrieval / critic / agent / asr / renderer
│   │                        # 每个角色 = Protocol + Mock + 真实实现
│   ├── material/            # preprocess / split / analyze / quality
│   ├── reference/           # ReferenceAnalysis → Reference EditPlanSpec
│   ├── planner/             # intent parser + story/rhythm + slot 定义
│   ├── matcher/             # 打分、分配、Adaptation       ← 语义决策
│   ├── critic/              # Passthrough / Structural / VideoTaste
│   ├── renderer/            # RendererProvider, FFmpegRenderer
│   │   └── compiler/        # TimelineSpec → FFmpeg 命令   ← 纯翻译
│   ├── agent/               # resolve / precheck / apply / versions / locks
│   ├── db/                  # SQLAlchemy models + session
│   ├── roles.py             # 十个模型角色（领域概念，两层共用）
│   ├── model_registry.py    # ★ 模型决策的唯一位置：CATALOG + ACTIVE_STACK
│   └── cli.py
├── pipelines/               # preprocess / analyze_material / analyze_reference
│                            # generate_candidates / rank_candidates / render
├── data/{raw,processed,preferences,eval,model_eval,media}/
├── experiments/{critic_baseline,best_of_n,preference_ft}/
├── scripts/{generate_corruptions,build_preference_pairs,evaluate_critic}.py
├── tests/
└── docs/
    ├── TECHNICAL_PLAN.md    # this file - the single execution baseline
    ├── MODEL_SELECTION.md   # model roles, bake-off, provisional stack
    ├── source/              # the documents converged into the two above
    └── {architecture,edit_plan_spec,timeline_spec,
         annotation_guideline,evaluation,model_changelog}.md
```

**`matcher/` 与 `renderer/compiler/` 的分界线**：任何需要判断"哪个镜头更合适""这段该多长"的代码属于 `matcher/`；`compiler/` 只做 `TimelineSpec → FFmpeg 命令` 的机械翻译，不含任何剪辑判断。违反这条会导致更换渲染后端时必须重写匹配逻辑。

### 14.3 端到端验收测试

块 A + B + C 跑通后必须全部通过。

**自动化（`pytest`，全部用 Mock Provider，不联网、不花钱）**

1. `test_specs`：所有 spec 序列化往返；`validate_timeline` 对 10 种构造出的违规输入正确报错。
2. `test_material`：给定一条 60 秒测试视频，切分出 ≥ 3 个 segment，质量指标全部落在 [0,1]。
3. `test_planner`：给定 mock 素材池产出 6 个候选，每个的 `sum(slots.target_duration)` 落在 `45 ± 15%`，且 6 个候选彼此不同（slot 数或时长序列有差异）。
4. `test_matcher`：TimelineSpec 全部通过硬不变量 1–6；无同源镜头连续出现；`adaptation_log` 记录了所有非平凡操作。
5. `test_renderer`：渲染 3 个 clip 的极小时间线，`ffprobe` 校验输出时长与 `TimelineSpec.duration` 误差 < 0.1 秒，分辨率与 fps 符合 `render_constraints`。
6. `test_e2e_mock`：CLI 全链路跑通，产出文件存在且 > 0 字节。
7. `test_determinism`：同样输入 + 同样 seed 产出同样的 EditPlanSpec 与 TimelineSpec。

**人工检查（真实素材，30–50 条 travel vlog）**

8. 连跑 5 次不崩溃，每次都产出可播放的 MP4。
9. 播放无黑帧、无音频断裂、无重复帧、无明显跳切错位。
10. 时长落在目标 ±15% 内。
11. `inspect --stage timelines` 能人工核对"这个 clip 来自哪条素材的哪一段、对应哪个 slot、经过了什么适配"。
12. `api_calls` 表有完整成本记录，端到端延迟落在 §9 预算内。

> 这一关**不要求成片好看**，只要求链路正确、可观测、可复现。"好看"是块 C 完成判据二的事。

## 15. 工程约定

1. **一切时间用秒（float）**，不用帧号或时间码。跨模块传递帧号是本项目最容易产生的一类 bug。
2. **所有 spec 都有 `spec_version`**，读取时校验。M0 就要有，否则后续格式演进无法兼容旧数据。
3. **确定性**：Planner 与 Matcher 接受 `seed`，同样输入 + 同样 seed 必须产出同样输出。否则无法做 ablation 和 bug 复现。
4. **中间产物全部落盘**：每个 project 目录下保存所有 EditPlanSpec、TimelineSpec、proxy 渲染、日志。单人调试时这是唯一的可观测性来源。
5. **禁止在业务层直接调厂商 SDK**，一律走 `providers/` Protocol（第 6 章）。
6. **禁止 Renderer 读取 EditPlanSpec**。Renderer 的输入只有 TimelineSpec。这条保证了未来换渲染后端或导出 NLE 工程时不需要理解剪辑意图。
7. **CI 只用 Mock Provider**，测试不得依赖网络或 API 额度。
8. **禁止用大模型做确定性计算**（§0.1 分层路由）。镜头边界、模糊度、beat 位置、任何时长与速率运算，一律用确定性方法。发现业务代码里出现"让 LLM 判断这一帧清不清晰"这类调用，视为架构缺陷。
9. **禁止全局单一模型配置**。每个角色独立配置（§0.6），不允许出现 `AIVE_MODEL` 这种全局变量。
10. **模型版本必须钉死到快照 ID**，禁止滚动 alias（§0.7）。
11. **编辑器 UI 不得直接修改 `TimelineSpec`**，必须构造 `EditOperation` 走 `apply()`（§11.2）。发现 UI 层直接写 spec，视为架构缺陷。
12. **Agent 不得输出 FFmpeg 命令，不得整体重写 timeline**（§11.4）。
13. **每个 PR/提交必须能回答**：它服务于"AI 默认成片更好""复刻更像""用户更少修改"中的哪一个。

---

## 16. 风险与硬性边界

### 16.1 Reference 的版权与风格边界（不可妥协）

系统迁移的是：
```
结构 / 节奏 / shot pattern / 镜头语言 / 转场密度 / 音乐-剪辑关系 / 字幕风格特征
```
**而不是**复制：
```
受保护的具体画面 / 原视频音乐文件 / 字幕原文 / 品牌素材 / 受版权保护的 motion graphics
```

工程执行：
- 参考视频**仅接受用户上传文件，不实现 URL 抓取**。
- `ReferenceAnalysis` **只保存抽象特征**，不保存源画面帧（分析用的关键帧在分析完成后删除，只留特征与 embedding）。
- 参考视频的音轨**只用于提取 BPM/beat/情绪特征**，绝不进入输出成片。
- 参考视频的字幕**只提取密度与风格，不提取文字内容**。

原则一句话：**transfer editing grammar, not protected content.**

### 16.2 成本与延迟

Video VLM + Best-of-N + 渲染是消费产品的主要成本风险。缓解措施已内建：分层筛选（第 9 章）、M0 起的 `api_calls` 全量记录、M0 用图像级而非视频级理解、embedding 用本地模型。

### 16.3 Vendor Dependency

百炼、火山等是 **provider，不是 architecture**。以下资产必须属于自己的 domain layer，任何时候都能在两周内换掉底层供应商：
```
EditPlanSpec / TimelineSpec / MaterialSegment Schema
Preference Dataset
Planner / Critic interfaces
Evaluation
```

### 16.4 交互式修改的失控（新增，M2.5 起的头号风险）

比"AI 剪得不够好"更伤用户信任的是"**我只让它改一处，它把别的地方改坏了**"。用户已经接受的部分被破坏，会直接摧毁继续对话的意愿。

四道防线，全部是代码层硬约束而不是 prompt 请求：

1. **Scope 必须显式解析并落库**（§11.6），`global` 只能由用户指令明确支持。
2. **Lock 检查在执行器里做**，命中即拒绝并告知，不做"尽量少动"。
3. **每轮结果过 §4.4 不变量校验**，硬错误整轮回滚，绝不产出坏预览。
4. **Unintended Change Rate 自动测量**（§12.1），把 scope 外的每一处变化都记为缺陷。

### 16.5 AI 输出稳定性

Consumer 产品失败容忍度低。防线是 **candidate generation + Critic + 规则检查（4.4 节不变量）+ 可局部修复（M5 编辑器）**，而不是一次生成直接交付。M0 就要有不变量校验，它是最便宜的一道防线。

### 16.6 审美主观性

不存在唯一"最好看"。必须条件化到内容品类、用户 prompt、reference 和个人偏好，避免训练成 Universal Beauty Score。数据侧的执行是 10.6 节：**disagreement 必须保留，不能被清洗掉。**

### 16.7 数据偏差

只学爆款会学到热点/平台偏差；只学专业剪辑师又可能过度"专业化"。混合 creator、普通用户和产品内真实 preference，并在评测中始终分开报告 expert 与 consumer。

---

## 17. 一句话技术路线

> 先跑通两条 Lite 主链路：① `Material → Auto Edit → Video`；② `Reference Video → Reference EditPlanSpec → User Material Remapping → AI Remake`。随后用 Human Preference 学出 Conditional / Reference-aware Editing Taste Critic，通过分层 Best-of-N 同时提高"剪得好不好"和"复刻得像不像"；等真实用户行为数据积累后，再训练 Planner、做个性化和必要的 RL。

两条链路共同构成 Lite，不是两个独立产品：

> **不知道怎么剪 → AI 帮你做审美和剪辑判断。**
> **知道自己喜欢什么但不会复刻 → 把参考视频作为 Aesthetic Prompt，让 AI 用你的素材做出 My Version。**

---

## 附录 A：MVP 完成定义（Lite）

**功能层**
```
✓ 用户能上传 20–100 条素材        ✓ 能上传一条参考视频
✓ 系统能自动分析素材              ✓ 能生成 Reference EditPlanSpec
✓ 能生成普通 AI 自动成片          ✓ 能用用户素材生成 AI Remake
✓ 能渲染并导出 MP4                ✓ 用户能做基础替换/删除/长短调整
```

**技术验证层**
```
✓ Preference annotation pipeline 可用
✓ 收集第一批 500–1000 preference pairs（含 expert/consumer 分层与 vote distribution）
✓ 有 Base VLM Critic baseline
✓ 有 human agreement 统计
✓ Taste Critic 完成至少一次可复现实验
✓ Best-of-N 与 single-generation 做过 blind comparison
```

**产品验证层**（邀请真实目标用户测试并记录）
```
Direct Export Rate      AI Edit Acceptance Rate    Remake Satisfaction
Reference Similarity Rating    修改次数    完成一个满意视频所需时间
```

达到这里，才进入"是否找人扩大项目"的决策点。

---

## 附录 B：MVP 最终 Demo

```
Step 1  上传我手机里的 40 个旅行视频
Step 2  选择"自动帮我剪"或"像这个视频一样剪"
Step 3  等待
Step 4  直接播放一个 30–60 秒成片
Step 5  不喜欢某一段 → 点它 → 换一个镜头 / 短一点 / 重做
Step 6  Export
```

用户完全不需要理解 Agent、VLM、IR、Reward Model、RLHF、FFmpeg、Timeline。做到这一点，MVP 就完成了。

---

## 附录 C：后期招人顺序（M2/M3 跑起来之后）

1. **强产品 / Full-stack 工程** —— 能把 AI pipeline + backend + frontend + video infra 真正做成产品
2. **懂剪辑 / creator** —— 不是只来"评价结果"，要参与 annotation rubric、editing grammar、failure taxonomy、preference evaluation
3. **Video / Multimodal ML** —— 在 Preference Critic 已证明有价值后再扩大模型侧工作
4. **后期才需要** —— NLE / media infra 专家、mobile、growth、design

**单人阶段最危险的事**：把自己变成"视频播放器工程师 + FFmpeg 工程师 + Agent framework 工程师 + 前端工程师 + 模型训练工程师"，结果半年后仍然没有用户可以试。

---

## 附录 D：`MODEL_SELECTION.md` 状态与待补项

**状态：v1.0 已交付**，覆盖了平台深度调研、候选矩阵、五项 bake-off 任务、Taste Critic 的四阶段路线、Provider 抽象、Model Registry、成本与区域策略。其 `FD-M01`–`FD-M10` 已收录为 §0.8。

**必须补齐才能解除 M0 门禁的项**（原文 §14 + 本轮新增）：

```
[ ] 各挑战者的准确 model_id 与多模态能力（控制台/API smoke test 确认，不能猜）
[ ] 各候选的视频时长/大小/采样上限与结构化输出实测
[ ] ASR 的 timestamp / diarization 实测能力
[ ] 各平台中国区价格快照
[ ] 各平台视频数据保留策略与跨境方案
[ ] 可训练 Critic 的视频样本上传限制
[ ] ★ Interactive Editing Agent（角色 ⑥）的候选与 Agent Round 方案
[ ] ★ FD-M11 / FD-M12 回写
[ ] ★ E4/E5/E6 评测集定义与 §0.3 对齐
```

★ 为本轮产品定义变化带来的新增项。

**原始交付清单**（用于校验完整性）：

**1. 候选清单** —— 每个候选按 §0.2 的属性表逐项填写。重点是那三项必须**实测**的：视频输入方式、最大时长/帧数、时间戳是否可靠。

**2. 五个角色的分别推荐**，各自附一句「为什么是它」：
```
① Material Understanding VLM  → ?
② Reference Understanding VLM → ?
③ Editing Planner LLM         → ?
④ Embedding / Retrieval       → ?
⑤ Taste Critic baseline       → ?（并注明可微调路径）
```
允许（而且很可能）① 与 ⑤ 不是同一个模型 —— 见 §0.1 的第 2 条结论。

**3. 填好的 Model Scorecard**（§0.4 模板），含每-project 成本换算，而非 per-token 单价对比。

**4. §0.5 的分支判定**：Reference pipeline 走路径 A 还是 B，附 E2 的边界 F1 与时长 MAE 实测值。**这一项不给结论，M2 就无法开工。**

**5. 辅助能力选型**：ASR、OCR、Beat detection 各选一个（这几项成熟，不需要 bake-off，够用即可）。

**6. 兜底方案**：每个角色的第二选择，以及触发切换的条件（配额、涨价、可用性、区域限制）。

**7. 未决项**：明确列出哪些留到 M3 再定（如 Critic 的最终微调方式）。

补齐后更新本文档的 §0.2 / §0.5 / §0.8。**在此之前，只做块 A 与块 B 的确定性部分**（§13.1 / §13.2），不要开始依赖模型行为的实现。
