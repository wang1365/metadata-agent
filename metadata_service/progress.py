"""Safe projection of official SDK events, without prompts, reasoning or values."""

import time

TURN_FAILURES = {
    "max-tokens": ("DSH_MAX_TOKENS", "模型输出达到上限，DSH 未完成本轮；请分批提交结果后重试"),
    "error": ("DSH_MODEL_ERROR", "DSH 本轮以错误结束，请检查模型服务及前序工具事件"),
    "cancelled": ("DSH_CANCELLED", "DSH 本轮被取消"),
}


def turn_failure(reason):
    return TURN_FAILURES.get(
        reason,
        ("DSH_NO_TURN_END", "DSH 没有返回本轮结束状态")
        if reason is None
        else ("DSH_TURN_FAILED", "DSH 本轮未正常完成"),
    )


def safe_error_code(exc):
    candidate = str(exc).split(":", 1)[0]
    known = {
        "DSH_MAX_TOKENS",
        "DSH_MODEL_ERROR",
        "DSH_CANCELLED",
        "DSH_NO_TURN_END",
        "DSH_TOOLS_NOT_OBSERVED",
        "BAILIAN_API_KEY_MISSING",
        "DSH_VERSION_MISSING_SKILLS",
        "UNEXPECTED_DSH_TOOLS",
        "METADATA_MCP_NOT_READY",
        "DSH_TURN_FAILED",
        "BUDGET_EXHAUSTED",
        "TASK_NOT_ACTIVE",
        "MYSQL_OPERATION_FAILED",
        "MYSQL_QUERY_FAILED",
        "DEFINITION_UNAVAILABLE",
        "SAMPLES_DISABLED",
        "UNKNOWN_EVIDENCE",
        "CONFIRMED_MAPPING_IMMUTABLE",
        "MAPPING_NOT_CONFIRMED",
    }
    if candidate in known:
        return candidate
    return "RESULT_SCHEMA_INVALID" if type(exc).__name__ == "ValidationError" else type(exc).__name__


class DSHProgress:
    def __init__(self, store, job_id):
        self.store, self.job_id = store, job_id
        self.requests = 0
        self.started = None
        self.tools = set()
        self.calls = {}

    def __call__(self, event):
        kind = event.get("type")
        if kind == "request/header":
            self.tools = {tool.get("name") for tool in event.get("data", {}).get("header", {}).get("tools", [])}
            self.requests += 1
            self.started = time.monotonic()
            self.store.log(
                self.job_id,
                "model.request",
                f"第 {self.requests} 轮 DSH 请求已提交，等待模型与工具执行",
                stage="等待模型响应",
            )
        elif kind == "tool/call":
            data = event.get("data", {})
            if data.get("name") in self.tools:
                self.calls[data.get("callId")] = data["name"]
        elif kind == "tool/result":
            for block in event.get("data", {}).get("message", {}).get("content", []):
                name = self.calls.pop(block.get("toolCallId"), None)
                if block.get("isError"):
                    self.store.log(
                        self.job_id,
                        "dsh.tool_error",
                        "DSH 收到工具错误；可能为输入 Schema 校验失败，请由 DSH 按契约修正小批结果",
                        level="error",
                        tool=name,
                        code="DSH_TOOL_ERROR",
                    )
        elif kind == "assistant/message":
            elapsed = int((time.monotonic() - self.started) * 1000) if self.started else None
            self.store.log(
                self.job_id,
                "model.response",
                "模型消息已接收，DSH 继续处理",
                duration_ms=elapsed,
                stage="DSH 处理模型响应",
            )
            self.started = None
        elif kind == "turn/end":
            reason = event.get("data", {}).get("reason", {}).get("kind")
            code, message = (
                ("completed", "DSH 本轮执行结束，检查结构化产物") if reason == "completed" else turn_failure(reason)
            )
            self.store.log(
                self.job_id,
                "dsh.turn_end",
                message,
                code=code,
                level="info" if reason == "completed" else "error",
                stage="检查执行结果",
            )
