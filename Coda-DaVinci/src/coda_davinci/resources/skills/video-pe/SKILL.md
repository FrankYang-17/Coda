---
name: video-pe
description: >
  After inspecting a complete Blender proxy, write the final video-model prompt,
  bind reference media and identities, generate the full movie, inspect the actual
  result, and revise failed inputs before delivery. Used by the root in --video-render.
---

When using minimax-h3 or minimax-h3-max, the complete submitted text must be at most 7000 characters, including spaces, newlines and automatically appended subject, shot and reference-binding descriptions. Budget for those descriptions while writing the prompt; keep them concise, preserve required events and reference mappings, and check the complete text length before submitting.


# Full-video PE and revision

Write the generation prompt in the root session after inspecting the complete proxy. Deliver the inspected final movie through finish.

## WBench / sequential interaction mode

When the system selects sequential interactions, its practical policy overrides the strict whole-movie delivery rules below. Work on the current turn only, preserving the previous endpoint state and the same scene, scripts, identities and child agents. Do not reconstruct the scene or inspect future instructions.

Choose references yourself: previous generated video plus an updated proxy, the previous video alone, or its last frame obtained with `video_tail_frame`. For reference-only generation set `proxy_video=null`, `shot_manifest_path=null` and provide the current `duration_seconds`. A new proxy upload is not mandatory. Save reusable scene state and a `.blend` checkpoint before finishing the round.

A practical acceptance means usable for continuing the interaction, not perfect or officially scored. One useful inspection is sufficient; choose more views only when they resolve a specific concern. You may accept with nonblocking issues and deliver with `remaining_issues`. Do not repeatedly fix minor textures, lighting, clothing or small timing/framing deviations. Deliver the best usable result honestly. Missing or undecodable output remains a failure.

When an event completes during darkness, occlusion or an offscreen interval, inspect
its resulting state in the saved Blender scene before preparing the next generation.
A previous tail frame may preserve camera and illumination while leaving that state
unreadable. For a reveal, supply an updated proxy showing the completed state as it
becomes visible, or an inspected reference representing the current state. Bind each
source to those attributes and check the first visible moments after the obstruction
clears. Preserve completed changes in the scene across the boundary.
For an action that continues offscreen, advance its animation phase and preserve any
contact relationships while it is hidden. When existing references leave the ongoing
motion ambiguous, supply an updated proxy expressing its current phase. Inspect the
subject's first visible pose, motion and any contact when it re-enters.

For a requested return to an earlier composition, inspect a frame from that earlier
accepted video; when using a proxy, compare its return view with that frame.
If the latest endpoint omits the returning
subject or its original framing, include the earlier frame as a labeled composition
reference. Bind it to the target screen size, margins, perspective and established
appearance; bind the latest endpoint and the current state's chosen references to
the current action phase and event-driven state. Check the generated return against
the earlier composition.
When restoring that composition is an explicit task requirement, substantial subject
enlargement or cropping of key boundaries that contradicts the target view warrants
the available correction. Account for legitimate pose and state changes, retaining
the practical policy's tolerance for minor framing variation. If image-only continuation loses
the return framing, guide that correction with an inspected updated camera proxy
and the earlier composition reference.

When the original reference supplies appearance and the task keeps that visual style,
carry its visual medium into newly revealed areas across rounds:
linework, shading, texture treatment and spatial abstraction remain consistent with
the requested style. Use the latest accepted endpoint for current framing and state,
and retain the original reference's appearance role when a continuation introduces
new surfaces. Compare the newly revealed region with that original style as well as
with the preceding clip. Keep temporal-state and appearance bindings explicit.

## Establish what must be preserved

For T2V, read `scene-reference` and use the inspected scene image in `reference_images`.
Bind it to stable scene appearance, materials, lighting and object construction. The
white-model video remains the source for layout, counts, camera, motion, contacts and
time-varying states. I2V and V2V keep their supplied image/video references.

Use the original request for intended subjects, events and hard requirements. Inspect the actual proxy and references; a manifest or child's claim is not independent visual evidence. Read [the proxy manifest contract](references/proxy-manifest.md) when preparing `shot_manifest_path`.

