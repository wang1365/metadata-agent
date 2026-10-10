# 百炼 Python Harness 示例

## 需求与技术架构

- [需求说明](docs/requirements.md)：功能范围、分析流程、默认限制及验收标准。
- [技术架构](docs/architecture.md)：以 DSH 为唯一智能体运行时，说明应用服务、Metadata MCP、数据模型、API 与部署设计。

以上文档是待实施的第一版设计。当前仓库提供的 CLI 示例使用方式如下。

## 运行现有示例

使用完整 `sdk` profile。百炼路由在 `dsh-home-1/settings.yaml`；MCP 和自定义插件在 `dsh-home-1/profiles/sdk/cordis.patch.yml`。

API Key 使用启动环境中的 `BAILIAN_API_KEY`，或 `dsh-home-1/.env` 中的同名变量，环境变量优先。无需设置 `DEEPSEEK_API_KEY` 或 `DEEPSEEK_BASE_URL`。

```powershell
uv sync
Copy-Item dsh-home-1/.env.example dsh-home-1/.env
# 编辑 dsh-home-1/.env，填写百炼密钥与数据库连接信息；已有 .env 时不要覆盖。
./start.ps1 --check
./start.ps1 --smoke
./start.ps1
```

`--check` 仅验证初始化；`--smoke` 验证一次模型调用；无参数时在同一会话执行原有的两次只读数据库查询。可用 `--prompt "你的任务"` 或 `--model "百炼实际可用的模型ID"` 覆盖默认值。

首次配置请复制 `.env.example` 后填写百炼密钥与数据库连接信息。`dsh-home-1/.env` 已被 Git 忽略；实际密钥和密码不会提交到仓库。

`sdk-minimal` 下的补丁不会应用到 `sdk`。自定义插件的日志应写入 stderr（例如 `console.error`），stdout 由 SDK 的 JSON-RPC 协议独占。

`sdk` 补丁保留 `compression: none`，兼容当前 home 中原有的未压缩 JSONL 会话；默认 zstd 编码不能与这些会话混用。
