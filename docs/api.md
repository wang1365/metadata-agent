# REST API 接入说明

本文只记录当前已实现接口。需求基线见[需求](requirements.md)，组件职责见[架构](architecture.md)，验证范围见[实施记录](implementation.md)。全部分析由DSH完成；调用查询接口不会自动启动新分析。

## 文档入口与部署

| 入口 | 地址 |
| --- | --- |
| Swagger UI，可查看字段并执行请求 | `/api/v1/docs` |
| OpenAPI 3.1 JSON，可导入Postman/生成客户端 | `/api/v1/openapi.json` |
| 兼容旧入口 | `/docs`、`/redoc`跳转Swagger；`/openapi.json`跳转新JSON地址 |

开发模式访问 `http://127.0.0.1:5173/api/v1/docs`，Vite代理文档、规范及静态资源到API端口；直接访问后端则为 `http://127.0.0.1:8000/api/v1/docs`。自定义端口遵循 `METADATA_API_PORT` / `METADATA_UI_PORT`。单机服务模式前后端同源。Swagger静态资源随Python依赖安装，本地提供，不依赖CDN或前端构建；没有Worker/模型连接也能打开文档。

当前服务是本地单用户版本，**没有API Key/OAuth鉴权或租户隔离**，默认监听127.0.0.1。接入其他服务时使用受控网络和网关。未默认开启CORS；浏览器客户端优先同源反向代理，服务间HTTP调用不受浏览器CORS约束。

## 通用约定

- 业务接口统一前缀 `/api/v1`，JSON编码UTF-8，上传使用`multipart/form-data`字段`file`。
- `id`是不透明字符串；名称和查询参数需要URL编码，不依赖ID的长度或内部数据库格式。
- 时间是带时区的ISO 8601 UTC，例如`2026-01-01T00:00:00+00:00`；北京时间显示由客户端转换。
- 没有统一成功包装：列表可能直接返回数组，分页返回具名列表和`total`，创建任务返回`task_id`。以各操作的OpenAPI返回结构为准。
- POST创建数据源、分析和问题没有通用幂等键；超时后不要盲目重发。确认接口对已确认任务可重复调用；重试接口明确创建新任务。
- 以下curl为Bash写法，仅含占位符。Windows可在Git Bash运行；原生PowerShell使用`curl.exe`并按其引号规则传JSON。

```bash
BASE='http://127.0.0.1:8000/api/v1'
curl --fail-with-body "$BASE/health"
```

`health`返回status、runtime、worker、model、queues及display_timezone。它只验证API可响应，不验证Worker存活、模型可用或数据库连接。

## Excel 接入流程

1. 上传文件，响应201，保存数据源`id`。支持xlsx/xls，单文件默认最大20MB；上传只检查工作簿。

```bash
curl --fail-with-body -X POST "$BASE/sources/excel" \
  -F 'file=@/path/to/dictionary.xlsx'
```

2. 可先预览原始Sheet/区域；返回网格和样式不等于已识别数据字典。

```bash
curl --fail-with-body --get "$BASE/sources/<source_id>/preview" \
  --data-urlencode 'name=<sheet_name>' --data-urlencode 'region=A1:J20' \
  --data-urlencode 'formatted=true'
```

3. 创建任务，响应202及`{"task_id":"<job_id>"}`。Excel不传`tables`或传空列表。

```bash
curl --fail-with-body -X POST "$BASE/analyses" \
  -H 'Content-Type: application/json' \
  -d '{"source_id":"<source_id>","allow_samples":false}'
curl --fail-with-body "$BASE/analyses/<job_id>"
```

4. 轮询到`awaiting_confirmation`，读取`GET /analyses/<job_id>/preview`，取得`version/findings/evidence`。需要修正时，保存完整的`{"version":<当前版本>,"findings":<完整修正结果>}`为本地文件，保留合法证据引用。`PUT /mapping`替换完整草稿而非局部补丁，成功后版本加1；版本过期或状态不可编辑返回409。

```bash
curl --fail-with-body "$BASE/analyses/<job_id>/preview"
curl --fail-with-body -X PUT "$BASE/analyses/<job_id>/mapping" \
  -H 'Content-Type: application/json' --data-binary @corrected-mapping.json
curl --fail-with-body -X POST "$BASE/analyses/<job_id>/confirm"
```

5. 确认后继续轮询；成功发布后从任务` snapshot_id `读取快照。确认不是同步完成分析。

## MySQL 接入流程

创建连接只保存配置，密码加密存储且不出现在公开响应；建议独立只读账号。第一版验证目标为MySQL 8.0/8.4，其他版本连接测试可能返回告警。

```bash
curl --fail-with-body -X POST "$BASE/sources/mysql" \
  -H 'Content-Type: application/json' \
  -d '{"name":"<source_name>","host":"db.example.invalid","port":3306,"username":"<readonly_user>","password":"<database_password>","database":"<database_name>"}'
curl --fail-with-body -X POST "$BASE/sources/<source_id>/test"
curl --fail-with-body --get "$BASE/sources/<source_id>/objects" \
  --data-urlencode 'query=<name_fragment>' --data-urlencode 'match=fuzzy' \
  --data-urlencode 'offset=0' --data-urlencode 'limit=100'
```

