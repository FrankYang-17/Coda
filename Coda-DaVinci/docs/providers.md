# Provider configuration

## Coding agent

Set `MODEL`, `BASE_URL` and `API_KEY`. The same model configuration is used for the
root, delegated agents, routing assessment and independent acceptance reviewer.
`AGENT_PROTOCOL` selects the transport:

| Value | Interface | Base URL example |
| --- | --- | --- |
| `responses` | OpenAI-compatible Responses, streaming tool calls and images | `https://api.openai.com/v1` |
| `chat` | OpenAI-compatible Chat Completions with tools and images | Provider's `/v1` endpoint |
| `anthropic` | Anthropic Messages with streaming tools and image blocks | `https://api.anthropic.com` |
| `gemini` | Gemini native `generateContent`, function calls and inline images | `https://generativelanguage.googleapis.com/v1beta` |

Models must support image input and tool use. Select a context limit supported by
your deployment through `HARNESS_CONTEXT_MAX_TOKENS`; the packaged default is
900000. `AGENT_MAX_OUTPUT_TOKENS` controls the Anthropic output limit. Gemini uses
`GEMINI_MAX_OUTPUT_TOKENS` (default 65536). `REASONING_EFFORT` is optional and must
be accepted by the selected model. `AGENT_HTTPS_PROXY` overrides the coding
client's proxy; the standard HTTP(S) proxy environment also works.

## Appearance images

Set `IMAGE_MODEL`, `IMAGE_BASE_URL` and `IMAGE_API_KEY` for an Images-compatible
service. Coda calls `images.generate` for a scene or character reference and
`images.edit` when a reference is supplied. Responses may contain base64 image
bytes or a downloadable URL. `IMAGE_SIZE` and `IMAGE_API_TIMEOUT` can override
request defaults. The service must support the requested sizes and image editing.

## Video gateway

`VIDEO_MODEL_BASE_URL` points to the service root; the client appends `/v1/...`.
The adapter is in `providers/video.py`. This is a specific asynchronous gateway
protocol. A provider with different routes or payloads needs an adapter here.

Requests to the gateway carry `Authorization: Bearer <VIDEO_MODEL_API_KEY>`.
Media storage PUTs and output downloads use their returned URLs. The API bearer
credential is attached only to requests to the gateway's own host.

### Upload media

1. `POST /v1/media/uploads`

   ```json
   {"filename": "proxy.mp4", "mime_type": "video/mp4", "size_bytes": 12345}
   ```

   Expected response:

   ```json
   {"id": "upload-id", "upload_url": "https://storage.example/upload"}
   ```

2. `PUT <upload_url>` with raw bytes, `Content-Type`, `Content-Length` and
   `Content-Range: bytes 0-<last-byte>/<size>`.
3. `POST /v1/media/uploads/<id>/complete`

   Expected response:

   ```json
   {"status": "ready", "https_url": "https://storage.example/reference.mp4"}
   ```

Images follow the same flow with an image MIME type.

### Submit and poll

`POST /v1/videos/generations` receives the selected model, generation parameters
and media references. A representative reference request is:

```json
{
  "model": "your-video-model",
  "content": [
    {"type": "text", "text": "Generation prompt with subject bindings"},
    {"type": "image_url", "image_url": {"url": "https://storage.example/ref.png"}, "role": "reference_image"},
    {"type": "video_url", "video_url": {"url": "https://storage.example/proxy.mp4"}, "role": "reference_video"}
  ],
  "duration_seconds": 8,
  "resolution": "720p",
  "aspect_ratio": "16:9",
  "generate_audio": false,
  "n": 1,
  "output_format": "mp4",
  "omni_reference_task_type": "auto"
}
```

Text-only submissions use `prompt` in place of `content`. The service returns an
`id`. The client polls `GET /v1/videos/generations/<id>` until the
status is terminal. A completed response contains a downloadable video URL, e.g.:

```json
{"id": "task-id", "status": "completed", "video_url": "https://storage.example/result.mp4"}
```

The selected model must accept the resolution and duration requested and support
reference images together with a proxy reference video. Sequential generation uses
the prior tail frame as another reference image.

Transient failures use bounded backoff. Saved task IDs are reused while polling;
explicit terminal failures are recorded before another submission is attempted.
The polling timeout defaults to 150 minutes per video request. Total task time also
includes coding, rendering and any review repairs.

`VIDEO_MODEL_HTTP_PROXY`, `VIDEO_MODEL_HTTPS_PROXY` and `VIDEO_MODEL_NO_PROXY`
provide video-client-specific proxy settings.
