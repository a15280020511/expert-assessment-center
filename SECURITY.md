# 安全边界

- 不提交 Secret 值、令牌、私钥或个人数据。
- 不允许另一个业务中心读取本仓库运行目录、Environment Secret 或 Artifact。
- 不使用 Git submodule、跨仓库运行时 Artifact 下载或中心间 repository_dispatch。
- 公共合同只能使用冻结副本、版本和哈希；业务运行时不得跨仓库读取治理文件。
