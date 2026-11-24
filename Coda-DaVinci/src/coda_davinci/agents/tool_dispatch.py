"""Dispatch independent calls from one model response and preserve result order."""

from concurrent.futures import ThreadPoolExecutor
import logging
from coda_davinci.tools.tool_results import ToolResult

LOG = logging.getLogger(__name__)


def execute_calls(tools, calls: list[tuple[str, str]]) -> list[ToolResult]:
    """Work tools spawn background jobs; controls can wait concurrently within a batch."""

    def execute(call):
        (name, arguments) = call
        LOG.info("Tool starting: %s", name)
        if len(calls) > 1 and name in {"finish", "submit_task", "submit_video_acceptance"}:
            result = ToolResult(
                {"status": "error", "message": "Call completion tools by themselves"}
            )
        else:
            result = tools.execute(name, arguments)
        if len(calls) > 1 and result.finished:
            result = ToolResult(
                {
                    "status": "error",
                    "job_id": result.data.get("job_id"),
                    "message": "Collect final delivery with wait_tool in a separate, single-call turn.",
                }
            )
        LOG.info("Tool complete: %s | %s", name, result.data.get("status", "success"))
        return result

    if len(calls) < 2:
        return [execute(call) for call in calls]
    with ThreadPoolExecutor(
        max_workers=min(len(calls), 32), thread_name_prefix="tool-dispatch"
    ) as pool:
        return list(pool.map(execute, calls))
