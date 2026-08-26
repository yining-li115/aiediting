# MODEL_SELECTION.md

**项目**：AI 视频剪辑产品（Lite MVP 优先）  
**版本**：v1.1（新增 Interactive Editing Agent 角色）  
**调研截止日期**：2026-08-25  
**文档状态**：模型与平台选型基线；已与 `docs/source/` 下的产品与技术执行文档一起收敛进 `docs/TECHNICAL_PLAN.md`（唯一执行基线）  
**研究原则**：优先采用官方模型文档、官方产品文档和官方开源仓库；供应商自报 benchmark 只用于建立候选集，不作为最终选型依据。

---

# 0. 结论先行

## 0.1 对“国内娱乐方向的模型/应用可能更成熟”的判断

这个判断 **在应用层和媒体工具层得到部分支持**，但还不能推导为“国内模型一定更有剪辑审美”。

调研结果更准确地说是：

1. **字节/火山在媒体专用执行能力上最完整。**  
   除通用多模态模型外，火山侧已经形成 MediaKit、ASR、OCR、场景切分、高光提取、剧情线分析、音频分离、视频抠图、媒体 Agent Skills 等明确面向音视频生产的能力集合。

2. **阿里在“模型 + Workflow + Skills + 多模态检索 + 可调优开源 VL 模型”这一整套组合上最适合中国区快速搭 MVP。**  
   尤其是 Qwen3.7、Qwen3-VL Embedding/Rerank、Qwen ASR、百炼 Workflow 和 Agent Skills，角色覆盖很完整，公开 API 规格也相对清楚。

3. **Google 目前是必须纳入 benchmark 的全球视频理解候选。**  
   Gemini 3.7 Flash 原生支持视频和音频、1M 上下文、结构化输出。但标准视频理解默认以 1 FPS 采样，官方明确提醒快速运动和快速切镜可能丢失信息；这与 TikTok/抖音式快剪存在直接冲突。

4. **OpenAI 很适合 Planner、工具编排、结构化判断和 ASR benchmark，但不是当前 Raw Video Understanding 的首选。**  
   当前官方 GPT-5.6 系列支持文本和图片输入，不支持原始视频输入；OpenAI 也正在收缩自助式 fine-tuning，因此不应把 OpenAI 作为未来可训练 Taste Critic 的唯一基础。

5. **真正的“剪辑审美”无法通过供应商宣传判断。**  
   哪个模型更懂中国短视频、Vlog 节奏、参考视频复刻和普通用户偏好，必须由本项目自己的 M-1 Bake-off 与 Human Preference 数据证明。

因此，本项目采用：

> **China-first hybrid stack，而不是 Alibaba-only、ByteDance-only 或单一全球模型。**

---

## 0.2 MVP 的临时推荐模型栈

下表是 **进入 M-1 Bake-off 前的 provisional stack**，不是未经测试的永久结论。

| 系统角色 | MVP 临时主选 | 必测挑战者 | 结论 |
|---|---|---|---|
| Shot / Audio / Quality 预处理 | FFmpeg + OpenCV + 专用算法 | 火山 MediaKit 原子能力 | 时间边界与技术质量不交给通用 VLM 猜 |
| Material Understanding | `qwen3.7-plus` + 独立 ASR | Seed2.0 Lite 0428、Gemini 3.7 Flash、腾讯 YT-VITA | 四者必须做同一数据集 Bake-off |
| Reference Understanding | `qwen3.7-plus` + 预切镜头 + ASR | Seed2.0 Lite 0428、Gemini 3.7 Flash、Pegasus 1.5 | 不直接假设“整条视频一次输入”最好 |
| AV 情绪/环境辅助理解 | `qwen3.5-omni-plus` | Seed2.0 Lite、YT-VITA | Omni 只做辅助，不作为唯一结构化 Parser |
| Editing Planner | `qwen3.7-plus-2026-05-26` | Seed2.1 Pro（控制台确认 ID）、Gemini 3.7 Flash、GPT-5.6 Terra | Planner 只读结构化摘要，易替换 |
| Slot Retrieval | `qwen3-vl-embedding`，1024 维起步 | 豆包视觉 Embedding、Marengo 3.0、Gemini Embedding 2 | 先召回，不承担最终判断 |
| Slot Rerank | `qwen3-vl-rerank` | VLM rerank、自研轻量 cross-encoder | 对 Top-K 做精排 |
| ASR | `qwen-audio-3.0-asr-flash-filetrans` | 豆包 ASR 2.0、OpenAI Transcribe | 说话人/时间戳/方言按项目实测 |
| Zero-shot Taste Critic | M-1 中胜出的 Raw Video VLM | 全部候选交叉比较 | 先证明现成模型能否判断 A/B |
| Trainable Taste Critic | `qwen3-vl-8b-instruct` SFT/LoRA | Qwen3-VL Thinking、Amazon Nova 2 Lite Video SFT | Managed SFT 与自托管 reward head 两条路线 |
| Renderer | FFmpeg-first `RendererProvider` | 火山 MediaKit / VOD adapter | 不自研媒体引擎 |
| **Interactive Editing Agent ⑥-Fast** | **待定：M-1 Agent Round 选出** | 各家轻量/快速 LLM | 低延迟 + 稳定 tool calling，不用视频模型 |
| **Interactive Editing Agent ⑥-Deep** | **待定：优先复用 Planner 主选** | Planner 的挑战者集合 | 需要重新规划审美与叙事时才升级 |
| Agent / Workflow Runtime | 项目代码为主；Skills 只封装工具 | 百炼 Workflow、火山 AgentKit、OpenAI Agents SDK | 核心业务状态机不能只存在于黑盒画布 |

---

## 0.3 当前冻结的模型战略

后续 Coding Agent 可以先把这些作为 Frozen Decisions：

```text
FD-M01  不寻找“一个模型包办全部任务”；按角色选择模型。
FD-M02  中国区 MVP 采用 China-first hybrid stack。
FD-M03  Raw Video VLM 必须完成 M-1 Bake-off 后才能冻结。
FD-M04  默认时间精度来自 shot detector / ASR / audio DSP，不来自 VLM 自报时间戳。
FD-M05  Qwen3.7 Plus 是 provisional planner，不是永久绑定。
FD-M06  qwen3-vl-embedding + qwen3-vl-rerank 是 provisional matcher baseline。
FD-M07  Taste Critic 先 zero-shot → SFT/LoRA → Best-of-N；不直接做 RL。
FD-M08  OpenAI 不作为 Raw Video Parser 或长期可训练 Critic 的唯一依赖。
FD-M09  Workflow / Skills 用于原型和工具包装；核心 EditPlanSpec、TimelineSpec、
        ranking、feedback log 和实验逻辑必须保留在自有代码库。
FD-M10  所有生产模型必须锁定 snapshot/model ID，并进入 Model Registry。
FD-M11  Interactive Editing Agent（角色 ⑥）单独选型，不与 Video VLM 共用；
        ⑥-Fast / ⑥-Deep 分离。
FD-M12  Fast / Deep 路由由 operation 类型静态决定（TECHNICAL_PLAN §11.3 表），
        不由模型临时判断。
```

---

# 1. 本项目究竟需要选哪些“模型”

本项目不是选一个聊天模型，而是选择一组承担不同认知职责的组件。

