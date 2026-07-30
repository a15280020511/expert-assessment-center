# V5 真实盲评基准 Issue #23 复盘

## 总结

Issue #23 已执行两次真实基准：

| Run | 状态 | 模型请求 | 实际费用 | 主要阻断 |
|---|---|---:|---:|---|
| `30516841488` | `technical_failure` | 25 | 1.8563575 USD | 未发送输出许可时触发 OpenRouter 最坏情形额度预授权 HTTP 402 |
| `30517985049` | `budget_or_call_limit_exceeded` | 23 | 0 USD | 10,000 Token 许可使用了 Endpoint 不支持的参数名；随后账户 Credits 仍不足 |

两次运行都没有形成完整五任务盲评，因此不得声称 V5 优于或低于 V3。生产入口始终没有切换。

## Run 30516841488

### 证据

- 真实执行任务：1 个任务开始，0 个任务完成盲评；
- API 请求计数：25；
- 实际费用：1.8563575 USD；
- V5 第一层：6/6 节点成功；
- V5 第一层质量门：0.961429–1.0；
- V5 第二层：3 个节点因 HTTP 402 未开始生成；
- V3：7 条调用记录中 6 条为 HTTP 402，只有 1 条取得响应；
- 最强单模型、最低价单模型、固定 3+1、随机组合均在首次请求时被 HTTP 402 拒绝；
- 四名候选盲评裁判同样没有取得两个完整合法评分；
- 专家无工具请求审计：PASS；
- 生产入口切换：false；
- Artifact：`8749404646`；
- Artifact digest：`d1df0e5dcda716fcde53dea800d786de02afdf66e6a4d90316b13818bdaa7833`。

### 原因

模型请求不携带输出许可时，OpenRouter 会按模型或 Endpoint 的最大可能输出检查最坏情形额度。高价模型即使实际只需要几千 Token，也可能因为账户无法覆盖最大输出费用而在生成前被拒绝。

错误明确出现：

```text
This request requires more credits, or fewer max_tokens.
```

因此“完全不发送输出许可”不适合在有限余额账户上进行可比基准。

## Run 30517985049

### 证据

- 工作流前置编译和当前 API Key 元数据读取成功；
- API Key 的 `limit` 和 `limit_remaining` 均为 `null`，说明该 Key 没有有限 spending limit；
- API Key 累计 usage：1.966098826 USD；
- 未配置 `OPENROUTER_MANAGEMENT_KEY`，因此账户级 Credits 未被核验；
- 10,000 Token 最大许可已启用；
- 记录了23次请求尝试；
- OpenRouter 返回的实际费用合计为0 USD；
- V5、V3低价候选和其他基线大量返回 HTTP 404：

```text
No endpoints found that can handle requested parameters.
```

- Endpoint 市场记录显示多数直接 Endpoint 支持 `max_tokens`，不支持 `max_completion_tokens`；
- 请求同时使用了 `require_parameters=true`，因此参数名不匹配时 OpenRouter 正确拒绝；
- 部分高价 V3 模型即使使用10,000 Token许可仍因账户 Credits 不足返回 HTTP 402；
- 五任务盲评仍为0/5；
- 生产入口切换：false；
- Artifact：`8749646151`；
- Artifact digest：`b8e5aa481acfc4a9f70874ccd210eacaf8cf7eadf17b75f7b426ef4820f17e9b`。

### 结论

第二次运行证明：

1. 10,000 Token 许可避免了首轮高额预授权实际消费；
2. 输出许可必须使用真实 Endpoint 声明支持的参数名称；
3. 当前无限额 API Key 不能作为基准资金充足性的证明；
4. 未验证账户 Credits 时，不应继续重新运行付费基准。

## 已实施修正

### 1. Endpoint 参数兼容

基准根据 Endpoint 的 `supported_parameters` 选择：

```text
明确支持 max_completion_tokens → 使用 max_completion_tokens
否则 → 使用 OpenRouter Endpoint 普遍声明的 max_tokens
```

V3 基准入口固定使用其候选 Endpoint 实际支持的 `max_tokens`。

10,000 Token 仍只是最大许可，不要求模型输出满额。

### 2. 有限 API Key spending limit

重新运行前，基准专用 API Key 必须设置有限额度：

```text
limit != null
limit_remaining != null
limit_remaining >= max_cost_usd
```

无限额 Key 将在任何模型推理前被拒绝，避免把“无限额”误判为“账户资金充足”。

### 3. 账户级 Credits

推荐增加只用于读取余额的：

```text
OPENROUTER_MANAGEMENT_KEY
```

工作流可用它读取账户总 Credits 和总 Usage。没有该 Secret 时，有限 API Key 只能证明 Key 本身允许支出，不能证明账户实际余额充足。

### 4. HTTP 402 全局停止

任何策略或裁判第一次出现 HTTP 402 时，整个基准立即停止，不继续尝试其他模型。

### 5. 盲评遥测

所有裁判请求成功或失败都会写入：

```text
blind-evaluation-attempts.json
```

## 重新执行的外部条件

重新打开或新建 `[v5-benchmark]` Issue 前必须完成：

1. 给基准使用的 OpenRouter API Key 设置有限 spending limit；
2. `limit_remaining` 至少为20 USD；
3. OpenRouter账户实际可用 Credits 至少覆盖同一预留；
4. 推荐配置 `OPENROUTER_MANAGEMENT_KEY`，让工作流在调用前核验账户级余额；
5. 不满足上述条件时，不得再次运行付费基准。

代码可以修复参数兼容和失败治理，但无法通过 GitHub 工具替用户充值 OpenRouter 或修改其账户级 Credits。
