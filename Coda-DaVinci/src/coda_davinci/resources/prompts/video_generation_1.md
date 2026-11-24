
FULL-VIDEO GENERATION LOOP:
Write the video-generation prompt in the root session after inspecting the proxy. Build and encode the COMPLETE Blender proxy, inspect it and
its key actions, then read_skill(name="video-pe", path=null). Use that skill to write
self-contained generation guidance for the actual movie and its reference bindings.
Write a shot_manifest matching the actual proxy render_id, frame timing, subjects
and references. Registered subject IDs/outfits are resolved from that manifest.
Call video_model with your prompt, proxy_video, optional reference_videos/images,
shot_manifest_path and revision. The input proxy is a full movie, not a segment.
The service returns a full generated video. Do not split it into independent jobs
or concatenate generated segments. Respect the original requested total duration.
Wait for the generation result. Inspect the ACTUAL downloaded video with inspect_video,
collect its image results with wait_tool, and check against the original request,
proxy, references and PE. Check every shot, transitions and key action/contact moments;
inspect uncertain portions more densely. A successful download is not acceptance.
Record findings using record_video_review. If any requirement fails, reject the result
and diagnose whether the cause is Blender/geometry, proxy animation/camera, reference
selection/identity, or PE. Change the responsible inputs, or resume_task the asset,
camera or review child using its task_id and concrete timestamped feedback. Rerender/reinspect a changed proxy, revise the PE
and references as needed, then submit a NEW full-video revision and review it again.
Use the SAME revision to query/resume an unchanged attempt; never create duplicate paid
jobs to poll. Unknown submissions need investigation; no blind new revision retries.
After technical checks and your visual review, request independent video acceptance.
Allow at most three repairs per interaction round. After the third rejected version, perform the final repair, inspect the result yourself, and finish directly with remaining_issues. Further acceptance-agent checks are skipped. If the repair fails, deliver the best available generated video and disclose its remaining issues.
Use the selected video_model result's render_uuid and shot_manifest_path=null.
Preserve failed attempts and review findings in the workspace.
