# 三中心日志与诊断契约

## 目标

在不破坏中心隔离的前提下，为每次任务建立可审计关联链：

```text
center
→ task_id
→ Issue
→ Run ID / attempt
→ Job ID
→ head SHA
→ stage
→ result or primary_failure
→ Artifact
→ manifest and SHA-256
```

本契约不引入共享运行时依赖。各中心分别生成诊断文件，只共享字段语义。

## 通用字段

结构化诊断至少包含 `schema_version`、`created_at`、`status`、`run_identity`、`stage_status`、`primary_failure` 与证据清单或 Manifest 合同，并显式记录 `security.secret_values_included: false`。

`run_identity` 只允许包含 repository、run_id、run_attempt、sha、workflow、job 和 issue_number。不得保存完整环境变量、Authorization、Cookie、API Key 或 Token 的值。

## 专家团中心

权威诊断文件包括：

- `diagnostic-summary.json`
- `execution-diagnosis.json`
- `execution-audit.json`
- `request-audit.json`
- `call-ledger.json`
- `execution-console.log`
- `artifact-manifest.json`

诊断覆盖任务票据、动态专家计划、模型调用、OpenRouter Provider 实际返回、恢复、费用、报告发布和 Artifact 交付。Provider 只作为观测信息，不得据此实施 Provider 资格门禁。

## Secret 与公开数据

- Secret 文件必须位于 Runner 临时目录，不得进入 Artifact；
- 只记录 Secret 环境变量名称，不记录值；
- 远程网关地址和认证凭据不写入 Snapshot；
- 不记录 Authorization、Cookie 或 Set-Cookie；
- 公开 Issue 回退正文不得包含个人、受监管或私密数据。

## 错误分类

- 输入或 Schema 无法解析：结构错误；
- 权限、网关或 Secret 缺失：`BLOCKED`；
- 临时 I/O、平台、Provider 或上游超时：有限恢复；
- HTTP 成功但业务状态失败：业务失败；
- 必要数据为空：空数据失败，除非任务显式允许；
- 外部系统仅返回通用错误时：报告外部根因不可见。

Canary、费用阈值、Provider、模型公司、TopN 排名或 Artifact 独立复核结果均可记录为诊断信息，但不得作为专家模型资格门禁。

## 运营者读取顺序

```text
Issue comments
→ Run
→ Jobs and Steps
→ full Job logs
→ Artifact metadata
→ structured diagnostic files
→ manifest and hashes
```
