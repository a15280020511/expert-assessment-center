# V5 全动态专家执行图：实施基线

> 状态：V5 基础层和阶段 A 任务资源编译层已实现。V3 生产入口保持不变，V5 未经完整基准验收不得替换生产。

## 1. 最终目标

V5 不再输出固定专家名单，而是输出：

```text
SelectedNode + SelectedEdge + ExecutionGraph
```

系统在固定安全宪法下，将任务解释、任务拆解、职业能力、节点功能、红队、提示词、思考模式、参数、模型、Provider、专家数量、信息可见性、执行拓扑、综合方式和恢复策略全部作为动态变量。

优化顺序固定为：

```text
硬约束成立
→ 最大化鲁棒整体性能 Q*
→ 动态计算质量容差 δ
→ 保留 Q >= Q* × (1 - δ)
→ 质量带内最小化完整总成本、故障面和结构复杂度
```

禁止重新引入“先最小化专家人数”“固定3+1”“固定红队”“固定裁判”或逐席位贪心选模。

## 2. 固定安全宪法

以下规则不参与优化：

- 专家模型请求禁止网页、工具、插件、文件、代码执行、数据库、外部API和其他模型；
- 禁止 OpenRouter Auto Router、online、batch 和隐藏路由；
- 禁止虚构证据、隐藏调用、隐藏替换和隐藏 fallback；
- 执行图必须为 DAG，禁止循环和无上限自我修订；
- 节点、边、调用、层级、预算、重试、替换和求解时间必须有硬上限；
- 模型与 Provider 不支持的参数不得发送；
- 独立复核节点不得读取彼此结果，必要时不得复用模型、模型家族或 Provider；
- 所有选择、过滤、剪枝、求解、费用、Token、重试和节点输出必须可审计。

## 3. 已完成

### 3.1 执行图数据契约

`execution_graph.py` 已提供：

- `SelectedNode`
- `SelectedEdge`
- `ExecutionGraph`
- `GraphLimits`
- `ValidationIssue`

### 3.2 执行图确定性校验

`execution_graph_validator.py` 当前校验：

- 节点和边唯一性；
- DAG 与执行层级；
- 入度入口和出度终点；
- 原子工作覆盖；
- 预算、节点、边、调用和层级上限；
- 节点成本与图总费用对账；
- 专家请求中禁止工具字段；
- 禁止 Router、online、batch 模型；
- 独立节点不得交换结果或复用同一模型；
- 质量、不确定性、失败概率和费用数值范围。

### 3.3 阶段 A：任务资源编译

新增独立于 V3 席位选择器的模块：

- `task_semantic_compiler.py`
- `atomic_work_graph.py`
- `resource_matrix.py`
- `task_resource_artifacts.py`

当前能力：

- 在读取任何模型、Provider、价格和 Benchmark 前生成最多三种任务解释；
- 支持按认知操作、按专业领域和混合方式拆解；
- 为每个原子工作计算重要性、错误代价、可验证性、领域和认知操作需求；
- 计算提示词、思考、上下文、输出和独立性需求；
- 建立并验证原子工作依赖 DAG、执行层、根节点、终点和关键路径；
- 使用 NumPy 生成能力需求、硬约束、评分置信度、提示词需求和思考需求矩阵；
- 动态增加 `domain:<name>` 能力维度，不通过固定职业表限制任务领域；
- 输出 `task-interpretations.json`、`atomic-work-graph.json`、`task-resource-matrix.json` 和 SHA 清单。

阶段 A 的输出明确记录：

```text
model_ids_read = false
provider_endpoints_read = false
prices_read = false
benchmarks_read = false
fixed_professions_used = false
fixed_seats_used = false
fixed_team_topology_used = false
```

### 3.4 测试

- 执行图单元测试覆盖正常图、环、工具字段、独立性、预算、工作覆盖和拓扑分层；
- Hypothesis 性质测试自动生成 DAG，并验证任何反向闭环都被识别；
- 阶段 A 新增多解释、DAG、矩阵维度、独立副本、结果可复现、审计产物和闭环拒绝测试。

## 4. 尚未完成

以下内容仍属于 V5 后续实现，不得声称已完成：

1. 职业能力画像、节点功能和提示词候选生成器；
2. 思考策略与真实模型参数兼容映射；
3. `model_id × provider_endpoint` 实时市场编译；
4. 候选专家节点、候选关系边和信息可见性生成；
5. Pareto 候选剪枝；
6. OR-Tools 对解释、节点、工作、模型、Provider、参数和拓扑的联合求解；
7. NetworkX DAG 执行器；
8. 动态质量门、恢复、升级和停止；
9. 完整 V5 请求审计、节点结果、恢复和最终 Artifact Manifest；
10. 与最强单模型、最低价可行模型、固定3+1、V3和随机可行组合的正式基准对照。

## 5. 迁移纪律

- V3 保留在现有生产链；
- V5 新模块不得通过 Monkey Patch 或运行时字符串替换伪装成动态图；
- 每一阶段必须先通过单元测试、性质测试和可审计 dry-run；
- 只有代表性基准证明 V5 在质量、成本、稳定性和结构复杂度上稳定优于 V3，才允许切换生产入口；
- 无可行解时必须报告冲突约束，禁止自动放宽安全要求。

本文件与 Issue #18 共同作为 V5 开发和验收的约束基线。
