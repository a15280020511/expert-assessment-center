# Expert Assessment Center

本仓库是正式独立的专家研判中心。GPTs 是本中心与其他业务中心之间唯一的控制与证据中继。

## 最高原则：性价比最高

专家团中心的最高原则只有一个：

```text
在满足全部硬约束的可行方案中，选择综合性价比最高的方案。
```

这里的“性价比”不是单纯追求最低价格，也不是先追求最高质量，而是直接计算：

```text
风险调整后的任务效用
÷
（预计模型费用 + 调用开销）
```

任务覆盖、能力、上下文、输出、独立性、预算、安全和工具禁用属于硬约束，任何低价方案都不能突破。通过硬约束后，模型、Provider、提示词、参数、工作包组合、专家数量和综合节点统一按整体性价比求解。

## 当前架构：先算需求，再选资源

```text
任务正文与显式约束
→ 原子工作单元
→ 提示词、能力、上下文、输出和参数需求
→ 需要从OpenRouter提取的市场字段
→ 候选工作包合并方式
→ 模型 × Provider × 提示词模块 × 参数联合矩阵
→ Google OR-Tools CP-SAT全局求解
→ 并行动态工作包
→ 动态综合节点
→ GitHub Actions执行、证据与审计
```

阶段A不读取具体模型ID。只有任务资源需求确定后，阶段B才读取OpenRouter实时目录、Benchmark和价格。

## 不再使用

- 简单／中等／复杂对应固定人数；
- 固定1+1、2+1、3+1、4+1；
- 固定核心、交叉、证据等席位模板；
- 固定完整提示词模板；
- 固定参数模板库；
- 先最小化专家人数；
- 先最大化质量、再在质量容差带内降成本；
- 历史模型绩效账本；
- 逐席位贪心选模；
- OpenRouter Auto Router、Fusion或Agent黑箱路由。

## 阶段A：任务资源需求

系统先计算：

- 领域 × 操作形成的原子工作单元；
- 各工作单元重要性与独立复核要求；
- 所需提示词模块；
- 所需模型能力和模态；
- 最小上下文与预计输出；
- reasoning等级、temperature倾向、verbosity和结构化输出要求；
- 综合节点需要处理的范围。

输出：

```text
task-resource-requirements.json
task-parameter-matrix.json
```

## 阶段B：市场联合优化

只提取本任务要求的OpenRouter信息：

- 模型ID和Provider；
- 官方智能排序和分领域Benchmark；
- 输入、输出价格；
- 上下文、最大输出；
- 支持参数和模态；
- reasoning能力；
- 知识截止、到期和版本状态。

CP-SAT联合决定：

- 原子工作如何合并为工作包；
- 需要多少工作包；
- 每个工作包选择哪些提示词模块；
- 每个工作包选择哪个模型和Provider；
- 每个模型使用哪些实际支持的参数；
- 综合节点的模型、提示词和参数；
- 哪个完整执行方案的风险调整效用与有效成本之比最高。

正式优化顺序：

```text
覆盖全部硬资源需求
→ 最大化综合性价比
```

专家数量由整体性价比优化产生，不再作为第一阶段目标。

## 任务输入

普通任务无需填写参数。需要人工硬约束时可加入：

```text
<expert-team-input>
{
  "budget_usd": 0.8,
  "min_experts": 1,
  "max_experts": null,
  "strict_provider_diversity": true,
  "candidate_pool_per_work_package": 16,
  "solver_timeout_seconds": 12,
  "forbidden_models": [],
  "preferred_models": []
}
</expert-team-input>
```

`max_experts`默认不设固定人数。执行层仅保留最多16次模型调用的安全上限，不代表固定团队模式。`preferred_models`只能作为软偏好，不能突破能力、上下文、预算、覆盖和独立性硬约束。

旧版 `quality_tolerance_pct` 输入仅为兼容历史票据而保留，当前优化器明确忽略，不再影响选模和组团。

## 动态提示词与参数

提示词由原子模块组合，例如：任务边界、证据纪律、定量严谨、情景推演、红队反证、工程交付、决策比较、不确定性校准和综合裁决。

参数根据工作包和模型实际支持能力生成：

```text
reasoning effort
temperature
verbosity
structured output
预计输出Token
```

每个提示词组合和参数组合均使用内容哈希标识，写入审计产物。

## 全球成熟方案参考

详细说明见：

```text
open-model-market/FULL_DYNAMIC_RESOURCE_PLANNING.md
```

主要借鉴：HuggingGPT、LLMCompiler、Microsoft Foundry Model Router、Amazon Bedrock Intelligent Prompt Routing、Not Diamond、RouteLLM、Mixture-of-Agents、AutoGen SelectorGroupChat和DSPy。

这些方案只用于吸收任务规划、DAG、Pareto权衡、动态协作和提示词程序化思想，不增加外部运行依赖。本中心最终采用可审计的整体性价比目标，而不采用“最高质量优先”的路由原则。

## 审计产物

每次选择至少生成：

- `task-resource-requirements.json`：原子工作和完整资源需求；
- `task-parameter-matrix.json`：兼容名称，内容升级为V3资源矩阵；
- `team-optimization.json`：候选工作包、提示词、参数、模型、Provider、质量效用、有效成本和性价比；
- `model-selection.json`：运行时选择证据；
- `benchmark-market.json`：Benchmark来源与降级状态；
- `artifact-manifest.json`：产物SHA与完整性清单；
- V5路径额外生成 `v5-optimization.json` 和 `v5-execution-graph.json`。

无可行解时直接报告冲突约束，不回退旧选择器。

## 关键实现

- `open-model-market/resource_requirements.py`
- `open-model-market/value_resource_plan_optimizer.py`
- `open-model-market/benchmark_selection.py`
- `open-model-market/v5_value_optimizer.py`
- `open-model-market/v5_pipeline.py`
- `open-model-market/resource_runtime_compat.py`
- `open-model-market/resource_call_budget.py`
- `open-model-market/FULL_DYNAMIC_RESOURCE_PLANNING.md`
- `requirements-runtime.txt`

`resource_plan_optimizer.py`和`v5_planner.py`中的旧质量带求解函数仅保留为历史兼容实现，正式V3和V5入口均已切换到性价比优先优化器。

## 隔离边界

- 本仓库只运行专家研判中心任务；
- 禁止中心间直接调用、运行时导入、Artifact互取和共享业务Secret；
- 禁止模型调用外部工具、网页、插件、文件、代码执行、API或其他模型；
- OpenRouter只提供模型目录、Benchmark和直接模型调用，不负责路由与选模。

## 迁移证据

查看 `MIGRATION_MANIFEST.json`、`MIGRATION_PROVENANCE.json`、`MIGRATION.md` 和 `governance-compatibility.json`。
