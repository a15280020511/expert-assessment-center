# Expert Assessment Center

本仓库是独立的专家研判中心。网页 GPTs 通过治理中心下发任务与候选模型信息；专家中心负责按当前任务动态组织专家并执行。专家中心不得直接连接其他业务中心。

最高治理规则见 [`CONSTITUTION.md`](CONSTITUTION.md)。当前生产入口为 `.github/workflows/execution-ticket.yml`。

## 当前生产链

```text
任务与治理候选模型清单
→ 根据当前任务动态确定专家数量、角色、模型、协作关系和恢复候选
→ OR-Tools / 启发式方法生成可执行组合
→ NetworkX 校验有限 DAG
→ OpenRouter 执行专家请求，Provider 由 OpenRouter 动态选择
→ 汇总可用结果
→ 发布报告与诊断证据
```

## 动态专家原则

专家组合不存在固定 4+4、固定公司数量、公司去重、Top20/Top50-only、旗舰门槛、最低价门槛或固定角色拓扑。简单任务可以只需要一个专家；复杂任务可以增加独立分析、复核、对抗和综合节点。专家数量、角色、模型、主备关系及执行 DAG 均由当前任务决定。

OR-Tools 用于可行组合求解，但 `OPTIMAL` 不是执行资格门禁；可行解和必要的启发式回退均可进入执行。模型目录和历史排行榜可以作为候选信息，但不能成为唯一资格池。

## Provider 路由

生产专家请求采用 `unrestricted-openrouter`：

- 不发送 Provider `only` 或 `order`；
- 不使用 ZDR、数据收集策略、Provider 价格、精确端点等条件筛掉上游服务器；
- 不设置 `require_parameters` 作为 Provider 资格门槛；
- 不禁止 OpenRouter fallback；
- Provider 返回值只用于运行日志和诊断，不参与专家资格判断。

旧 schema 中若仍出现 provider 字段，只能作为非绑定目录元数据，不能进入实际 OpenRouter 路由对象。

## 已取消的业务门禁

以下规则不得阻断专家执行：

- free-first 或免费 Canary 前置资格；
- 当前 SHA 必须先取得免费资格证据；
- production/main SHA 锁；
- admission lock、ticket budget gate、重复提交资格门禁；
- 固定调用次数、固定 4 主 + 4 备；
- 公司唯一、治理公司不得担任专家；
- Top20/Top50-only、旗舰、价格、Provider、ZDR 资格门槛；
- OR-Tools 必须证明全局最优；
- 独立 Artifact 复核通过后才允许发布已有专家结果。

费用、Canary、Artifact、模型排名和 Provider 观测可以继续记录为遥测或诊断证据，但不再作为专家资格门禁。

## 保留的基础安全边界

动态专家并不等于取消基础安全：

- GitHub 身份与仓库权限校验；
- Secret 不写入日志或 Artifact；
- 专家执行节点禁止任意外部工具；
- 专家中心与其他业务中心保持仓库隔离；
- 执行图必须有限且无环，防止无限递归或无限重试；
- 输入结构、JSON/schema 与证据完整性仍需可解析和可审计。

这些边界只保护执行安全与数据完整性，不用于限制专家公司、模型、Provider、价格或组合方式。

## 主要生产组件

- `v5_price_ranked_issue_ticket.py`：从任务与治理候选信息生成动态执行票据；
- `v5_top50_pool_optimizer.py`：历史命名的动态候选组合器，当前不再要求 Top50、4+4、公司唯一或 OPTIMAL；
- `v5_governed_plan_orchestrator.py`：构建动态专家计划；
- `v5_soft_proposal_materializer.py`：只执行结构性物化，不做公司/Provider/预算资格门禁；
- `v5_dynamic_pipeline.py`：当前无业务门禁执行入口；
- `v5_production_expert_policy.py`：在实际请求前删除 Provider 路由限制；
- `v5_provider_lock.py`：兼容旧函数名，但只接受 unrestricted Provider routing。

## 测试纪律

零费用测试、Canary 和静态审计用于发现代码问题，不再决定付费专家是否具备资格。测试本身也不得重新引入 production 锁、固定专家组合、公司唯一、Provider 锁或免费 Canary 前置。
