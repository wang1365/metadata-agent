"""Safe projection of official SDK events, without prompts, reasoning or values."""

import time


def safe_error_code(exc):
    candidate = str(exc).split(":", 1)[0]
    known = {
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

    def __call__(self, event):
        kind = event.get("type")
        if kind == "request/header":
            self.requests += 1
            self.started = time.monotonic()
            self.store.log(
                self.job_id,
                "model.request",
                f"第 {self.requests} 轮 DSH 请求已提交，等待模型与工具执行",
                stage="等待模型响应",
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
            self.store.log(self.job_id, "dsh.turn_end", "DSH 本轮执行结束，检查结构化产物", stage="检查执行结果")
