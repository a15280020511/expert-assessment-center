# 全动态任务资源规划：性价比优先版

## 结论

系统分成两个严格阶段：

```text
阶段A：任务 → 原子工作单元 → 资源需求
阶段B：资源需求 + OpenRouter实时市场 → 综合性价比最高的可行执行方案
```

模型目录在阶段A结束后才进入系统，避免先看到模型再反向迁就任务。

专家团中心的最高原则是：

```text
在满足全部硬约束的可行方案中，最大化综合性价比。
```

不再采用：

```text
先最大化质量
→ 再在质量容差带内最低成本
```

## 阶段A：任务资源需求计算

系统先从任务计算：

- 原子工作单元：领域 × 操作；
- 每个工作单元的重要性；
- 是否需要独立复核；
- 所需上下文；
- 所需输出规模；
- 所需推理等级；
- 是否必须结构化输出；
- 所需提示词模块；
- 所需模型能力和模态；
- 最终综合节点需要处理的内容。

典型原子工作单元：

```text
分析@商业金融
决策@商业金融
证据核验@证据研究
定量计算@定量建模
独立反证@法律合规
工程实现@软件工程
```

阶段A输出 `task-resource-requirements.json`。它不含具体模型ID、固定专家人数、固定席位或固定参数模板。

## 阶段B：实时市场联合优化

系统只提取阶段A明确要求的OpenRouter字段：

```text
模型ID、Provider、智能排名、分领域Benchmark、价格、上下文、最大输出、
支持参数、模态、reasoning支持、知识截止、到期日期和版本状态
```

随后动态生成：

- 原子工作单元的不同合并方式；
- 每种工作包的提示词模块组合；
- 每个模型实际支持的参数候选；
- 模型 × Provider × 工作包 × 提示词 × 参数的联合候选矩阵。

CP-SAT决策变量近似为：

```text
x[工作包, 模型, Provider, 参数配置, 提示词配置] ∈ {0,1}
```

V5进一步把候选扩展为：

```text
候选执行节点 + 信息依赖边 + 动态综合节点
```

## 优化顺序

### 1. 硬约束

- 所有原子工作必须被覆盖；
- 高风险核心分析和证据工作必须独立覆盖；
- 模型上下文、最大输出、参数和结构化输出能力必须满足需求；
- 模型不得重复；
- 默认Provider不得重复；
- 有预算时不得超预算；
- 仅使用实时稳定直接模型和智能榜前50；
- 禁止Router、online、batch、free、preview等变体；
- 禁止模型调用工具、网页、插件、文件、代码执行、API或其他模型；
- 执行图必须满足节点数、调用数、阶段数、边数和重试上限。

硬约束不参与价格交换。任何更便宜的方案，只要不能满足硬约束，就不是候选方案。

### 2. 最大化综合性价比

性价比分子是风险调整后的任务效用：

```text
任务领域Benchmark
+ 领域匹配
+ 操作匹配
+ 当前稳定条件
+ 上下文余量
+ 推理参数匹配
+ 提示词模块匹配
- 失败概率折损
- 质量不确定性折损
```

性价比分母是有效成本：

```text
模型输入费用
+ 模型输出费用
+ 提示词Token开销
+ 每次模型调用的运行开销
```

正式目标：

```text
综合性价比
=
风险调整后的任务效用
÷
有效成本
```

系统不是挑选单个“性价比最高模型”，而是计算完整执行方案的整体性价比，包括：

- 工作如何拆分或合并；
- 需要多少工作包；
- 每个工作包使用什么模型和Provider；
- 使用哪些提示词模块；
- 使用哪些参数；
- 是否需要独立复核；
- 综合节点采用什么资源；
- 总调用次数和总费用。

专家数量不是人为第一目标，而是整体性价比优化的结果。

## 求解方法

质量与成本的比值属于分式整数优化，不能直接作为普通线性CP-SAT目标。

当前实现采用有界的Dinkelbach式迭代：

```text
先找到最低有效成本的可行方案
→ 根据当前效用/成本比构造线性目标
→ 最大化：效用 × 当前成本分母 - 有效成本 × 当前效用分子
→ 更新比值并重复
→ 在最优比值约束下，以最低有效成本进行最终平局裁决
```

该过程保持：

