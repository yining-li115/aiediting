# 火山引擎模型、数据服务与部署启用计划

状态：投资人 Demo 与受邀内测阶段的服务选型和启用依据。
记录日期：2026-09-27。价格为当前官方公开价格的规划算例，实际下单前仍以控制台为准。

## 1. 决策摘要

火山引擎适合作为 AIVE 的模型和云基础设施供应商，但不接管产品架构。

```text
Harness + versioned typed contracts
  -> 模型负责理解、规划和审美判断
  -> ProjectService 负责权限、校验、Patch 和 Revision
  -> FFmpeg / Remotion 负责确定性渲染
  -> PostgreSQL 负责权威业务状态
  -> TOS 负责媒体字节
  -> OpenViking 负责可重建的 Skill / Resource 检索
```

以下边界不因采用火山服务而改变：

- `ProjectDoc` 的不可变 Revision 是唯一权威编辑状态。
- Conversation、AgentTurn、Job、Artifact 和 Blueprint 都必须指向明确的 Project/Revision。
- 模型只返回我们定义的版本化结构，不直接写数据库、ProjectDoc、FFmpeg 命令或 Remotion 源码树。
- OpenViking、Embedding、索引、Compaction 和 1M 模型上下文都是派生上下文，不是永久记忆或项目真相。
- 所有模型和云服务都在 Provider/Repository/Storage/Renderer 接口之后，可以替换而不改变 ProjectDoc、Patch 和 Revision。

## 2. 数据所有权不能合并

| 数据 | 权威存储 | 说明 |
| --- | --- | --- |
| Project、完整 ProjectDoc、不可变 Revision、Patch 记录、Lock | PostgreSQL | 唯一权威项目状态 |
| Conversation、Message、AgentTurn、Job、Tool Trace、Review | PostgreSQL | 可审计工作流和恢复状态 |
| Asset、Moment、StyleBlueprint、Artifact 元数据 | PostgreSQL | 保存稳定 ID、状态、证据引用和 provenance |
| 原视频、proxy、thumbnail、音频、证据帧、preview、export、Remotion bundle | TOS | 数据库只保存 storage key、hash、权限和生命周期 |
| Moment/素材向量 | Demo 使用 PostgreSQL `vector` | 派生检索数据，可重新生成 |
| EditorialSkill 的规范版本、Manifest、评测版本 | `skills/` + PostgreSQL/TOS Registry | 明确版本、权限、Reviewer 标准和 Golden Run |
| 可搜索的 Skill、品牌资料和剪辑方法副本 | OpenViking Resource/Skill | 只作发现和召回，命中后仍解析为精确版本 |
| 跨项目用户偏好 | 内测阶段的 OpenViking Memory | 不能保存 ProjectDoc、密钥或未脱敏媒体内容 |

1M 上下文只表示一次模型请求的容量。正确运行方式仍然是：保存原始历史，生成可审计 Compaction，按需检索 Memory/Skill，然后冻结本轮 `AgentRunContext`。

## 3. 投资人 Demo 阶段：需要开通的服务

这里的“Demo 阶段”不是最小玩具版本。凡是会影响真实剪辑质量、Golden Run 或云端完整链路的服务，都应在投资人 Demo 验收前接通。

### 3.1 正式模型栈

| Role | 正式选择 | 现在需要的原因 |
| --- | --- | --- |
| Director、复杂连续编辑、Creative Reviewer | `doubao-seed-2-1-pro-260915` | 负责长链路工具调用、EditBlueprint、局部编辑和最终审美判断 |
| Material、Style、Audio Understanding、Moment Grouping、Fast Agent、Rerank | `doubao-seed-2-1-lite-260915` | 负责视频/音频联合理解、开放结构 StyleBlueprint 和低成本高频调用 |
| ASR | Seed-ASR 2.0 录音文件识别 | 生成文字、语言、说话人和原始时间轴证据，不能由通用模型摘要代替 |
| Embedding | `doubao-embedding-vision-251215` | 文本、图片、视频统一召回，实现“用自然语言找镜头” |
| Renderer | FFmpeg + Remotion | 确定性合成，不是模型服务 |

