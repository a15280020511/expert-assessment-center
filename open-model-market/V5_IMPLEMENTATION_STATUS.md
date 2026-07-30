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
→ 多样性保留 Pareto 剪枝
→ OR-Tools CP-SAT 性价比联合求解
→ ExecutionGraph 安全校验
→ NetworkX 分层并行执行
→ 动态质量门
→ 审计产物与最终报告
```

V5 真实运营链路已经通过一次严格受限的微型 Canary：8个节点全部成功、8次真实模型调用、实际费用 `$0.00141417`、请求审计 `PASS`，并生成450字符最终综合结果。

但必须区分：

```text
V5真实执行链已跑通 = 是
V5已证明优于V3 = 否
production_cutover_allowed = false
V3正式生产入口切换 = 禁止
V3删除 = 禁止
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
- `v5_planner.py`：真实 Endpoint 市场、职业能力画像、节点功能、提示词、思考和参数候选；
- `v5_capability_calibration.py`：在不降低0.48硬证据底线、不修改任务需求、不抬高模型能力分数的前提下，校准稀疏模型目录证据；
- `v5_candidate_diversity.py`：保留满足独立副本所需的不同模型候选；
- `v5_value_optimizer.py`：以综合性价比为最高原则的 V5 CP-SAT 执行图求解；
- `execution_graph.py`：最终节点、边和执行图契约；
- `execution_graph_validator.py`：DAG、覆盖、预算、独立性、无工具、无 Router 和资源上限校验；
- `v5_output_contract_delivery.py`：把输出契约转换为可执行交付指令，拒绝契约元数据回声；
- `v5_executor.py`：按 DAG 分层并行执行、质量门、重试、替换、停止和请求审计；
- `v5_economy_zero_call_diagnostic.py`：零模型调用的结构、节点、Endpoint和预算可行性诊断；
- `v5_live_benchmark_economy_verified.py`：将真实经济型盲评与零调用Endpoint证据对齐；
- `v5_micro_canary.py`：一美分以内的V5-only真实运营链路验证；
- `v5_live_benchmark.py`：保留五任务、六策略、匿名多裁判的完整诊断基准；
- `v5_live_benchmark_final.py`：保留完整高置信盲评的资金预检与正式V3/V5对齐；
- `v5_pipeline.py`：独立端到端入口和完整 Artifact 输出。

## 固定安全边界

- 专家节点禁止工具、插件、网页、文件、代码执行、浏览器、外部 API 和其他模型；
- 禁止 Auto Router、online、batch 和隐式 Provider fallback；
- 每个节点绑定明确的 `model_id × provider_endpoint`；
- 独立副本不得复用同一模型或同一 Endpoint，且不得互相传递结果；
- 总节点、边、阶段、调用、重试、替换和预算受硬上限约束；
- 质量门或恢复失败时停止，不发布伪成功结果；
- 生产入口不发送人为输出Token上限；测试专用上限只代表最大许可，不代表强制输出长度；
- V3不得在切换验收中删除，且盲评通过前继续承担生产任务。

## 零调用可行性门

零调用诊断 Run `30536650572`、Artifact `8756936350` 在0次模型推理、0美元费用下证明三个跨领域任务均存在经济型V5可行图：

| 任务 | 推荐节点 | 估算V5成本 |
|---|---:|---:|
| 零售扩张与单位经济 | 9 | `$0.177379` |
| 软件执行器安全 | 6 | `$0.169683` |
| 公共政策传播 | 10 | `$0.162489` |

该诊断促成以下真实入口对齐：

- V5单题最多10个节点；
- 价格门应用于具体Provider Endpoint，而不是模型目录汇总价格；
- Endpoint上限为prompt `$5/M`、completion `$15/M`、可靠性至少0.80；
- 重试0、替换0；
- 三题V5/V3经济型盲评调用上限46；
- 全局实际费用上限仍为1.5美元，硬上限仍为2美元。

## 低成本V5/V3盲评门

默认切换验收只购买会影响“V5是否替代V3”这一决策的证据：

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

当前经济型盲评配置：

