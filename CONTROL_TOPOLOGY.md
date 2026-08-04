# 专家中心控制拓扑

本文件是 `governance-compatibility.json` 的人类可读说明。出现冲突时，以机器合同和 `CONSTITUTION.md` 的更严格规则为准。

## 唯一外部入口

网页 GPTs 不得直接控制本仓库。唯一外部控制者、任务派发者和证据中继是：

```text
a15280020511/decision-system-governance
```

本仓库的专家 Issue 前缀只供治理仓库创建子任务使用，不是 GPTs 的直接入口。

## 隔离边界

- 不直接调用情报中心或计算中心；
- 不读取情报中心、计算中心或治理仓库的运行时 Artifact；
- 不使用跨仓库 `repository_dispatch`；
- 不共享业务 Secret 或工作流；
- 不访问 Hugging Face 私有 Dataset；
- 不配置 `HF_TOKEN`；
- 不访问计算中心基准库；
- 专家执行继续禁止工具和网络。

## 正确链路

```text
网页 GPTs
→ Decision System Governance
→ Expert Assessment Center 子 Issue
→ 专家中心执行并生成 Manifest、Artifact、可信终态
→ Decision System Governance 核验
→ 网页 GPTs
```

任何需要情报、计算和专家协同的任务，都必须由治理仓库分阶段派发。三个业务中心不得彼此发送任务或互读结果。
