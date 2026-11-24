"""Restore native conversation history and continue its existing turn audit trail."""

import json
from coda_davinci.media.image_transport import compress_history_images


def _check_tool_pairs(history: list[dict], protocol: str):
    pending = set()

    def complete(call_id):
        if call_id not in pending:
            raise ValueError("Saved conversation has a tool result without its preceding call")
        pending.remove(call_id)

    for item in history:
        if not isinstance(item, dict):
            raise ValueError("Saved conversation items must be objects")
        if protocol == "responses":
            if item.get("type") == "function_call":
                pending.add(item["call_id"])
            elif item.get("type") == "function_call_output":
                complete(item["call_id"])
        elif protocol == "chat":
            if item.get("role") == "assistant":
                pending.update((call["id"] for call in item.get("tool_calls", []) or []))
            elif item.get("role") == "tool":
                complete(item["tool_call_id"])
        else:
            content = item.get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_use":
                    pending.add(block["id"])
                elif block.get("type") == "tool_result":
                    complete(block["tool_use_id"])
    if pending:
        raise ValueError(
            "Saved conversation has unresolved tool calls; cannot resume safely without their outcomes"
        )


def prepare_conversation(tools, goal: str, protocol: str) -> tuple[list[dict], int]:
    context = tools.context
    resuming = context is not None and context.activation > 1
    history = []
    if resuming:
        request = json.loads((tools.meta / "request.json").read_text(encoding="utf-8"))
        saved_protocol = (
            "anthropic"
            if request.get("provider") == "anthropic"
            else request.get("openai_api", "responses")
        )
        if request.get("task_id") != context.task_id or saved_protocol != protocol:
            raise ValueError("Saved conversation belongs to a different task or API protocol")
        if (
            protocol == "chat"
            and request.get("chat_tool_mode", "native") != tools.settings.chat_tool_mode
        ):
            raise ValueError("Saved conversation uses a different chat tool transport")
        history = json.loads((tools.meta / "history.json").read_text(encoding="utf-8"))
        if not isinstance(history, list) or not history:
            raise ValueError("Saved conversation must be a nonempty list")
        compress_history_images(history)
        _check_tool_pairs(history, protocol)
        if history[0].get("role") == "system":
            history = history[1:]
    if protocol == "chat":
        history.insert(0, {"role": "system", "content": tools.prompt})
    history.extend([{"role": "user", "content": goal}, *tools.skill_messages])
    if not resuming:
        from coda_davinci.media.reference_videos import initial_video_message

        history.extend(initial_video_message(tools.meta.parent, protocol))
    numbers = [
        int(p.name[5:]) for p in tools.meta.glob("turn_*") if p.is_dir() and p.name[5:].isdigit()
    ]
    return (history, max(numbers, default=0) + 1)


def session_identity(context) -> dict:
    return {
        "task_id": context.task_id if context else None,
        "activation": context.activation if context else 1,
        "interaction_round": getattr(context, "interaction_round", 0),
    }
