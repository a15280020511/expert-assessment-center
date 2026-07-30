# V5 真实盲评基准与生产切换纪律

## 目的

确定性规划测试只能证明：

- 任务能够被拆解；
- 硬约束能够被满足；
- CP-SAT 能够找到可行图；
- 请求中没有工具、Router、在线模型或隐式 Provider 回退。

它不能证明真实回答质量。因此 V5 替换 V3 前必须运行一次独立的真实多任务盲评。

## 入口

真实基准只通过仓库所有者创建以下 Issue 触发：

```text
[v5-benchmark] V5 live blind cutover benchmark
```

Issue 正文是可选 JSON：

```json
{
  "benchmark_id": "v5-live-cutover-20260730",
  "max_cost_usd": 20,
  "max_calls": 200,
  "max_strategy_cost_usd": 4,
  "task_ids": [
    "municipal-investment-portfolio",
    "retail-expansion-unit-economics",
    "software-job-runner-security",
    "dual-source-supply-chain",
    "public-health-rumor-response"
  ]
}
```

只有仓库所有者创建、标题以 `[v5-benchmark]` 开头的 Issue 会运行。普通 `[execution]` 生产票据不受影响。

## 五个独立任务

固定测试集覆盖：

1. 城市公共投资组合；
2. 零售扩张与现金流；
3. 软件任务执行器安全审计；
4. 双供应商与连续中断风险；
5. 公共卫生谣言响应。

所有任务均提供完整输入数据，不要求专家联网，避免因“专家禁止工具”而产生不公平比较。

## 六种真实策略

每个任务运行：

- `v5_joint_graph`；
- `v3`；
- `strongest_single_model`；
- `lowest_price_single_model`；
- `fixed_3_plus_1`；
- `random_feasible`。

单模型和固定／随机基线同样绑定明确的模型与 Provider Endpoint，禁止 Router、online、batch、工具字段和隐式回退。

## 匿名裁判

每个任务的六份答案会被随机映射为匿名标签。裁判请求中不出现策略名称。

上线证据至少要求：

- 两名有效裁判；
- 两个不同模型；
- 两个不同 Provider；
- 每份答案的两名裁判分差不超过 35 分；
- V5 五个任务的平均裁判分差不超过 20 分。

两名裁判分歧超过 15 分时，系统尝试启用第三名裁判。

## 全局硬闸门

基准使用一个共享账本：

- 默认实际费用总上限：20 美元；
- 默认付费调用总上限：200 次；
- 默认单策略单任务规划费用上限：4 美元；
- 超限立即停止；
- 已完成的请求、费用和结果仍写入 Artifact；
- 不发送人为 `max_tokens` 或推理 Token 上限。

V5 运行时的 `max_retries` 和 `max_replacements` 也改为全图共享，而不是逐节点重复获得额度。

## 切换门

只有同时满足以下条件，`production_cutover_allowed` 才能为 `true`：

- 至少五个独立任务；
- 六种策略都覆盖全部任务；
- 每条记录均有合格的独立盲评；
- V5 安全失败为零；
- V5 盲评致命错误为零；
- V5 成功率至少 80%，且不低于 V3；
- V5 平均盲评质量至少高于 V3 2%；
- V5 平均费用不超过 V3 的 125%，且绝对回退不超过策略容差；
- V5 裁判分歧满足阈值。

基准工作流只生成决定和证据，不自动修改生产入口。只有在 Artifact 核验完成后，才能通过新的受审计 PR 切换入口。若门未通过，V3 继续生产，V5 根据阻断项迭代后重新测试。

## 主要产物

```text
v5-live-benchmark-results.json
v5-live-benchmark-summary.md
benchmark-config.json
benchmark-console.log
tasks/<task_id>/task-benchmark-result.json
tasks/<task_id>/blind-evaluation.json
tasks/<task_id>/<strategy>/...
artifact-manifest.json
```

Artifact 默认保留 90 天。
