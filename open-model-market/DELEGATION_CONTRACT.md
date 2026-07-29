# Web GPT → GitHub 专家团委托契约

用户明确要求“交给GitHub专家团分析”“不许网页GPT自己分析”或同等含义时，执行本契约。

## 角色边界

- 用户：确定问题、约束、调用额度、质量偏好和可使用证据；
- 网页GPT：忠实整理任务，创建正式票据，监控状态，取回并转述GitHub产物；
- GitHub确定性政策引擎：校验权限、Schema、唯一性、调用额度和模型硬门槛；
- GitHub语义路由器：仅在分类置信度不足且有额外调用额度时补充领域、复杂度、风险和能力画像；
- 三名专家：独立完成主研、交叉验证和反证；
- 裁判：综合专家结果并形成最终报告；
- GitHub审计链：记录全部调用、Token、Provider、费用、错误、报告SHA和Artifact。

## 网页GPT禁止事项

GitHub报告产生前，网页GPT不得：

- 自行回答实质问题；
- 混入自己的战略、商业或政策结论；
- 指定具体模型ID；
- 把排队、票据接收或Workflow启动说成专家已经完成；
- 在GitHub失败时用自身分析替代结果；
- 新建重复Issue绕过去重和受控重试。

## 网页GPT必须执行

1. 按 `execution-ticket.schema.json`生成合法票据；
2. 用户未指定质量档时使用 `quality_tier=value`；
3. 只在原Issue进行受控重试；
4. 区分 `ACCEPTED`、`COMPLETED`、`DEGRADED`和`FAILED`；
5. 只有 `EXECUTION_COMPLETED`和完整报告才能称为正常PASS；
6. 转述时明确内容来自GitHub专家团；
7. 失败时报告直接根因、调用证据和Artifact状态。

## 调用额度

固定3名专家加1名裁判需要4次模型调用。

`approved_budget.calls`只能是4—6：

- 4次：固定3+1，不允许语义路由或替换；
- 5次：提供1次共享额外调用；
- 6次：提供2次共享额外调用。

共享额外调用可用于：

1. 最多一次语义路由；
2. 专家技术故障替换；
3. 最多一次裁判技术故障替换。

不得无限重试。已经成功的专家不得因裁判失败而重新调用。

## Token政策

路由器、专家和裁判的生产请求不发送人为：

```text
max_tokens
max_completion_tokens
reasoning.max_tokens
```

模型只受自身和Provider能力限制。系统使用低推理强度、低冗长度和紧凑提示词鼓励简短输出，但完整性优先，不设置固定字数或Token上限。

## 费用政策

- 不设置总金额硬上限；
- 价格和性价比继续参与选模；
- 调用次数仍由4—6次硬限制；
- 所有成功、失败和替换调用都必须记录实际Provider、Token和费用；
- 费用未知不直接否定分析内容，但至少导致 `DEGRADED`；
- 旧票据中的 `max_cost_usd`仅为兼容字段，生产执行忽略。

## 语义路由器禁止事项

语义路由器不是分析专家，不得：

- 回答原问题；
- 判断证据真假、提出建议或形成结论；
- 指定或提及具体模型；
- 降低代码识别出的复杂度或风险等级；
- 调用网页、工具、插件、文件或其他模型；
- 在总调用额度只有4次时产生隐藏调用。

路由输出越权、无效或置信度不足时，系统保留调用记录并回退确定性分类。

## 状态语义

- `queued` / `in_progress`：仅表示GitHub Actions状态；
- `EXECUTION_REJECTED`：未进入模型调用；
- `EXECUTION_ACCEPTED` / `EXECUTION_RETRY_ACCEPTED`：票据已通过校验；
- `EXECUTION_COMPLETED`：审计PASS、完整报告和交付证据成立；
- `EXECUTION_DEGRADED`：已交付，但发生部分输出、替换或费用证据降级；
- `EXECUTION_FAILED`：没有形成可接受交付。

不得把 `EXECUTION_DEGRADED`表述成完整成功。

## 日志要求

每次运行应尽可能保留：

- 票据和任务指纹；
- 语义路由结果；
- 三名专家每次尝试；
- 裁判每次尝试和替换；
- 原始清洗响应和诊断；
- 输入、输出和推理Token；
- Provider实际费用或保守估算；
- 标准化错误代码；
- 统一 `execution-diagnosis.json`；
- 报告评论清单和SHA-256；
- Artifact Manifest和运行来源。

## 受控重试

`EXECUTION_REJECTED`、`EXECUTION_FAILED`或`EXECUTION_DEGRADED`后，在原Issue评论：

```text
/retry-expert-team <unique_retry_id>
```

`retry_id`不得复用。不得新建同语义Issue绕过去重。