一个 Ark API Key 可以调用多个已授权 Model ID。每个 Role 仍保留独立 Prompt、Schema、预算、超时、重试、评测和调用记录，不为每个 Role 部署一份相同模型。

暂不开通 Seedance 和 Seedream。它们是生成视频/图片服务，不属于当前“编辑用户已有素材”的主链路。

### 3.2 影响剪辑质量的 Skill 服务现在就接

EditorialSkill 不能等到上线后才第一次验证，因为它会直接改变 Director 的选镜、节奏、字幕、音乐、转场和 Reviewer 标准。

Demo 阶段开通 OpenViking 个人版，但只启用 Resource/Skill 路径：

```text
用户 Prompt + 项目意图
  -> 检索适用 EditorialSkill
  -> 解析精确 skill_id + version
  -> 冻结到 AgentRunContext
  -> SourceUnderstanding / StyleBlueprint / EditBlueprint
  -> typed Patch
  -> Render + Creative Review
```

每个 EditorialSkill 至少包含：

- 适用场景与反例；
- StyleBlueprint 先验和 EditBlueprint 约束；
- 可调用工具、Patch 能力和权限；
- 正例、反例和禁用模式；
- Caption、Music、Transition、Remotion 组件偏好；
- Reviewer 评分规则；
- Prompt/Schema/Skill 版本；
- 可重复的 Golden Footage 与 Eval。

OpenViking 只保存可检索副本。Skill 的规范版本必须由 AIVE Registry 管理；模型和 OpenViking 都不能自行覆盖已发布 Skill。

Demo 的 Skill 验收至少包括：同一素材和 Prompt 分别在“无 Skill”和“指定 Skill”下运行，比较 EditBlueprint、Revision、Preview 和 Reviewer 结果，证明变化来自已记录的 Skill 版本，而不是隐藏规则或随机 Prompt。

跨项目自动 Memory 写入暂时关闭，避免 Golden Run 被历史偏好污染。它在内测阶段以用户隔离和可删除策略启用。

### 3.3 Demo 云数据与运行服务

| 服务 | Demo 用途 | 是否开通 | 边界 |
| --- | --- | --- | --- |
| AIDAP Serverless PostgreSQL | 正式 Project/Revision/Conversation/Job/Blueprint 状态 | 必须 | 替换部署环境 SQLite；不存媒体字节 |
| TOS | 原始媒体、proxy、preview/export、证据和 Remotion Artifact | 必须 | 私有 Bucket；浏览器使用受控 URL |
| OpenViking 个人版 | EditorialSkill、品牌/剪辑资源的检索验证 | 必须 | Demo 只用 Resource/Skill，不作为 Project Memory |
| veFaaS Web App | Web/API 与 Agent application process | 必须 | API 只编排 application services |
| veFaaS 异步任务 | Media、ASR orchestration、FFmpeg/Remotion render worker | 必须 | 每个任务携带 project/job/revision identity |
| veFaaS 云沙箱 | `create_component_candidate` 的隔离编译、渲染和 PNG 验证 | 必须按需 | 禁网、限时、无发布权限；只输出 Candidate Artifact |
| 日志与指标 | Job/Agent/Render/Review 串联和费用记录 | 必须 | 不记录 API Key、完整用户媒体或未脱敏 Prompt |

Remotion 候选创建已经是 Demo 功能的一部分，因此云沙箱不是可有可无的“未来服务”。当前 `LocalRemotionSandbox` 只适合 macOS 本地 Demo；云端必须替换为容器或 microVM 隔离实现。发布仍需要独立 Approval，Sandbox 中的 Agent 没有 publish 权限。

### 3.4 Demo 阶段暂不开通