Assign responsibilities by attribute:
- The proxy establishes demonstrated subject counts, layout, scale, paths, heading, camera, contacts, occlusion, event order and timing.
- Check repeated-unit counts, grouping, populated extent and spatial density in the proxy against the task and references. Simplified surfaces and low-poly parts are appropriate; the full arrangement of objects and occluding volumes must already be represented. Repair missing units or implausibly empty regions in Blender before generation.
- Text states the complete intended content and adds compatible appearance and natural detail that the proxy does not express.
- Original images/videos supply only their authorized identity, appearance, motion style, camera or scene attributes. State each source's purpose; do not copy unrelated contents of a reference.

For human and animal proxies, follow character-animal-proxy. Bind the proxy to root
paths, scale, event timing, contact targets and resulting object states. Let the
subject references and action semantics guide natural anatomy, joint transitions
and gait appropriate to the requested subject. For realistic people, repair segmented
body shapes and awkward holding poses; for toys or stylized characters, retain the
requested proportions and joint design. Review appearance and performance against
that subject design alongside the spatial requirements.

Do not treat all proxy geometry as a final surface specification. Capsules may become people or animals while retaining spatial occupancy and the required motion. Distinguish an absent detail from a wrong constraint: a capsule following a path may gain natural gait, but a proxy passing through a table or showing the wrong action outcome needs upstream repair. Do not expect prose to repair explicitly wrong key motion. Preserve required transparency, reflections, emission and interacting clothing volume; do not hide errors with fog or depth of field.

Check each shot and key interaction. Sparse samples cannot prove the full action; inspect peaks, handoffs, contact moments and both sides of cuts as needed. If evidence is insufficient, obtain more views. If the proxy or manifest is wrong, revise it now or resume the responsible child with timestamped feedback. Do not emit `needs_proxy_fix` and end the task.

## Write the full generation prompt

Write a self-contained natural-language prompt for this complete video. The video model does not read your conversation, local plan or manifest. Include the relevant action, subjects, key outcome, timing, camera, appearance and continuity; never rely on “see the manifest” or “follow the earlier plan.” Do not repeat the complete story as the action of every shot.

A useful structure is **intended finished appearance → asset roles and bindings → actual shots in order → shared constraints**. Keep it concise; do not add sections without useful content.

Start with the requested visual medium and the finished subjects and environment.
Describe the physical construction visible at the intended shot size: characteristic
shape, major parts, material response, surface detail and illumination. For a
photorealistic request, describe real scale, plausible construction, varied surface
roughness and grounded contact shadows. For a stylized request, use the requested
shape and shading language. Give each prominent subject a concise finished-appearance
description; words such as "high quality" alone supply little visual information.

Assign preservation requirements to specific attributes: counts, spatial arrangement,
camera path, subject trajectory, event timing, contact and resulting state. Explicitly
describe the intended finished construction and surfaces of simplified proxy assets.
Scope shape preservation to task-relevant proportions, silhouettes, openings and
contact regions. Scope color preservation to colors required by the task or identity.
This lets the prompt express both the demonstrated spatial constraints and the
surface richness, local contours and natural movement needed in the finished movie.

For each action, name the actor, motion, target, contact or release and visible result.
Use the action phases already demonstrated in the proxy. A later view describes the
current state, location and possession after preceding events; mention completed
actions in the past tense when needed. Specify what remains visible at an emptied
location and where a transferred object is now. Describe motion with concrete verbs
and its actual pace, including weight shift, support and settling when visible.

