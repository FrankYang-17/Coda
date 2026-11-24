---
name: blender-text-routing
description: >
  Use whenever you are about to start preparing a Seedance submission and are
  unsure whether Blender is needed. Choose either Blender proxy video + prompt
  + optional references, or prompt + optional references. Then recommend a
  suitable native Blender implementation for the first route.
---

# Blender / Text Routing

Analyze each requirement, then choose one input bundle for each shot/segment submission. Both routes require a prompt preserving the requested content; include relevant references if available. Missing references do not block either route. Never submit Blender output alone. “Prompt” means generation instructions, not subtitles.

## 1. Choose one of two routes

- **`blender_prompt`: Blender proxy video + prompt + optional references.** Prefer for **tutorial/explainer animations, movement-direction control, and camera control**, when a reliable proxy can establish the required counts, layout, scale, occlusion, paths, contacts, or state changes. Blender expresses necessary structure/events; the prompt states the full intent and supplements appearance or natural detail. References provide their stated appearance, scene, or motion cues. Do not treat the proxy as a substitute for the prompt.
- **`prompt_only`: Prompt + optional references, with no Blender proxy video in the submission.** Prefer for **multi-subject interactions, static single-subject narratives, human/animal limb motion, and intense fights** when proxy control adds little value or risks unnatural performance. This includes complex limb movements, facial acting, intricate finger motion, fine deformation, and material-detail close-ups. Keep the requested action and outcome in the prompt. If precise interaction or outcomes cannot be reliably controlled, report the limitation rather than claiming this route solves it.

Ask: **What must be controlled? Can the proxy visibly and credibly express it? What ambiguity does it remove?** Complexity alone is not a routing rule. Keep only necessary detail; never encode visibly wrong motion and expect text to repair it.

These are defaults based on the shot's main purpose. Overall movement direction/path is distinct from limb choreography. For mixed cases, prioritize the essential action: a dexterity tutorial may favor `prompt_only`, while a controlled multi-object mechanical demonstration may favor `blender_prompt`.

Within `blender_prompt`, decide which requirements the proxy expresses and which the prompt supplements; these are responsibilities, not additional routes. Preserve necessary transparency, reflections, emission, and clothing volume in the proxy. Preserve original user intent; Keep implementation choices consistent with the original request.

## 2. For `blender_prompt`, choose the implementation

Prefer procedural geometry and built-in features with the available resources. Do not assume external assets, motion libraries, mocap, or third-party solvers exist; honor explicitly supplied assets and user requirements.

| Content to express | Suggested implementation |
|---|---|
| Tutorial steps, layouts, assembly demonstrations | Simple geometry, staged states, and keyframes/constraints; preserve the demonstrated sequence. |
| Movement direction, paths, camera control | Animate subject root and camera transforms; control heading, route, and framing without inventing detailed limb choreography. |
| Explicit mechanical motion or controlled release | Keyframes/constraints; optionally hand off to physics after release. |
| Falling, rolling, bouncing, simple collapse | Rigid bodies with appropriate collision geometry. |
| Flags, curtains, draping | Cloth with suitable pins and colliders. |
| Simple compression, rebound, wobble | Soft body or controlled geometric deformation. |
| Filling, liquid level, localized smoke | Mantaflow when coarse dynamics matter and cost is reasonable. |
| Surface marks, wetness, contact ripples | Dynamic Paint; make the resulting change visible. |

Simulation generates responses, not action plans or guaranteed target outcomes. Cloth does not automatically fold clothing; soft bodies do not automatically model cutting or tearing. Do not replace explicitly required physical simulation with authored motion.

Inspect rendered frames and playback at key events. Fix technical faults within budget, then simplify or change the method if progress stalls. Preserve essential contacts, event order, and outcomes. If these remain unsupported, report the gap and recommend `prompt_only`, subject to pipeline compatibility below. Preserve the full prompt and available references; never report an unsupported event as verified.

## 3. Concrete cases

| Shot requirement | Choice and implementation |
|---|---|
| Product assembly tutorial | `blender_prompt`: simple parts and staged keyframes/constraints; prompt describes the steps and final appearance. |
| Person follows a specified route around a table | `blender_prompt` for overall direction/path; prefer `prompt_only` if natural gait or detailed limb action is the main point. |
| Camera circles an object along a specified path | `blender_prompt`: camera keyframes/constraints establish the orbit and framing; prompt supplies content and appearance. |
| One stationary person reflects or delivers a monologue | `prompt_only` when performance drives the scene and no precise spatial/camera control is needed. |
| Dance, animal limb motion, or piano-finger close-up | Prefer `prompt_only`; describe the performance and use references if available. Exact contacts or synchronization remain unverified. |
| A hands a cup to B / several people interact | Prefer `prompt_only` for natural interpersonal interaction. Use `blender_prompt` only when coarse transfer/layout is the main requirement and can be reliably demonstrated; parenting alone is not visual evidence. |
| Intense close combat or wrestling | Prefer `prompt_only`; preserve participants, action intent, and outcome in the prompt rather than inventing rigid limb/contact animation. |
| Ball drops into a bowl | `blender_prompt`: rigid bodies with an open bowl collider; prompt for the event and materials. |
| Flag flutters / person ties a scarf | `blender_prompt` with cloth for the flag; prefer `prompt_only` for intricate knotting without a reliable motion solution. |
| Water fills a glass / splash close-up | `blender_prompt` with Mantaflow and transparency for filling; prefer `prompt_only` when splash detail dominates. |
| Silk coat | `prompt_only` if only appearance matters; `blender_prompt` when garment volume needs control for silhouette, occlusion, or contact. |

For each submitted segment, state the route, proxy responsibilities and implementation if applicable, prompt content, available reference roles, and acceptance check. References are optional in every case above.

## 4. Apply within the current full-video workflow

The current `--video-render` workflow always builds a complete Blender proxy and keeps final PE/generation/review in the root session. Use the guidance above to divide responsibilities between geometry and text, not to switch to a text-only or segmented workflow. If the chosen proxy implementation cannot express a required event, revise or simplify the implementation while preserving the requirement; do not silently remove the event.

After the full proxy is inspected, read `video-pe` to author the final prompt, bind references, call `video_model`, review the actual generated video, and repair failed Blender/proxy/reference/PE inputs or resume the responsible child. References remain optional, but the complete generated proxy is required. Only a reviewed generated result may finish.




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
