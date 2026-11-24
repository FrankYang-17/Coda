---
name: showcase-camera
description: Design camera paths, pacing, framing and shot transitions during scene construction; diagnose sluggish movement in architectural showcases, navigation, tracking and generated video.
---

Use this guidance directly while constructing or revising a scene. The scene owner
plans and implements camera work alongside geometry and animation by default, in both
recursive and single-agent modes. Camera planning does not require a dedicated agent.
Any child may use this skill for camera coverage within its assigned scope.

If the parent chooses to delegate a separate camera task, use kind="camera" and
skills=["showcase-camera"]. Supply a packed scene snapshot, stable target names and
video timing constraints. The child follows the camera-package guidance below; the
parent applies the package and inspects the integrated scene. For revisions, resume
that child with updated inputs. These package requirements apply only to delegated work.

Read the scene snapshot, spatial constraints and intended display priorities. Choose
views that reveal useful information while respecting the requested camera behavior;
keep fixed views when requested and do not add unnecessary cuts. Camera plans
should guide asset detail early.

## Motion intent and pacing

Choose the shot's purpose and energy before setting keys: reveal a space, follow an
action, traverse a route, revisit a landmark, or inspect a detail. Name what the viewer
should discover by the end. Preserve specified fixed cameras, navigation directions,
one-take requirements and cuts. Choose unspecified speed from scene scale and mood;
use brisk, readable travel for energetic showcases and fast-action requests.

Describe each move with duration, travel distance or orbit angle, height change,
subject/framing target and speed profile. Make intended progress visible early.
For a brisk 5-second move, short acceleration/deceleration windows around 0.2–0.4 s
are useful starting points; adapt them to the action and clearance. Keep travel between
them purposeful. Use whole-shot ease-in/out for a deliberately gradual departure and
arrival. Repeated full-duration easing creates a stop-start rhythm across shots.

Carry motion through a cut when appropriate. For consecutive navigation rounds with
the same direction, retain endpoint position and compatible outgoing velocity when
the interface permits; record these with scene state. Stops and reversals should follow
the requested action. Keep a fixed lens for ordinary dollies and orbits; specify zoom
explicitly when it serves the brief.

For a continuation without a requested cut, save the evaluated camera position,
orientation, projection type and framing parameters at the end of the round
(perspective lens/sensor or orthographic scale, plus camera shift). Preserve the
projection type across ordinary camera moves. Start the next proxy from that state
and inspect its first frame alongside the previous accepted video's final frame.
If the generated endpoint differs, use its visible landmarks to adjust the opening
camera while retaining established scene geometry. Implement pan/tilt as rotation
and tracking as translation according to the task. Keep the lens fixed during pan
or tilt when the user has not requested simultaneous zoom; compare middle frames and endpoints for sudden
scale changes or jumps while a subject leaves or re-enters the view.
Repeated navigation continues from the latest accumulated pose, including its
orientation; apply the requested additional motion to that pose. Verify useful
landmarks at the next opening and ending views to confirm cumulative progress.

When the task asks to restore an earlier composition, retain that view's camera
checkpoint and an inspected frame from the accepted video. Compare the return view
with that frame for subject size, margins, perspective and background landmarks.
Read the evaluated camera transform and projection parameters from that earlier
checkpoint to establish the return target, then carry the current scene and action
state forward. Compare stable landmarks, the subject's fraction of frame width and
key visible boundaries in the target image and return preview, accounting for its
current pose and state. Correct substantial framing differences between the saved
proxy view and accepted video. Check the connecting
move and endpoint before describing the composition as restored.

## Spatial staging and framing

Plan the camera corridor while placing geometry. Leave usable routes through doors,
aisles and around furniture; honor supplied scene constraints. When clearance limits
travel, record the actual usable distance. Keep scene scale and navigation semantics
intact. A 0.3 m move over 5 s is a small inspection move and needs a deliberate reason.

Judge movement in screen space as well as world units. Use nearby edges, pillars or
furniture to make parallax readable while revealing a middle-distance subject and
background. An orbit can hold its subject near the same screen position while the
surrounding geometry changes clearly. Set radius, angle and height together; a small
arc around a distant subject gives a restrained reveal.

Give adjacent shots complementary coverage: entry/reveal, an oblique bar pass, a
lower dining-aisle view and a terrace reveal, for example. Choose useful changes in
distance, elevation or direction. For tracking, move with the subject and anticipate
its route; preserve look room and consistent screen direction. During a single take,
blend position and orientation tangents through waypoints without stopping at each
one. Plan foreground occlusions as brief reveals with clear subject recovery.

Read `references/motion-design.md` when implementing or repairing a camera move for
practical examples and Blender interpolation pitfalls.

Use `preview_camera_variants` on a saved scene to compare the baseline with a few
camera-only variations before full rendering. It provides matched frames and short
clips in one batch. `distance_scale` changes travel about each shot's starting point;
`height_offset`, `lens_mm` and `pace` adjust elevation, focal length and timing. Keep
these changes within the task's camera and initial-view constraints. Inspect the
returned sheets or clips, then use `apply_camera_variant` to save the chosen plan into
a new `.blend`. Pass that returned checkpoint to subsequent rendering tools. The
selected camera animation is baked in world space; its original parent/constraint rig
is retained in the preview's base checkpoint.