MySQL目录返回`objects:[{name,kind}],total,source_total`。`query`是大小写不敏感的包含匹配，`match=regex`使用正则搜索；先过滤再分页。`limit`默认100/最大500，`query`最多256字符，整个目录正则匹配预算200ms。Excel的同接口返回`objects`（Sheet目录），没有`total/source_total`。

显式选择1–500个唯一表/视图；可跨目录页累积名称，不能使用空范围代表全库。目录变更后无效选择会报错，需刷新目录。

```bash
curl --fail-with-body -X POST "$BASE/analyses" \
  -H 'Content-Type: application/json' \
  -d '{"source_id":"<source_id>","tables":["<table_name>","<view_name>"],"allow_samples":false}'
```

MySQL分析通常无需Excel草稿确认步骤。结构采集、关系理解及加工血缘由DSH运行时执行，Python接口层不实现推断。

## 异步任务、日志与取消

分析和问答共用`/analyses/{job_id}`任务查询接口，但由独立队列执行。`GET /analyses?queue=analysis|qa|all`可筛选任务列表（当前不分页）。建议每2秒轮询一次状态：

| 状态 | 客户端行为 |
| --- | --- |
| queued | 等待对应Worker领取 |
| running | 继续轮询，可增量获取日志 |
| awaiting_confirmation | Excel需读取/修正草稿并confirm；不是终态 |
| succeeded | 停止轮询；分析读取snapshot_id，问答读取conversation_id/answer_id |
| partial_success | 停止轮询；可读取snapshot_id，检查快照warnings及证据边界 |
| failed | 停止轮询，检查error及安全日志，可显式retry |
| cancelled | 停止轮询，可显式retry |

任务响应还包含stage、queries、sample_rows、cancel_requested等；queries为业务查询预算使用量，不是工具调用总数。完整可选字段见OpenAPI。

```bash
curl --fail-with-body --get "$BASE/analyses/<job_id>/logs" \
  --data-urlencode 'after=0' --data-urlencode 'limit=200'
curl --fail-with-body -X POST "$BASE/analyses/<job_id>/cancel"
curl --fail-with-body -X POST "$BASE/analyses/<job_id>/retry"
```

日志响应为`events,next_cursor,has_more,truncated,status`，下次把`next_cursor`作为`after`；`has_more=true`时继续读，`seq`只保证有序，不保证连续。每任务保留最近5000条，单页最大500。日志不返回SQL、原始样例、模型正文或凭据。取消运行中任务为异步请求，仍需轮询；retry仅接受failed/cancelled/partial_success，返回新task_id并保留范围、既有预算及原任务记录。服务重启中断执行会标记失败，不自动重跑模型调用。

## 查询快照、图、证据和审核

| 方法与路径（省略前缀） | 参数与主要响应 |
| --- | --- |
| GET `/snapshots` | 摘要数组：id/source_id/created/tables（对象数） |
| GET `/snapshots/{id}/tables` | query包含搜索、offset默认0、limit默认50/最大100；tables/total/warnings |
| GET `/snapshots/{id}/tables/{table_name}` | 完整名称精确匹配；返回字段、键、注释及evidence_ids |
| GET `/snapshots/{id}/graph` | kind=er/lineage，root可选；tables/edges/root/total_edges/edges_truncated，最多200条边 |
| GET `/snapshots/{id}/entity-graph` | origin/confidence/review可重复参数，root可选，limit默认及最大5000；nodes/relationships/root/total_relationships/returned_relationships/truncated |
| GET `/snapshots/{id}/evidence/{evidence_id}` | 返回该快照证据，内容随kind变化 |
| PATCH `/relationships/{relationship_id}?snapshot_id={id}` | JSON status=pending/confirmed/rejected，note可选；返回status |

力导图节点只含id/name/kind/comment/column_count；详细字段用精确详情接口读取。默认包含全部来源（constraint/dictionary/inferred）、全部置信度（high/medium/low）、pending和confirmed；显式加入rejected才包含驳回关系。省略筛选参数采用默认，传空值表示该维度不选任何项。先筛选、稳定排序再截断；未指定root保留全部节点，指定root保留筛选结果中的全部一跳节点。前端合并连线不改变原始relationships；该接口只提供已保存关联，不生成实体或分析结论。

```bash
curl --fail-with-body --get "$BASE/snapshots/<snapshot_id>/entity-graph" \
  --data-urlencode 'origin=constraint' --data-urlencode 'origin=inferred' \
  --data-urlencode 'review=pending' --data-urlencode 'review=confirmed' \
  --data-urlencode 'root=<table_name>'
curl --fail-with-body -X PATCH "$BASE/relationships/<relationship_id>?snapshot_id=<snapshot_id>" \
  -H 'Content-Type: application/json' -d '{"status":"confirmed","note":"<review_note>"}'
```

