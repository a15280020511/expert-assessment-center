# V5 R8 付费工作流政策

## 当前状态

```text
旧V5付费工作流：已禁用
Issue自动触发付费：已禁用
定时V5付费运行：不存在
OpenRouter推理密钥：仅OPENROUTER_API_KEY
Management Key：不属于R8依赖
生产入口：仍为V3
```

以下旧工作流仅保留为不可执行的手动占位文件，不读取任何OpenRouter Secret，也不执行模型调用：

- `v5-live-benchmark.yml`
- `v5-live-benchmark-final.yml`
- `v5-low-cost-pilot.yml`
- `v5-micro-canary.yml`

R8未来如需进行真实三题匿名比较，必须在Issue #64中单独人工解锁，并新建经过审查的专用工作流。该工作流只能注入`OPENROUTER_API_KEY`，信用预检使用`v5_r8_single_key_preflight.py`，费用安全依赖API Key自身可报告额度和运行时硬成本上限，不读取账户管理接口。

任何付费工作流不得同时完成生产切换或V3删除；这两项必须使用后续独立证据门和独立PR。
