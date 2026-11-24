"""Gemini 原生调用与现有 chat 工具循环之间的数据转换。"""

import json
import os
import time
import uuid
import httpx
from openai.types.chat import ChatCompletion
from coda_davinci.providers.agent_http import AgentHTTPClient


class GeminiClient:
    def __init__(self, api_key, base_url, timeout):
        self.key = api_key
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        self.base = base if base.endswith("/v1beta") else base + "/v1beta"
        self.http = AgentHTTPClient(timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.http.close()

    def generate_chat(self, model, messages, tools=(), **kwargs):
        (contents, system, names) = ([], [], {})
        for message in messages:
            role = message["role"]
            if role in ("system", "developer"):
                system.append(message["content"])
                continue
            if role == "tool":
                parts = [
                    {
                        "functionResponse": {
                            "name": names[message["tool_call_id"]],
                            "response": {"result": message["content"]},
                        }
                    }
                ]
            elif role == "assistant" and message.get("gemini_parts"):
                parts = message["gemini_parts"]
                for call in message.get("tool_calls", []):
                    names[call["id"]] = call["function"]["name"]
            else:
                parts = []
                content = message.get("content") or []
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                for item in content:
                    if item["type"] == "text":
                        parts.append({"text": item["text"]})
                    elif item["type"] == "image_url":
                        (header, data) = item["image_url"]["url"].split(",", 1)
                        parts.append(
                            {"inlineData": {"mimeType": header[5:].split(";")[0], "data": data}}
                        )
                    else:
                        raise ValueError(f"Unsupported Gemini content: {item['type']}")
                for call in message.get("tool_calls", []):
                    names[call["id"]] = call["function"]["name"]
                    parts.append(
                        {
                            "functionCall": {
                                "name": call["function"]["name"],
                                "args": json.loads(call["function"]["arguments"]),
                            }
                        }
                    )
            if not parts:
                continue
            native_role = "model" if role == "assistant" else "user"
            if (
                contents
                and contents[-1]["role"] == native_role
                and all(("functionResponse" in part for part in contents[-1]["parts"] + parts))
            ):
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": native_role, "parts": parts})
        body = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": kwargs.get(
                    "max_completion_tokens",
                    int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "65536")),
                )
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system)}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["function"]["name"],
                            "description": t["function"].get("description", ""),
                            "parametersJsonSchema": t["function"]["parameters"],
                        }
                        for t in tools
                    ]
                }
            ]
        response = self.http.post(
            self.base + "/models/" + model + ":generateContent",
            headers={"x-goog-api-key": self.key},
            json=body,
        )
        if response.is_error:
            raise httpx.HTTPStatusError(
                "Gemini: " + response.text[:3000], request=response.request, response=response
            )
        data = response.json()
        if not data.get("candidates"):
            raise ValueError(f"Gemini returned no candidate: {data.get('promptFeedback')}")
        candidate = data["candidates"][0]
        parts = candidate.get("content", {}).get("parts", [])
        calls = [
            {
                "id": uuid.uuid4().hex,
                "type": "function",
                "function": {
                    "name": p["functionCall"]["name"],
                    "arguments": json.dumps(p["functionCall"].get("args", {})),
                },
            }
            for p in parts
            if "functionCall" in p
        ]
        reason = candidate.get("finishReason")
        if reason not in ("STOP", "MAX_TOKENS"):
            raise ValueError(f"Gemini generation stopped: {reason}")
        usage = data.get("usageMetadata", {})
        result = ChatCompletion.model_validate(
            {
                "id": data.get("responseId", uuid.uuid4().hex),
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "length"
                        if reason == "MAX_TOKENS"
                        else "tool_calls"
                        if calls
                        else "stop",
                        "message": {
                            "role": "assistant",
                            "content": "".join(
                                (p.get("text", "") for p in parts if not p.get("thought"))
                            ),
                            "tool_calls": calls or None,
                            "gemini_parts": parts,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": usage.get("promptTokenCount", 0),
                    "completion_tokens": usage.get("candidatesTokenCount", 0)
                    + usage.get("thoughtsTokenCount", 0),
                    "total_tokens": usage.get("totalTokenCount", 0),
                },
            }
        )
        return result