- Define each subject unambiguously using its stable ID and task-grounded semantic description; distinguish subjects of the same class by their roles or explicit attributes. With the colored proxy, use the subject/color plan made before construction and the inspected current proxy's entity_map to state each actual color-to-ID-to-subject correspondence in the DiT prompt. Use the same subject definitions in action/shot descriptions and reference bindings, so every color identifies exactly one described subject. Resolve missing, ambiguous or conflicting definitions against the actual proxy before submission. Identity colors are lookup markers only, not final clothing, skin or material colors; preserve the user's actual appearance requirements and authorized references. For neutral-clay or original-appearance proxies, anchor subjects by geometry, positions and task roles without inventing identity colors.
- Preserve registered subject IDs and each shot's outfit/state. The tool adds resolved identity/outfit images and attachment-number mappings. Use actual labels; never invent image numbers or a reference that does not exist. Character sheets show several views of one subject, not extra cast members.
- Describe visible appearance appropriate to shot size: material, texture scale, reflection/translucency, light and contact shadows as relevant. Prefer concrete details over “cinematic” quality adjectives. Use the scene reference for stable appearance rather than expecting prose to turn a proxy into a finished surface. Make only a few compatible choices for unspecified details; do not add people, props, events, cuts or dialogue.
- Briefly state each existing shot's action and camera requirements, then its appearance priorities. Preserve reliable fractional times exactly (for example, a 1.5-second cut); do not round or lengthen short shots. Use shot order without invented timestamps when timing is uncertain. Match the complete proxy's pacing and total duration.
- Keep identity and base appearance stable across shots. Carry forward wetness, damage, possession and other event-driven states. Lighting continuity follows scene space, not a fixed screen direction after camera changes.
- Preserve explicitly requested audio in the prompt when relevant; do not invent audio requirements. Visual previews do not verify audio. Record any requested property that the available inspection cannot establish rather than claiming it was checked.

Write the final generation prose in English unless the user requests another language; preserve exact requested text, names and dialogue. Pass the authored prompt string to `video_model`. Put assumptions and unresolved issues in your working notes and review evidence, not inside the generation prompt. Do not include API settings, implementation choices, critique, or claims of guaranteed hard control.

## Generate and inspect

Call `video_model` with the prompt, complete `proxy_video`, labeled `reference_videos` and `reference_images`, its `shot_manifest_path`, and a positive `revision`. I2V/V2V use their supplied media; T2V includes the inspected scene reference generated in this session. The tool resolves registered identity/outfit references, appends attachment mappings and sends the original full videos/images. It freezes each attempt's inputs and checks returned duration, dimensions and full decodability.

Generation is asynchronous: keep the returned job ID and collect its result. Use the same revision to retrieve/resume unchanged work; change revision for corrected inputs or a known failed generation. An unknown submission must be checked through its saved audit/task ID, not blindly recreated as another paid task. Service or session limits are real boundaries; do not silently shorten or split the requested movie to bypass them.

Before submitting a cropped or resized reference, check its actual dimensions against
the selected service's limits. For Seedance 2.5 reference images, retain at least
90,000 pixels, a width AND height of at least 300 pixels each, and a width/height
aspect ratio within 0.39–2.50. Check every resolved image, including automatically
attached identity/outfit images and continuation frames; area alone is insufficient.
Include enough surrounding context or pad/resize the image while preserving the
subject's proportions. Inspect the resulting file and update its explicit path or
subject binding before submitting it. Prepare a separate copy and retain originals;
respect frozen-reference restrictions and report conflicts instead of changing them.

### Recover from PrivacyInformation

`InputImageSensitiveContentDetected.PrivacyInformation` means the provider interpreted a submitted image as potentially depicting a real person. Review the actual frozen images in this revision's `inputs/` and the reference list in `submission.json`, including automatically attached identity and outfit images.

- Read `character-appearance` and rework the affected appearance reference into a clear character design board: front/side/back views of the same character, outfit/material details and a small expression panel, arranged on a plain background with view labels. For fictional/generated characters, an illustrated or 3D character-design presentation can clarify the reference's purpose. Preserve the requested identity attributes and intended movie style.
- Inspect the new image. Update the explicit image paths and subject/outfit bindings; use `select_subject_identity_reference` when replacing the selected identity anchor. Check the resolved reference list so the next submission uses the revised assets.
- Submit the corrected inputs under a new `revision`, then inspect the returned video normally. Keep the failed request and the actual reference changes in working notes. Further attempts should address what the latest response and inspected images show.

A successful tool result is not visual approval. Call `inspect_video` on the **downloaded result**, collect the actual images with `wait_tool`, then compare it with the original request, proxy and authorized references. Empty timestamps select samples across the known shots; add denser views for actions and uncertainty. Checking Blender PNGs does not inspect the generated video.

