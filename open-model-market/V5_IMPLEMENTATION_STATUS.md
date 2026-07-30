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
→ OR-Tools CP-SAT 联合求解
→ ExecutionGraph 安全校验
→ NetworkX 分层并行执行
→ 动态质量门
→ 有限重试与不同模型 Endpoint 替换
→ 审计产物与基准门
```

V5 目前仍通过独立入口 `v5_pipeline.py` 运行。V3 正式生产入口未切换、未删除。

## 已实现模块

- `task_semantic_compiler.py`：市场无关的多任务解释与原子工作编译；
- `atomic_work_graph.py`：原子工作 DAG、执行层、入口、终点和关键路径；
- `resource_matrix.py`：能力、硬约束、提示词和思考需求矩阵；
- `v5_planner.py`：真实 Endpoint 市场、职业能力画像、节点功能、提示词、思考和参数候选、Pareto 剪枝及 CP-SAT 联合图求解；
- `execution_graph.py`：最终节点、边和执行图契约；
- `execution_graph_validator.py`：DAG、覆盖、预算、独立性、无工具、无 Router 和资源上限校验；
- `v5_executor.py`：按 DAG 分层并行执行、质量门、重试、替换、停止和请求审计；
- `v5_benchmark.py`：最强单模型、最低价单模型、固定 3+1、V3 结构代理和随机组合的确定性规划对照，以及正式上线门；
- `v5_pipeline.py`：独立端到端入口和完整 Artifact 输出。

## 固定安全边界

- 专家节点禁止工具、插件、网页、文件、代码执行、浏览器、外部 API 和其他模型；
- 禁止 Auto Router、online、batch 和隐式 Provider fallback；
- 每个节点绑定明确的 `model_id × provider_endpoint`；
- 独立副本不得复用同一模型或同一 Endpoint，且不得互相传递结果；
- 总节点、边、阶段、调用、重试、替换和预算受硬上限约束；
- 质量门或恢复失败时停止，不发布伪成功结果；
- 不发送人为 `max_tokens` 或推理 Token 上限。

## 优化目标

CP-SAT 按以下顺序求解：

```text
满足全部硬约束和原子工作副本覆盖
→ 最大化任务质量、可靠性和解释质量
→ 在质量容差带内最小化费用、调用数和失败概率
```

专家人数、工作包、模型、Provider、提示词、思考策略、参数和拓扑均由任务和市场数据共同决定，不使用固定团队人数或固定席位。

## 审计产物

V5 每次规划至少生成：

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

## 生产切换纪律

确定性规划基准只能证明结构可行、约束满足和估算上的 Pareto 表现，不能证明真实回答质量。因此规划测试永远不能直接授权替换 V3。

生产切换必须由 `live_cutover_gate` 基于真实盲评记录决定，至少要求：

- 五个相互独立任务；
- 覆盖 V5、V3、最强单模型、最低价单模型、固定 3+1 和随机可行组合；
- V5 无安全失败；
- V5 成功率不低于 V3；
- V5 盲评质量至少高于 V3 2%；
- V5 费用回退不超过策略上限。

未满足上述条件时，`production_cutover_allowed` 必须保持 `false`，V3 继续作为生产入口。
