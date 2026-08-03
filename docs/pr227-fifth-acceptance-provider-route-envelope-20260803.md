# PR #227 第五次验收 Provider 路由包络修复记录

日期：2026-08-03

## 第五次受限生产验收

- 验收目标：`164dbc819a2c8b2600e968d0cfbf589542702320`
- 验收分支：`acceptance/pr227-final-paid-20260803-r5`
- 授权提交：`1636f7d95993d7e3f8bfd29e73546112879a5996`
- 总调用上限：4
- 恢复调用上限：0
- 费用异常阈值：0.25 美元

验收在实时目录规范化阶段 fail-closed，GPT、Claude 和专家聊天调用均未发生。结构化错误为：

```text
V5_PRODUCTION_RUNTIME_FAILED
CatalogViewError: conflicting duplicate exact catalog endpoint: ('google/gemma-4-31b-it', 'together')
```

本次模型调用为 0，模型费用为 0 美元，不能作为生产 PASS。

## 根因

同一 OpenRouter 模型和同一可锁定 Provider 标签下，可以存在多个物理端点变体。它们共享相同路由键，但上下文、最大输出、支持参数或价格可能不同。请求层只能锁定 Provider 标签，无法进一步指定物理变体，因此：

- 任取第一行会低估风险；
- 直接拒绝所有差异会把合法路由变体误判为目录损坏；
- 正确安全表示应是该 Provider 路由下所有物理变体的保守能力与成本包络。

## 修复策略

修复提交：`2d4fb00f2d0f88a1798b1616e297fdb658cd0f10`。

同一 `(model, provider)` 路由键的多个规范化端点行现在按以下规则合并：

- 上下文长度：取最小值；
- 最大输出 Token：取最小值；
- 输入价格：取最大值；
- 输出价格：取最大值；
- 支持参数：取交集；
- 测试资产标记：取逻辑并集；
- 模型、公司、官方排名、Provider、路由 ID 和输入输出模态：必须完全一致；
- 出现任何未显式处理的新字段、身份字段冲突，或合并后失去输出长度控制参数时，继续 fail-closed。

该策略保证运行时在只能锁定 Provider 标签的条件下，不会高估能力或低估费用。

## 零聊天实时验证

专用验证 Run：`30773750993`。

- Ruff：PASS；
- Python 全量编译：PASS；
- 完整单元回归：PASS；
- 八个随机种子的路由包络顺序压力：PASS；
- 仓库逐行审计：Critical 0、High 0、Medium 0；
- 连续三轮实时 OpenRouter 目录与端点查询：PASS；
- 每轮符合硬约束的官方排序模型：2；
- 每轮原始端点行：15；
- 每轮规范化 Provider 路由：6；
- 三轮目录 SHA-256 均为：`bd92bd32658712c8ac3088b5bb9c20574ce2b0ca0f9965ff830c79d7ea6237e0`；
- 聊天模型调用：0；
- 模型费用：0 美元。

验证 Artifact：

- Artifact ID：`8842064846`
- Artifact SHA-256：`e96919d47ce40633159c5dc7436cd5308a171242243df613864fb789985fb5f1`

临时重写脚本和一次性验证工作流已经自动删除。`main` 与 `production` 未移动。下一次真实生产验收仍必须获得完整调用账本、主 Artifact、独立复算、最终状态和最终 Attestation 全部 PASS。
