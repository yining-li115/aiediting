# 投资人 Demo 完成清单

状态：项目执行清单。产品范围与架构仍以
`INVESTOR_DEMO_ROADMAP.md` 和 `AGENTIC_WORKSPACE_ARCHITECTURE.md` 为准。

## 勾选规则

- `[x]` 只表示当前工作树中已经接通、经过相应测试且能够产生真实状态或媒体结果。
- `[ ]` 表示尚未完成，或只有 Contract、Provider、mock、策略文本、按钮外观等局部实现。
- 一个用户功能只有在 `UI -> API/Job -> Service -> ProjectDoc Revision -> Artifact/UI`
  路径闭环后才能勾选。
- 每完成一项，应同时补充相关自动化测试；涉及用户路径时还要完成一次浏览器验收。
- 不允许为了勾选清单而增加第二套 Timeline、绕过 Patch，或把预录结果伪装成实时生成。

## 0. 架构与项目内核

- [x] 架构边界、AI Coding Rules 和 Investor Demo Roadmap 已写入 `docs/`。
- [x] ProjectDoc 是唯一权威编辑状态。
- [x] Patch v1 闭合操作、Scope、Lock 与完整文档校验已实现。
- [x] Immutable Revision、幂等键和 current Revision compare-and-swap 已实现。
- [x] Candidate、accept/reject、undo/restore 状态机已实现。
- [x] Project 创建时自动生成 initial Revision，浏览器可直接进入项目。
- [x] 前后端 Contract 类型由后端 Schema 生成。
- [x] Alembic 可以从空数据库升级到当前 Schema，模型与迁移无漂移。
- [x] 已创建无 AI 签名的 Continuous Editing checkpoint commit `5aaef04`。

## 1. 素材上传与理解

- [x] 视频、照片、音频按 Project 隔离上传并写入 AssetRef Revision。
- [x] 上传后创建 durable media-processing Job。
- [x] 生成 proxy、thumbnail，并持久化 readiness/error 状态。
- [x] 确定性 Shot 边界检测，不按固定帧数切剧情。
- [x] 质量分析、关键帧与来源时间映射。
- [x] ASR 证据带原始 Asset 时间范围和 provenance。
- [x] 跨 Shot 语义 Moment 聚合，失败时安全回退为 Shot Moments。
- [x] `search_moments` 与 `inspect_media` 只读取冻结、项目内的证据。
- [x] 浏览器显示素材处理与理解进度。
- [ ] 用真实火山 ASR/视觉/语义模型跑通一组 Golden Footage。
- [ ] 为真实模型结果建立质量基线：Shot、ASR、Moment 可用率与失败样例。

## 2. AI 自动生成第一版剪辑

- [x] 上传素材后自动创建 durable Director Job。
- [x] AgentRunContext 冻结 Prompt、ProjectDoc、素材状态和 Moments。
- [x] Director 生成 Creative/Copy/Caption/Music Strategy 和镜头选择。
- [x] First-cut Decision 转换为 typed Patch，不直接写 ProjectDoc。
- [x] ProjectService 生成 immutable candidate Revision。
- [x] Candidate 自动渲染 revision-bound preview。
- [x] 渲染后 Reviewer 执行确定性检查，并支持火山多模态 Provider。
- [x] Reviewer 拒绝时可进行一次受限 AutoFix；失败保留旧 Revision。
- [x] Review 通过后才原子接受 Candidate。
- [x] 浏览器显示 Director 说明、Reviewer 结果、进度和实际预览。
- [ ] 使用真实火山 Director + Reviewer 完成一次端到端质量验收。
- [x] 首版 Caption Strategy 将所选 Moment 的 ASR 写入 `caption.clean@1.0.0` CaptionTrack，并在 preview/export 路径实际渲染。
- [x] 首版 Music Strategy 必须选择项目内明确授权的 music Asset，并实际生成 Gain/Fade/Ducking 音轨与混音预览。
- [ ] 建立第一版剪辑质量评测：开场、连贯性、节奏、重复镜头、音画匹配。

## 3. Continuous Editing Agent

