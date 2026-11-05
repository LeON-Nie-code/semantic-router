---
is_mtpe: false
translation:
  source_commit: "9156d5bc1ed9edff626b95a2b8260a77cb1712c5"
  source_file: "docs/tutorials/plugin/shadow-dispatch.md"
  outdated: false
---

# 影子分发

## 概览

`shadow_dispatch` 对已批准的请求进行采样，将副本发送给一组候选模型并记录结果，主响应无需等待，也不会被候选结果改变。

## 主要优势

- 在灰度发布前，用同一批线上流量比较多个候选模型。
- 主响应不受候选模型的延迟、失败或输出影响。
- 通过调用次数、时间、字节数和并发限制控制后台工作量。
- 将候选结果关联到主请求的回放记录，便于后续比较。

## 解决什么问题？

离线评估可能无法覆盖真实的提示词分布。Shadow dispatch 让候选模型接收贴近生产的请求，同时仍由主模型回答用户。每个候选模型的结果都会追加到请求的回放记录中，供后续比较。

每份 shadow 副本都从请求插件执行后的已批准中性请求生成。路由器通过常规编码和提供商适配为候选模型构建请求，包括推理控制、提供商的 `chat_path` 和 Azure OpenAI 的 `api_version`。Shadow 调用始终使用非流式响应。

身份验证使用各候选模型配置的提供商凭据。HTTPS 严格校验 TLS 证书。请求中只传播 W3C `traceparent` 和 `tracestate` 请求头；客户端凭据、`baggage` 和决策请求头改写都不会转发。路由器不跟随重定向，因此提示词和凭据只会发送到配置的后端地址。

## 何时使用

- 在灰度发布前用线上流量评估候选模型。
- 让候选模型的后台工作独立于线上响应。
- 收集回放观测，用于对比数据集或外部评审模型。

该插件只记录结果，不为回答评分，也不影响线上响应。

## 配置

将该插件与 `router_replay` 一起添加到决策，以便结果有记录可附着。将候选名称替换为 `providers.models` 中配置的逻辑模型：

```yaml
plugins:
  - type: router_replay
    configuration:
      enabled: true
  - type: shadow_dispatch
    configuration:
      enabled: true
      models: [candidate-a, candidate-b]
      sample_rate: 0.05
      execution:
        max_concurrency: 2
        max_queue_size: 8
        timeout: 30s
      budget:
        max_calls: 2
        max_response_bytes: 2097152
      capture:
        enabled: false
        max_bytes: 8192
```

这是一次不兼容的配置更新：请用此结构替换旧的 shadow 配置。旧配置会在加载时被拒绝。

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `enabled` | 必需 | 设为 `true`，为该决策启用 shadow dispatch。 |
| `models` | 启用时必需 | 已配置逻辑模型的列表，最多 8 个。单个候选也使用列表：`[candidate-a]`。 |
| `sample_rate` | `1.0` | 要分发的合格请求组比例，范围为 `[0, 1]`。整个请求组只做一次采样决定。`0` 禁用分发。 |
| `execution.max_concurrency` | `2` | 同一 recipe 和 decision 下，跨请求共享的候选 HTTP 调用并发上限。 |
| `execution.max_queue_size` | `8` | 同一 recipe 和 decision 下，跨请求共享的待处理请求组上限。计数单位是组，而不是候选调用。 |
| `execution.timeout` | `30s` | 每组的截止时间，包括队列等待及全部候选调用。必须为正，且不超过 `5m`。 |
| `budget.max_calls` | 配置的模型数量 | 每组实际 HTTP 调用次数上限，必须足以覆盖全部配置模型。首个版本不重试。 |
| `budget.max_response_bytes` | 配置的模型数量 × `1048576` | 每组响应字节总预算，在分发前分配给有效候选。 |
| `capture.enabled` | `false` | 在回放结果中存储候选文本的有界摘录。 |
| `capture.max_bytes` | 配置的模型数量 × `4096` | 每组采集字节总上限，在有效候选间平均分配。 |

路由器对每个请求组只采样一次，并从有效候选中排除主模型。请求被采样排除，或组内没有剩余候选时，只产生指标，不产生 shadow 结果。通过 looper 执行的决策（ratings、confidence、fusion、ReMoM、workflows）会在配置加载时拒绝该插件；该 hook 只在单模型提供商分发上运行。

并发与队列容量由每个 recipe 和 decision 下的请求共享。截止时间和预算则分别作用于每个请求组。在上述双候选示例中，每个候选获得 1 MiB 响应预算和 4096 字节采集额度。额度在分发前固定；发生错误时仍保守地计入已分配额度，不会释放给其他候选。

准入限制覆盖调用次数、耗时、字节数和并发。返回的 token 用量和基于目录定价估算的费用只是观测数据，不是 token 或费用准入限制。缺失的用量表示未知，而不是零。

