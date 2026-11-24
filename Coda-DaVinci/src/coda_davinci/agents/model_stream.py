import logging
import time
from openai import APIError, omit
from openai.lib.streaming.chat import ChatCompletionStream
from openai.types.chat import ChatCompletion
from coda_davinci.providers.api_retry import (
    RetryableResponseError,
    get_request_id,
    validate_response,
    with_request_id,
)

LOG = logging.getLogger(__name__)


def responses_stream(client, **request):
    stream = None
    started = time.monotonic()
    first_delta = False
    try:
        with client.responses.create(**request, stream=True) as stream:
            for event in stream:
                if event.type in (
                    "response.output_text.delta",
                    "response.function_call_arguments.delta",
                ) and (not first_delta):
                    LOG.info(
                        "Model stream output started after %.1fs | request_id=%s",
                        time.monotonic() - started,
                        get_request_id(stream),
                    )
                    first_delta = True
                if event.type == "error":
                    raise APIError(
                        event.message, request=stream.response.request, body=event.model_dump()
                    )
                if event.type in ("response.completed", "response.failed", "response.incomplete"):
                    response = event.response
                    response._request_id = get_request_id(stream)
                    validate_response(response, "responses")
                    return response
            raise RetryableResponseError("Responses stream ended before a terminal response event")
    except Exception as exc:
        raise with_request_id(exc, stream)


def chat_stream(client, **request):
    from coda_davinci.providers.gemini_client import GeminiClient

    if isinstance(client, GeminiClient):
        result = client.generate_chat(**request)
        validate_response(result, "chat")
        return result
    raw = None
    try:
        with client.chat.completions.create(
            **request, stream=True, stream_options={"include_usage": True}
        ) as raw:
            stream = ChatCompletionStream(raw_stream=raw, response_format=omit, input_tools=omit)
            for _ in stream:
                pass
            parsed = stream.get_final_completion()
            parsed._request_id = get_request_id(raw)
            validate_response(parsed, "chat")
            data = parsed.model_dump(
                exclude={
                    "choices": {
                        "__all__": {
                            "message": {
                                "parsed": True,
                                "tool_calls": {
                                    "__all__": {
                                        "index": True,
                                        "function": {"parsed_arguments": True},
                                    }
                                },
                            }
                        }
                    }
                }
            )
            response = ChatCompletion.model_validate(data)
            response._request_id = get_request_id(raw)
            return response
    except AssertionError as exc:
        raise with_request_id(
            RetryableResponseError("Chat stream ended without a complete response"), raw
        ) from exc
    except Exception as exc:
        raise with_request_id(exc, raw)