| 服务 | 决定 | 原因/替代方案 |
| --- | --- | --- |
| VikingDB 专用向量库 | 暂不开 | 当前数据量用 AIDAP PostgreSQL `vector` 足够；Embedding 模型仍立即接入 |
| AgentKit Session Store | 暂不开 | 已有 durable Conversation/AgentTurn；继续使用同一 PostgreSQL Schema |
| AgentKit Runtime/专属网关 | 暂不开 | 现有 Harness 已定义运行边界；veFaaS 可先承载 API/Worker |
| Supabase BaaS | 暂不开 | 与 FastAPI、SQLAlchemy、Alembic 和现有权限边界重叠 |
| Mem0 | 暂不开 | 与计划中的 OpenViking Memory 重叠 |
| Redis/Kafka/Celery | 暂不开 | 先使用 durable Job + PostgreSQL 原子领取；按真实并发指标决定 |
| Kubernetes/VKE | 暂不开 | 一人团队和 Demo 负载不需要集群运维 |
| GPU 服务器 | 暂不开 | 正式理解模型通过 API 调用，渲染先使用 CPU Worker |

“暂不开”不是默认列入内测采购清单。只有达到本文后面的明确触发条件，才重新评估。

## 4. Demo 推荐部署拓扑

```text
Browser
  ├── Web assets / preview delivery ─────────────── TOS + controlled URL
  └── API / progress events ────────────────────── veFaaS Web App
        ├── ProjectService / Revision Repository ─ AIDAP PostgreSQL
        ├── VideoAgentHarness ──────────────────── Ark / Seed-ASR
        ├── Skill lookup ───────────────────────── OpenViking Resource/Skill
        └── durable Job dispatch
              ├── Media worker ─────────────────── veFaaS async task
              ├── Render worker ────────────────── veFaaS async task
              └── Component candidate sandbox ─── veFaaS cloud sandbox
```

所有资源优先放在华北 2（北京），归属 `aive-video-editor` 项目并使用同一套资源标签。媒体由浏览器/TOS 传输，不经过 API 进程中转大文件。

推荐的初始资源策略：

- API：1 vCPU / 2 GiB 弹性实例；开发时不常驻，演示前预热。
- Render Worker：4 vCPU / 8 GiB、并发 1、异步任务；自定义镜像包含 FFmpeg、Node、Remotion、Chromium、fontconfig 和批准字体。
- PostgreSQL：0.25 CU 起，允许扩到 2 CU，空闲约 5 分钟后挂起。
- TOS：私有 Bucket；original、proxy、artifact 使用不同前缀和生命周期。
- Sandbox：按任务创建，完成或超时即释放。

veFaaS 异步任务支持最长 3 小时，适合音视频处理。若真实 Remotion Golden Run 证明 Chromium 冷启动、临时磁盘或稳定性不满足演示要求，只把 Render Worker 切换为 4 vCPU / 8 GiB ECS；ProjectDoc、Job、Storage 和 RendererProvider 均不改变。

## 5. Demo 非模型成本算例

| 项目 | 当前官方计费口径 | 规划算例 |
| --- | --- | --- |
| AIDAP PostgreSQL | 北京 ¥0.5/CU/小时；¥0.00125/GiB/小时 | 0.25 CU 每天活跃 2 小时 + 10 GiB，约 ¥16.5/月 |
| TOS | 标准存储约 ¥0.099/GiB/月；公网下载约 ¥0.5/GB；请求 ¥0.01/万次 | 100 GiB 存储 + 100 GB 下载约 ¥60/月 |
| OpenViking 个人版 | ¥0.01/小时；前 50 文件免费；超额每万文件 ¥0.003/小时 | 基础约 ¥7.2/月，另计 VLM/Embedding 调用 |
| veFaaS 弹性 CPU | ¥0.0001012/vCPU/秒 | 4 vCPU / 8 GiB 渲染 10 分钟约 ¥0.37/次 |
| veFaaS 弹性内存 | ¥0.0000256/GiB/秒 | 100 次上述渲染约 ¥36.6 |
| veFaaS 公网流量 | ¥0.8/GiB | Preview 应由 TOS/CDN 提供，避免 API 流量 |

投资人 Demo 的非模型月度预算按使用量预计约 ¥60–300。主要变量是媒体下载流量、渲染时长、API 是否保留热实例和 Sandbox 使用时长。这是规划算例，不是采购报价。

参考：

