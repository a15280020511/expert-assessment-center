# V5 低成本真实试运行

## 目的

完整五任务盲评仍要求可验证的 OpenRouter 资金预留。低成本试运行用于在资金配置完成前验证真实链路：

```text
实时模型目录
→ 真实模型 × Provider Endpoint
→ V5 动态执行图
→ V3
→ 四种结构基线
→ 至少两名独立盲评裁判
→ 费用、调用、请求和 Artifact 审计
```

试运行只覆盖一个自包含任务，不具备生产切换资格。

## 入口

创建仓库所有者 Issue：

```text
[v5-pilot] V5 low-cost live pilot
```

正文示例：

```json
{
  "pilot_id": "v5-low-cost-pilot-20260730",
  "task_id": "retail-expansion-unit-economics",
  "max_cost_usd": 0.5,
  "max_calls": 40,
  "max_strategy_cost_usd": 0.12,
  "output_allowance_tokens": 2000
}
```

## 硬限制

- 实际费用上限不超过 0.50 USD；
- 调用上限不超过 40；
- 单策略预计费用上限不超过 0.12 USD；
- 输出许可不超过 2,500 Token，模型无需使用满额；
- 模型目录价格上限：输入 1.50 USD / 百万 Token、输出 4.00 USD / 百万 Token；
- Provider Endpoint 可靠性不得低于 0.80；
- 每个直接调用前计算最坏情形费用，超出剩余总额度则不发请求；
- 第一次 HTTP 402 立即停止；
- 禁止工具、搜索、网页、插件、路由模型、在线模型和批处理模型；
- 生产入口始终不变。

## Key 规则

完整基准要求 API Key 设置有限 spending limit。低成本试运行允许使用无限额 Key，因为：

1. 运行总额硬限制为 0.50 USD；
2. 只允许低价 Endpoint；
3. 单次直接调用先按最大输出许可计算最坏费用；
4. V5 和 V3 各自仍受预计费用门控制；
5. 第一次资金不足即全局停止。

若 API Key 已设置有限额度，剩余额度低于试运行预留时仍会在模型推理前拒绝。

## 通过条件

试运行门要求：

- V5 成功；
- V3 成功；
- 六种策略中至少四种成功；
- 至少两名不同模型、不同 Provider 的盲评裁判返回合法完整评分；
- 无安全失败；
- 实际费用不超过 Issue 上限。

无论是否通过，结果均包含：

```text
production_cutover_allowed = false
full_benchmark_still_required = true
```

## 与完整基准的关系

低成本试运行通过只能证明真实执行链可用，不能证明 V5 优于 V3。

生产切换仍必须完成：

- 五个独立任务；
- 六种策略完整覆盖；
- 每任务至少两个独立盲评裁判；
- V5 成功率、质量、费用和安全门全部通过；
- 单独受审计 PR 执行生产入口切换。
