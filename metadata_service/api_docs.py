"""API documentation metadata and local UI assets; no analysis or task execution."""

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from swagger_ui_bundle import swagger_ui_path

from .models import Findings, LineageEdge, Relationship, Table

PREFIX = "/api/v1"
INTRODUCTION = """
面向上层系统的元数据 REST API。DSH 负责全部分析；本服务负责接口、异步任务及结果查询。

**接入流程**
1. Excel：上传文件 → 创建分析 → 轮询至 awaiting_confirmation → 读取/修正草稿 → confirm → 继续轮询。
2. MySQL：创建数据源 → 测试连接 → 查询对象目录 → 显式选择 tables → 创建分析 → 轮询。
3. 使用任务的 snapshot_id 查询表、ER/血缘、实体关系图和证据，或导出 JSON/CSV。
4. 对快照提交问题 → 轮询独立问答任务 → 使用 conversation_id 获取答案及历史。

**约定**：业务接口前缀 /api/v1；JSON 使用 UTF-8；上传使用 multipart/form-data；
任务返回 202 仅表示入队，须轮询 GET /analyses/{job_id}。
终态为 succeeded、partial_success、failed、cancelled；awaiting_confirmation 不是终态。
建议每2秒轮询，问答与分析分别排队；同一会话问答串行。
时间是含时区的 ISO 8601 UTC，客户端按需要转换。

**错误**：400 业务参数错误、404 不存在、409 状态/版本冲突、413 文件超限、422 请求结构错误。
统一使用 detail 字段；422 detail 为 loc/type/msg 列表，不回显原始输入。
POST 不承诺通用幂等键；网络超时后避免盲目重复创建任务。

**部署**：当前为本地单用户服务，未实现 API Key/OAuth 鉴权。默认监听127.0.0.1，
上层跨域客户端需通过同源反向代理或自行配置网关，不默认开放跨域。
Swagger JS/CSS由本服务提供，不依赖CDN；可下载同源 openapi.json 导入Postman或生成客户端。
完整接入说明和curl示例见仓库 docs/api.md。
"""
TAGS = [
    {"name": "系统", "description": "服务健康与运行配置；不代表Worker或模型健康检查。"},
    {"name": "数据源", "description": "Excel上传、MySQL连接及显式结构/样例预览；预览不启动DSH。"},
    {"name": "任务", "description": "分析/问答任务查询、日志、人工确认、取消及显式重试。"},
    {"name": "快照", "description": "读取已发布结果；不自动触发重新分析。"},
    {"name": "审核", "description": "保留来源和证据，逐条确认/驳回关系。"},
    {"name": "问答", "description": "绑定快照的异步问答及持久化会话历史。"},
    {"name": "导出", "description": "JSON完整结果及表字段CSV；图像由前端导出。"},
]


class ErrorResponse(BaseModel):
    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int]
    type: str
    msg: str


class ValidationErrorResponse(BaseModel):
    detail: list[ValidationIssue]


class PublicSource(BaseModel):
    id: str
    created: str
    type: str = Field(description="excel或mysql")
    name: str
    config: dict | None = Field(default=None, description="MySQL host/port/username/database；不含密码")
    sheets: list[dict] | None = Field(default=None, description="Excel Sheet的name/rows/columns")
    size: int | None = Field(default=None, description="上传文件字节数")


class TaskAccepted(BaseModel):
    task_id: str = Field(description="轮询 /analyses/{job_id} 时使用")


class HealthResponse(BaseModel):
    status: str
    runtime: str
    worker: str
    model: str
    queues: dict[str, int]
    display_timezone: str


class ConnectionResponse(BaseModel):
    connected: bool
    version: str
    warnings: list[str]


class ObjectPage(BaseModel):
    objects: list[dict]
    total: int | None = Field(default=None, description="MySQL过滤后数量；Excel不提供")
    source_total: int | None = Field(default=None, description="MySQL全目录数量；Excel不提供")


class VersionResponse(BaseModel):
    version: int


class CancelResponse(TaskAccepted):
    status: str
    cancel_requested: bool


