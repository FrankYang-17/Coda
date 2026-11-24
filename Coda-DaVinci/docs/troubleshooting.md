# Troubleshooting

- **Blender is missing.** Extract the full distribution into `external/blender/`, or
  set `BLENDER_EXECUTABLE` to its executable. Keep the adjacent `lib` and version
  directories. Confirm the executable starts with `--background --version`.
- **No CUDA device is visible.** Check `nvidia-smi`, the driver and
  `CUDA_VISIBLE_DEVICES`. Blender's Cycles preferences must enumerate a CUDA device.
- **bubblewrap cannot create a namespace.** The host/container must permit the
  required user and mount namespaces. Ask the worker administrator to enable them.
  `SANDBOX_ENABLED=0` is available for an already isolated, trusted worker.
- **API authentication or model access fails.** Check the specific service's URL,
  model ID and credential. Coding, images and video use separate configuration.
  Inspect the recorded response/request ID in the episode logs.
- **The endpoint returns 404.** Confirm the protocol and base URL in
  `docs/providers.md`, especially the video upload routes and Gemini API version.
- **The image endpoint rejects a size.** Set `IMAGE_SIZE` to a size that the chosen
  image model supports. Both scene generation and character edits use that service.
- **A video request is rejected.** Inspect its submission and provider response in
  `scene/video_generations/`. Check media constraints, resolution, duration and
  provider content rules before changing the inputs.
- **A task finishes with remaining issues.** Read `episode_result.json` and the
  acceptance records. Completion records the delivered artifact and any unresolved
  defects after the repair budget.
- **An API call is interrupted.** Keep the episode directory. Its task IDs, request
  records and downloaded files are available for diagnosis. Automatic submission
  resumption operates inside an active task; the CLI starts a new episode per run.
