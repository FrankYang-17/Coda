# Proxy manifest for full-video generation

Write this after encoding and inspecting the complete Blender proxy. It is generation/review metadata, not a claim that the final generated video already matches it.

- `render_id`: the `render_uuid` of the proxy's `encode_video` result; use that result's `video` path as `proxy_video`.
- `proxy_video_label`: use `白模视频`; original reference labels must be distinct.
- `fps` and `total_frames`: copy the final proxy's adjacent `sequence.json`. Frames are zero-based output positions, not Blender timeline frame numbers.
- `frame_interval_convention`: `[start_frame, end_frame)`.
- `entity_map`: one entry per tracked subject, with `entity_id`, `user_subject` and, in colored-proxy mode, `proxy_color` containing `name` and `hex`. Use registered `subject_id` values unchanged. If no subjects need tracking, use `[]`.
- `shots`: actual chronological shots with `shot_id`, `start_frame`, `end_frame`, `visible_entity_ids` and a concise observed `description`. Intervals must cover the full proxy without gaps or overlap. Do not invent short-shot boundaries just to format the prompt.
- `reference_bindings`: optional per entity; each entry uses an existing `media_label`, a `subject_selector` and the authorized `attributes`. Do not invent references. Registered identity/outfit references are resolved by the tool.
- `subject_states`: optional per shot, keyed by registered subject ID, with `outfit_id` and optional `state`; specify the outfit when more than one is registered.
- `preserve_attributes`: optional user-supported structural appearance requirements and their sources, such as required transparency or an emissive object illuminating another subject.

For `--no-colored-proxy`, set `appearance_mode="original"` and describe subjects through `appearance_description`; unique identity colors are not required. Preserve the original appearance requirements.

Keep the manifest beside your editable scene/PE files and pass its workspace-relative path to `video_model`. The tool validates it against the selected proxy and resolves the shared subject registry before submitting. If it reports a mismatch, correct the source or manifest in the current session. The final generated output receives a separate render ID; only that ID can finish the task.