```text
max_cost_usd = 1.5
hard_max_cost_usd = 2.0
max_calls = 46
hard_max_calls = 60
max_strategy_cost_usd = 0.25
output_allowance_tokens = 1800
V5 max nodes per task = 10
V5 retries = 0
V5 replacements = 0
```

完整三题V5/V3盲评尚未完成。OpenRouter当时可用余额不足以支撑高价Endpoint的1,800 Token请求，系统在 Run `30537800508` 中于5次调用、实际费用 `$0.0071075` 时自动停止，未接近1.5美元费用上限。

## 最终微型Canary通过

为避免继续使用三题盲评试错，系统改用一题、V5-only、0.01美元硬上限的运营Canary。

最终权威证据：

```text
Issue: #39
Run: 30539886693
Artifact: 8758262394
Artifact digest: sha256:7bbb2799dd383de3f5e0dd8b5bf16b568f572143f785e1f461452a6c2bda4cb9
Main commit: 0f4ee2ddb66504731c82418a04f3601d164180e8
Status: passed
```

真实结果：

```text
任务: software-job-runner-security
选中节点: 8
成功节点: 8 / 8
真实模型调用: 8 / 8
估算图成本: 0.00388773 USD
实际费用: 0.00141417 USD
最终答案长度: 450 characters
请求审计: PASS
重试: 0
替换: 0
V3执行: false
```

实际模型与明确Provider Endpoint：

- `deepseek/deepseek-v4-flash@deepinfra/fp4`；
- `z-ai/glm-5.2@decart/fp4`。

请求固定明确Provider，`allow_fallbacks=false`；未启用工具、网页、搜索、插件、文件、代码执行或其他模型调用。可选推理被关闭，输出上限600 Token；Canary专用精简JSON模式只在Canary环境变量开启，正式V5默认输出不受该长度限制。

该Canary证明以下链路真实可用：

```text
实时模型目录
→ 真实Provider Endpoint市场
→ 任务资源矩阵
→ 候选节点
→ OR-Tools性价比求解
→ 8节点V5 DAG
→ 真实模型执行
→ 质量门
→ 最终综合结果
→ Artifact与请求审计
```

该Canary不能证明V5优于V3，也不能授权生产切换。

## 审计产物

V5 每次规划或执行至少生成：

- `task-interpretations.json`；
- `atomic-work-graph.json`；
- `task-resource-matrix.json`；
- `v5-model-endpoint-market.json`；
- `v5-candidate-graph.json`；
- `v5-optimization.json`；
- `v5-execution-graph.json`；
- `v5-planning-benchmark.json`；
- `v5-dry-run.json` 或真实节点结果、请求审计和最终报告；
- `artifact-manifest.json`。

微型Canary额外生成：

- `v5-micro-canary-config.json`；
- `v5-micro-canary-result.json`；
- `v5-micro-canary-summary.md`；
- `v5-micro-canary-reasoning-policy.json`；
- `v5-node-results.json`；
- `v5-request-audit.json`；
- `v5-final-report.md`。

## 生产切换纪律

确定性规划测试与微型Canary只能证明结构可行、约束满足、求解正确和真实执行链可运行，不能证明V5优于V3。

生产切换仍至少要求：

- 三个相互独立且跨领域的真实任务；
- V5与V3覆盖全部任务；
- V5三个任务全部成功，且成功率不低于V3；
- V5至少赢得三个任务中的两个；
- V5无安全失败和盲评致命错误；
- V5平均盲评质量至少高于V3 2%；
- V5费用回退不超过25%或每题0.02美元；
- 每条结果至少取得两名不同模型、不同Provider的有效匿名裁判；
- 裁判分歧超过15分时必须取得第三名裁判。

当前状态：

```text
V5运营链路 = 通过
V5优于V3 = 尚未证明
production_cutover_allowed = false
生产入口切换 = 禁止
V3删除 = 禁止
V3继续生产 = true
付费测试Issue = 已关闭
自动付费重试 = 停止
```

即使未来经济型门通过，也只授权后续受审计的入口切换提交；盲评工作流本身不得修改生产入口或删除V3。
