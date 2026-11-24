---
name: direct-video
description: Plan natural motion, generate a complete video from text and authorized references, and review the result.
---

Read the original task and inspect supplied references. Register acceptance criteria
covering the required subjects, actions, outcomes, timing and appearance.

Write a brief action plan: who acts, how the movement develops, important contacts,
the outcome, and camera framing. Express timing only as precisely as the task requires.
Preserve supplied reference roles. Use the authorized images/videos that establish the
required subjects and scene; text-only generation is available when references are absent.

Write a self-contained generation prompt. Keep identities and descriptions consistent,
and preserve requested text and events. For minimax-h3 and minimax-h3-max, the complete
submitted text, including reference mappings, must fit within 7000 characters.
For Seedance 2.5, reference images require both sides at least 300 pixels, area at least
90000 pixels and aspect ratio between 0.39 and 2.50. Preserve reference content and aspect ratio.

Call video_model with the complete duration, prompt, reference_images/reference_videos,
proxy_video=null, shot_manifest_path=null, and revision=1. Retain returned task IDs.
Use the same revision to resume unchanged input; use a new revision for revised content
or a confirmed failed generation. Resolve uncertain submission status before resubmitting.

Inspect the downloaded video with inspect_video and collect its frames with wait_tool.
Check movement, subject appearance, contacts, event order and the requested outcome.
Record findings using record_video_review and request_video_acceptance. Correct specific
failures in the action plan, prompt or authorized references. Allow at most three repairs;
then inspect and deliver the best available result with its remaining issues.
Submit finish with the generated video's render_uuid and shot_manifest_path=null.
