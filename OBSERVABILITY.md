# 三中心日志与诊断契约

## 目标

在不破坏三个中心运行隔离的前提下，为每次任务建立可审计关联链：

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

本契约不引入共享运行时依赖。专家团、计算和 API 中心分别生成自己的诊断文件，只共享字段语义。

## 通用字段

每个结构化诊断文件至少包含：

- `schema_version`
- `created_at`
- `status`
- `run_identity`
- `stage_status`
- `primary_failure`
- 证据文件清单或 Manifest 合同
- `security.secret_values_included=false`

`run_identity` 只允许包含 repository、run_id、run_attempt、sha、workflow、job 和 issue_number。不得保存完整环境变量、Authorization、Cookie、API Key 或 Token。

## 专家团中心

权威诊断文件：

- `diagnostic-summary.json`
- `execution-diagnosis.json`
- `execution-audit.json`
- `request-audit.json`
- `call-ledger.json`
- `execution-console.log`
- `artifact-manifest.json`

诊断覆盖票据、路由、模型选择、专家席位、裁判、请求捕获、费用、报告发布和 Artifact 交付。

## 计算中心

权威诊断文件：

- `compute-diagnostics.json`
- `compute-error.json`
- `compute-audit.json`
- `compute-console.log`
- `artifact-manifest.json`

失败文件记录错误码、失败阶段、异常类型、消息、完整 traceback、task_id、operation、ticket SHA、运行身份、耗时和可重试性。

计算诊断不再把 Manifest 写成长期 `PENDING`。`write_manifest=DEFERRED_TO_DELIVERY_STAGE` 表示 Manifest 由控制台捕获之后的独立交付步骤生成；最终是否成功以 Workflow Step 和 `artifact-manifest.json` 为准。这样避免诊断文件和 Manifest 相互哈希造成循环依赖。

## API 接入中心

### 正式 `[api]` 任务

权威文件：

- `api-snapshot.json`
- `api-audit.json`
- `api-diagnostics.json`
- `api-console.log`
- `api-summary.md`
- `api-gateway-runtime.log`
- `api-gateway-container-inspect.json`
- `artifact-manifest.json`

每条请求记录 connector_id、连接器 SHA、白名单参数、HTTP 状态、业务状态、业务错误码、尝试次数、观测时间、非空数据判定和响应正文。

`API_COMPLETED`、`API_PARTIAL`、`API_BLOCKED`、`API_FAILED` 是业务状态。HTTP 200 或 Workflow success 不能单独替代业务成功。

### API中心验证任务

权威文件：

- `api-center-diagnostics.json`
- `api-center-audit.json`
- `api-center-unit-tests.log`
- `api-center-runtime.log`
- `api-center-container-inspect.json`
- `api-center-health.json`

无论健康检查成功或失败，都必须先捕获 `docker inspect` 和 `docker logs`，再判定 Step。

## Secret 与公开数据

- 正式 `[api]` 票据只接受公开、非个人数据；
- Secret 文件必须位于 Runner 临时目录，不得进入 Artifact；
- 只允许记录 Secret 环境变量名称，不记录值；
- 远程网关 URL 和认证 Token 不写入 Snapshot；
- 只记录安全响应头，不记录 Authorization、Cookie 或 Set-Cookie；
- 公开 Issue 回退正文不得包含个人、受监管或私密数据。

## 错误分类

- 输入或 Schema：不可原样重试；
- 权限、网关或 Secret：`BLOCKED`；
- 临时 I/O、平台、Provider 或上游超时：有限重试；
- HTTP 成功但业务状态失败：业务失败；
- HTTP 和业务成功但必要数据为空：空数据失败，除非票据显式允许空结果；
- 业务不变量失败：`FAIL`；
- 证据缺失：不得报告完整 `PASS`；
- 外部系统仅返回通用错误时：报告外部根因不可见。

## 保留与公开回退

Artifact 是原始证据，Issue 评论是长期公开回退。关键状态、错误码、Run URL、Artifact ID 和 SHA 发布到 Issue。只有符合公开、非个人数据合同的 API Snapshot 才允许分段发布。

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

只有完成中心业务状态和证据完整性检查，才可报告 `PASS`。
