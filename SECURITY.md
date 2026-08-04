# 安全边界

- 不提交 Secret 值、令牌、私钥或个人数据。
- 网页 GPTs 不得直接控制本仓库；唯一外部控制者是 `a15280020511/decision-system-governance`。
- 不允许另一个业务中心读取本仓库运行目录、Environment Secret 或 Artifact。
- 不使用 Git submodule、跨仓库运行时 Artifact 下载或中心间 `repository_dispatch`。
- 不配置或使用 `HF_TOKEN`，不直接访问私有 Hugging Face Dataset。
- 可以使用治理仓库随当前任务提供的、固定版本且范围受限的数值基准证据；不得浏览完整基准库或自行提取数据。
- 不读取情报中心或计算中心 Artifact；跨中心任务只能由治理仓库分阶段派发并核验。
- 专家执行继续禁止工具、浏览器、插件、代码执行和网络。
- 公共合同只能使用冻结副本、版本和哈希；业务运行时不得跨仓库读取治理文件。
