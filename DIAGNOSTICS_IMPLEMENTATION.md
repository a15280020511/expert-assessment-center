# 专家中心统一日志与自动诊断

## 边界

本改造不改变专家执行的禁止工具、禁止联网原则，也不向专家模型暴露 GitHub、日志、Artifact 或 Secret。`Workflow Diagnostic Sweep` 是独立的 GitHub Actions 运维工作流，只读取本仓库运行元数据和失败日志。

## 自动诊断

每 30 分钟扫描近期 Run，统一记录 Run ID、Attempt、Commit SHA、Workflow、Job、Step、触发者、耗时和结论。对失败、取消、超时和启动失败运行，额外下载完整日志并执行凭据脱敏、关键错误抽取、失败 Step 定位、错误分类、失败指纹和有限重试建议。

诊断类别覆盖权限或 Secret、限流、超时、网络、依赖、Schema、Artifact、模型 Provider、测试断言、资源耗尽、运行时异常和未知错误。

## 与专家业务诊断的关系

已有 `diagnostic-summary.json`、`execution-diagnosis.json`、`execution-audit.json`、`request-audit.json`、`call-ledger.json`、`execution-console.log` 和 `artifact-manifest.json` 继续作为专家执行的权威证据。统一扫描器负责 GitHub Actions 外层诊断，不替代模型调用账本、专家席位、单次红队、综合节点、费用、报告发布和业务质量结论。

## 读取顺序

```text
summary.md
→ diagnostic-index.json
→ runs/<run_id>/failure.json
→ runs/<run_id>/key-lines.jsonl
→ runs/<run_id>/jobs.jsonl
→ runs/<run_id>/redacted-logs/
→ manifest.json
→ GitHub Artifact Attestation
```

禁止记录完整环境变量、Authorization、Cookie、API Key、Token、模型密钥、SendKey、提示词正文、用户敏感数据或模型原始私密输入。Pull Request 仅验证诊断器；定时和手动正式运行才生成 Artifact Attestation。