```text
User Materials
      │
      ├─ Deterministic Preprocessing
      │    ├ shot boundary
      │    ├ blur / exposure / shake
      │    ├ audio beat / onset / RMS
      │    └ ASR / OCR
      │
      ▼
Material Understanding VLM
      │
      ▼
SegmentSummary / Candidate Pool
      │
      ├───────────────────────────────┐
      │                               │
Reference Video                      Prompt
      │                               │
      ▼                               ▼
Reference Understanding VLM     Intent Parser
      │                               │
      ▼                               │
Reference EditPlanSpec ───────────────┘
      │
      ▼
Editing Planner
      │
      ▼
N × EditPlanSpec
      │
      ▼
Embedding Retrieval + Rerank
      │
      ▼
N × TimelineSpec
      │
      ▼
Structural Critic
      │
      ▼
Top-K Proxy Render
      │
      ▼
Video Taste Critic
      │
      ▼
Best TimelineSpec → Renderer → Video
```

需要独立选型的角色至少包括：

1. Material Understanding VLM
2. Reference Understanding VLM
3. Audio-Visual Understanding 辅助模型
4. Editing Planner LLM
5. Multimodal Embedding
6. Multimodal Reranker
7. ASR
8. Taste Critic backbone
9. **Interactive Editing Agent（⑥-Fast / ⑥-Deep）**
10. Workflow / Agent runtime
11. Renderer / media tool layer

---

# 2. 选型标准

供应商通用 benchmark 不能替代项目评测。每个模型必须按以下维度评估。

## 2.1 视频理解

- 是否原生接收视频文件
- 是否理解视频内音频
- 采样 FPS 是否可控
- 快速切镜是否会丢失
- 是否能定位好 moment，而不只概括整段内容
- 是否能识别景别、镜头运动、构图、情绪、动作阶段
- 是否能输出时间范围
- 单文件时长、大小、视频数量限制

## 2.2 剪辑相关理解

- shot sequence reasoning
- 节奏与镜头长度判断
- 视觉重复
- wide / medium / close-up / detail pattern
- music-image relationship
- transition taste
- narrative arc
- reference style decomposition
- candidate A/B preference judgment

## 2.3 工程能力

- JSON Schema / Structured Output
- Function Calling
- snapshot pinning
- Batch
- cache
- API 稳定性
- 中国区访问和合规
- 延迟与并发
- 价格透明度
- OpenAI-compatible API 是否只是传输兼容，还是能力也一致

## 2.4 可训练性

- 视频 SFT
- LoRA / efficient SFT
- preference tuning / DPO
- 能否自托管
- 能否增加 reward head
- 训练数据大小与视频限制
- 微调产品是否长期稳定存在

## 2.5 产品风险

- vendor lock-in
- 模型 ID 是否透明
- 模型别名是否会自动漂移
- 输出策略/安全策略是否影响普通生活素材
- 用户视频是否需跨境
- 数据保留策略
- 模型下线与 embedding migration 成本

---

# 3. 平台深度调研

# 3.1 阿里云百炼 / Qwen

## 3.1.1 Qwen3.7 Plus

官方能力：

```text
模型：qwen3.7-plus
快照：qwen3.7-plus-2026-05-26
输入：Text / Image / Video
输出：Text
上下文：1,000,000
视频：最长 2 小时，最大 2 GB
结构化输出：支持
Function Calling：支持
模型调优：不支持
```

对本项目的优点：

- 长视频和多素材摘要的容量足够大。
- Structured Output 很适合输出 `SegmentSummary`、`Reference EditPlanSpec`。
- API、快照、地域和价格规格相对清楚。
- 中国区调用与后续百炼 Workflow、ASR、Embedding 连接方便。
- 适合作为 Planner 和结构化 Video Parser baseline。

关键限制：

- 常规 Qwen3.7 视频理解 **不处理视频文件中的音频轨**。
- 因而必须把音轨单独送到 ASR / audio pipeline。
- 模型本身不能调优，不适合作为最终自有 Taste Critic 的唯一载体。
- 2 小时能力不代表它能精确理解 0.3–1.0 秒短镜头；仍需预切镜头与自有测试。

结论：

> **Qwen3.7 Plus 是当前最合适的中国区 provisional planner + structured video parser，但不能单独承担音视频联合理解。**

---

## 3.1.2 Qwen3.5 Omni Plus

官方能力：

```text
模型：qwen3.5-omni-plus
快照：qwen3.5-omni-plus-2026-03-15
输入：Text / Image / Video / Audio
输出：Text / Audio
上下文：262,144
音视频理解：约 400 秒 720p，1 FPS
结构化输出：不支持
Function Calling：别名支持；快照能力需单独核验
模型调优：不支持
```

优点：

- 能把语音、环境音和视觉结合起来分析。
- 适合判断：
  - 情绪
  - 对话与画面的关系
  - 环境声音
  - 音乐氛围
  - audio-visual event
- 可以作为 Qwen3.7 的 AV 辅助通道。

限制：

- 1 FPS 对快剪不够。
- 不支持严格 Structured Output。
- 400 秒限制不适合长视频统一处理。
- 不可微调。

结论：

> **把 Omni 当“AV 辅助观察者”，不要把它当唯一 Reference Parser。**

推荐形式：

```text
Qwen3.7 visual structured analysis
+
Dedicated ASR / audio features
+
Qwen3.5 Omni AV semantic analysis
↓
Aggregator
↓
Reference EditPlanSpec
```

---

## 3.1.3 Qwen3-VL Embedding + Rerank

### `qwen3-vl-embedding`

官方支持：

- 文本、图片、视频
- 独立向量或融合向量
- 可选维度：2560、2048、1536、1024、768、512、256
- 适合跨模态搜索和文/图搜视频

MVP 建议：

```text
dimension = 1024
```

原因：

- 2560 对早期数据量没有明显必要。
- 1024 在存储、检索速度和表达能力之间更合理。
- 后续可以在相同 benchmark 下比较 512 / 1024 / 2560。

### `qwen3-vl-rerank`

官方支持：

- query：文本或图片
- document：文本、图片、视频
- 一次最多 4 个视频 document
- 适合对 embedding Top-K 做精排

推荐检索链：

```text
Reference Slot / Semantic Query
      ↓
qwen3-vl-embedding
      ↓
Top 20–50 segments
      ↓
规则过滤：
duration / quality / people / aspect / duplicate
      ↓
qwen3-vl-rerank
      ↓
Top 3–5
      ↓
Planner / Critic final choice
```

结论：

> **阿里是当前最清楚、最完整的多模态 Retrieval + Rerank MVP 组合，暂定为 Matcher baseline。**

---

## 3.1.4 可训练 Qwen3-VL

百炼当前支持：

```text
qwen3-vl-8b-instruct
qwen3-vl-8b-thinking
qwen3-vl-4b-instruct
```

其中 Qwen3-VL 8B 支持：

- full SFT
- efficient SFT / LoRA
- 图片/视频多模态 SFT
- 不支持百炼托管 DPO

对 Taste Critic 的两条路线：

### 路线 A：百炼托管 SFT

训练输入：

```text
Material Summary
Prompt
Reference Summary
Edit A proxy
Edit B proxy
```

输出：

```json
{
  "winner": "A",
  "confidence": 0.72,
  "dimensions": {
    "shot_selection": "A",
    "rhythm": "A",
    "narrative": "tie",
    "music_match": "B",
    "reference_match": "A"
  }
}
```

这本质是 generative pairwise judge。

### 路线 B：自托管 Qwen3-VL + LoRA + Reward Head

训练：

\[
P(A \succ B|x)=\sigma(r_\theta(x,A)-r_\theta(x,B))
\]

优点：

- 可以直接建模 scalar / distributional reward。
- 可以保存 soft vote distribution。
- 可以进一步做 personalized taste。

缺点：

- 需要自建训练与推理服务。

结论：

> **Qwen3-VL-8B 是当前最合适的 Trainable Critic 起点，但 M3 前不需要训练。**