声明约束、字典声明、推断关系和加工血缘必须区分。关联方向不代表加工方向或已证明基数。复合关联的source_columns与target_columns按顺序对应；evidence_ids需在同一快照读取。人工审核不覆盖原来源及证据。

## 问答与历史

```bash
curl --fail-with-body -X POST "$BASE/snapshots/<snapshot_id>/questions" \
  -H 'Content-Type: application/json' -d '{"question":"<metadata_question>"}'
```

响应202：`{"task_id":"<job_id>","conversation_id":"<conversation_id>"}`。省略conversation_id创建会话，继续对话时传入同快照会话ID；问题长度1–4000。问答只读取已发布快照，不新增数据库业务查询，同一会话串行。

轮询`GET /analyses/{job_id}`后使用 `GET /conversations/{conversation_id}`读取`answers`和`tasks`；答案包含question/answer/evidence_ids，失败或排队的无答案任务也在tasks中。没有单独的`GET /answers/{id}`接口。`GET /conversations?snapshot_id={id}&offset=0&limit=20`查询历史，snapshot_id可省略，limit最大100，按最近提问倒序。

## 结构和样例预览、导出

`GET /sources/{id}`查看公开配置。MySQL原始结构通过`GET /sources/{id}/preview?name={完整表名}`获取，返回tables/foreign_keys/warnings；原始结构是证据，不是DSH推断结果。Excel `formatted=false`返回sheet/region/cells，`formatted=true`返回网格样式（行列尺寸、合并及单元格文本），最多2000单元格，不执行公式。

```bash
curl --fail-with-body -X POST "$BASE/sources/<source_id>/sample" \
  -H 'Content-Type: application/json' \
  -d '{"table":"<table_name>","columns":["<column_name>"],"limit":10}'
curl --fail-with-body "$BASE/snapshots/<snapshot_id>/exports/json" -o metadata.json
curl --fail-with-body "$BASE/snapshots/<snapshot_id>/exports/csv" -o fields.csv
```

样例接口必须显式传columns，1–4096个唯一真实字段，limit默认10/最大20，每值最多200字符，非空值统一字符串、空值null。响应含rows/method/max_rows/max_value_chars/sent_to_model=false；它读取首批行，不是随机样本，不发送给DSH或持久化到快照。

JSON导出完整快照（findings/evidence/reviews等），CSV为表字段、UTF-8 BOM；当前没有关系CSV或服务端图像导出接口。ER/血缘图像由前端导出，力导图首版仅PNG、最长边4096像素，“完整图”仍受已加载关系范围限制。

## 错误与限制

| HTTP | 形状/例子 | 处理方式 |
| --- | --- | --- |
| 400 | `{"detail":"TABLE_SELECTION_REQUIRED"}` | 修正业务参数；也可能为受控数据库/预览读取失败 |
| 404 | `{"detail":"NOT_FOUND"}`或TABLE_NOT_FOUND | 检查ID、快照归属和精确对象名称 |
| 409 | `{"detail":"STALE_OR_NOT_EDITABLE_MAPPING"}` | 重新读取版本/状态；确认和重试也可能状态冲突 |
| 413 | `{"detail":"FILE_LIMIT_EXCEEDED"}` | 缩小文件或拆分 |
| 422 | `{"detail":[{"loc":["body","source_id"],"type":"missing","msg":"Field required"}]}` | 修正请求类型/必填项；不包含输入值 |
| 500 | 非业务异常 | 保存请求时间/任务ID，检查服务端诊断；不要直接重试有副作用POST |

参数边界既有手动业务检查（400），也有Pydantic/Query检查（422），调用方应兼容两者。常见业务码还包括INVALID_PAGINATION、INVALID_REGEX、REGEX_TIMEOUT、UNKNOWN_TABLE_SELECTION、UNKNOWN_GRAPH_ROOT、INVALID_ENTITY_GRAPH_FILTER、CONVERSATION_SNAPSHOT_MISMATCH。DSH失败码在任务error或过程事件中，不等同于提交请求的HTTP失败。

单次分析最多500对象/20000字段、默认30次业务查询；原始样本发送默认关闭。分析与问答各默认1个并发槽，可分别配置1–8，剩余任务排队。当前没有删除数据源、任务清理API、全量会话分页展开或通用幂等请求机制；不要将架构中的待实现接口当作已可调用能力。

## 验证

`python -m pytest tests/test_api_docs.py -q`覆盖文档、离线静态资源、旧链接、反向代理root_path、29项操作Schema及422脱敏。先安装前端依赖后，`python -m scripts.smoke_api_docs`启动隔离API与Vite，以随机自定义端口通过真实HTTP验证侧栏目标、旧地址、OpenAPI及本地资源，然后回收进程；不运行DSH、不连接业务库。该检查验证HTTP链路，不声称完成浏览器视觉验收。
