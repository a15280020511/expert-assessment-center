# V5 全动态专家执行图：实现与验收状态

## 当前结论

V5 已具备独立的完整技术链：

```text
任务语义编译
→ 多任务解释候选
→ 原子工作依赖 DAG
→ 资源／提示词／思考需求矩阵
→ model × 明确 Provider Endpoint 市场编译
→ 候选节点与工作包生成
→ Pareto 剪枝
→ OR-Tools CP-SAT 性价比联合求解
→ ExecutionGraph 安全校验
→ NetworkX 分层并行执行
→ 动态质量门
→ 有限重试与不同模型 Endpoint 替换
→ 审计产物与真实盲评门
```

V5 目前仍通过独立入口 `v5_pipeline.py` 运行。V3 正式生产入口未切换、未删除。

## 最高优化原则

```text
满足全部硬约束
→ 最大化风险调整后的任务效用 ÷ 有效总调用成本
```

正式V3入口使用 `value_resource_plan_optimizer.py`，正式V5规划使用 `v5_value_optimizer.py`。旧的“先最大化质量，再在质量容差带内降成本”不再是正式执行目标。

专家数量、工作包、模型、Provider、提示词、思考策略、参数和拓扑均由任务需求、实时市场和整体性价比共同决定，不使用固定团队人数或固定席位。

## 已实现模块

- `task_semantic_compiler.py`：市场无关的多任务解释与原子工作编译；
- `atomic_work_graph.py`：原子工作 DAG、执行层、入口、终点和关键路径；
- `resource_matrix.py`：能力、硬约束、提示词和思考需求矩阵；
- `v5_planner.py`：真实 Endpoint 市场、职业能力画像、节点功能、提示词、思考和参数候选及 Pareto 剪枝；
- `v5_value_optimizer.py`：以综合性价比为最高原则的 V5 CP-SAT 执行图求解；
- `execution_graph.py`：最终节点、边和执行图契约；
- `execution_graph_validator.py`：DAG、覆盖、预算、独立性、无工具、无 Router 和资源上限校验；
- `v5_executor.py`：按 DAG 分层并行执行、质量门、重试、替换、停止和请求审计；
- `v5_live_benchmark.py`：保留五任务、六策略、匿名多裁判的完整诊断基准；
- `v5_live_benchmark_final.py`：保留完整高置信盲评的资金预检与正式V3/V5对齐；
- `v5_live_benchmark_economy.py`：默认生产切换验收，只运行三任务、V5对V3和两名独立匿名裁判；
- `v5_pipeline.py`：独立端到端入口和完整 Artifact 输出。

## 固定安全边界

- 专家节点禁止工具、插件、网页、文件、代码执行、浏览器、外部 API 和其他模型；
- 禁止 Auto Router、online、batch 和隐式 Provider fallback；
- 每个节点绑定明确的 `model_id × provider_endpoint`；
- 独立副本不得复用同一模型或同一 Endpoint，且不得互相传递结果；
- 总节点、边、阶段、调用、重试、替换和预算受硬上限约束；
- 质量门或恢复失败时停止，不发布伪成功结果；
- 生产入口不发送人为输出Token上限；盲评专用上限只代表最大许可，不代表强制输出长度；
- V3不得在切换验收中删除，且盲评通过前继续承担生产任务。

## 低成本渐进式真实盲评

默认切换验收不再一次性运行五任务、六策略和固定双裁判。现在只购买会影响“V5是否替代V3”这一决策的证据：

```text
3个相互独立任务
×
V5 与 V3 两种生产候选
+
每题2名独立匿名裁判
```

默认三个任务覆盖：

1. 零售扩张、单位经济与现金流；
2. 软件任务执行器安全与可靠性；
3. 公共政策传播、事实完整性与伦理边界。

裁判保持独立性：

- 每个任务至少使用两名不同模型、不同Provider的匿名裁判；
- 两名裁判对任一候选分歧超过15分时，才增加第三名裁判；
- 少于两名有效独立裁判时不能授权切换。

以下高费用参考策略默认不执行：

```text
最强单模型
最低价单模型
固定3+1
随机可行组合
```

这些策略仍保留在完整诊断基准中，仅在结果争议、回归调查或用户明确要求高置信复核时运行。

## 费用和调用硬边界

默认经济型验收配置：

```text
max_cost_usd = 1.5
hard_max_cost_usd = 2.0
max_calls = 45
hard_max_calls = 60
max_strategy_cost_usd = 0.25
output_allowance_tokens = 1800
```

与原完整基准的20美元、200次调用相比，默认费用上限下降92.5%，调用上限下降77.5%。

额外控制包括：

- 只使用满足价格上限和可靠性门槛的候选 Endpoint；
- V5最多8个模型调用、无同模型重试、最多1次替换；
- V3固定3专家加1裁判，取消额外替换调用；
- 第一条HTTP 402立即终止；
- 有限API Key的剩余额度低于1.5美元时，在0次模型调用前拒绝；
- 未配置管理Key时允许在2美元硬上限内运行，不再要求为低成本测试额外配置第二把凭据。

## 历史完整盲评尝试

原五任务、六策略完整盲评曾通过 Issue `#39` 发起：

```text
Issue: #39
Run: 30531556998
Artifact: 8754890022
状态: budget_or_call_limit_exceeded
任务完成: 0 / 5
预留调用: 9
实际费用: 0 USD
生产入口切换: false
V3删除: false
```

Artifact 证明实际阻断不是代码编译、求解器或200次调用上限，而是 OpenRouter 账户余额不足：模型请求允许最多10,000完成Token时，OpenRouter返回 HTTP 402，并指出不同模型当时仅能负担约430、716或2,867 Token。

该失败记录保留作为完整基准证据，但不再要求先准备20美元才能进行生产切换验收。

## 审计产物

V5 每次规划或执行至少生成：

- `task-interpretations.json`
- `atomic-work-graph.json`
- `task-resource-matrix.json`
- `v5-model-endpoint-market.json`
- `v5-candidate-graph.json`
- `v5-optimization.json`
- `v5-execution-graph.json`
- `v5-planning-benchmark.json`
- `v5-dry-run.json` 或真实节点结果、请求审计和最终报告
- `artifact-manifest.json`

真实盲评额外生成：

- `credit-preflight.json`
- `v5-live-benchmark-results.json`
- `v5-live-benchmark-summary.md`
- 每个任务、V5、V3和匿名裁判的独立Artifact证据。

## 生产切换纪律

确定性规划测试只能证明结构可行、约束满足和求解正确，不能证明真实回答质量。默认生产切换必须由经济型真实盲评门决定，至少要求：

- 三个相互独立且跨领域的真实任务；
- V5与V3覆盖全部任务；
- V5三个任务全部成功，且成功率不低于V3；
- V5至少赢得三个任务中的两个；
- V5无安全失败和盲评致命错误；
- V5平均盲评质量至少高于V3 2%；
- V5费用回退不超过25%或每题0.02美元；
- 每条结果至少取得两名不同模型、不同Provider的有效匿名裁判；
- 裁判分歧超过15分时必须取得第三名裁判。

未满足上述条件时：

```text
production_cutover_allowed = false
生产入口切换 = 禁止
V3删除 = 禁止
V3继续生产 = true
```

即使经济型门通过，也只授权后续受审计的入口切换提交；盲评工作流本身不得修改生产入口或删除V3。
