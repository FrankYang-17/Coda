"""Anthropic Messages loop using the same workspace tools and local audit files."""

import json
import logging
import time
from pathlib import Path
from anthropic import APIError
from coda_davinci.agents.agent import save_json
from coda_davinci.providers.api_retry import (
    RetryableResponseError,
    request_with_retry,
    retry_limit,
    validate_response,
    with_request_id,
    get_request_id,
)
from coda_davinci.agents.context_compaction import ContextCompressor
from coda_davinci.runtime.config import Settings
from coda_davinci.agents.conversation import prepare_conversation, session_identity
from coda_davinci.tools.workspace import ToolResult, WorkspaceTools, image_input
from coda_davinci.agents.tool_dispatch import execute_calls
from coda_davinci.agents.agent_progress import IdleTurnGuard

LOG = logging.getLogger(__name__)


def tool_result_block(call, result: ToolResult) -> dict:
    content = [{"type": "text", "text": json.dumps(result.data, ensure_ascii=False)}]
    for viewed in result.images:
        (header, data) = image_input(viewed.path)["image_url"].split(",", 1)
        content.extend(
            [
                {"type": "text", "text": viewed.label},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": header[5:].split(";", 1)[0],
                        "data": data,
                    },
                },
            ]
        )
    return {
        "type": "tool_result",
        "tool_use_id": call.id,
        "content": content,
        "is_error": result.data.get("status") == "error",
    }


def receive_message(client, request):
    stream = None
    try:
        with client.messages.stream(**request) as stream:
            finished = False
            for event in stream:
                if event.type == "message_stop":
                    finished = True
            if not finished:
                raise RetryableResponseError("Anthropic stream ended before message_stop")
            response = stream.get_final_message()
            response._request_id = get_request_id(response) or get_request_id(stream)
            return response
    except RetryableResponseError as exc:
        raise with_request_id(exc, stream)
    except (AssertionError, RuntimeError, ValueError) as exc:
        raise with_request_id(
            RetryableResponseError(f"Anthropic stream could not assemble a final message: {exc}"),
            stream,
        ) from exc
    except Exception as exc:
        raise with_request_id(exc, stream)


def run_anthropic_agent(
    client, settings: Settings, workspace: Path, goal: str, context=None
) -> dict:
    tools = WorkspaceTools(workspace, settings, context, goal=goal)
    schemas = [
        {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["parameters"],
        }
        for tool in tools.schemas
    ]
    prompt = tools.prompt
    effort_options = (
        {"output_config": {"effort": settings.reasoning_effort}}
        if settings.reasoning_effort is not None
        else {}
    )
    (history, first_turn) = prepare_conversation(tools, goal, "anthropic")
    compressor = ContextCompressor(tools, settings, history, goal, "anthropic")

    def summarize(system, text):
        result = receive_message(
            client,
            dict(
                model=settings.model,
                system=system,
                messages=[{"role": "user", "content": text}],
                max_tokens=8192,
                **effort_options,
            ),
        )
        validate_response(result, "anthropic")
        return "\n".join((block.text for block in result.content if block.type == "text"))

    save_json(
        tools.meta / "request.json",
        {
            "provider": "anthropic",
            "goal": goal,
            "model": settings.model,
            **session_identity(context),
            "instructions": prompt,
            "tools": schemas,
            "max_turns": settings.max_turns,
            "max_tokens": settings.max_tokens,
            "api_max_retries": retry_limit("anthropic", getattr(settings, "api_max_retries", None)),
            "stream": True,
            **effort_options,
            "sandbox": {"enabled": settings.sandbox, "bwrap": str(settings.bwrap)},
        },
    )
    final = {"status": "iteration_limit_reached"}
    progress = IdleTurnGuard()
    last_response_id = None
    try:
        for step in range(1, settings.max_turns + 1):
            turn = first_turn + step - 1
            turn_dir = tools.meta / f"turn_{turn:04d}"
            turn_dir.mkdir()
            history.extend(tools.background_messages())
            request = dict(
                model=settings.model,
                system=prompt,
                messages=history,
                tools=schemas,
                max_tokens=settings.max_tokens,
                tool_choice={"type": "auto", "disable_parallel_tool_use": False},
                **effort_options,
            )
            save_json(turn_dir / "input.json", request)
            LOG.info(
                "Model request %d/%d (Anthropic Messages); audit turn %d",
                step,
                settings.max_turns,
                turn,
            )
            started = time.monotonic()

            def call_model():
                history.extend(tools.background_messages())
                return compressor.call(
                    request,
                    turn_dir,
                    lambda value: receive_message(client, value),
                    summarize,
                    lambda response: validate_response(response, "anthropic"),
                )

            response = request_with_retry(
                call_model,
                tools=tools,
                turn_dir=turn_dir,
                api_error_type=APIError,
                protocol="anthropic",
            )
            save_json(turn_dir / "response.json", response.model_dump(mode="json"))
            LOG.info("Model output complete (%.1fs)", time.monotonic() - started)
            last_response_id = response.id
            history.append(
                {
                    "role": "assistant",
                    "content": [
                        block.model_dump(mode="json", exclude_none=True)
                        for block in response.content
                    ],
                }
            )
            calls = [block for block in response.content if block.type == "tool_use"]
            results = []
            for block in response.content:
                if block.type == "text":
                    LOG.info("Agent: %s", block.text)
            stalled = progress.observe(
                has_calls=bool(calls),
                has_active_jobs=tools.jobs.has_active(),
                text="\n".join((block.text for block in response.content if block.type == "text")),
            )
            if stalled:
                final = stalled
                save_json(turn_dir / "tool_outputs.json", [])
                LOG.warning("%s", final["message"])
                break
            outcomes = execute_calls(
                tools, [(call.name, json.dumps(call.input, ensure_ascii=False)) for call in calls]
            )
            for call, result in zip(calls, outcomes):
                results.append(tool_result_block(call, result))
                if result.finished:
                    final = result.data
            pending = [
                {
                    "role": "user",
                    "content": results
                    or [
                        {
                            "type": "text",
                            "text": "Continue using the enabled tools. " + tools.completion_hint,
                        }
                    ],
                }
            ]
            save_json(turn_dir / "tool_outputs.json", pending)
            history.extend(pending)
            if not calls and tools.jobs.has_active():
                history.extend(tools.background_messages(wait=True))
            if final["status"] == "completed":
                break
    except KeyboardInterrupt:
        final = {"status": "interrupted"}
        raise
    except Exception as exc:
        final = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        tools.close()
        history.extend(tools.background_messages())
        save_json(tools.meta / "history.json", [{"role": "system", "content": prompt}, *history])
        save_json(tools.meta / "result.json", {**final, "last_response_id": last_response_id})
    return final