---

## 3.1.5 Qwen ASR

默认候选：

```text
qwen-audio-3.0-asr-flash-filetrans
```

官方能力：

- 非实时文件转写
- Prompt 上下文
- 热词
- 说话人分离
- 多语种与多种中文方言
- 最长 12 小时 / 2 GB

适合：

- 用户素材语音转写
- Reference 讲话内容
- 对话结构
- 字幕候选
- 说话人切换

注意：

- ASR 只负责“说了什么/谁说的/何时说”，不负责 beat。
- Beat、onset、RMS、silence 和音乐结构仍应使用音频 DSP。

---

## 3.1.6 百炼 Workflow 与 Agent Skills

Workflow 支持组合：

- 大模型
- API
- 函数计算
- 变量和批处理
- Webhook
- 工作流节点

Agent Skills 支持：

- 官方 Skill 市场
- 自定义 ZIP
- 根目录 `SKILL.md`
- 版本管理
- 多 Agent 复用

对项目的适用方式：

```text
适合：
- 做 M-1 快速 spike
- 包装 analyze_video / transcribe / render_proxy 等工具
- 快速测试供应商内链路
- 做内部运营工具

不适合：
- 把 EditPlanSpec 状态机只存在画布中
- 把核心 ranking 规则封在不可测试节点里
- 让实验版本依赖人工点选的 UI 配置
```

结论：

> **百炼适合作为能力供应商和原型平台，但生产核心 pipeline 仍应是可测试、可版本控制的代码。**

---

# 3.2 字节跳动 Seed / 火山引擎

## 3.2.1 Seed2.0 Lite 0428

Seed 官方页面明确说明，4 月底升级后的 Seed2.0 Lite：

- 原生统一理解视频、图片、音频和文本
- 加强 temporal understanding
- 加强 motion perception
- 支持 joint audio-visual understanding
- 定位为兼顾质量和速度的 production-grade 模型

这与本项目高度相关，因为 Reference Remake 需要理解：

- 快速动作
- 镜头变化
- 音乐与画面
- 环境音
- 时间结构

为何必须进 M-1：

> 在所研究的中国平台中，Seed2.0 Lite 的公开定位最直接地针对动态视频和联合音画理解。

但当前不能直接冻结为主选，原因是：

- 官方 Seed 页面更偏模型发布页。
- 静态公开文档中，0428 升级版对应的 Ark 精确 `model_id`、文件限制、采样率、结构化输出细节不够清楚。
- 必须在火山方舟控制台取得实际 endpoint 后做 smoke test。
- 供应商 benchmark 不能替代剪辑任务评测。

需要在 M-1 前确认：

```text
- 0428 版本的准确 model_id / endpoint_id
- 视频最大时长和大小
- 是否读取文件内原始音频
- JSON Schema / tool calling 支持
- timestamp 输出稳定性
- 视频 token / 秒计费
- 中国区并发和异步任务能力
```

---

## 3.2.2 Seed2.1 Pro

适合作为：

- Editing Planner challenger
- 复杂 Reference grammar synthesis
- 多步骤 Agent 编排

不建议未经验证直接用作 Raw Video 主解析器。其确切 API ID 和多模态输入规格需要在 Ark 模型列表中确认。

---

## 3.2.3 火山 MediaKit

这是本次调研中最值得重视的媒体工程资产之一。

官方 MediaKit CLI 提供 FFmpeg 兼容命令面，并包含：

### 剪辑原子能力

```text
裁剪
拼接
图片/字幕叠加
调速
音量
滤镜
翻转
淡入淡出
混音
添加/提取音频
图片转视频
```

### AI 媒体能力

```text
人声/背景音分离
ASR
OCR
画质增强
字幕擦除
高光片段提取
剧情故事线分析
场景切分
绿幕/人像抠图
视频元信息
```

### Agent Skills

```text
byted-mediakit-editing
byted-mediakit-audio
byted-mediakit-image
byted-mediakit-video
byted-mediakit-shared
```

项目使用建议：

```text
M0：
FFmpegRenderer 作为标准基线

M0/M1 可选：
MediaKitProvider 做 scene split / audio split / OCR / proxy operation

后续：
在同一个 TimelineSpec 上比较 FFmpegRenderer 与 MediaKitRenderer
```

重要边界：

> MediaKit 能显著减少媒体 plumbing，但它不会替代 Reference-to-Edit Grammar、Material Matching 或 Taste Critic。

---

## 3.2.4 火山 AgentKit / Skills

火山 AgentKit 已提供：

- Skills 中心
- Skill 版本管理
- Runtime
- Tools
- Memory
- Knowledge
- MCP
- AIO Sandbox / Skills Sandbox
- Local / Cloud / Hybrid 配置

适合：

- 包装媒体工具
- 管理异步任务
- 做内部 Agent prototype
- 复用 MediaKit Skills

仍然不建议把核心业务全部迁入平台专有配置。

---

## 3.2.5 字节视觉 Embedding 与 ASR

M-1 应加入：

```text
doubao-embedding-vision-250615
```

作为视频 embedding challenger。

ASR 方面加入豆包语音识别 2.0，与 Qwen ASR 和 OpenAI Transcribe 在下列场景比较：

- 中文普通话
- 方言
- 英中混合
- 背景音乐强
- 多说话人
- 手机远场
- 短视频嘈杂环境

---

## 3.2.6 对字节/火山的总评

优势：

- 媒体和娱乐链路最强
- 原子音视频能力非常多
- Seed2.0 Lite 公开强调 temporal/motion/AV
- Skills 与媒体 CLI 已经开始产品化
- 可能最贴近中国短视频生产场景

风险：

- 某些模型版本和 endpoint 规格需要控制台确认
- 媒体能力多不等于模型拥有更好的“审美”
- 过深绑定会让 TimelineSpec 与执行后端耦合
- 平台产品变化较快，需要 registry 和 adapter

结论：

> **字节是本项目最重要的媒体能力供应商和 Raw Video VLM 候选，但仍需与 Qwen、Gemini、YT-VITA 在同一数据上盲测。**

---

# 3.3 Google Gemini / Vertex AI

## 3.3.1 Gemini 3.7 Flash

官方规格：

```text
模型：gemini-3.7-flash
输入：Text / Image / Video / Audio / PDF
输出：Text
上下文：1,048,576
最大输出：65,536
Structured Output：支持
Function Calling：支持
Batch / Cache：支持
```

优点：

- 原生视频和音频
- 结构化输出
- 长上下文
- 全球可用
- 可以同时充当 Raw Video oracle、Planner challenger 和 Zero-shot Critic

最关键限制：

> Gemini 官方视频理解文档说明，视觉描述默认按 1 FPS 采样，并明确提示快速运动和快速场景变化可能丢失细节。

这对于普通长视频摘要影响不一定大，但对本项目非常关键：

```text
0.4 s shot
0.7 s detail shot
0.3 s insert
0.8 s face
```

在 1 FPS 下可能完全不可见。

因此 Gemini 不能只测：

```text
raw_reference.mp4 → JSON
```

还必须测：

```text
shot detector
→ each shot / sampled frames / timestamps
→ Gemini
→ aggregate
```

结论：

> **Gemini 3.7 Flash 是必须保留的全球 oracle，但若不做预切镜头，它对快剪 reference 可能结构性失真。**

---

## 3.3.2 Gemini Embedding 2

官方支持：

- text / image / video / audio / PDF
- 同一 embedding space
- 可选维度 128–3072
- 视频最长 120 秒
- 每个视频最多处理 32 帧
- ≤32 秒时 1 FPS；更长时均匀取 32 帧
- 视频文件中的音频轨不会被处理

