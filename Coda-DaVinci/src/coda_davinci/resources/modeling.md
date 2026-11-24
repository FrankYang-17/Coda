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