- [x] 每个 Project 有 durable primary Conversation 和 Message history。
- [x] 每轮创建 durable AgentTurn、Job、幂等键和明确的 base Revision。
- [x] AgentTurnContext 冻结 ProjectDoc、Moments、最近对话和 WorkspaceSelection。
- [x] Tool Registry 闭合为 `read_project`、`search_moments`、`inspect_media`、`propose_patch`。
- [x] 模型可动态选择工具；Tool Loop 最多六步并持久化每一步。
- [x] stale Revision、越权工具、重复 call ID、非法 Trace 和超步数请求会失败。
- [x] `propose_patch` 只能产生提案，最终仍经过 ProjectService。
- [x] Agent 根据实际提交操作解释“改了什么”，并关联结果 Revision。
- [x] 左侧对话面板已接入 Job、Revision、preview 和 history 刷新。
- [x] 生产运行时已删除离线 Director 与关键词/规则回退；模型未配置或输出非法时失败并保留当前 Revision。
- [x] Preview Player 将当前播放帧写入 WorkspaceSelection；完整 preview 和 range preview 都映射为 Revision 的绝对帧，历史 Revision/source proxy 不会污染当前 Turn。
- [x] Preview Annotation v1 已接通：用户可在 `Preview edit` 标记当前帧或区间、写评论并引用到左侧对话；引用绑定 ready preview Artifact 与精确 Revision，持久化到 Message/AgentTurnContext，并确定性解析为重叠 timeline IDs。
- [ ] 右侧工作区把真实 clip/track/section 选区传入 WorkspaceSelection。
- [ ] 完整 Workspace 继续传入 visible range、选区和 comparison Revision。
- [x] 正式 Director 的闭合 schema、Tool Registry 和 ProjectService/Revision/Preview 路径已支持 clip-boundary Trim、whole-clip Move/Delete；Gemini 与火山共用同一 Provider port，不使用自然语言关键词枚举。
- [x] 真实 Gemini 3.1 Flash Lite 已完成一次 whole-clip Delete：playhead 定位 -> read_project -> typed Patch -> ProjectService -> 新 Revision -> preview enqueue。
- [x] 真实 Gemini 多轮 Golden Run 已验收 Trim -> Move -> Delete -> Replace -> Crop/Center Reframe -> Constant Speed -> Gain/Fade：同一 Conversation 连续生成七代 child Revision，8 份 revision-bound FFmpeg 预览均保留且包含音视频流。
- [x] `propose_patch` 在提交前执行无写入 ProjectService + renderer dry-run；非法 scope、stale ID、gap/overlap 等错误回到六步 Tool Loop 受限修正，失败 proposal 不推进 head。
- [x] 真实模型 Golden Run 通过后已移除运行时离线关键词策略。
- [x] 生产 Director 未配置时失败关闭，模型问题通过 AgentTurn/Job 错误显示到前端；测试使用显式 scripted fixtures。
- [x] 自然语言替换镜头端到端可用；替代素材必须先经过 `search_moments` 和 `inspect_media`，并同步替换来源原声。
- [x] 自然语言 Crop/Center Reframe 端到端可用；未支持的 face/saliency/manual Reframe 继续失败关闭。
- [x] 自然语言 Constant Speed 端到端可用；源范围、输出时长、来源音频和 timeline ripple 保持精确一致。
- [x] 自然语言来源音频 Gain/Fade 端到端可用，并生成新的 Revision-bound Preview。
- [x] Continuous Editing 的正式模型 schema/Tool Registry 已开放字幕生成、文字、样式、时序操作，并通过 scripted AgentTurn -> Revision -> Preview 端到端测试；真实模型 Golden Run 待完成。
- [x] Continuous Editing 可从冻结 runtime Assets 选择已授权音乐，调整音量、淡入淡出和 `speech` ducking，并通过 scripted 端到端测试；真实模型 Golden Run 待完成。
- [x] 自然语言 Graphic 可通过 Registry 搜索已发布组件并生成 typed Patch；Agent 找不到组件时可在 Sandbox 生成候选，但不能自行发布。
- [ ] 自然语言转场、Effect、Visual Motion 端到端可用。
- [x] 无 playhead/选区 grounding 的模糊指令先经过模型语义澄清门；Golden Run 中“把这个镜头调整一下”只产生追问，不产生 Patch、Preview 或新 Revision。
- [ ] 高影响修改进入 Candidate -> Preview -> Reviewer/用户确认流程。
- [ ] 局部编辑只重渲染受影响范围并保留必要的转场/音频 handles。
- [ ] 对话超过窗口后使用可审计 Compaction/Memory，而不只是截断最近消息。
- [ ] 用最终生产 Director 模型完成多轮 Continuous Editing Golden Run。

## 4. 右侧视频编辑工作区与手动编辑

- [ ] 选定并记录编辑器方案：成熟开源编辑器内核或自研最小外壳。
- [ ] 编辑器直接读写 ProjectDoc/Patch，不建立第二份权威 Timeline。
- [ ] Player、时间轴、playhead、缩放和播放状态同步。
- [ ] 视频/照片/音频/字幕/Graphic 轨道可视化。
- [ ] 缩略图、波形、Clip 边界和选中状态可见。
- [ ] 拖动、Trim、Split、Move 等手动操作转换为 typed Patch。
- [ ] Inspector 修改 Crop、Speed、Gain、Fade、Caption Style 等属性。
- [ ] 用户可锁定 section/track/clip，Agent 和手动操作都遵守 Lock。
- [x] 已提交 Revision 历史可查看，Restore 会创建新 Revision。
- [ ] 两个 Revision 可视化比较修改范围与 Agent 说明。
- [ ] Undo/Redo 在浏览器中使用 Revision/Patch 语义完成。
- [ ] 手动编辑后 Agent 能继续编辑且不破坏锁定或手动保留内容。