适合：

- global multimodal retrieval challenger
- image/text/video cross-modal baseline

不适合作为默认 matcher 的原因：

- 32 帧上限对快速 Vlog moment retrieval 过于稀疏
- 用户素材应先切成较短 segment 再 embedding
- 音频必须单独建模

结论：

> **Gemini Embedding 2 适合做短 segment benchmark，不适合把整条用户素材直接送入做精细 moment matching。**

---

## 3.3.3 Vertex Tuning

官方当前支持：

- Gemini 3.5 Flash 等模型 SFT
- Gemini 2.5 Flash / Flash-Lite preference tuning
- 多种 modality 的 SFT 文档

但对本项目需要额外验证：

- 视频 preference pair 是否能直接作为 preference tuning 输入
- 输出是否适合 pairwise critic
- 当前区域、配额和价格
- 旧 2.5 preference-tuned 模型与新 3.7 inference model 的性能差距

因此：

> **Vertex preference tuning 是备选研究路线，不是 MVP 默认训练路径。**

---

## 3.3.4 Google Agent 平台

Google 提供 Interactions API、Agent 平台和托管 sandbox 能力。

对本项目的价值主要是：

- 通用 Agent runtime
- 长任务
- 工具调用
- 受控执行环境

它不是媒体专用剪辑引擎，优先级低于模型本身和 MediaKit。

---

# 3.4 OpenAI

## 3.4.1 GPT-5.6 系列

当前官方推荐层级包括：

```text
GPT-5.6 Sol   高质量
GPT-5.6 Terra 智能与成本平衡
GPT-5.6 Luna  高吞吐低成本
```

例如 GPT-5.6 Terra：

```text
上下文：1.05M
Structured Output：支持
Function Calling：支持
输入：Text / Image
原始 Video：不支持
Audio：不支持
```

对项目适合：

- Editing Planner
- EditPlanSpec repair
- Structural Critic
- 复杂规则推理
- Agent/tool orchestration
- 从抽帧 + ASR + features 生成计划

不适合：

- 直接读取 raw video
- 直接进行联合音画理解
- 作为当前默认 Raw Video Taste Critic

结论：

> **OpenAI 应进入 Planner Bake-off，而不进入 Raw Video Parser Bake-off。**

建议挑战者：

```text
gpt-5.6-terra
```

理由：

- Planner 输入已经是结构化摘要
- Terra 在能力和成本之间更合理
- Sol 可以作为少量高难 eval oracle，而非默认生产模型

---

## 3.4.2 OpenAI Fine-tuning 风险

OpenAI 官方在 2026-05-08 更新中说明正在逐步关闭自助式 fine-tuning：

- 新用户不能进入
- 既有用户仅在过渡期继续创建 job
- 已有 fine-tuned models 只持续到 base model deprecated

因此：

> **不能把 OpenAI fine-tuning 作为 Taste Critic 的长期核心路线。**

OpenAI 仍可作为：

- inference-only Planner
- external judge
- ASR
- image-based evaluator

---

## 3.4.3 OpenAI Agents SDK

当前 Agents SDK 的优势：

- MCP
- Skills
- AGENTS.md
- shell / apply patch
- native sandbox
- 自带或第三方 sandbox provider
- long-horizon tool use

同时，OpenAI 已宣布 Agent Builder 与 Evals 产品将在 2026-11-30 后停止，建议使用 Agents SDK。

对项目建议：

- 若 Planner 选 OpenAI，可用 Agents SDK 做工程 harness。
- 不建议为了使用 Agents SDK 而把所有模型换成 OpenAI。
- Provider abstraction 应允许 OpenAI Planner 调用阿里/火山媒体工具。

---

## 3.4.4 OpenAI ASR

将 OpenAI Transcribe 系列作为国际 ASR benchmark，重点比较：

- 英文
- 多语言
- 中英混说
- 音乐背景
- 时间戳
- 价格与延迟

中国区生产默认仍优先测试 Qwen / Doubao ASR。

---

# 3.5 腾讯云 TokenHub：YT-VITA

官方 TokenHub 文档列出：

```text
模型：youtu-vita
视频理解：画面 + 音频
图片理解：支持
协议：OpenAI-compatible
最长视频：10 分钟
最大文件：100 MB
一次请求：1 个视频
```

为什么值得加入 M-1：

- 中国区
- 明确联合理解视频画面和音频
- API 接入简单
- 10 分钟足够 Lite reference 与 segment 任务
- 可以检验“国内娱乐视频场景模型”假设

公开文档中仍需补充确认：

- structured output
- timestamp 稳定性
- 采样策略
- 调优能力
- price / quota
- snapshot pinning

结论：

> **YT-VITA 是 Mandatory Wildcard，不应因为品牌声量较小而忽略。**

---

# 3.6 百度千帆：ERNIE VL

官方文档提供视频理解，并允许某些 ERNIE 视觉模型通过：

```json
"use_audio": true
```

联合理解视频音频。

重要规格：

- 单视频最大 64 MB
- `ernie-4.5-turbo-vl-preview` 可设置 `fps`
- 官方示例范围约 0.2–5 FPS
- 部分模型可理解文件内音频

对本项目的价值：

- 可控 FPS 对快剪比固定 1 FPS 更有吸引力
- 中国区
- 可作为 AV VLM wildcard

限制：

- Preview 型号变化风险
- 64 MB 对大量原始素材需要压缩或分段
- 需要实测结构化输出和稳定性

结论：

> **不作为首轮四强默认项，但在 M-1 扩展轮应加入 ERNIE VL，特别测试 2–5 FPS 的快剪恢复能力。**

---

# 3.7 Amazon Nova 2 Lite

官方能力：

- 原始视频理解
- 场景、动作、事件、时序分段
- 1M 级长上下文
- 视频 SFT
- 可以对视频任务做托管/自定义训练

关键限制：

- ≤16 分钟视频固定约 1 FPS
- 视频被缩放并可能失真到 672×672
- 不处理视频音频轨
- 官方也提示多语言视频帧理解有限

优点：

> Amazon Nova 2 Lite 是少数官方明确提供 Video Understanding SFT 的托管模型。

因此它适合：

- 作为 Trainable Video Critic 备选
- 对比 Qwen3-VL managed SFT
- AWS 体系客户或后续国际市场

不适合：

- 当前中国 Lite MVP 默认 Raw Video Parser
- 快切、强音乐、中文娱乐内容的未经验证首选

---

# 3.8 Twelve Labs：视频专用模型

## Marengo 3.0

定位是视频 embedding / search，明确支持：

- visual + audio + text
- motion search
- cinematography terms，如 zoom、pan、tracking
- music、lyrics、sound、silence
- 36 种语言 + 英文
- 512 维 embedding
- 最长 4 小时
- 视频按 1–10 秒 segment 生成多个 embedding

这与 Material Matching 高度相关。

价值：

- 不是“通用 VLM 顺便做 embedding”，而是视频搜索专用模型。
- 能理解运动、摄影、声音。
- 很适合作为 qwen3-vl-embedding 的 specialist challenger。

## Pegasus 1.5

定位是直接 video-to-text：

- 视频 segmentation
- timestamped structured JSON
- custom segment definitions
- visual + audio + text
- 最长 2 小时
- 中文 partial support

价值：

- 可以作为 Reference Decomposition oracle
- 可测试自定义“editorial segment / shot role / narrative”定义

限制：

- 中国区可用性、延迟和成本需确认
- 中文只是 partial support
- 引入又一个专业供应商
- 不能因此放弃自有 EditPlanSpec

结论：

