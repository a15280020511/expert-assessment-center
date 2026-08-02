# PR #227 第四次验收目录重复缺陷与修复记录

日期：2026-08-03

## 第四次受限生产验收

- 验收目标：`add4a7bc94fccf57e2979103686887d6920eec66`
- 验收分支：`acceptance/pr227-final-paid-20260803-r4`
- 授权提交：`b756c5510ff7da25b1efc0ef32e5db4389189806`
- 总调用上限：4
- 恢复调用上限：0
- 费用异常阈值：0.25 美元

验收在实时目录规范化阶段 fail-closed，任何 GPT、Claude 或专家聊天调用均未发生。结构化错误为：

```text
V5_PRODUCTION_RUNTIME_FAILED
CatalogViewError: duplicate exact catalog endpoint: ('google/gemma-4-31b-it', 'together')
```

因此本次验收模型调用为 0，模型费用为 0 美元，不能作为生产 PASS。

## 根因

实时目录在同一次端点查询中可能返回相同 `(model, provider)` 键的重复行。原实现无论重复行的规范化内容是否完全相同，都会直接拒绝。该策略能够防止冲突覆盖，但把上游的无害完全重复也误判为目录冲突。

## 修复策略

修复提交：`1042cc9f6929db1f015f723bce13ec57e35dcfef`。

- 对端点行先做确定性规范化和规范 JSON 序列化。
- 相同 `(model, provider)` 键且所有规范化字段完全一致时，只保留第一行。
- 相同键但价格、上下文、最大输出、参数能力或其他规范化字段存在任何差异时，继续 fail-closed，并报告 `conflicting duplicate exact catalog endpoint`。
- `compact_endpoint_catalog()` 与 `catalog_index()` 使用同一去重合同，避免目录生成与消费阶段规则漂移。
- 新增完全重复折叠和冲突重复拒绝回归测试。

## 零聊天实时验证

专用验证 Run：`30772976475`。

- Ruff：PASS。
- Python 全量编译：PASS。
- 完整单元回归：PASS。
- 八个随机种子的目录顺序压力：PASS。
- 仓库逐行审计：Critical 0、High 0、Medium 0。
- 实时目录源：`openrouter-live`。
- 符合硬约束的官方排序模型：2。
- 原始端点行：15。
- 规范化唯一端点：6。
- 目录 SHA-256：`54ff294d5db8b32d4dc00d0f15b142ba9e2ff009caf53d0930a0d4339c9855a1`。
- 聊天模型调用：0。
- 模型费用：0 美元。

验证 Artifact：

- Artifact ID：`8841117408`
- Artifact SHA-256：`a8228dab452bc3c837f807c90d68708af38c9cbbb841dfcfbf68a008a75efce9`

临时补丁、确定性重写脚本和一次性验证工作流已经自动删除。`main` 与 `production` 未移动。修复后的下一次真实生产验收仍必须获得完整调用账本、主 Artifact、独立复算、最终状态和最终 Attestation 全部 PASS。
