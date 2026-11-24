# Practical camera motion

## Travel, framing and timing

For an architectural reveal, identify the occluding edge and the view it will expose.
An oblique pass near a counter or column can reveal depth over a short path. Keep
foreground objects readable and preserve a clear route. Choose the time of the reveal
so the shot develops throughout its useful duration.

An illustrative brisk 5-second aisle traversal might cover about 4 m with a 0.3 s
acceleration, continuous cruising and a 0.3 s arrival. These are layout-dependent design
choices. A continuous segment boundary can lie within cruising motion; honor the
prompt's specified stop, reversal or speed. When a subject must stay in frame, adjust
the framing target along the route with clear look room.

For a hero orbit, identify which faces should become visible. Choose angle, radius and
height to reveal them. A 45–90 degree arc may suit an energetic showcase; an intimate
inspection may need less. Check clearance and foreground occlusion. Preserve the
benchmark's allowed camera action and duration when applying this guidance.

Treat spatial waypoints and travel timing as separate choices. Equal increments in a
curved path's parameter can cover unequal distances. For steady movement, approximate
arc length and advance by distance over time. Smooth tangents through bends and apply
local acceleration where the action needs it. Each waypoint need not be a stop.

For a chase, define a subject route and a camera offset along it. Look ahead toward an
approaching turn. Make side/front tracking a planned maneuver and keep banking
restrained unless requested. For multiple shots, begin each at its useful action,
choose duration from the information revealed, and preserve geography and action
continuity through cuts.

Use world-space look-at targets or compatible quaternion keys for large turns. Keep
consecutive quaternion signs consistent. With Euler keys, preserve compatible angle
branches across wraparound. Inspect orientation together with translation: changes in
aim can weaken a reveal even when the camera covers appreciable ground.

## Blender 5 layered actions

On the project's Blender 5 layered-action path, camera curves are in channel bags.
After baking an intentionally timed trajectory, this sets between-frame interpolation:

```python
action = camera.animation_data.action
curves = [
    curve
    for layer in action.layers
    for strip in layer.strips
    for bag in strip.channelbags
    for curve in bag.fcurves
]
if not curves:
    raise RuntimeError("Camera action has no animation curves")
for curve in curves:
    if curve.data_path in {"location", "rotation_euler", "rotation_quaternion"}:
        for key in curve.keyframe_points:
            key.interpolation = "LINEAR"
```

This preserves baked timing; easing already embedded in sampled positions remains.
Choose timing before baking. Inspect evaluated world transforms at the opening,
middle and end, with closer samples around speed changes. Parent transforms and
constraints also affect the final camera. Keep inspection local to the active camera.

## Generated-video handoff

Describe an observable result in the PE: advance briskly along the aisle; shelf ends
pass through the near foreground; the far window grows in the middle distance;
continue moving into the next segment. Supply time windows for changes of direction
or height. Smooth, stable and cinematic describe qualities of motion; specify pace
and travel separately.

When the proxy moves but generation holds the starting view, inspect PE demands about
maintaining the initial composition throughout the clip and any slow/gentle wording.
Preserve identities and layout while allowing the viewpoint to evolve. When the proxy
barely moves, revisit its usable travel and timing. Keep requested static shots static.

## Observations from project clips

These measurements inform diagnosis and are not acceptance thresholds.

- Skyline `episode_00000_rank0_0vlrn2dn`: 20 s at 24 FPS, five shots. The camera code
  attempted linear interpolation through the legacy fcurves interface. Inspection of
  the saved Blender 5.2.1 scene found Bezier keys on all five cameras. The dining shot
  covers 3.4 m in about 6 s but moves only about 0.068 m in its first 0.5 s. Apply the
  intended interpolation to the actual action and inspect the opening pace.
- Skyline `codex_skyline_20261011T135438Z_bZnXEj`: all four shots explicitly use
  SINE/EASE_IN_OUT. Longer bar and dining translations provide stronger spatial
  changes, while each shot still accelerates and decelerates. Choose pacing per shot.
- Selected WBench_48 round 1: 0.30 m over 5 s with smoothstep. Proxy and generated
  samples show a small viewpoint change; the PE requests a modest step and stop.
  Consider actual clearance and intended navigation before prescribing that travel.
- Selected WBench_175 round 1: about 3 m over 5 s with full-shot smoothstep. Shelves
  provide useful parallax; the opening begins near rest. Compatible velocities can
  maintain momentum across continuing navigation rounds when the action permits.
- Selected WorldScore dining_spaces_012: 17-degree orbit over 5 s, 43 mm lens,
  constant-speed per-frame keys. Its restrained reveal requires considering arc,
  radius and foreground depth; easing is not its principal limitation.

The inspected exports were 24 FPS. Selected proxies were 5 s and generated clips
about 5.04 s; these examples showed no substantial duration expansion.