When a proxy was submitted, the receipt includes a synchronized comparison page and
video. `inspect_video` on the generated result automatically returns paired frames:
proxy above, generated below, with their actual timestamps. Compare camera direction
and pace, subject position, occlusion/reappearance and event/cut timing. Both start at
zero seconds without time stretching; the shorter source becomes black after it ends.
Use a few additional timestamps around a visible discrepancy to locate its cause.
Distinguish intended appearance enrichment from changes to required geometry or action.
Reference-only interactions retain ordinary generated-video inspection.

Also compare the finished appearance with the requested style: recognizable object
construction and scene density, natural body proportions, material and lighting
detail, and readable movement through contact and release. A photorealistic request
needs visibly developed subjects and environments. Record retained block-like shapes,
segmented doll-like bodies or sliding motion as specific defects when they conflict
with that request. Choose a correction from the observed cause and the existing budget.

If the comparison includes `scene_checkpoint`, use `inspect_scene_at` on a relevant
location in the proxy frame to identify the object and its construction/animation
controls. Use the proxy coordinates and actual timestamp shown in the paired sample.
This helps localize geometry, occlusion and camera problems before revising the scene.

Record content, identity, continuity and prompt agreement through `record_video_review`, including concrete evidence and limitations. Request independent review with `request_video_acceptance(render_uuid=...)`. The video-acceptance skill distinguishes acceptable minor defects from required repairs; rework/unverified findings guide repairs within the three-repair budget.

## Rework within this session

| Observed problem | Revise the responsible input |
| --- | --- |
| Wrong position, occlusion, count, camera or key contact already present in the proxy | Correct Blender/animation/camera, or `resume_task` the owning asset/camera agent; rerender and inspect the full proxy and update its manifest. |
| Repeated structures cover too little of the scene, groups lack their constituent units, or visible occupancy is too sparse for the task | Restore the required extent, instances, grouping and occluding volumes using simple reusable geometry; inspect the affected camera views. |
| Ambiguous identity, conflicting appearance or incorrect outfit | Correct reference roles or the shared subject record/reference selection; keep stable IDs and update the manifest. |
| Proxy is adequate but generated appearance/action interpretation is wrong | Make the PE specific about the missed requirement; remove competing or irrelevant instructions; change references only when they address the cause. |
| A cut loses identity, object state or scene continuity | Inspect both sides; repair proxy/PE/reference bindings according to where the discontinuity originates. |
| Evidence is insufficient | Inspect additional times or ask a review child for targeted findings; the root still inspects the returned video before acceptance. |

On failure, record a rejected review, identify the cause and make the relevant correction. Use the existing child's `task_id` to resume its context with the latest scene/reference snapshots and exact observed defect. After child delivery, integrate, rerender and inspect the changed proxy before another generation. In single-agent mode make the same corrections yourself. Repeat generation and inspection while the session budget permits; preserve prior attempts. If blocked, report the remaining cause without a successful `finish`.

Deliver an independently accepted output or the selected output after the three-repair budget is exhausted. Call `finish` with the `render_uuid` returned by `video_model`, record remaining findings in `remaining_issues`, and use `shot_manifest_path=null`. During the repair budget, changed inputs require a new generated revision and independent acceptance.


Allow at most three repairs per interaction round. After the third rejected version, perform the final repair, inspect the result yourself, and finish directly with remaining_issues. Further acceptance-agent checks are skipped. If the repair fails, deliver the best available generated video and disclose its remaining issues.



When using minimax-h3 or minimax-h3-max, the complete submitted text must be at most 7000 characters, including spaces, newlines and automatically appended subject, shot and reference-binding descriptions. Budget for those descriptions while writing the prompt; keep them concise, preserve required events and reference mappings, and check the complete text length before submitting.
# Generation and review contract