For a specific obstruction or framing problem, use `inspect_scene_at` with the
corresponding scene checkpoint, time and normalized image x/y (origin at top left).
It returns the first geometric hit, construction entrypoint when recorded, parents,
animation keys and active camera. Transparent surfaces count as geometric hits.
New render_sequence results include `scene_checkpoint` for this purpose.

Implement camera setup in the scene construction code or a reusable local helper.
For a delegated camera package, provide a portable setup script applied to the supplied scene.
Do not rebuild or delete existing geometry. Identify targets by stable entity
names or explicit coordinates. Do not depend on the current active object or selection.
Set frame range, frame rate, active camera, animation and cut markers explicitly.
When video output is requested without a specified frame rate, default to 24 FPS;
honor an explicitly requested frame rate instead. For a new timeline, set
scene.render.fps=24, scene.render.fps_base=1.0 and scene.frame_step=1.
Derive frame ranges, camera keyframes and cut markers from that rate and the requested
duration: a 20-second video at 24 FPS needs 480 frames, for example frames 1..480.
Do not change only FPS while leaving a higher-rate timeline's old frame count and timing.

render_sequence records effective FPS by dividing timeline FPS by fps_base and the frame
sampling interval frame_step. encode_video with fps=null inherits it. Apply frame-rate reduction only once. To preserve an existing
24 FPS camera timeline for an explicitly requested 2 FPS preview, keep fps=24 and fps_base=1.0 and use
frame_step=12; do not also set fps=2, which would reduce output to 1/6 FPS.
For the default fps=24 timeline, keep frame_step=1 unless further sampling is explicitly
intended. After rendering, check sequence.json for output FPS and frame count, and
confirm that the resulting duration matches the shot plan.
Use script-relative paths for any packaged dependencies.

Keep shot frame bounds, targets, lenses and purpose explicit in the scene code or a
local shot plan; delegated camera packages include this information in shots.json.
Use a single shared scene across cuts. Verify there are no unintended gaps or overlaps
in the output timeline. Use sudden camera changes only for explicit cuts.

Inspect evaluated camera motion after key insertion. Apply the intended interpolation
to the actual curves of the installed Blender version. In layered actions, inspect the
camera's channel bags; an empty legacy fcurves lookup can leave default Bezier easing
in place. Linear keys on densely sampled smoothstep positions still produce an eased
trajectory. Check both sampled positions and their interpolation.


Preview the path. Inspect shot starts/ends, intermediate views, close passes
by geometry and both sides of a cut. Check wall clipping, target occlusion, excessive
perspective distortion, speed changes and whether required components are visible.
For close passes, inspect the interval around the nearest foreground surface and the
last visible frames before the cut. A cabinet, post or wall can fill the image briefly
between otherwise clear samples. Confirm the required contact or state is visible
before and after an intended occlusion, and adjust the camera corridor when needed.
Watching only endpoints is insufficient. Inspect continuous motion when useful;
do not claim collision-free paths based solely on sparse samples.
Use view_video_clip with start_seconds, duration_seconds and preview fps to inspect
ordered, timestamped samples of a short interval. Increase preview fps for rapid movement,
near passes and cuts, rather than sending all frames of the whole video. This sampling
does not change render or encoding FPS. Use view_video_frame for exact cut-adjacent frames.

For pacing concerns, sample densely near the opening, closing and shot boundaries,
and inspect a low-resolution preview at the intended playback speed. Look for visible
progress, long near-stationary intervals, repeated framing, speed pulses, delayed
tracking and meaningful reveals. Widely spaced stills cannot establish their timing.
Use that preview to choose a local curve, path or framing correction before spending
on another full-resolution render.

When using video_model, carry the camera plan into video-pe with time windows,
direction, pace, framing changes and continuity landmarks. Describe smoothness and
speed separately: a fast move can have a smooth trajectory. Compare generated motion
with the proxy at corresponding times. Correct the stage that loses the move: proxy
path/timing, conflicting PE wording, or generated-video adherence. Keep these checks
within ordinary review.


Read the render skill for sample and resolution defaults before configuring renders.
Use render_image or render_sequence and view the results. For preview MP4 output, pass
the completed frame directories to encode_video as described in the render skill.

When submitting a delegated camera package, read delivery-contract and include the setup
script, shots.json, copied previews and remaining issues. Declare
validation_scene="inputs/<scene snapshot>.blend" in the manifest, include all helper code
and parameter files in files, and apply the entrypoint to the already loaded scene.
Check that setup and inspect previews before submit_task; the parent inspects the
integrated result after receipt. For local camera work, keep code with the scene and
follow the current task's normal completion flow; no camera package is required.

Use Cycles for camera previews and delivered white-model videos.




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