### 失败开放行为 {#fail-open-behavior}

请求路径做一次非阻塞的组准入检查后返回。其余工作在主分发响应构建完成后，于有界 worker 中运行。主路径不等待候选返回，缓慢或失败的候选不会改变主响应。每个候选调用都以一种结果结束：

| 结果 | 原因 |
| --- | --- |
| `completed` | `completed` |
| `failed` | `backend_unresolved`、`credential_unresolved`、`encode_failed`、`timeout`、`queue_timeout`、`canceled`、`router_closing`、`transport_error`、`upstream_status`、`redirect_rejected`、`response_too_large`、`malformed_response` |
| `dropped` | `queue_full`、`same_as_primary`、`internal_request`、`request_unavailable` |
| `sampled_out` | `sampled_out` |

结果和原因导出为 `sr_shadow_dispatch_total{decision,result,reason}`，以及 `sr_shadow_dispatch_latency_seconds`、`sr_shadow_dispatch_inflight` 和 `sr_shadow_dispatch_queued`。由资源边界导致的丢弃通过指标和结构化 `shadow_dispatch_dropped` 事件报告，而不是写入回放，因此过载的 shadow 通道不会放大回放存储负载。

### 解读失败 {#interpreting-failures}

`failed` 结果并不总是说明候选模型本身。请将原因与结果元数据中的 `status_code`、`attempts` 和截断的 `error` 一起阅读：

| 含义 | 原因 | 如何解读 |
| --- | --- | --- |
| 候选拒绝了请求 | 带 4xx `status_code` 的 `upstream_status` | Shadow 模型无法接受已批准请求，例如不支持的参数或上下文窗口太小。计入候选问题。 |
| 候选健康或容量 | 带 5xx `status_code` 的 `upstream_status`、`timeout`、`transport_error` | 后端不可达、过载，或无法在该组的 `execution.timeout` 内完成。这衡量部署，而不是回答质量。 |
| 候选尝试重定向 | 带 3xx `status_code` 的 `redirect_rejected` | 后端以重定向应答。路由器永不跟随，因此提示词和 shadow 凭据只到达配置的后端。请将 shadow 模型指向后端的最终地址。 |
| 候选输出问题 | `malformed_response`、`response_too_large` | 后端已应答，但正文对其线格式无效，或超过其分配到的 `budget.max_response_bytes` 额度。 |
| 路由器侧，与候选无关 | `backend_unresolved`、`credential_unresolved`、`encode_failed` | 路由器无法构建或寻址 shadow 调用。修复配置，并从任何候选比较中排除这些项。 |

`dropped` 和 `sampled_out` 永不进入回放记录。它们表示路由器选择不发送 shadow，因此不携带关于候选模型的信号，只在指标和日志中可见。

### 回放与审计采集 {#replay-and-audit-capture}

每个已完成或失败的候选调用会向主请求的回放记录追加一条结果，带有 `source: shadow_dispatch`、`target: model`、`target_ref: <shadow model>`、判定和原因。结果元数据携带主路径与 shadow 的请求身份、主路径与 shadow 的模型和后端、决策和配方、采样率、入队、开始和完成时间戳、队列等待和延迟、尝试次数、状态码、响应大小、停止原因、token 计数，以及 shadow 文本的 SHA-256。结果只追加不改写。