class ReviewResponse(BaseModel):
    status: str


class QuestionAccepted(TaskAccepted):
    conversation_id: str = Field(description="读取答案/历史及继续提问时使用")


class PublicJob(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    created: str
    source_id: str | None
    status: str = Field(description="queued/running/awaiting_confirmation/succeeded/partial_success/failed/cancelled")
    stage: str
    snapshot_id: str | None = Field(default=None, description="分析发布后保存的快照标识")
    bound_snapshot_id: str | None = Field(default=None, description="问答绑定的快照")
    conversation_id: str | None = None
    answer_id: str | None = None
    question: str | None = None
    error: str | None = Field(default=None, description="失败时的安全错误码")
    queries: int
    sample_rows: int
    cancel_requested: bool
    queue: str | None = Field(default=None, description="列表响应为analysis或qa；旧任务详情可能不含此字段")


class SnapshotSummary(BaseModel):
    id: str
    source_id: str
    created: str
    tables: int


class TablePage(BaseModel):
    tables: list[Table]
    total: int
    warnings: list[str]


class GraphResponse(BaseModel):
    tables: list[Table]
    edges: list[Relationship | LineageEdge]
    root: str | None
    total_edges: int
    edges_truncated: bool


class EntityNode(BaseModel):
    id: str
    name: str
    kind: str
    comment: str
    column_count: int


class EntityGraphResponse(BaseModel):
    nodes: list[EntityNode]
    relationships: list[Relationship]
    root: str | None
    total_relationships: int
    returned_relationships: int
    truncated: bool


class DraftResponse(BaseModel):
    version: int
    findings: Findings
    evidence: dict


class TaskLogPage(BaseModel):
    events: list[dict] = Field(description="seq/job_id/time/type/level/message及可选工具名、耗时、错误码")
    next_cursor: int
    has_more: bool
    truncated: bool
    status: str


class SampleResponse(BaseModel):
    rows: list[dict[str, str | None]]
    method: str
    max_rows: int
    max_value_chars: int
    sent_to_model: bool


class ConversationSummary(BaseModel):
    id: str
    created: str
    snapshot_id: str
    title: str
    task_count: int
    status: str
    updated: str


class ConversationPage(BaseModel):
    conversations: list[ConversationSummary]
    total: int


class AnswerResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    created: str
    conversation_id: str
    question: str
    answer: str
    evidence_ids: list[str]


class ConversationResponse(BaseModel):
    id: str
    created: str
    snapshot_id: str
    answers: list[AnswerResponse]
    tasks: list[PublicJob]


# These models describe responses only, preserving existing wire payloads.
OPERATIONS = {
    "health": ("系统", "查询服务健康与队列配置", "不探测DSH、模型或数据库连通性。", HealthResponse),
    "sources": ("数据源", "列出数据源", "不返回密码或服务器文件路径。", list[PublicSource]),
    "upload": (
        "数据源",
        "上传Excel数据字典",
        "multipart字段file；支持xlsx/xls，最大20MB。这里只检查工作簿，不运行分析。",
        PublicSource,
    ),
    "mysql_source": ("数据源", "创建MySQL数据源", "加密保存密码，创建本身不验证连接。", PublicSource),
    "test_connection": ("数据源", "测试MySQL只读连接", "检查连接与读取权限；Excel数据源不适用。", ConnectionResponse),
    "objects": (
        "数据源",
        "查询候选表/视图或Sheet",
        "MySQL先过滤再分页：match=fuzzy/regex，query最长256字符，正则预算200ms；offset≥0，limit默认100/最大500。MySQL返回objects/total/source_total，Excel只返回objects。",
        ObjectPage,
    ),
    "source_detail": ("数据源", "查看数据源详情", "隐藏密码及服务器文件路径。", PublicSource),
    "source_preview": (
        "数据源",
        "预览原始结构或Excel区域",
        "MySQL的name必须为精确表/视图名；Excel的name为Sheet、空值选首个，region默认A1:J20/最多2000单元格；formatted=true附带网格/合并/样式。返回结构取决于数据源，不运行DSH。",
        dict,
    ),
    "source_sample": (
        "数据源",
        "显式读取MySQL样例",
        "columns默认由客户端全选，接口必须传入；1–4096个真实唯一字段，limit默认10/最大20，值截断200字符；首批行非随机，sent_to_model=false，不保存到快照。",
        SampleResponse,
    ),
    "start_analysis": (
        "任务",
        "创建异步分析任务",
        "MySQL必须显式选择1–500张唯一真实表/视图；Excel不传tables。allow_samples默认false。202表示入队，读取返回task_id后轮询。",
        TaskAccepted,
    ),
    "jobs": (
        "任务",
        "列出分析和问答任务",
        "queue=all/analysis/qa，默认all；返回列表目前不分页，隐藏草稿及原始证据。",
        list[PublicJob],
    ),
    "job": (
        "任务",
        "查询任务状态与结果标识",
        "每2秒轮询；awaiting_confirmation需人工确认，终态停止轮询。错误码见error，分析用snapshot_id，问答用conversation_id/answer_id读取结果。",
        PublicJob,
    ),
    "task_logs": (
        "任务",
        "增量读取安全过程日志",
        "after默认0，limit默认200/最大500；按next_cursor续读，has_more为true时继续翻页。每任务保留5000条；不含模型正文、SQL或样例。",
        TaskLogPage,
    ),
    "preview": (
        "任务",
        "读取Excel分析草稿与版本",
        "awaiting_confirmation时读取；使用version执行乐观并发修正。草稿可包含尚未确认的分析结果。",
        DraftResponse,
    ),
    "mapping": (
        "任务",
        "修正待确认草稿",
        "必须处于awaiting_confirmation且version匹配；提交完整findings，保留合法证据引用。成功version加1；冲突409。",
        VersionResponse,
    ),
    "confirm": (
        "任务",
        "确认草稿并继续分析",
        "首次确认要求非空表且处于awaiting_confirmation；已确认请求可重复，不重复派发。",
        TaskAccepted,
    ),
    "cancel": (
        "任务",
        "请求取消任务",
        "排队/等待确认可立即取消；运行中由Worker处理，仍需轮询。终态请求不改变原有结果。",
        CancelResponse,
    ),
    "retry": (
        "任务",
        "创建关联重试任务",
        "仅failed/cancelled/partial_success可重试；创建新task_id，不覆盖原任务，保留范围与既有预算。",
        TaskAccepted,
    ),
    "snapshots": ("快照", "列出已发布快照", "仅摘要，tables为对象数量。", list[SnapshotSummary]),
    "tables": (
        "快照",
        "分页搜索表与字段",
        "query在已保存表JSON中做包含搜索；offset≥0，limit默认50/最大100。",
        TablePage,
    ),
    "table_detail": (
        "快照",
        "精确查询表或视图详情",
        "table_name必须为完整名称，使用URL编码；不做模糊匹配，不存在404。",
        Table,
    ),
    "graph": (
        "快照",
        "查询ER或加工血缘图",
        "kind=er/lineage，默认er；无root保留全部节点，有root保留一跳。边最多200条，total_edges/edges_truncated说明截断。",
        GraphResponse,
    ),
    "entity_graph": (
        "快照",
        "查询实体关系力导图数据",
        "仅声明/推断关联，不含加工血缘。origin/confidence/review支持重复参数，省略为全部来源/置信度及pending+confirmed；空值为不选任何项。先筛选再稳定排序截断，limit默认/最大5000；无root保留全部节点，有root保留筛选后全部一跳节点。",
        EntityGraphResponse,
    ),
    "evidence": (
        "快照",
        "读取快照中的原始证据",
        "evidence_id来自当前快照对象/关系的evidence_ids；不得跨快照引用。结构随证据kind变化。",
        dict,
    ),
    "review": (
        "审核",
        "审核一条已保存关系",
        "snapshot_id为必填查询参数；status=pending/confirmed/rejected，保留原关系来源与证据，记录审核时间和说明。",
        ReviewResponse,
    ),
    "question": (
        "问答",
        "提交异步元数据问题",
        "绑定已发布快照；省略conversation_id创建新会话，传入则继续该快照会话；最长4000字符，不新增业务查询。",
        QuestionAccepted,
    ),
    "conversations": (
        "问答",
        "分页查询历史会话",
        "snapshot_id可选；offset≥0，limit默认20/最大100；按最近提问倒序，包括无答案的失败或排队任务。",
        ConversationPage,
    ),
    "conversation": (
        "问答",
        "读取会话答案及完整任务历史",
        "返回snapshot_id、answers和tasks；answers包含question/answer/evidence_ids，tasks保留无答案任务。",
        ConversationResponse,
    ),
    "export": (
        "导出",
        "下载快照结果",
        "format=json/csv；JSON为完整快照，CSV为表字段（UTF-8 BOM），不是关系CSV；图PNG/SVG由前端导出。",
        None,
    ),
}


def operation(name: str, status: int = 200):
    tag, summary, description, model = OPERATIONS[name]
    responses = {
        400: {"model": ErrorResponse, "description": "业务输入或数据源读取失败，detail为安全错误码"},
        404: {"model": ErrorResponse, "description": "对象不存在"},
        422: {"model": ValidationErrorResponse, "description": "请求结构不合法；不回显输入值"},
        status: {"description": "已入队，请轮询任务" if status == 202 else "成功"},
    }
    if model is not None:
        responses[status]["model"] = model
    if name in {"mapping", "confirm", "retry"}:
        responses[409] = {"model": ErrorResponse, "description": "任务状态或草稿版本冲突"}
    if name == "upload":
        responses[413] = {"model": ErrorResponse, "description": "Excel文件超过20MB"}
    if name == "export":
        responses[200]["content"] = {
            "application/json": {"schema": {"type": "object", "additionalProperties": True}},
            "text/csv": {"schema": {"type": "string"}},
        }
    result = {"tags": [tag], "summary": summary, "description": description, "responses": responses}
    examples = {
        "mysql_source": {
            "name": "<source_name>",
            "host": "db.example.invalid",
            "port": 3306,
            "username": "<readonly_user>",
            "password": "<database_password>",
            "database": "<database_name>",
        },
        "start_analysis": {"source_id": "<source_id>", "tables": ["<table_name>"], "allow_samples": False},
        "question": {"question": "<metadata_question>"},
        "source_sample": {"table": "<table_name>", "columns": ["<column_name>"], "limit": 10},
        "review": {"status": "confirmed", "note": "<review_note>"},
    }
    if name in examples:
        result["openapi_extra"] = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "examples": {"placeholder": {"summary": "请替换占位符", "value": examples[name]}}
                    }
                }
            }
        }
    return result


def install_docs(app: FastAPI):
    app.mount(PREFIX + "/docs/assets", StaticFiles(directory=swagger_ui_path), name="swagger-assets")

    @app.get(PREFIX + "/docs", include_in_schema=False)
    def docs(request: Request):
        root = request.scope.get("root_path", "").rstrip("/")
        return get_swagger_ui_html(
            openapi_url=root + app.openapi_url,
            title="Metadata Service · API文档",
            swagger_js_url=root + PREFIX + "/docs/assets/swagger-ui-bundle.js",
            swagger_css_url=root + PREFIX + "/docs/assets/swagger-ui.css",
            swagger_favicon_url=root + PREFIX + "/docs/assets/favicon-32x32.png",
            swagger_ui_parameters={"filter": True, "displayRequestDuration": True, "validatorUrl": None},
        )

    @app.get("/docs", include_in_schema=False)
    @app.get("/redoc", include_in_schema=False)
    def legacy_docs(request: Request):
        return RedirectResponse(request.scope.get("root_path", "").rstrip("/") + PREFIX + "/docs")

    @app.get("/openapi.json", include_in_schema=False)
    def legacy_schema(request: Request):
        return RedirectResponse(request.scope.get("root_path", "").rstrip("/") + app.openapi_url)
