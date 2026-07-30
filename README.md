# Expert Assessment Center

本仓库是正式独立的专家研判中心。GPTs 是本中心与其他业务中心之间唯一的控制与证据中继。

## 当前选择架构

专家团选择核心已经统一为：

```text
任务正文与显式约束
→ 任务参数化与需求拆分
→ 任务—能力—席位计算矩阵
→ OpenRouter 实时模型目录与 Benchmark
→ Google OR-Tools CP-SAT 全局优化
→ 动态专家席位、模型与精确推理参数
→ GitHub Actions 执行、证据与审计
```

选择器不再使用：

- “简单／中等／复杂”对应固定人数的模式表；
- 固定 1+1、2+1、3+1、4+1 模板；
- 固定参数模板库作为决策输入；
- 本地历史模型绩效账本；
- 逐席位贪心选择；
- OpenRouter Router、在线模型变体或 Agent 路由。

## 动态组合原则

CP-SAT 同时决定：

- 需要激活多少名专家；
- 每个专家席位承担哪些任务需求；
- 每个席位使用哪个 OpenRouter 直接模型；
- 每个模型使用哪些其实际支持的推理参数；
- Provider 是否重复；
- 在满足全部硬需求后，如何兼顾质量和预计费用。

优化按词典序执行：

1. 在覆盖全部任务硬需求的前提下，最小化专家数量；
2. 在最小充分团队中最大化实时能力与任务匹配；
3. 在质量容差范围内最小化预计费用。

因此，专家数量是计算结果，不是预设模式。

## 任务输入

普通任务无需填写参数。系统会从任务正文生成：领域、任务操作、风险、证据要求、定量要求、预测要求、反证要求、实施要求、上下文需求和输出要求。

需要设置硬约束时，可在任务正文加入：

```text
<expert-team-input>
{
  "budget_usd": 0.8,
  "min_experts": 1,
  "max_experts": 4,
  "strict_provider_diversity": true,
  "candidate_pool_per_seat": 12,
  "solver_timeout_seconds": 8,
  "quality_tolerance_pct": 3,
  "forbidden_models": [],
  "preferred_models": []
}
</expert-team-input>
```

也可通过 `EXPERT_TEAM_INPUT_JSON` 传入同一结构。`preferred_models` 只能作为软偏好；能力、上下文、参数兼容、预算和任务覆盖等硬约束不能被偏好覆盖。

## 实时模型输入

选择器只使用本次任务时可取得的 OpenRouter 数据：

- 模型 ID、Provider、版本状态；
- 官方智能排序；
- Benchmark；
- 输入和输出价格；
- 上下文长度与最大输出；
- 支持参数；
- 输入与输出模态；
- reasoning 能力；
- 知识截止日期与失效日期。

不读取本地历史成功率，不因旧任务表现永久奖励或惩罚模型。

## 审计产物

每次选择至少生成：

- `task-parameter-matrix.json`：任务拆分、需求向量和约束；
- `team-optimization.json`：CP-SAT 状态、目标边界、候选、选定席位、模型、参数和费用；
- `model-selection.json`：运行时模型选择证据；
- `benchmark-market.json`：Benchmark 来源与降级状态；
- `artifact-manifest.json`：产物 SHA 与完整性清单。

不存在可行解时直接失败并报告冲突约束，不回退到旧选择器。

## 关键实现

- `open-model-market/task_matrix_optimizer.py`
- `open-model-market/benchmark_selection.py`
- `open-model-market/dynamic_runtime.py`
- `open-model-market/TASK_MATRIX_SELECTION.md`
- `requirements-runtime.txt`

## 隔离边界

- 本仓库只运行专家研判中心任务；
- 禁止中心间直接调用、运行时导入、Artifact 互取和共享业务 Secret；
- 禁止模型调用外部工具、网页、插件、文件、代码执行、API 或其他模型；
- OpenRouter 仅提供模型目录、Benchmark 和直接模型调用，不负责路由与选模。

## 迁移证据

查看 `MIGRATION_MANIFEST.json`、`MIGRATION_PROVENANCE.json`、`MIGRATION.md` 和 `governance-compatibility.json`。
