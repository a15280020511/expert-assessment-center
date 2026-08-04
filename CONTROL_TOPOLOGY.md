# 专家中心控制拓扑

本文件是 `governance-compatibility.json` 的人类可读说明。出现冲突时，以机器合同和 `CONSTITUTION.md` 的更严格规则为准。

## 唯一外部入口

网页 GPTs 不得直接控制本仓库。唯一外部控制者、任务派发者和证据中继是：

```text
a15280020511/decision-system-governance
```

本仓库的专家 Issue 前缀只供治理仓库创建子任务使用，不是 GPTs 的直接入口。

## 基准库使用

专家中心可以使用私有数值基准库 `compute-numeric-baselines`，但使用方式固定为：

```text
Hugging Face 私有基准库
→ 治理仓库选择任务相关表、版本、列和范围
→ 治理仓库生成任务级数值证据
→ 写入专家任务输入
→ 专家中心在无工具、无网络条件下研判
```

这不授予专家中心数据库访问权。专家中心不得配置 `HF_TOKEN`，不得直接访问 Hugging Face，不得浏览完整基准库，也不得自行下载跨仓库 Artifact。

## 隔离边界

- 不直接调用情报中心或计算中心；
- 不读取情报中心、计算中心或治理仓库的运行时 Artifact；
- 不使用跨仓库 `repository_dispatch`；
- 不共享业务 Secret 或工作流；
- 不直接访问 Hugging Face 私有 Dataset；
- 不配置 `HF_TOKEN`；
- 允许使用治理仓库随任务提供的基准库数值证据；
- 专家执行继续禁止工具和网络。

## 正确链路

```text
网页 GPTs
→ Decision System Governance
→ 治理仓库准备任务级情报、计算结果和数值基准证据
→ Expert Assessment Center 子 Issue
→ 专家中心执行并生成 Manifest、Artifact、可信终态
→ Decision System Governance 核验
→ 网页 GPTs
```

任何需要情报、计算和专家协同的任务，都必须由治理仓库分阶段派发。三个业务中心不得彼此发送任务或互读结果。
