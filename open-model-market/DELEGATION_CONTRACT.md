# Web GPT → GitHub V5 动态专家图委托契约

用户明确要求“交给 GitHub 专家中心分析”“不许网页 GPT 自己分析”或同等含义时，执行本契约。

## 角色边界

- 用户：确定问题、约束、批准的总调用额度、恢复预留、质量偏好和可使用证据；
- 网页 GPT：忠实整理任务，创建正式票据，监控状态，取回并转述 GitHub 产物；
- GitHub 确定性政策引擎：校验权限、Schema、唯一性、批准预算和安全硬门槛；
- V5 任务资源编译器：把任务拆成原子工作、能力、职业、提示词、推理、上下文、输出和复核需求；
- V5 CP-SAT 优化器：从实时多通道模型与 Provider 候选池中计算性价比最高的可行执行图；
- 动态执行节点：严格隔离地完成主研、验证、反证、实现或综合，不允许使用外部工具；
- GitHub 审计链：记录全部调用、Token、Provider、费用、错误、报告 SHA、主 Artifact 和最终证明 Artifact。

## 网页 GPT 禁止事项

GitHub 报告产生前，网页 GPT 不得：

- 自行回答实质问题；
- 混入自己的战略、商业或政策结论；
- 指定具体模型 ID 或 Provider；
- 把排队、票据接收或 Workflow 启动说成专家已经完成；
- 在 GitHub 失败时用自身分析替代结果；
- 新建重复 Issue 绕过去重和受控重试；
- 静默修改用户批准的调用或费用政策。

## 网页 GPT 必须执行

1. 按 `execution-ticket.schema.json` 生成合法票据；
2. 用户未指定质量档时使用 `quality_tier=value`；
3. 明确填写付费调用总硬上限和总额内恢复预留；
4. 只在原 Issue 进行受控重试；
5. 区分 `ACCEPTED`、`COMPLETED`、`DEGRADED` 和 `FAILED`；
6. 只有 `EXECUTION_COMPLETED`、完整报告、主 Artifact 和最终证明 Artifact 都成立时，才能称为正常 PASS；
7. 转述时明确内容来自 GitHub V5 动态专家图；
8. 失败时报告直接根因、调用证据和 Artifact 状态。

## V5 原生预算契约

示例：

```json
{
  "approved_budget": {
    "calls": 6,
    "maximum_recovery_calls": 2,
    "cost_policy": "unbounded_with_anomaly_guard",
    "cost_anomaly_usd": 1.5
  }
}
```

语义：

```text
初始调用 + 重试调用 + 替换调用 <= approved_budget.calls
```

- `calls`：所有付费模型请求合计硬上限，范围 4—16；
- `maximum_recovery_calls`：包含在 `calls` 之内，不是额外额度，范围 0—4；
- `cost_policy`：当前只允许 `unbounded_with_anomaly_guard`；
- `cost_anomaly_usd`：可选的任务级异常停止阈值，不是消费目标；
- 运行时不得把用户批准的 6 次静默改成 16 次；
- 规划器必须先从总额中预留恢复调用，再确定最大初始节点数；
- 已经成功的节点不得因其他节点失败而重复调用；
- 不得无限重试。

## 性价比与节点数量

最高原则是性价比最高，而不是默认使用最多节点。

- `budget`：普通情况下最多规划 4 个初始节点；
- `value`：普通情况下最多规划 6 个初始节点；
- `quality`：可在批准的初始容量内扩展；
- 新增节点的预期质量收益必须覆盖费用、失败概率、恢复成本和延迟成本；
- 简单自包含任务不得默认扩张到 14—16 个节点；
- 多通道候选池应合并智能排名、任务匹配、性价比、低价合格和上下文适配候选，再交给优化器，不得只按单一榜单提前截断。

## Token 政策

生产请求不发送人为：

```text
max_tokens
max_completion_tokens
reasoning.max_tokens
```

配置中的 10,000 Token 是允许上限和能力门槛，不是强制输出目标，也不得在节点请求中被静默压成更小的人为硬上限。系统通过任务驱动的提示词、推理强度、上下文裁剪和质量门控制成本与输出长度。

## 费用政策

- 不设置统一固定美元消费目标；
- 可设置任务级费用异常停止阈值；
- 价格、失败概率和预期恢复成本共同参与选模；
- 估算费用与实际费用偏差必须记录；
- 所有成功、失败和替换调用都必须记录实际 Provider、Token 和费用；
- 实际费用超过票据异常阈值时，确定性审计必须失败；
- 旧 `max_cost_usd` 字段已删除，不能继续接受后忽略。

## 并发与幂等

- 生产入场使用串行零调用 admission lock；
- 正式付费执行使用仓库级 `expert-production-global` 原子并发组；
- 同时触发多个任务时，只允许最早的任务进入付费执行；
- 其余任务必须明确发布 `EXECUTION_BUSY`，模型调用为 0；
- 不得静默排队多个付费任务；
- 任务指纹、Issue 状态和唯一 retry ID 继续共同防止重复执行。

## 状态与证据链

- `queued` / `in_progress`：仅表示 GitHub Actions 状态；
- `EXECUTION_REJECTED`：未进入模型调用；
- `EXECUTION_ACCEPTED` / `EXECUTION_RETRY_ACCEPTED`：票据已通过校验；
- `EXECUTION_COMPLETED`：执行审计 PASS、完整报告、主 Artifact 和最终证明 Artifact 成立；
- `EXECUTION_DEGRADED`：已交付，但发生受控降级；
- `EXECUTION_FAILED`：没有形成可接受交付。

证据采用两阶段结构：

```text
主 Artifact
→ 获得 Artifact ID 与 digest
→ 生成 final-status.md
→ 生成 final-attestation.json
→ 上传最终证明 Artifact
```

`final-attestation.json` 至少绑定：Run ID、提交 SHA、主 Artifact ID、主 Artifact digest、审计状态、报告 SHA、Manifest SHA 和最终状态 SHA。

不得把 `EXECUTION_DEGRADED` 表述成完整成功，也不得在最终证明 Artifact 上传失败时把 Job 判为成功。

## 日志要求

每次运行应保留：

- 票据、批准预算和任务指纹；
- 任务资源矩阵、候选池、优化结果和次优比较；
- 全部动态节点每次尝试；
- 原始清洗响应和诊断；
- 输入、输出和推理 Token；
- Provider 实际费用、估算费用和偏差；
- 标准化错误代码；
- 统一 `execution-diagnosis.json`；
- 报告评论清单和 SHA-256；
- 主 Artifact Manifest；
- `final-attestation.json` 和最终证明 Artifact。

## 受控重试

`EXECUTION_REJECTED`、`EXECUTION_FAILED` 或 `EXECUTION_DEGRADED` 后，在原 Issue 评论：

```text
/retry-expert-team <unique_retry_id>
```

`retry_id` 不得复用。不得新建同语义 Issue 绕过去重。