回放脱敏对 shadow 结果的应用方式与记录其余部分相同：没有内容权限的查看者可以看到路由和时序字段，但看不到 `target_ref`、`reason` 或 `metadata`。仅在需要保存响应文本时启用 `capture.enabled`。片段见：
[`config/fragments/plugin/shadow-dispatch/sampled.yaml`](https://github.com/vllm-project/semantic-router/blob/main/config/fragments/plugin/shadow-dispatch/sampled.yaml)。

### 导出对比数据集 {#export-a-comparison-dataset}

采集到的结果可通过 `GET /api/v1/observability/replays/dataset` 导出为对比数据集。该接口读取常规回放过滤条件选中的记录，返回一份带版本的清单，其中包含每条输入的主模型与影子模型分支。

清单以自身摘要作为标识，因此导出请求需要携带拆分方案：`seed` 固定拆分分配，每个可重复的 `split` 写作 `name:weight`，例如 `?seed=2026-q3&split=train:8&split=eval:2`。相同记录在相同 seed 与拆分下会重建出相同的清单，包括每条样本所属的拆分，因此后续新增的观测不会移动已经放置好的样本。

```bash
curl -H "Authorization: Bearer $ROUTER_MANAGEMENT_TOKEN" \
  "$ROUTER_MANAGEMENT_URL/api/v1/observability/replays/dataset?recipe=vault&seed=2026-q3&split=train:8&split=eval:2"
```

清单只携带标识、输出摘要与来源信息，不含提示词或响应文本，因此可以与它支撑的数据一同发布。一条观测要么整条进入，要么完全不进入：失败的请求、未结束的请求、输入被截断的请求，以及从未记录摘要的请求都会被排除并按原因计数，`counts` 会报告保留了什么、丢弃了什么。由于清单描述的是构建它的整个选择集，超过 5000 条记录的选择会被拒绝，而不是按页导出。请缩小过滤条件后重新导出。

某一个 recipe 或某一个 decision 通常会主导线上流量，基于它构建的数据集读起来像是关于整个路由器的结论，实际上只是关于那个 decision 的结论。`balance_by` 与 `balance_max` 限制单个分组最多能贡献多少条，分组方式为 `recipe`、`decision` 或 `primary_model`。上限保留哪些行由 seed 决定而非时间先后，因此均衡后的数据集是对流量的采样，而不是对到达时间的采样；因均衡而丢弃的行与其他排除一样计入 `balance_cap`。两个参数必须同时给出，只给其一会被拒绝。

导出需要 `replay.read` 权限，读取的记录与列表 API 相同。被比较的决策必须开启正文采集，否则未采集到请求的观测会以 `request_body_missing` 被排除。

### 交给评审模型

`GET /api/v1/observability/replays/dataset/judge-tasks` 接受与导出相同的选择条件与拆分方案，外加一个 `blinding_key`，返回成对的评审任务：一条主模型回答对一条影子模型回答，并附上两者收到的请求。每一对都会以交换左右位置的形式出现两次，因此偏好先出现答案的评审模型会表现为两种顺序之间的不一致。每一侧都带有不透明的分支标签，既不暴露模型名称，也不暴露哪一侧是主模型，且每一对中的标签都不相同。只有该密钥能把标签映射回模型，因此不要让评审模型接触到它。密钥不能与已公开的清单 seed 相同。

```bash
curl -H "Authorization: Bearer $ROUTER_MANAGEMENT_TOKEN" \
  "$ROUTER_MANAGEMENT_URL/api/v1/observability/replays/dataset/judge-tasks?recipe=vault&seed=2026-q3&split=train:8&split=eval:2&blinding_key=$JUDGE_KEY"
```

只有当请求和两段回答都能哈希回清单中的摘要时才会构建一对任务。存储的请求会写明路由器把它发往的模型，因此输入摘要覆盖去掉顶层模型字段（记录中的请求为 `Model`，客户端请求体为 `model`）后的请求体，这也是评审读到的 `input`，每个任务都带有这个 `input_digest`。这改变了输入标识、去重和拆分，因此清单版本为 `shadow-dataset.v2`，任务版本为 `shadow-judge-tasks.v2`。`shadow-dataset.v1` 清单会被拒绝。影子文本来自采集的摘录，因此影子决策需要开启 `capture.enabled`，并把 `capture.max_bytes` 设得足够大，使每个候选分到的额度都能容纳完整回答。被截断的摘录，或在存储前被响应阶段插件改写的主模型回答，都会被排除并计为 `arm_text_digest_mismatch`；完全没有文本的一对计为 `arm_text_missing`。与摘要不符的请求计为 `input_text_digest_mismatch`，缺失的请求计为 `input_text_missing`。提到自身模型名称的回答会被标记为 `names_own_model`，因为任何标签都无法对评审模型隐藏这一点。

任务携带提示词与回答文本，因此该接口需要 `replay.detail` 权限，对没有该权限的调用方直接拒绝，而不是返回脱敏后的任务。评审模型在路由器之外运行。

### 发布数据集与评审结果

`tools/calibration/shadow-dataset` 把清单，以及评审模型针对它返回的评审结果（可选），发布到一个存储目录中。任何挂载为文件系统的对象存储都可以作为该目录。该工具在写入任何内容之前会校验全部输入：清单必须与其自身摘要一致，任务必须正是该清单与盲化密钥根据任务所携带的文本生成的那一组，评审结果必须回答这些任务，且评审记录中不能出现未定义的字段。密钥只用于这项校验，永远不会被发布。

```bash
make run-shadow-dataset GO_TOOL_ARGS="--dest /mnt/shadow-datasets \
  --manifest $PWD/manifest.json --tasks $PWD/tasks.json --judgments $PWD/judgments.json \
  --blinding-key $JUDGE_KEY"
```

清单写入 `manifests/<digest>/<digest of the file>.json`，因为清单摘要不包含计数，同一批样本的两次导出可能排除了不同的记录，评审结果写入 `judgments/<manifest digest>/<digest of the set>.json`，旁边附带一份 `.report.json`，统计各类结果、各位置被选中的次数，以及有多少对任务跟随了位置而不是答案。所有名称都由内容决定，因此重复发布相同文件不会产生变化，已发布的名称也永远不会被替换为不同的内容。评审任务只用于校验评审结果，永远不会被发布，因为它们携带提示词与两个回答。不要把数据集提交到本仓库。
