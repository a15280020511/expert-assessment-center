# Expert Assessment Center

本仓库由 `a15280020511/test` 在固定提交 `abac3d776340c8c162b8fc0c670167fde94f3baa` 拆分迁入。

## 职责

固定三专家加一裁判、红队反证、战略研判、模型选择审计、费用账本与可审计专家报告。

## 机器权威目录

`open-model-market/expert-team-capabilities.json`

## 隔离边界

- 本仓库只运行本中心任务。
- GPTs 是三个业务中心之间唯一的控制与证据中继。
- 禁止中心间直接调用、运行时导入、Artifact 互取和共享业务 Secret。
- 原业务目录 `open-model-market/` 暂时保留，避免迁移与路径重构同时发生。
- 旧仓库在验收完成前保留为治理记录和回滚源，本次不删除旧内容。

## 迁移证据

查看 `MIGRATION_MANIFEST.json`、`MIGRATION.md` 和 `governance-compatibility.json`。

## V3 integrity controls

A model reliability ledger schema and fail-closed report integrity gate now require 3/3 usable experts, a non-empty judge report, at least four call-ledger records, and verified Artifact SHA values.
