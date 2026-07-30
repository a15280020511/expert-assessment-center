# V5 真实盲评基准 Run 30516841488 复盘

## 结论

Issue #23 和 Run `30516841488` 成功验证了工作流、Secret、模型目录、真实 Endpoint 编译、付费调用、费用账本和 Artifact 上传，但没有形成有效的五任务盲评结果。

这次运行不得解释为“V5 质量低于 V3”。主要失败原因是 OpenRouter 在模型生成前执行的额度预授权，而不是答案被盲评否决。

## 实际证据

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

## 为什么不发送输出上限会触发 402

模型请求未携带 `max_completion_tokens` 时，OpenRouter 会按模型或 Endpoint 允许的最大输出检查最坏情形额度。对于最大输出 65,536 或 128,000 Token 的高价模型，即使模型实际只需要几千 Token，账户或 API Key 剩余额度不足以覆盖最大可能费用时，请求也会在生成前被拒绝。

首轮错误中明确出现：

```text
This request requires more credits, or fewer max_tokens.
```

因此“完全不发送上限”在当前额度条件下无法完成可比基准。

## 修正方案

### 1. 基准专用 10,000 Token 最大许可

真实基准统一发送：

```json
{
  "max_completion_tokens": 10000
}
```

这是最大许可，不是强制输出量。模型可以提前自然停止。该设置只作用于真实基准入口，不修改普通 V3 或 V5 生产策略。

### 2. 付费前额度预检

工作流先调用 OpenRouter 当前 API Key 元数据接口，检查：

- API Key 额度上限；
- API Key 剩余额度；
- 当前基准要求预留的费用。

若配置了 `OPENROUTER_MANAGEMENT_KEY`，还会读取账户总 Credits 和总 Usage，计算账户级剩余额度。

任一已知额度低于本次基准费用预留时：

```text
模型推理调用 = 0
工作流失败关闭
生产入口不变
```

### 3. HTTP 402 全局停止

即使预检无法取得账户级余额，任何策略或裁判第一次出现 HTTP 402 时，整个基准立即停止，不再继续消耗调用尝试。

### 4. 盲评失败证据

所有裁判请求结果都会写入：

```text
blind-evaluation-attempts.json
```

即使不足两名有效裁判，也能看到模型、Provider、延迟、费用、finish reason、响应长度或错误正文。

### 5. 裁判价格保护

在满足不同模型、不同 Provider 和能力要求的前提下，盲评裁判优先从合理价格 Endpoint 中选择，避免高价裁判消耗过多基准预算。

## 重新执行条件

重新打开或新建 `[v5-benchmark]` Issue 前，应确保：

- `OPENROUTER_API_KEY` 的 `limit_remaining` 不低于 Issue 中的 `max_cost_usd`；
- OpenRouter 账户可用 Credits 足以覆盖同一预留；
- 推荐至少保留 20 USD 可用额度；
- 可选增加 `OPENROUTER_MANAGEMENT_KEY` Secret，以便工作流在调用前核验账户级 Credits。

未满足条件时不得重新进行付费基准。