> **Twelve Labs 不是默认主栈，但非常适合作为 Matcher 和 Reference Parser 的专业上限 benchmark。**

---

# 3.9 未进入主 shortlist 的模型

## Anthropic Claude

可作为高质量 Planner / text reasoning challenger，但当前项目最稀缺的是 raw video、AV、retrieval 和可训练 Critic。Claude 不应在第一轮扩大候选数量，除非 Planner Bake-off 中现有四个模型明显不足。

## Meta / Mistral 等开源模型

可用于后续私有化，但当前没有比 Qwen3-VL 更明确的中国区视频 SFT、API 与生态优势。第一轮不加入，避免 M-1 失控。

## 视频生成模型

Seedance、Veo、Sora 等不属于主剪辑 pipeline。只有当用户素材存在明显缺口，并且产品允许“生成补镜头”时，才作为可选 fallback；MVP 不应依赖生成模型弥补素材。

---

# 4. 最终候选矩阵

## 4.1 Raw Video / Reference Understanding

| 模型 | 原始视频 | 文件内音频 | Structured Output | 可调优 | 最大风险 | M-1 状态 |
|---|---:|---:|---:|---:|---|---|
| Qwen3.7 Plus | 是 | 否 | 是 | 否 | 快剪采样细节未知 | Mandatory |
| Seed2.0 Lite 0428 | 是 | 是 | 控制台确认 | 控制台确认 | endpoint 规格透明度 | Mandatory |
| Gemini 3.7 Flash | 是 | 是 | 是 | 当前主模型不作为默认 tuning | 视觉默认 1 FPS | Mandatory |
| Tencent YT-VITA | 是 | 是 | 待测 | 待确认 | 公开规格有限 | Mandatory Wildcard |
| Qwen3.5 Omni Plus | 是 | 是 | 否 | 否 | 1 FPS / 400s | Auxiliary |
| ERNIE VL | 是 | 部分模型是 | 待测 | 待确认 | Preview / 64MB | Extended |
| Pegasus 1.5 | 是 | 是 | 是 | 否 | 成本/中文 partial | Specialist |
| Nova 2 Lite | 是 | 否 | 可生成 JSON | 是 | 1 FPS / 画面失真 | Trainable Alternative |
| OpenAI GPT-5.6 | 否 | 否 | 是 | 否 | 无 raw video | 不进入本轮 |

---

## 4.2 Planner

| 模型 | 输入策略 | Structured Output | 长上下文 | 角色 |
|---|---|---:|---:|---|
| Qwen3.7 Plus snapshot | SegmentSummary + specs | 是 | 1M | Provisional default |
| Seed2.1 Pro | 结构化输入 | 待控制台确认 | 待确认 | China challenger |
| Gemini 3.7 Flash | 结构化输入，可带 proxy | 是 | 1M | Global challenger |
| GPT-5.6 Terra | 结构化输入 + 图片 | 是 | 1.05M | Reasoning challenger |
| GPT-5.6 Sol | 少量高难样本 | 是 | 1.05M | Oracle only |

---

## 4.3 Retrieval / Matching

| 模型 | 视频 | 音频 | 运动/摄影理解 | Rerank | 状态 |
|---|---:|---:|---:|---:|---|
| Qwen3-VL Embedding | 是 | 视频音轨不作为主依据 | 通用 | 有 Qwen3-VL Rerank | Provisional default |
| Doubao Vision Embedding | 是 | 待确认 | 通用视频 | 另行实现 | Mandatory challenger |
| Marengo 3.0 | 是 | 是 | 明确支持 | 搜索/相似度 | Specialist challenger |
| Gemini Embedding 2 | 是 | 视频内音轨否 | 32 帧限制 | 无专用 rerank | Global challenger |

---

## 4.4 Taste Critic

| 路线 | 优点 | 缺点 | 使用阶段 |
|---|---|---|---|
| Zero-shot best VLM | 无训练，最快验证 | 偏好不稳定 | M-1 / M2 |
| Qwen3-VL-8B managed SFT | 中国区、视频 SFT、LoRA | 无托管 DPO | M3 |
| Self-host Qwen3-VL + reward head | 最符合 pairwise reward | 训练/部署复杂 | M3+ |
| Nova 2 Lite Video SFT | 托管视频 SFT 明确 | 1 FPS、无音频、中文未知 | Alternative |
| Gemini preference tuning | 官方有人类偏好路线 | 当前主要针对旧型号；视频输入需验证 | Research |
| OpenAI fine-tuning | 不建议 | 平台正在 wind-down | Excluded |

---

# 5. M-1：Model Bake-off 方案

# 5.1 目的

M-1 不评“谁是世界上最强模型”，只回答：

1. 谁最适合解析本项目的用户素材？
2. 谁最适合把 Reference 变成 `EditPlanSpec`？
3. 谁最适合做 Slot Matching？
4. 谁最适合做 Planner？
5. 现成模型是否已经能当 Taste Critic baseline？
6. 每个角色的成本、延迟和失败模式是什么？

---

# 5.2 两阶段数据规模

## Phase A：Access Smoke

用于确认 API 和 schema，不做结论。

```text
5 组 raw footage packs
5 条 reference videos
20 个 material segments
20 个 slot queries
30 个 A/B edit pairs
```

输出：

- API 是否可访问
- model ID 是否正确
- file limit
- JSON 能否稳定解析
- 成本计量
- 初始 latency

## Phase B：正式 Bake-off

```text
20–30 组授权 Travel / Daily Vlog 素材包
每组 20–100 clips

15–20 条授权或合法使用的 reference videos

300–500 个 segment / slot retrieval queries

200–300 个 candidate edit A/B pairs
```

内容分层：

```text
快剪 / 慢剪
有对白 / 无对白
音乐主导 / 环境音主导
人物 / 风景 / 美食 / 聚会
竖屏 / 横屏
清晰素材 / 混合质量素材
中文 / 英文 / 中英混合
```

---

# 5.3 两套输入协议

每个 Raw Video VLM 必须同时测试两种输入。

## Protocol A：Raw Direct

```text
原始视频
+
统一 prompt
+
统一 JSON schema
```

测供应商原生视频理解能力。

## Protocol B：Standardized Preprocessed Package

```text
shot boundaries
+
每 shot 原始小片段或关键帧
+
精确 timestamps
+
ASR transcript
+
beat / onset / RMS
+
quality features
+
统一 prompt/schema
```

测模型在公平、可控输入上的推理能力。

决策规则：

> 如果模型 Raw Direct 表现一般，但 Protocol B 很强，则保留为 reasoning model，不让它直接承担时间采样。

---

# 5.4 任务一：Material Understanding

模型输入：

```text
clip / segment
+
technical features
+
optional transcript
```

输出 schema：

```json
{
  "semantic": "",
  "shot_scale": "wide|medium|close_up|detail|unknown",
  "camera_motion": [],
  "people_count": 0,
  "main_action": "",
  "emotion": [],
  "interesting_moments": [
    {
      "start": 0.0,
      "end": 0.0,
      "reason": ""
    }
  ],
  "keep_score": 0.0,
  "quality_issues": []
}
```

指标：

- schema first-pass validity
- action / semantic accuracy
- shot-scale macro F1
- camera-motion F1
- good-moment Precision@K
- expert keep/discard agreement
- timestamp error
- latency / clip
- cost / minute

注意：

> Shot boundary 本身不由 VLM 评分；VLM 只在 detector 提供的 segment 内判断 moment。

---

# 5.5 任务二：Reference → EditPlanSpec

人工为每条 reference 建立轻量标注：

```text
sections
shot count
shot duration sequence
shot scale sequence
semantic role sequence
transition density
subtitle density
music / beat relationship
style tags
```

