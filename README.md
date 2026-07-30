# Expert Assessment Center

本仓库是正式独立的专家研判中心；拆仓来源和固定提交仅记录在迁移证据文件中，不参与日常运行。

## 职责

根据任务画像、OpenRouter 模型目录、Benchmark、价格、参数能力和本地可靠性账本，使用 Google OR-Tools CP-SAT 动态组成 1—4 名专家加 1 名裁判；执行红队反证、战略研判、模型选择审计、费用账本与可审计专家报告。

## 动态专家团

默认拓扑规则：

- 简单、低风险、单领域任务：1 名核心专家 + 1 名裁判；
- 中等复杂度或双领域任务：核心专家 + 交叉验证专家 + 裁判；
- 复杂或高风险任务：核心专家 + 交叉验证专家 + 独立反证专家 + 裁判；
- 同时满足复杂、高风险、长上下文或三个以上领域时：再增加证据与定量校准专家，形成 4+1。

高风险任务不得少于 3 名专家，且必须包含独立反证席。所有正式组合默认禁止模型复用和 Provider 复用。

## 任务输入参数

系统会从任务自动生成领域、复杂度、风险、上下文和能力要求。需要人工约束时，可在任务正文加入：

```text
<expert-team-config>
{
  "objective": "value",
  "expert_count": 3,
  "budget_usd": 0.8,
  "strict_provider_diversity": true,
  "candidate_pool_per_seat": 12,
  "solver_timeout_seconds": 8,
  "forbidden_models": [],
  "preferred_models": []
}
</expert-team-config>
```

`objective` 允许 `budget`、`value`、`quality`。高风险任务对人数和红队席位的硬约束不能被任务参数关闭。也可通过 `EXPERT_TEAM_INPUT_JSON` 传入同一结构。

## 优化器输入与输出

输入包括：任务画像、所需专家角色、OpenRouter 智能榜前 50、Benchmark、模型价格、上下文、最大输出、支持参数、正式版本状态、历史成功率和费用偏差。

求解器联合决定：专家人数、专家职业和领域、每个席位的模型、裁判模型、参数模板、Provider 分布和预计费用。每次选择生成 `team-optimization.json`，记录求解状态、目标函数、约束、输入参数和选定组合；不存在可行解时直接失败，不回退到旧的贪心选择器。

## 机器权威目录

`open-model-market/expert-team-capabilities.json`

## 隔离边界

- 本仓库只运行本中心任务。
- GPTs 是三个业务中心之间唯一的控制与证据中继。
- 禁止中心间直接调用、运行时导入、Artifact 互取和共享业务 Secret。
- 原业务目录 `open-model-market/` 暂时保留，避免迁移与路径重构同时发生。
- 迁移源仓库只作为回滚与审计来源，不是治理仓库，也不是运行时依赖。

## 迁移证据

查看 `MIGRATION_MANIFEST.json`、`MIGRATION_PROVENANCE.json`、`MIGRATION.md` 和 `governance-compatibility.json`。

## V4 integrity controls

完整性门按优化器实际选定的 1—4 名专家验收：要求全部计划专家输出可用、裁判报告非空、调用账本至少包含“专家人数 + 裁判”条记录，并验证 Artifact SHA。 
