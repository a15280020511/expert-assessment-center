# PR #227 第三次真实验收缺陷与语义收敛记录

日期：2026-08-03

## 第三次真实生产验收

- Run：`30772176028`
- 目标源码：`0a44879e847251a202c73099b12201ec4cabd616`
- 正式入口：`v5_production_ticket.py`
- 调用总上限：4
- 恢复调用上限：0
- 费用异常阈值：0.25 美元
- 主 Artifact：`8840875120`
- 主 Artifact SHA-256：`e32968713b982a89970f44d6470a610aaf5ba4265adef9479e9f1131a833c38e`
- 证明 Artifact：`8840875549`
- 证明 Artifact SHA-256：`9cd82811bfbaa698a26539c6a57ca3f750c15b9dfdb777347a196b49fcac5bba`

票据门、密钥检查、锁定依赖、Ruff、编译和完整回归均通过。正式入口按 fail-closed 结构化失败，主 Artifact 中记录：

```text
GPTSelectorError: node functions are invalid
```

因此该 Run 不是生产 PASS，且没有形成可用于资格判定的完整专家结果、调用总账和最终报告。

## 根因

Provider 兼容层会从发送到上游的结构化 Schema 中移除 `minItems`、`maxItems`、`uniqueItems` 等约束；模型因此可以合法返回空 `functions` 数组。本地解析器却仍要求 `functions` 至少包含一项，形成线上 Schema 与本地解析器不一致。

同时，旧运行时仍把 `functions` 中的自由文本 `synthesis`、`implementation` 当作机器控制标签，而前两次修复已经允许该字段使用自然语言描述。该字段同时承担“说明性文本”和“控制枚举”两种互斥语义。

## 修复

语义收敛提交：`60763eea3ec733cb293b49d6ac8539ef9b499431`。

- `functions` 明确改为可选的说明性元数据，允许 0–12 项。
- 非列表、超限、控制字符和重复非空描述继续 fail-closed。
- 执行图校验器和提案物化器不再要求每个节点必须包含函数描述。
- 提示词在函数描述为空时不再输出空白的“本节点功能”句子。
- 最终节点、综合节点和内容节点判定统一使用执行图 `final_nodes` 与显式 `final_delivery_node` 输出合同。
- 质量门、降级可用性和内容工作集合不再依赖自由文本 `functions` 标签。
- 新增空函数列表、空提示词、最终交付质量下限和内容工作排除最终节点等回归测试。

## 最终 Attestation 状态合同

同一修复还补充了最终 Attestation 顶层 `status`：

- 只有审计状态、诊断状态一致，报告存在且业务证据已冻结时，才允许输出 `PASS` 或 `DEGRADED`。
- 任一条件不满足均明确输出 `FAIL`。
- 新增审计与诊断状态不一致时必须 fail-closed 的回归测试。

## 零费用验证

专用修复门：`30772641877`。

- 三段补丁摘要与应用：PASS。
- 锁定开发依赖和 `pip check`：PASS。
- Ruff：PASS。
- Python 全量编译：PASS。
- 聚焦语义与 Attestation 回归：PASS。
- 完整回归，包括 Hypothesis 属性测试：PASS。
- 八个随机种子的同进程语义压力：PASS。
- 旧自由文本机器标签控制扫描：0。
- Critical：0。
- High：0。
- Medium：0。
- 模型调用：0。
- 模型费用：0 美元。

`main` 与 `production` 未移动。修复后的下一次真实生产验收必须重新生成完整专家结果、调用账本、主 Artifact、独立复算、最终状态和最终 Attestation，全部明确 PASS 后才能解除生产阻断。
