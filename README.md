# DSH 元数据服务与百炼 Python Harness 示例

## 需求与技术架构

- [需求说明](docs/requirements.md)：功能范围、分析流程、默认限制及验收标准。
- [技术架构](docs/architecture.md)：以 DSH 为唯一智能体运行时，说明应用服务、Metadata MCP、数据模型、API 与部署设计。

- [开发与运行公约](agent.md)：DSH 分析边界、插件与 Skills 优先、代码和设计文档同步提交。
- [实施记录](docs/implementation.md)：已实现能力、验证范围和待办。

## 元数据服务（本机测试版）

全部分析由 DSH 执行。Python 提供接口与任务管理；MCP 工具由 `dsh/plugins/metadata_tools/` 插件实现和加载，分析方法使用 `dsh/skills/` 原生 Skills。

MySQL 数据源点击“创建分析”后，可按表名片段（模糊匹配，如 `order`）或正则（如 `^(sys_|biz_)`）筛选，再勾选表/视图。支持分页、勾选全部匹配项、跨条件保留选择和移除已选项；每次必须显式选择 1–500 张。列表可浏览超过 500 张表的数据库，筛选后显示匹配数、全库数及已选数。DSH 仅处理选中范围，范围外关联/血缘引用记录告警，不自动扩大任务。REST 调用 `POST /api/v1/analyses` 也须传非空 `tables`，如 `["example_table"]`；Excel 不需要此参数。

开发调试（Linux/macOS Bash、Windows Git Bash；WSL 需要在 WSL 内安装依赖）：

```bash
# 首次配置：复制 .env.example 为 .env 并填写百炼 Key，不要覆盖已有文件。
bash ./start-dev.sh
```

脚本同步 Python 依赖，缺少前端依赖时执行 `npm ci`，同时启动 API、Worker 与 Vite。打开 `http://127.0.0.1:5173`，REST 文档为 `http://127.0.0.1:8000/docs`。前端支持热更新，API 支持自动重载；插件、Skills 和 Worker 代码修改后重启脚本。按 `Ctrl+C` 关闭进程树。

端口可在 `.env` 中设置 `METADATA_API_PORT` / `METADATA_UI_PORT`，或在启动环境覆盖。日志位于数据目录的 `dev-logs/`，便于查看 DSH 任务错误。下列命令使用默认本地数据目录：

```bash
tail -f data/dev-logs/api.log data/dev-logs/worker.log data/dev-logs/frontend.log
```

任务列表中的“过程日志”可查看 DSH 初始化、模型消息、工具调用及耗时、结果保存、心跳和失败事件，支持筛选、跟随和下载。运行中每 2 秒更新；服务每任务保留最近 5000 条，面板最多显示最近 2000 条。Bash 开发终端同时输出这些安全事件，单独启动 Worker 时也会输出到 stderr；任务目录的 `runner.log` 可用于本机诊断。过程日志不包含模型上下文、工具参数、SQL 或原始样本。

`DSH_MAX_TOKENS` 表示模型输出达到上限。新版交付工具提供完整 Schema，Skills 优先分批合并提交，避免反复重写大结果；任务不会自动续写或误报成功，可在升级后显式重试。

升级过程日志功能后，需要重新启动开发脚本和 Worker；已经启动的旧执行不能补录未采集的历史事件。

生产构建的本机测试方式：

```powershell
uv sync
# 尚未配置时：复制 .env.example 为 .env，填写百炼 Key；不要覆盖已有配置。
cd frontend
npm ci
npm run build
cd ..
./start-service.ps1
```

打开 `http://127.0.0.1:8000`，REST 文档为 `/docs`。API 与 Worker 也可分别启动：

```powershell
uv run uvicorn metadata_service.api:create_app --factory --host 127.0.0.1 --port 8000
uv run python -m metadata_service.worker
```

服务自动加载根目录 `.env`，兼容 `dsh-home-1/.env`；启动环境优先。运行数据位于忽略的 `data/`。未配置主密钥时生成本地 `data/.master.key`，请与数据一起妥善备份。样本默认关闭。

```powershell
uv run pytest --basetemp=data/test-run-local -q
# 显式调用真实 DSH/百炼，产生模型调用费用；只使用合成 Excel。
uv run python scripts/smoke_service.py
uv run python scripts/smoke_lineage.py
```

完整设计尚未全部验收，当前限制见实施记录。现有 CLI 示例使用方式如下。

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