模型输出：

```json
{
  "intent": {},
  "story": {},
  "slots": [],
  "rhythm": {},
  "text": {},
  "audio": {}
}
```

指标：

- shot count / duration pattern error
- slot semantic-role agreement
- shot-scale pattern F1
- transition density error
- beat alignment classification
- expert 1–5 “editing grammar fidelity”
- expert 1–5 “可迁移性”
- first-pass schema validity
- missing / hallucinated editing elements

核心人工问题：

```text
1. 这个 EditPlanSpec 是否抓住了参考视频“为什么这样剪”？
2. 它是否能迁移到不同素材，而不是逐帧抄写？
```

---

# 5.6 任务三：Slot Retrieval

构造：

```text
query:
reference slot + style / semantic constraints

documents:
user material segments
```

标注专家认为可替代该 slot 的候选集合，而不是只标唯一答案。

指标：

- Recall@5
- Recall@10
- nDCG@10
- MRR
- quality-filter violation rate
- duplicate rate
- cross-modal query success

比较：

```text
Qwen3-VL Embedding
Doubao Vision Embedding
Marengo 3.0
Gemini Embedding 2
+
是否增加 Qwen3-VL Rerank
```

---

# 5.7 任务四：Editing Planner

所有 Planner 只拿同一份结构化输入：

```text
Material Candidate Pool
Prompt
Reference EditPlanSpec 可选
Constraints
```

输出 N 个 `EditPlanSpec`。

指标：

- schema validity
- slot coverage
- duration constraints
- repetition
- story coherence
- material feasibility
- plan diversity
- reference fidelity
- downstream human preference
- cost / plan
- latency

不要让 Planner 自己读取 100 条原视频；视频感知与计划决策必须解耦。

---

# 5.8 任务五：Zero-shot Taste Critic

输入：

```text
same task context
+
Edit A proxy
+
Edit B proxy
```

输出：

```json
{
  "winner": "A|B|tie",
  "probabilities": {
    "A": 0.0,
    "B": 0.0,
    "tie": 0.0
  },
  "criteria": {}
}
```

指标：

- human majority accuracy
- expert-only accuracy
- consumer-only accuracy
- cross entropy against raw vote distribution
- Brier score
- calibration error
- tie detection
- style-cluster consistency

禁止只评：

```text
哪个画面更漂亮
```

必须给定：

```text
raw material context
prompt
reference
```

以避免模型只奖励昂贵摄影、漂亮人物或高级场景。

---

# 5.8b 任务六：Interactive Editing Agent（新增）

产品已从"生成 pipeline"升级为 **stateful editing agent**（见 `TECHNICAL_PLAN.md` §11）。角色 ⑥ 承担的任务与视频理解几乎不重叠，因此必须单独测一轮。

## 为什么不能沿用 Raw Video Round 的结论

⑥ 的输入是**结构化项目状态 + 自然语言指令**，不是视频。它要回答的是"用户这句话要改哪里、改多少、不能碰什么"，而不是"画面里有什么"。一个视频理解很强的模型完全可能在多轮指代消解和 scope 控制上表现很差，反之亦然。

## 评测集 E6

构造 **30–50 条多轮编辑指令**，覆盖四类：

```
1. 简单确定性     "删掉这段" "字幕小一点" "换一首音乐"
2. 指代消解       "这里" "刚才那版" "那个食物镜头" "后半段"
3. 复合 + preserve "整体节奏快一点，但开头别动"
                  "更像参考视频，但不要那么快"
4. 全局重规划     "后半段有点无聊，重新组织一下"
                  "把故事改成先看到结果，再回顾过程"
```

每条人工标注：**正确的 scope**（time_range / section / clip_set / global）、**期望的 operation 序列**、**必须保持不变的元素**。

## 评分维度

```
Scope Resolution Accuracy      解析出的范围是否正确
Operation Sequence Match       是否生成了正确的 operation 及顺序
Out-of-scope Violations        越界修改次数（最重要，越低越好）
Lock Respect Rate              命中锁定元素时是否正确拒绝
Tool-calling Accuracy          参数是否指向存在的 clip / section
Structured-output Stability    命令 JSON 的 first-pass validity
Multi-turn State Tracking      5 轮以上是否与真实状态漂移
Latency P50 / P95              ⑥-Fast 目标 < 5s；⑥-Deep 目标 < 60s
Cost per turn                  与 per-project 成本合并统计
中文自然表达理解                口语化、省略主语、模糊指代
```

> **门槛建议**：⑥-Fast 的 `Out-of-scope Violations` 必须为 **0**（在简单确定性指令上）。这一项不达标的模型直接淘汰——它比"听不懂"更伤产品，见 `TECHNICAL_PLAN.md` §16.4。

## 测试方式

用 Mock Project State（固定的 EditPlanSpec + TimelineSpec + 若干 locked clip）驱动，**不需要真实渲染**，因此这一轮成本极低、可以反复跑。同一批指令对每个候选模型跑 3 次取中位数，并做多轮累积测试（连续 8 轮指令后检查状态是否仍然正确）。

---

# 5.9 工程指标与门槛

建议门槛：

```text
First-pass JSON schema validity >= 95%
One-repair schema validity >= 99.5%

所有生产候选必须支持：
- timeout
- retry
- idempotency
- token/cost logging
- prompt version
- model snapshot
```

必须记录：

```text
P50 / P95 latency
failure rate
rate-limit rate
cost per video minute
cost per complete project
cache hit
repair count
safety refusal rate
```

任何模型如果：

- 经常拒绝普通生活素材
- 无法锁定版本
- 输出结构极不稳定
- 快剪漏检严重
- 成本无法满足消费产品

即使 benchmark 很高，也不能入选。

---

# 5.10 M-1 实验矩阵

## Raw Video Round

```text
Qwen3.7 Plus + ASR
Seed2.0 Lite 0428
Gemini 3.7 Flash
Tencent YT-VITA
```

扩展：

```text
Qwen3.5 Omni
ERNIE VL
Pegasus 1.5
```

## Planner Round

```text
Qwen3.7 Plus snapshot
Seed2.1 Pro
Gemini 3.7 Flash
GPT-5.6 Terra
```

## Retrieval Round

```text
Qwen3-VL Embedding + Rerank
Doubao Vision Embedding
Marengo 3.0
Gemini Embedding 2
```

## ASR Round

```text
Qwen Audio 3.0 Filetrans
Doubao ASR 2.0
OpenAI Transcribe
```

---

# 5.11 时间安排

```text
Week 0
- 开通账号 / API
- 核对精确 model IDs
- Provider adapter 骨架
- schema 与 logging

Week 1
- 准备 M-1 数据集
- 人工标注 reference grammar
- 建立统一 prompts

Week 2
- Raw Video / ASR / Retrieval bake-off
- 成本与 latency 统计

Week 3
- Planner / Zero-shot Critic bake-off
- Agent Round（E6，成本低可与其他轮并行）
- expert + consumer blind evaluation
- 冻结 MVP baseline stack

Week 4
- 写入 Model Registry
- 更新 TECHNICAL_PLAN.md
- 开始 M0 end-to-end
```

---

# 6. Taste Critic 的具体模型路线

# 6.1 Stage C0：Zero-shot

候选：

```text
M-1 胜出的 Raw Video VLM
```

先验证：

\[
Acc_{\text{VLM}} > 50\%
\]

更重要的是：

```text
是否高于：
- 简单 aesthetic score
- 只看 reference similarity
- 只看 technical quality
```

---

# 6.2 Stage C1：SFT Pairwise Judge

默认：

```text
qwen3-vl-8b-instruct
```

