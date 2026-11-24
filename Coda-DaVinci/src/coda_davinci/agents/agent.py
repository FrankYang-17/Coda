import json
import logging
import time
from pathlib import Path
from openai import APIError, OpenAI
from coda_davinci.providers.api_retry import request_with_retry, validate_response
from coda_davinci.agents.model_stream import responses_stream
from coda_davinci.agents.context_compaction import ContextCompressor
from coda_davinci.runtime.config import Settings
from coda_davinci.agents.conversation import prepare_conversation, session_identity
from coda_davinci.tools.workspace import WorkspaceTools, image_input
from coda_davinci.agents.tool_dispatch import execute_calls
from coda_davinci.agents.agent_progress import IdleTurnGuard

LOG = logging.getLogger(__name__)


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_agent(client: OpenAI, settings: Settings, workspace: Path, goal: str, context=None) -> dict:
    tools = WorkspaceTools(workspace, settings, context, goal=goal)
    tool_schemas = tools.schemas
    prompt = tools.prompt
    (history, first_turn) = prepare_conversation(tools, goal, "responses")
    reasoning_options = (
        {"reasoning": {"effort": settings.reasoning_effort}}
        if settings.reasoning_effort is not None
        else {}
    )
    compressor = ContextCompressor(tools, settings, history, goal, "responses")

    def summarize(system, text):
        response = responses_stream(
            client,
            model=settings.model,
            instructions=system,
            input=[{"role": "user", "content": text}],
            max_output_tokens=8192,
            store=False,
            **reasoning_options,
        )
        validate_response(response, "responses")
        return response.output_text

    save_json(
        tools.meta / "request.json",
        {
            "provider": "openai",
            "openai_api": "responses",
            "stream": True,
            "goal": goal,
            "model": settings.model,
            "instructions": prompt,
            **session_identity(context),
            "max_turns": settings.max_turns,
            "tools": tool_schemas,
            **reasoning_options,
            "sandbox": {"enabled": settings.sandbox, "bwrap": str(settings.bwrap)},
        },
    )
    last_response_id = None
    final = {"status": "iteration_limit_reached"}
    progress = IdleTurnGuard()
    try:
        for step in range(1, settings.max_turns + 1):
            turn = first_turn + step - 1
            turn_dir = tools.meta / f"turn_{turn:04d}"
            turn_dir.mkdir()
            history.extend(tools.background_messages())
            save_json(
                turn_dir / "input.json",
                {
                    "instructions": prompt,
                    "input": history,
                    "tools": tool_schemas,
                    "parallel_tool_calls": True,
                    **reasoning_options,
                },
            )
            LOG.info("Model request %d/%d; audit turn %d", step, settings.max_turns, turn)
            started = time.monotonic()

            def call_model():
                history.extend(tools.background_messages())
                save_json(
                    turn_dir / "input.json",
                    {
                        "instructions": prompt,
                        "input": history,
                        "tools": tool_schemas,
                        "parallel_tool_calls": True,
                        **reasoning_options,
                    },
                )
                request = dict(
                    model=settings.model,
                    instructions=prompt,
                    input=history,
                    tools=tool_schemas,
                    parallel_tool_calls=True,
                    tool_choice="auto",
                    store=False,
                    include=["reasoning.encrypted_content"],
                    **reasoning_options,
                )
                return compressor.call(
                    request,
                    turn_dir,
                    lambda value: responses_stream(client, **value),
                    summarize,
                    lambda response: validate_response(response, "responses"),
                )

            response = request_with_retry(
                call_model,
                tools=tools,
                turn_dir=turn_dir,
                api_error_type=APIError,
                protocol="responses",
            )
            save_json(turn_dir / "response.json", response.model_dump(mode="json"))
            LOG.info("Model output complete (%.1fs)", time.monotonic() - started)
            last_response_id = response.id
            history.extend(
                (item.model_dump(mode="json", exclude_none=True) for item in response.output)
            )
            if response.output_text:
                LOG.info("Agent: %s", response.output_text)
            calls = [item for item in response.output if item.type == "function_call"]
            stalled = progress.observe(
                has_calls=bool(calls),
                has_active_jobs=tools.jobs.has_active(),
                text=response.output_text,
            )
            if stalled:
                final = stalled
                save_json(turn_dir / "tool_outputs.json", [])
                LOG.warning("%s", final["message"])
                break
            pending = []
            if not calls:
                pending.append(
                    {
                        "role": "user",
                        "content": "Continue using the enabled tools. " + tools.completion_hint,
                    }
                )
            results = execute_calls(tools, [(call.name, call.arguments) for call in calls])
            for call, result in zip(calls, results):
                output = json.dumps(result.data, ensure_ascii=False)
                if result.images:
                    output = [{"type": "input_text", "text": output}]
                    for viewed in result.images:
                        output.extend(
                            [
                                {
                                    "type": "input_text",
                                    "text": f"{call.name}, call_id={call.call_id}: {viewed.label}",
                                },
                                image_input(viewed.path),
                            ]
                        )
                pending.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": output}
                )
                if result.finished:
                    final = result.data
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