- 所有决策可审计；
- 不调用额外模型做路由；
- 不依赖历史绩效账本；
- 不使用黑箱Router；
- 求解次数有固定上限；
- 结果仍由Google OR-Tools CP-SAT确定性计算。

## 兼容政策

旧版输入字段：

```text
quality_tolerance_pct
```

仅为兼容历史票据和旧调用接口而暂时保留。当前V3和V5正式入口会记录该字段，但明确忽略，不参与选模、组团或执行图求解。

旧模块中的质量带函数保留为历史兼容代码；正式入口已经切换到：

```text
value_resource_plan_optimizer.py
v5_value_optimizer.py
```

## 提示词不是固定模板

系统保留的是可复用的原子提示词模块，例如：

```text
任务边界
证据纪律
定量严谨性
情景推演
红队反证
工程交付
决策比较
不确定性校准
结构化交付
综合裁决
```

哪些模块被组合、给哪个工作包、产生多少Token开销，均由资源矩阵决定。原子模块类似编译器指令，不是固定专家模式或固定完整提示词模板。

## 参数不是固定模式

系统根据工作单元和模型支持能力动态生成：

- reasoning effort；
- temperature；
- verbosity；
- structured output；
- 预计输出Token。

生成结果使用内容哈希标识，例如 `params-xxxxxxxxxx`，用于审计和复现。

## 借鉴的成熟方案

### HuggingGPT

借鉴“任务规划 → 根据模型描述选资源 → 执行 → 汇总”的分层控制思想。当前系统把模型选择改成确定性资源矩阵和CP-SAT。

来源：https://arxiv.org/abs/2303.17580

### LLMCompiler

借鉴编译器式计划和任务DAG：可独立工作包并行执行，综合节点在其后执行。

来源：https://arxiv.org/abs/2312.04511

### Microsoft Foundry Model Router

吸收其任务级质量与成本联合比较思想，但不采用“最高质量附近容差带”作为本中心最高原则。

来源：https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router

### Amazon Bedrock Intelligent Prompt Routing

借鉴按请求分析内容和上下文、预测候选模型质量、再结合成本选择模型的流程。

来源：https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html

### Not Diamond

借鉴质量、成本、延迟的Pareto权衡和候选模型集合约束；当前系统不引入其外部路由API，也不需要训练数据。

来源：https://docs.notdiamond.ai/docs/key-concepts

### RouteLLM

借鉴任务级强弱模型路由和质量—成本权衡。由于RouteLLM依赖偏好数据训练，而本中心明确不使用历史绩效账本，因此不直接安装其训练路由器。

来源：https://arxiv.org/abs/2406.18665

### Mixture-of-Agents

借鉴多模型并行产生互补结果、再由综合节点吸收各工作包结果的结构；不照搬固定层数和固定Agent数量。

来源：https://arxiv.org/abs/2406.04692

### AutoGen SelectorGroupChat

借鉴按上下文动态选择参与者的原则，但不采用一个LLM黑箱决定下一个专家；最终资源决策仍由可审计CP-SAT完成。

来源：https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/selector-group-chat.html

### DSPy

借鉴把提示词视为可优化程序组件的思想。DSPy的MIPRO/GEPA通常需要训练样本和评价指标，因此当前只采用模块化提示词，不进行历史数据驱动的离线编译。

来源：https://dspy.ai/

## 没有直接采用的产品

- OpenRouter Auto Router：只解决单次调用的模型路由，无法联合优化多专家、提示词和参数；
- AWS或Azure托管Router：模型池和部署边界受平台约束；
- Not Diamond托管Router：需要额外服务，定制Router依赖评价数据；
- AutoGen/CrewAI：属于运行编排，不是约束优化器；
- DSPy离线优化器：需要训练集和指标，不符合当前无历史账本要求。

这些方案用于校准设计，而不作为运行依赖。

## 主要产物

```text
task-resource-requirements.json
task-parameter-matrix.json
team-optimization.json
model-selection.json
benchmark-market.json
v5-optimization.json
v5-execution-graph.json
artifact-manifest.json
```

`team-optimization.json`和`v5-optimization.json`必须记录：

- 硬约束覆盖情况；
- 候选工作包或候选执行节点；
- 提示词模块和参数；
- 模型和Provider；
- 风险调整效用；
- 实际预计费用；
- 调用开销；
- 综合性价比；
- 求解阶段和状态；
- 被忽略的历史质量容差字段；
- 是否发生fallback。