- [AIDAP 计费项与价格](https://docs.volcengine.com/docs/aidap/Billing_items_and_prices?lang=zh)
- [TOS 计费概览](https://docs.volcengine.com/docs/TorchObjectStorage/Billingoverview-2?lang=zh)
- [veFaaS 按量计费](https://docs.volcengine.com/docs/vefaas/Pay-as-you-go-27?lang=zh)
- [veFaaS 异步任务](https://docs.volcengine.com/docs/vefaas/Configuring_async_tasks?lang=zh)
- [OpenViking 发布与计费说明](https://docs.volcengine.com/docs/vector_database_vikingdb/Releasenotes-14?lang=zh)
- [VikingDB 向量库计费](https://docs.volcengine.com/docs/vector_database_vikingdb/Billingofavectordatabase?lang=en)

## 6. 当前实现与正式服务之间的缺口

开通控制台服务不等于功能已经接通。当前代码需要按以下顺序完成适配：

| 缺口 | 当前状态 | Demo 完成条件 |
| --- | --- | --- |
| 火山 Role 配置 | 已完成：Pro、Lite、Embedding、Seed-ASR 使用固定 ModelCard；Active Stack 已切换，模型与 Role 独立记录 | 用真实凭据跑通 Golden Run 后冻结版本 |
| Seed-ASR 2.0 | Adapter、独立语音 Key、TOS 短时 URL、提交/轮询和句级时间轴已完成真实调用 smoke test；临时对象会在调用后清理 | 用授权 Golden Footage 验证多窗口原始 Asset 时间回映、Audio Understanding 和失败状态 |
| Embedding Vision | 原生 `/embeddings/multimodal` Adapter、文搜图 Query/Corpus instruction、维度校验和 L2 归一化已完成 | 用真实素材验证费用、召回回归和批量吞吐 |
| TOS | 私有北京 Bucket、项目限定 IAM 用户、官方 SDK `TOSStorage`、本地缓存、原子 commit 和短时签名 URL 已接入；真实上传/签名下载/删除 smoke test 已通过 | 用完整项目验证 original/proxy/preview/export 生命周期和部署 Secret |
| PostgreSQL | 本地默认仍是 SQLite | 从空 AIDAP PostgreSQL 执行全部 Alembic migration 并跑 API/Job/Revision 集成测试 |
| OpenViking Skill | 尚未成为 Runtime Tool/Context source | 精确 Skill 版本检索、冻结、审计、失效处理和 A/B Golden Run 通过 |
| 云 Sandbox | 当前是 macOS `sandbox-exec` 实现 | 云端禁网、超时、依赖白名单、Artifact 上传和无 publish 权限验证通过 |
| veFaaS 部署 | 尚无正式部署定义 | Web、Media、Render 独立入口，重启恢复、Secret、健康检查和 smoke test 通过 |

新的火山配置不再使用单个 `AIVE_ARK_MODEL_ID`。Pro、Lite 和 Embedding
各自固定在独立 ModelCard 中并共享项目级 Ark Key；Seed-ASR 使用独立
Doubao Voice Key 和原生协议。真实密钥只进入本地/部署 Secret；
`.env.example` 只保留空占位，不把密钥写入日志、数据库或本文档。

## 7. 受邀内测上线前必须补齐

内测阶段不是把所有火山产品都打开，而是把真实多用户风险补齐。

### 7.1 必须项

- AIDAP PostgreSQL 走私网，启用备份/恢复演练、连接池、容量与慢查询告警。
- TOS Bucket 保持私有；上传、播放、导出使用短时授权，增加用户/项目隔离、生命周期和删除流程。
- API 至少保留一个热实例，Media/Render 独立扩缩容；限制单用户并发渲染和月度用量。
- 引入正式用户、组织、Project 权限和审计；不能仅凭 Project ID 访问。
- 配置模型、ASR、Embedding、数据库、TOS、Render、Sandbox 的预算阈值和告警。
- 每次调用记录 role、model、prompt/schema/skill version、latency、token/时长、费用和错误类型。
- 日志可串联 `user_id -> project_id -> turn_id -> job_id -> revision_id -> artifact_id`。
- 建立数据保留、用户删除、素材授权、音乐授权和失败 Artifact 清理策略。
- 完成限流、超时、取消、stale worker、重复回调、断点恢复和降级演练。
- 对生产模型、Skill 和 Renderer 版本执行固定 Golden Suite；升级不能直接替换线上版本。

### 7.2 内测阶段启用 OpenViking Memory

Memory 只保存跨项目、跨会话且用户明确可管理的偏好，例如：

- 常用视频比例、语言和字幕密度；
- 常用品牌语气、禁用词和授权音乐偏好；
- 用户对某类剪辑建议的接受/拒绝记录；
- 已批准的个人 EditorialSkill 绑定。

必须按用户/租户隔离，支持查看、删除和停用。以下内容永远不进入普通 Memory：API Key、ProjectDoc、完整媒体、未脱敏 transcript、不可替代的 Revision 状态。

OpenViking 个人版只适合单用户 Demo。进入多用户内测前，应选择具备用户隔离的企业版本，或继续由 AIVE PostgreSQL 管理 Memory 元数据并将 OpenViking 作为隔离的检索后端。

### 7.3 只有达到触发条件才评估

| 服务 | 触发条件 |
| --- | --- |
| VikingDB 专用向量库 | PostgreSQL `vector` 在真实数据规模、索引和查询优化后仍不能满足约定的召回质量、延迟或并发 |
| Redis/MQ/消息服务 | PostgreSQL Job 领取出现锁竞争、吞吐或延迟瓶颈，并有指标证明 |
| AgentKit Runtime/专属网关 | 需要统一 MCP/模型网关、独立 SLA 或托管 Runtime，且收益高于迁移现有 Harness 的成本 |
| Kubernetes/VKE | API/Media/Render 多实例调度复杂度超出 veFaaS/ECS 能力 |
| GPU Worker | 本地推理或 GPU 渲染成为明确需求，API 模型和 CPU Render 无法满足 |

这些服务不预先创建，不写进“内测上线必买清单”。

## 8. 控制台启用顺序与检查清单

当前状态：

- [x] 火山资源项目 `aive-video-editor` 已创建。
- [x] Ark API Key 管理页已打开到该项目。
- [x] 项目级 Ark API Key 已创建并写入本地 `.env`。
- [x] Seed-ASR 2.0 已开通，独立 Key 已在语音控制台创建。
- [x] 正式 Role ModelCard 与 Pro/Lite/Embedding/Seed-ASR Adapter 已接入。
- [x] 独立 Seed-ASR Key 与 TOS IAM AK/SK 已写入本地 `.env`，未进入 Git。
- [x] TOS 已开通，并创建私有北京 Bucket `aive-video-editor-demo-7352987798276051244`。
- [ ] 尚未创建 AIDAP、OpenViking、veFaaS/Sandbox 的正式资源。

你检查并确认本文后，按下面顺序操作：

1. [x] 创建项目级 Ark API Key，由你在浏览器中复制并写入本地 Secret。
2. [x] 固定 Pro、Lite、Embedding Vision 的正式 Model ID/接入协议。
3. [x] 开通 Seed-ASR 2.0 录音文件识别并创建独立 Key。
4. [ ] 设置账户余额、月度预算与费用告警，所有资源归属 `aive-video-editor`。
5. [ ] 私有 TOS Bucket、StorageProvider 和 Seed-ASR 短时 URL 已接通；继续用完整项目验证真实素材、proxy、preview、export 和 Remotion Artifact 生命周期。
6. [ ] 创建 OpenViking 个人版库，先导入一至两个受控 EditorialSkill；关闭自动 Memory 写入。
7. [ ] 完成 Provider/TOS/Skill Adapter 后，用本地 SQLite 跑真实 Vlog + Style Reference + Skill Golden Run。
8. [ ] 创建 AIDAP Serverless PostgreSQL，执行 migration 和 PostgreSQL 集成测试。
9. [ ] 创建 veFaaS Web/异步任务和云 Sandbox，部署 API、Media、Render 与 Candidate 验证入口。
10. [ ] 从全新浏览器完成上传到 HQ Export 的冷缓存、热缓存和失败恢复验收。

每一个会创建长期凭据、持续计费实例或不可逆配置的控制台动作，都需要操作当时的明确确认。开通后也不能以“控制台调用成功”代替 Golden Run 和用户可见链路验收。