## 5. 渲染、效果与最终导出

- [x] FFmpeg Renderer 直接读取指定 Revision 的 ProjectDoc。
- [x] 视频/照片硬切、恒定速度、Crop、中心 Reframe 可渲染。
- [x] 显式音频片段、Gain、Fade、Delay 和 Mix 可渲染。
- [x] `caption.clean@1.0.0` 字幕按精确帧范围渲染；中英文、字体、字号、描边、背景、换行和底部 Safe Area 参数可控。
- [x] 授权音频上传会生成 AAC proxy 与 waveform thumbnail；音乐可与原声/旁白通过 FFmpeg sidechain ducking 混音。
- [x] 完整 Preview、Range Preview 和 HQ Export 绑定精确 Revision。
- [x] 浏览器可播放 preview 并下载 ready export。
- [x] 新 Revision 不会覆盖或错误标记旧 Revision Artifact。
- [ ] 接入 FFmpeg `xfade` 第一批稳定转场并完成回归测试。
- [ ] 接入第一批 MIT Remotion Templates，但继续共用 ProjectDoc Timeline。
- [x] Remotion Component Manifest、Candidate、受限 Sandbox、真实 PNG 验证、显式 Approval 与不可变 Registry 基础已完成；候选源码的规范 Artifact 进入对象存储且绝不写入产品源码树，Agent Trace 保留可审计输入。
- [x] Agent 已接入 `search_components` / `create_component_candidate`；候选结果持久化，发布必须经过独立的用户/Reviewer Approval API，模型没有 publish 工具。
- [x] Hybrid Remotion RendererProvider 从 Registry 解析精确 Caption/Graphic 版本并按指定 Revision 生成 preview/range preview/export；ProjectService 在提交前校验发布状态、props 与 runtime。
- [ ] 完成 GL Transitions 技术验证并决定是否进入支持矩阵。
- [ ] Remotion Player 在右侧工作区读取同一 ProjectDoc timing。
- [x] Registry-backed 动态字幕、标题卡和品牌 Graphic 组件可预览并导出。
- [ ] Visual Motion、转场与像素级 Effect 的媒体输入 ABI、预览和导出。
- [x] 静态 `caption.clean` 的字体、Safe Area、竖屏文字换行和中英文排版可控；逐字高亮与动态字效仍待 Remotion。
- [ ] 音乐、旁白、原声与 ducking 的 preview/export 一致。
- [ ] 为转场、字幕、Graphic、Effect 建立完整 preview/export 一致性 fixtures；当前已有真实 Graphic 合成回归。

## 6. Reference Remake 模式

- [x] ProjectDoc/Patch 已有 ReferenceContext、Policy、Alignment 和 Adaptation 基础 Contract。
- [ ] Reference 视频上传、proxy 和分析进入正式 Project 工作流。
- [ ] 从参考视频生成 immutable Reference Blueprint 与证据。
- [ ] 用户可选择 preserve/adapt/exclude 的参考维度。
- [ ] 参考段落与本方 Moments 完成多镜头 Alignment。
- [ ] 素材缺失时生成明确 Adaptation Decision，不伪造匹配。
- [ ] Director 生成 reference-aware typed Patch 与 candidate。
- [ ] Reviewer 检查参考保持度和适应合理性。
- [ ] 浏览器并排比较 Reference 与输出，并展示映射与偏差解释。
- [ ] Reference 局部反馈只更新映射到的输出范围。

## 7. 投资人可见的产品体验

- [x] 创建页支持 Prompt、素材和 Autonomous/Reference 模式入口。
- [x] 基础左侧 Agent / 右侧 Preview 双栏工作区已形成。
- [x] 上传、理解、Director、Render、Reviewer 有可见进度。
- [ ] 将当前工程型页面整理为统一、无跳变的产品工作流。
- [ ] 首次进入时展示清晰的空状态、示例 Prompt 和下一步引导。
- [ ] 所有错误给出用户可执行的恢复操作，而不只显示技术信息。
- [ ] 所有重试复用幂等键，页面刷新后可恢复正在运行的任务。
- [ ] 失败的模型、分析或渲染不会清空最后可播放版本。
- [ ] 移除、禁用或明确标注所有尚未接通的装饰性控件。
- [ ] 统一中文/英文演示语言、术语和 Agent 语气。
- [ ] 完成常用桌面分辨率与全屏演示模式适配。
- [ ] 最终导出完成后有明确下载、文件信息和成功状态。