The root authors the prompt, submits video_model and reviews the actual output. Read references/proxy-manifest.md and write a manifest matching the inspected encoded proxy, exact timing and actual observed subjects. Never use missing joints or contact as evidence of demonstrated motion.
For non-interactive tasks generate the complete movie, without extra segmentation. In interactive mode work only on the current round, reuse scene/state/conversation, never read future turns, and obey the system's proxy and continuation policies. Save scene_state.json and a .blend checkpoint. finish is a round checkpoint.
Read scene-reference for T2V and use inspected authorized scene references. Do not restore obsolete object states from an appearance reference.
Call video_model with the authored prompt, current proxy/manifest when required, authorized references and positive revision. Keep job IDs; poll/resume the same immutable revision. Never create duplicate paid jobs to query uncertain submissions.
Before Seedance 2.5 submission, check every resolved reference image, including identity/outfit bindings and continuation frames: width >= 300 px, height >= 300 px, width * height >= 90,000 pixels, and width / height within 0.39-2.50; use PNG/JPEG/WebP. Area alone is insufficient. For editable references, prepare and inspect a separate proportional resize or authorized padding, preserve originals and update the submitted paths/bindings. Correct invalid dimensions before another submission. Keep these input checks out of the movie prompt.
Inspect the actual downloaded video with inspect_video and collect images with wait_tool. Compare original task, references, represented proxy attributes and current states. Geometry deliberately absent at the chosen level is not an upstream bug; do not upgrade to repair it. A wrong represented path, count or endpoint is an upstream bug. Repair within the selected profile.
Use record_video_review with honest findings, then call request_video_acceptance for the candidate. Repair rework/unverified findings and request acceptance again within the three-repair budget. Independently accepted minor defects may be recorded in remaining_issues. 
Finish only with the generated-video render_uuid. Keep all revisions and reviews. No proxy may be delivered as the final video.


Allow at most three repairs per interaction round. After the third rejected version, perform the final repair, inspect the result yourself, and finish directly with remaining_issues. Further acceptance-agent checks are skipped. If the repair fails, deliver the best available generated video and disclose its remaining issues.



## Proxy representation

Represent the environment, props and animals with small assemblies of primitives
that preserve their coarse silhouette and orientation. Include the structures,
landmarks, supports and foreground/background layers needed by the scene. Preserve
requested counts, grouping, occupied space, trajectories, event order and endpoint
states. Use neutral illumination and contact shadows. Keep surface detail simple.

Represent each person with a cylinder torso and sphere head. Move this fixed whole
through the scene and change its facing direction. Invisible logical anchors track
holding, transfer and ownership. Maintain each person's identity, size and occupied
space across shots. Describe fine limb motion in the video prompt and review that
motion in the final video. Apply this representation consistently during repairs.

Record represented attributes, unrepresented requirements, functional exceptions
and entity parts in proxy_capabilities.json. Tag parts with c2v_entity_id and
c2v_part_role when useful. Describe visible geometry and logical state separately.
Inspect the proxy against what it represents and the final video against the
original task, including complete anatomy and natural movement.

## Identity and appearance

Before delegation, assign stable entity_id values, semantic descriptions grounded
in the request, and distinct identity colors (name and hex) to tracked subjects
and props. Share these definitions with children. After inspecting the proxy,
record the observed colors in shot_manifest.entity_map. In the generation prompt,
bind each observed color to the same entity ID and subject description. Bind
reference media using their actual labels. Identity colors serve as lookup markers;
the original request and appearance references determine the finished materials.

## Physics

Drive task-relevant physical processes with executed dynamics calculations or
appropriate simulation. Solve collision, support and nonpenetration for interacting
bodies. Use cloth simulation for required fabric deformation, fluid simulation for
pouring and splashing, particle systems for discrete grains, and gas/smoke simulation
for suspended clouds. Bake the required time interval and render the evaluated
states. Preserve consistent units and propagate physical state through events.

Material deformation may require additional geometry, particles or volumes. Keep
that detail tied to the requested process. Record initial conditions, parameters,
solver settings, cache paths and sampled outcomes in physics_evidence.json. Record
failed or unsupported simulations accurately. The helpers in
scene-assembly/resources/physics.py cover constant gravity and static support;
choose the appropriate solver for more complex interactions.

## Submission and continuation

Read video-pe and write the final generation prompt after inspecting the proxy.
Every Blender-route generation uses the current inspected proxy and its matching
manifest. For continued clips, reuse the saved scene and subject identities, update
the current state, and attach the controller-provided previous final frame.
