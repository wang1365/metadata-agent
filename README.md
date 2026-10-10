# 百炼 Python Harness 示例

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
