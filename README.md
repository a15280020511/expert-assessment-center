# Expert Assessment Center

本仓库是正式独立的专家研判中心。GPTs 是本中心与其他业务中心之间唯一的控制与证据中继。

## 最高原则：性价比最高

在满足全部硬约束的可行方案中，选择风险调整后任务效用与有效成本之比最高的方案。

```text
风险调整后的任务效用
÷
（预计模型费用 + 调用开销）
```

任务覆盖、能力、上下文、输出、独立性、安全和工具禁用属于硬约束。任何低价方案都不能突破硬约束。

## 当前唯一生产运行时

```text
[execution] Issue
→ V5票据授权与去重
→ 任务语义编译
→ 原子工作图
→ 任务资源矩阵
→ OpenRouter实时模型与Provider目录
→ 候选节点矩阵
→ Google OR-Tools CP-SAT整体性价比求解
→ V5动态专家DAG
→ 动态综合节点
→ 报告发布、请求审计、费用账本、SHA与Artifact
```

当前代码树只保留 V5 R8 生产运行时。旧固定运行时、旧工作流、旧比较入口、旧测试入口和人工回滚路径均已删除。

系统失败时直接失败关闭，不调用其他运行时。

## 动态决策范围

V5根据任务动态计算：

- 原子工作和工作包组合；
- 节点数量、职业和职责；
- 独立复核、红队反证和综合节点；
- 模型、Provider及其明确锁定关系；
- 提示词模块；
- reasoning、temperature、verbosity和结构化输出；
- 上下文需求和输出许可；
- 有限重试与替换；
- 整体费用、质量效用和性价比。

10000 Token 仅表示允许的最高输出额度，不是固定请求值，也不是强制输出长度。每个节点的实际额度由任务需求、模型能力和费用动态计算。

## 生产硬边界

- 最多16个模型调用；
- 最多16个图节点；
- 最多8个执行阶段；
- 全局有限重试和替换；
- 专家禁止使用工具、网页、插件、文件搜索、代码执行或其他模型；
- 禁止隐式Provider fallback；
- 不使用OpenRouter Auto Router、Fusion或Agent黑箱路由；
- 没有备用运行时回退；
- 运行失败必须明确失败并保留诊断证据。

## 正式入口

- `.github/workflows/execution-ticket.yml`
- `open-model-market/v5_issue_ticket.py`
- `open-model-market/v5_production_ticket.py`
- `open-model-market/v5_pipeline.py`
- `open-model-market/v5_execution_auditor.py`
- `open-model-market/v5_final_status.py`

## 核心实现

- `open-model-market/task_semantic_compiler.py`
- `open-model-market/atomic_work_graph.py`
- `open-model-market/resource_matrix.py`
- `open-model-market/v5_planner.py`
- `open-model-market/v5_value_optimizer.py`
- `open-model-market/v5_executor.py`
- `open-model-market/v5_production_hardening.py`
- `open-model-market/v5_benchmark.py`

## 主要审计产物

每次正式任务至少生成：

- `task-interpretations.json`
- `atomic-work-graph.json`
- `task-resource-matrix.json`
- `v5-model-endpoint-market.json`
- `v5-candidate-graph.json`
- `v5-optimization.json`
- `v5-execution-graph.json`
- `v5-node-results.json`
- `v5-request-audit.json`
- `v5-execution-summary.json`
- `expert-team-report.md`
- `call-ledger.json`
- `execution-audit.json`
- `artifact-manifest.json`

## 隔离边界

- 本仓库只运行专家研判中心任务；
- 禁止中心间直接调用、运行时导入、Artifact互取和共享业务Secret；
- OpenRouter只提供模型目录、Benchmark信息和直接模型调用，不负责路由与选模；
- 生产选模和组团由仓库内可审计的确定性代码完成。