数据：

```json
{
  "context": {
    "prompt": "...",
    "reference_summary": {},
    "material_summary": {}
  },
  "edit_a": "...",
  "edit_b": "...",
  "target": {
    "vote_distribution": {
      "a": 0.2,
      "b": 0.7,
      "tie": 0.1
    },
    "expert_distribution": {},
    "consumer_distribution": {},
    "reasons": []
  }
}
```

若用托管 SFT，可先把 soft label 转成：

```text
winner + confidence + criteria
```

但原始数据库必须保留分布。

---

# 6.3 Stage C2：Reward Head

当有足够数据后：

```text
Qwen3-VL backbone
+
LoRA
+
pairwise reward head
```

损失可包括：

\[
L =
L_{\text{pairwise}}
+\lambda_1L_{\text{dimension}}
+\lambda_2L_{\text{distribution}}
+\lambda_3L_{\text{reference}}
\]

其中：

- pairwise：A/B 排序
- dimension：节奏、镜头、故事、音乐等辅助任务
- distribution：拟合 2:3 等 soft disagreement
- reference：复刻模式的条件一致性

---

# 6.4 Stage C3：Planner Preference Training

只有 Critic 已经在 held-out 人类评测上稳定后，才考虑：

```text
Best-of-N
↓
收集更多 chosen / rejected EditPlanSpec
↓
Planner SFT / DPO
```

注意：

- 百炼当前 Qwen3-VL 8B 托管训练不支持 DPO。
- DPO 可放在纯文本/结构化 Planner 或自托管模型上。
- 不需要为了“用了 preference data”而强行上 RL。

---

# 7. 预处理模型与算法：必须保留传统 CV / Signal Pipeline

本项目是 model-centered，但不是 model-only。

## 必须由确定性组件承担

```text
FFprobe metadata
shot boundary detection
exact frame timestamps
audio extraction
beat / onset
RMS / silence
blur
exposure
shake / motion magnitude
duplicate frames
aspect ratio
face crop risk
black frame
audio clipping
```

原因：

- 可重复
- 可解释
- 成本低
- 时间精确
- 可以作为 VLM context
- 便于排查模型错误

核心原则：

> **模型做语义与审美判断，算法提供时间坐标和客观信号。**

---

# 8. Provider Abstraction

建议接口：

```python
from typing import Protocol

class MaterialUnderstandingProvider(Protocol):
    async def analyze_segments(self, request): ...

class ReferenceUnderstandingProvider(Protocol):
    async def extract_edit_plan(self, request): ...

class PlannerProvider(Protocol):
    async def generate_plans(self, request): ...

class MultimodalEmbeddingProvider(Protocol):
    async def embed(self, request): ...

class RerankProvider(Protocol):
    async def rerank(self, request): ...

class ASRProvider(Protocol):
    async def transcribe(self, request): ...

class TasteCriticProvider(Protocol):
    async def compare(self, request): ...

class EditingAgentProvider(Protocol):
    """角色 ⑥。fast / deep 由 operation 类型静态路由（FD-M12）。"""
    async def resolve(self, request): ...   # 指令 + 项目状态 -> EditOperation[]

class RendererProvider(Protocol):
    async def render_proxy(self, timeline_spec): ...
    async def render_final(self, timeline_spec): ...
```

禁止业务代码中出现：

```python
analyze_with_qwen(...)
edit_with_doubao(...)
```

应该出现：

```python
providers.material_understanding.analyze_segments(...)
providers.planner.generate_plans(...)
```

---

# 9. Model Registry

建议使用版本控制的 YAML：

```yaml
registry_version: 1
updated_at: "2026-08-25"

roles:
  material_understanding:
    primary:
      provider: alibaba
      model_id: qwen3.7-plus-2026-05-26
      endpoint_region: cn-beijing
      modalities: [text, image, video]
      video_audio: false
      structured_output: true
      frame_sampling: vendor_managed
      prompt_version: material_v3
      schema_version: segment_summary_v2
      timeout_s: 180
      fallback: seed20_lite
      status: provisional
    challengers:
      - seed20_lite
      - gemini37_flash
      - youtu_vita

  planner:
    primary:
      provider: alibaba
      model_id: qwen3.7-plus-2026-05-26
      structured_output: true
      prompt_version: planner_v4
      schema_version: edit_plan_spec_v2
      status: provisional
    challengers:
      - seed21_pro
      - gemini37_flash
      - gpt56_terra

  slot_embedding:
    primary:
      provider: alibaba
      model_id: qwen3-vl-embedding
      dimension: 1024
      status: provisional

models:
  seed20_lite:
    provider: volcengine
    model_id: TO_VERIFY_IN_ARK_CONSOLE
    release_variant: "Seed2.0 Lite late-April omni upgrade"
    modalities: [text, image, video, audio]
    structured_output: TO_VERIFY
    status: bakeoff_required

  gemini37_flash:
    provider: google
    model_id: gemini-3.7-flash
    modalities: [text, image, video, audio, pdf]
    video_sampling: "default 1 FPS"
    structured_output: true
    status: bakeoff_required

  youtu_vita:
    provider: tencent
    model_id: youtu-vita
    modalities: [text, image, video, audio_in_video]
    max_video_duration_s: 600
    max_video_size_mb: 100
    status: bakeoff_required
```

每次调用日志必须包含：

```text
model_id
provider
snapshot
prompt_version
schema_version
input duration
input tokens
output tokens
cost
latency
retry count
repair count
request id
```

---

# 10. Workflow / Skills 的使用政策

## 允许放在 Workflow / Skill 中

```text
transcribe_audio
extract_frames
run_scene_split
render_proxy
upload_asset
poll_async_job
convert_format
generate_thumbnail
invoke_vendor_vlm
```

## 不应只存在于 Workflow 画布

```text
EditPlanSpec schema
TimelineSpec schema
Material Matching policy
candidate ranking
Taste Critic aggregation
preference logging
experiment assignment
model registry
business fallback logic
```

正确结构：

```text
Core Orchestrator（自有代码）
      │
      ├─ Alibaba Skill / Workflow adapter
      ├─ Volcengine MediaKit / AgentKit adapter
      ├─ Google model adapter
      └─ OpenAI model / Agents adapter
```

---

# 11. 成本与延迟策略

## 11.1 不把所有候选都高清渲染

```text
N = 8 EditPlanSpec
↓
Structural Critic，不渲染
↓
Top 3 TimelineSpec
↓
360p / 540p proxy
↓
Video Taste Critic
↓
Top 1
↓
1080p / 4K final
```

## 11.2 多层模型路由

```text
cheap deterministic checks
↓
cheap VLM / embedding
↓
expensive rerank / planner
↓
expensive video critic only for Top-K
```

## 11.3 Cache

缓存键至少包括：

```text
asset_hash
segment boundaries
model snapshot
prompt version
schema version
sampling config
```

用户重新生成时，不能重复分析全部素材。

---

# 12. 数据、安全与区域

## 12.1 数据来源

本模型选型不以未经授权大规模抓取 TikTok / 抖音为前提。

优先：

- 用户授权素材
- creator 合作
- 剪辑师 raw → final pairs
- 合法许可数据集
- synthetic corruption
- 产品内 preference

## 12.2 区域策略

中国用户素材默认优先：

```text
中国区对象存储
中国区模型 endpoint
中国区 ASR / media processing
```

国际模型只用于：

- benchmark
- 明确同意的测试素材
- 未来国际部署

## 12.3 数据控制

每个 provider adapter 必须记录：

- 上传到哪个地域
- 临时文件 TTL
- 日志是否含视频 URL
- 供应商默认数据保留
- 是否可关闭训练使用
- 删除接口
- 访问控制

