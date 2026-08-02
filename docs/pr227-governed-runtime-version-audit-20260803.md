# PR #227 治理运行时版本合同审计

日期：2026-08-03

## 发现

正式生产证据构建器 `v5_run_evidence.py` 使用 `v5-gpt-claude-runtime-1`，但完整性审计器原先独立硬编码 `v5-native-runtime-1`。因此真实执行即使成功，规范化后的 `production-runtime.json` 与 `expert-team-result.json` 仍会在完整性审计阶段被错误拒绝。

## 修复

修复提交：`2140ab4807d3412e1280acc8add9c2c189ba44a3`。

- 完整性审计器不再维护独立运行时版本字符串。
- 审计器直接引用生产证据构建器的唯一 `RUNTIME_VERSION` 常量。
- 原生审计夹具同步使用当前 GPT → Claude → GPT 治理运行时版本。
- 票据准入层的 `v5-native-runtime-1` 仍只表示准入控制面版本，不再与生产证据运行时混淆。

## 验证

- Ruff：PASS。
- Python 全量编译：PASS。
- 原生审计、生产切换与证据包聚焦回归：PASS。
- 完整单元回归：PASS。
- 8 个随机种子的运行时证据顺序压力：PASS。
- Critical：0。
- High：0。
- Medium：0。
- 模型调用：0。
- 模型费用：0 美元。

`main` 与 `production` 未移动。真实付费生产资格仍要求正式票据入口、主 Artifact、独立复算、最终状态和最终 Attestation 全部 PASS。