## 8. Demo 运行环境与可观测性

- [x] Job、Agent Run、Render、Review 可在进程重启后恢复未完成工作。
- [x] Provider 按 Director/Reviewer 等 Role 配置，不使用一个全局模型名。
- [x] Gemini Director 使用固定版本候选链；只对 quota/timeout/5xx 自动降级，实际调用模型进入调用日志和成本记录。
- [ ] 将火山正式 Endpoint、Model ID 和密钥以部署 Secret 配置。
- [ ] 明确各 Role 的模型、输入限制、超时、重试、成本和降级策略。
- [ ] 为模型调用记录 latency、token、费用、错误类型和 prompt version。
- [ ] 为每次演示串联 project_id、turn_id、job_id、revision_id、artifact_id 日志。
- [ ] 增加 Job 取消、超时、stale worker 和异常退出测试。
- [ ] 收紧 CORS、Project 访问控制和上传限制以适合演示部署。
- [ ] 部署持久数据库、对象存储、API、Worker 和 Web。
- [ ] 从全新浏览器访问部署环境完成上传到导出的 smoke test。
- [ ] 建立 Demo 健康检查页或运行前诊断脚本。

## 9. Golden Demo 与演示验收

- [ ] 选定一组授权、稳定且能体现故事性的 Golden Footage。
- [ ] 固定 Autonomous 首版 Prompt、连续编辑 Prompt、手动编辑动作和 Reference 视频。
- [ ] 定义预期 Revision 历史、关键镜头、字幕、音乐和最终时长。
- [ ] 提供一键创建/重置 Golden Project 的脚本。
- [ ] 冷缓存完整跑通一次，不依赖之前生成的隐藏结果。
- [ ] 热缓存完整跑通一次，并验证幂等重放不产生重复 Revision。
- [ ] 演练模型超时、素材分析失败和渲染失败，确认旧版本仍可播放。
- [ ] 连续三次完成“创建 -> 上传 -> 首版 -> 对话修改 -> 手动修改 -> 锁定 -> Agent 再编辑 -> 对比/恢复 -> 导出”。
- [ ] 完成投资人讲解脚本、操作节奏和每一步预期画面。
- [ ] 准备只读的已缓存 Artifact 应急展示并明确标识；不得离线生成或伪装为本次模型结果。
- [ ] 在正式演示机器、网络和投屏环境完成最终彩排。

## 当前推荐实施顺序

当前产品决策是先完成 Preview-only 全链路，验收效果后再接 Workspace：

1. **已完成：保存 Continuous Editing checkpoint**：提交已通过测试的 Conversation、Context、Tool Loop、Patch、Revision、Preview 和左侧对话面板。
2. **已完成：Preview-only 高频编辑包**：Trim、删除/移动、替换、Crop、中心 Reframe、恒定 Speed、Gain 和 Fade 已通过真实模型和 FFmpeg Golden Run。
3. **进行中：Preview-only 表现层**：字幕、授权音乐和 Ducking 基线已完成；Remotion Caption/Graphic 的候选、沙箱、Approval、Registry、Agent 工具和 Revision-bound Renderer 闭环已完成；Preview Annotation v1 已完成，下一步是第一批真实组件/转场与产品级前端打磨。
4. **Preview 产品体验**：左侧对话和右侧播放器形成无跳变流程，显示 Agent/Render 阶段、实际改动、历史、Restore、失败恢复和 HQ Export。
5. **真实模型 Golden Run**：先用 Gemini 调通与评测，随后对素材理解、Director、Reviewer 和多轮 Continuous Editing 完成火山等生产候选的质量调参与 Role 选型。
6. **Reference Remake Preview-only**：复用同一 ProjectDoc、Agent Harness、Patch、Reviewer 和渲染链路完成参考复刻全流程。
7. **Golden Project、部署、可观测性和完整彩排**。
8. **Workspace**：最后加入只读 Timeline/AI 操作展示，再开放 Selection 和手动编辑。

### 下一项

- [x] Preview-only 第一组：静态字幕渲染、授权音乐上传/混音与 speech ducking。
- [x] Remotion 组件基础：候选源码在临时沙箱验证并存入对象存储，不进入产品源码；发布需要显式 Approval。
- [x] 接入 Agent 组件搜索/创建工具和 Hybrid Remotion RendererProvider，完成 Graphic 的真实 Revision-bound render 回归。
- [ ] 在前端加入候选 PNG 审核/发布入口，并完成第一批投资人演示用动态字幕、标题卡和品牌组件。