---

# 13. 最终推荐：MVP Baseline Stack

在 M-1 结果出来前，Coding Agent 可以按以下 baseline 搭 adapter，但不能删掉 challengers。

```text
Preprocessing
  FFmpeg + OpenCV + shot detector + audio DSP

ASR
  qwen-audio-3.0-asr-flash-filetrans

Material Understanding
  qwen3.7-plus-2026-05-26
  + ASR transcript
  + deterministic features

Reference Understanding
  shot-first pipeline
  qwen3.7-plus-2026-05-26
  + Qwen ASR
  + optional qwen3.5-omni-plus AV summary

Planner
  qwen3.7-plus-2026-05-26

Matcher
  qwen3-vl-embedding, dimension=1024
  → qwen3-vl-rerank

Zero-shot Critic
  暂不冻结；由 M-1 Raw Video Round 选出

Trainable Critic
  qwen3-vl-8b-instruct SFT/LoRA

Renderer
  FFmpegRenderer
  + optional MediaKitProvider

Agent Runtime
  自有 Python orchestration
  + provider-specific skills as tools
```

Mandatory challengers：

```text
Seed2.0 Lite 0428
Gemini 3.7 Flash
Tencent YT-VITA
Seed2.1 Pro
GPT-5.6 Terra
Doubao Vision Embedding
Marengo 3.0
Gemini Embedding 2
Doubao ASR 2.0
OpenAI Transcribe
```

---

# 14. 在合并进 TECHNICAL_PLAN.md 前必须补齐的未知项

```text
[ ] Seed2.0 Lite 0428 在 Ark 的准确 model_id
[ ] Seed2.0 Lite 视频时长、大小、采样和结构化输出
[ ] Seed2.1 Pro 的准确 model_id 与多模态能力
[ ] Doubao Vision Embedding 当前正式 model_id、限制和价格
[ ] Doubao ASR 2.0 精确 API 与 timestamp/diarization 能力
[ ] YT-VITA Structured Output 与采样策略
[ ] ERNIE 当前稳定非 Preview 型号
[ ] 各平台中国区价格快照
[ ] 各平台视频数据保留策略
[ ] Gemini / Google Cloud 中国用户数据跨境方案
[ ] Twelve Labs 中国区 latency / availability
[ ] Qwen3-VL SFT 视频样本实际上传限制
[ ] ⑥-Fast 候选清单与各家轻量模型的 tool-calling 稳定性实测
[ ] ⑥-Deep 是否直接复用 Planner 主选，还是需要独立模型
[ ] 各候选在多轮状态跟踪上的实际漂移点（第几轮开始不可靠）
```

这些不能由 Coding Agent 猜测；应通过控制台/API smoke test 写回 registry。

---

# 15. 给 Coding Agent 的执行约束

在生成最终 `TECHNICAL_PLAN.md` 时：

1. 把本文件的 `FD-M01`–`FD-M10` 放入 Architecture Decisions。
2. 在 M0 前新增 `M-1 Model Bake-off`。
3. 不把任何 provisional model 写成永久唯一供应商。
4. 所有模型通过 provider interface 接入。
5. 所有 prompts、schemas、model snapshots 进入版本控制。
6. Raw Video 与 Preprocessed Package 两套协议都要支持。
7. 时间边界由 deterministic pipeline 提供。
8. M3 前不实现 RL。
9. 不因平台已有 Workflow/Skill 而删除 `EditPlanSpec`、`TimelineSpec` 或 Preference Log。
10. M-1 未完成前，只实现最小 adapter 和统一 eval harness，不大规模开发依赖某个模型特殊输出的代码。
11. Interactive Editing Agent（⑥）通过 `EditingAgentProvider` 接入，与 Video VLM 分离配置（FD-M11）。
12. Agent 只能调用 `TECHNICAL_PLAN.md` §11.3 目录中的标准化 operation，**不得输出 FFmpeg 命令，不得整体重写 timeline**。
13. Scope 与 Lock 检查在执行器代码里实现，**不是写在 prompt 里的请求**。

---

# 16. 官方资料索引

## Alibaba Cloud / Qwen

- Visual understanding:  
  https://help.aliyun.com/en/model-studio/vision-model/
- Qwen3.7 Plus:  
  https://help.aliyun.com/zh/model-studio/qwen3-7-plus
- Qwen3.5 Omni Plus:  
  https://help.aliyun.com/zh/model-studio/qwen3-5-omni-plus
- Embedding:  
  https://help.aliyun.com/zh/model-studio/embedding
- Rerank:  
  https://help.aliyun.com/en/model-studio/rerank
- Model tuning overview:  
  https://help.aliyun.com/zh/model-studio/model-training-overview
- ASR:  
  https://help.aliyun.com/zh/model-studio/asr-model
- Workflow applications:  
  https://help.aliyun.com/zh/model-studio/workflow-application/
- Agent Skills:  
  https://help.aliyun.com/zh/model-studio/managed-agents-skill

## ByteDance / Volcengine

- Seed2 official model page:  
  https://seed.bytedance.com/en/seed2
- MediaKit CLI official repository:  
  https://github.com/volcengine/mediakit-cli
- Volcengine AgentKit documentation:  
  https://docs.volcengine.com/docs/86681/2205064?lang=zh

## Google

- Gemini 3.7 Flash:  
  https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash
- Video understanding:  
  https://ai.google.dev/gemini-api/docs/video-understanding
- Gemini Embeddings:  
  https://ai.google.dev/gemini-api/docs/embeddings
- Vertex / Gemini tuning:  
  https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/tuning

## OpenAI

- Models:  
  https://developers.openai.com/api/docs/models
- GPT-5.6 Terra:  
  https://developers.openai.com/api/docs/models/gpt-5.6-terra
- Fine-tuning platform update:  
  https://openai.com/index/introducing-improvements-to-the-fine-tuning-api-and-expanding-our-custom-models-program/
- Agents SDK update:  
  https://openai.com/index/the-next-evolution-of-the-agents-sdk/
- AgentKit / Agent Builder transition:  
  https://openai.com/index/introducing-agentkit/

## Tencent

- TokenHub multimodal understanding / YT-VITA:  
  https://cloud.tencent.com/document/product/1823/130988

## Baidu

- Qianfan visual and video understanding:  
  https://intl.cloud.baidu.com/en/doc/qianfan/s/fm8r1ndsm-intl-en

## Amazon

- Nova 2 multimodal understanding:  
  https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-multimodal-models.html
- Nova 2 video SFT:  
  https://docs.aws.amazon.com/nova/latest/nova2-userguide/nova-sft-2-fine-tune.html

## Twelve Labs

- Marengo 3.0:  
  https://docs.twelvelabs.io/docs/concepts/models/marengo
- Pegasus 1.5:  
  https://docs.twelvelabs.io/docs/concepts/models/pegasus
- Marengo Embeddings:  
  https://docs.twelvelabs.io/docs/cloud-partner-integrations/amazon-bedrock/create-embeddings

---

# 17. 一句话决策

> **先用阿里作为结构清晰、可快速落地的 MVP baseline；把字节 Seed2.0 Lite、Gemini 3.7 Flash 和腾讯 YT-VITA 放进同一套 Raw Video Bake-off；用 Qwen3-VL Embedding/Rerank 搭第一版 Matcher，用 Qwen3-VL-8B 作为可训练 Taste Critic 起点。平台已有 Workflow 和 Skills 用来减少 plumbing，但项目真正需要积累的仍然是自己的 EditPlanSpec、TimelineSpec、Preference Dataset、Taste Critic 和用户修改数据。**
