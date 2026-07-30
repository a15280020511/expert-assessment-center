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
- `v5_live_benchmark.py`：五任务、六策略、匿名多裁判真实比较与切换门；
- `v5_live_benchmark_final.py`：将真实盲评绑定到当前正式V3/V5性价比优化器，并执行资金预检；
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

## 五任务真实盲评状态

最终盲评已通过 Issue `#39` 发起，使用以下固定任务：

1. 城市公共投资组合；
2. 零售扩张与现金流；
3. 软件任务执行器安全审计；
4. 双供应商连续中断风险；
5. 公共卫生谣言响应。

每个任务比较：

```text
V5
V3
最强单模型
最低价单模型
固定3+1
随机可行组合
```

### 已完成的真实尝试

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

Artifact 已证明实际阻断不是代码编译、求解器或200次调用上限，而是 OpenRouter 账户余额不足：模型请求允许最多10,000完成Token时，OpenRouter返回 HTTP 402，并指出当前不同模型仅能负担约430、716或2,867 Token。

因此五任务真实盲评仍未完成，`production_cutover_allowed` 必须保持 `false`。

## 资金预检纪律

完整五任务盲评在任何模型推理前必须同时满足：

- `OPENROUTER_API_KEY` 已配置；
- `OPENROUTER_MANAGEMENT_KEY` 已配置，用于只读查询账户总额度和累计使用；
- 经管理Key验证的剩余额度不低于本次 `max_cost_usd`；
- 若API Key设置有限消费上限，其剩余额度也不得低于 `max_cost_usd`；
- 运行时仍由全局实际费用和调用账本限制，并对HTTP 402立即停止。

当前最终基准配置的全局上限为：

```text
max_cost_usd = 20
max_calls = 200
max_strategy_cost_usd = 4
```

未获得可验证的20美元剩余额度前，不再发起付费重试，避免重复失败和无效调用。

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
- 每个任务、策略和匿名裁判的独立Artifact证据。

## 生产切换纪律

确定性规划测试只能证明结构可行、约束满足和求解正确，不能证明真实回答质量。生产切换必须由 `live_cutover_gate` 基于真实盲评记录决定，至少要求：

- 五个相互独立任务；
- 六种策略覆盖全部任务；
- 每条结果至少有两个不同模型、不同Provider的有效匿名裁判；
- V5无安全失败和盲评致命错误；
- V5成功率至少80%，且不低于V3；
- V5平均盲评质量至少高于V3 2%；
- V5费用回退不超过策略上限；
- 裁判分歧满足规定阈值。

未满足上述条件时：

```text
production_cutover_allowed = false
生产入口切换 = 禁止
V3删除 = 禁止
V3继续生产 = true
```
