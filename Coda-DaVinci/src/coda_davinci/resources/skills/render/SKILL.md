---
name: render
description: Configure Blender rendering with 4 samples, 4 CPU threads and persistent scene data; encode rendered frames into a video.
---

Use CYCLES for all rendering, with the harness-assigned CUDA device and CPU fallback disabled.

Use 4 Cycles samples, 4 render threads and persistent scene data for both previews
and complete proxy videos. Use 720p output (1280 x 720 pixels at 100% resolution)
unless the task specifies another resolution.

For Cycles, set the maximum sample count and resolution in the render setup script:

```python
import bpy

scene = bpy.context.scene
scene.cycles.samples = 4
scene.render.threads_mode = "FIXED"
scene.render.threads = 4
scene.render.use_persistent_data = True
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.resolution_percentage = 100
```

Adaptive sampling may finish below that maximum. For other engines, use the corresponding
render sample setting supported by the installed Blender version; Cycles sample settings
do not configure EEVEE.

Apply the requested settings or these defaults after loading the .blend and applying
camera/scene scripts. Do not silently inherit a higher sample count or resolution from
an asset or an earlier render. Keep geometry construction separate from render settings
so changing samples or resolution does not require rebuilding the scene.

Keep persistent render data enabled after loading the scene. GPU denoising may also
be configured when supported:

```python
if scene.render.engine == "CYCLES":
    scene.render.use_persistent_data = True
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.denoising_use_gpu = True
```

Persistent data reuses unchanged render data across frames in the same Blender process;
it does not reuse rendered pixels or share caches across separate tool calls/processes.
It is useful for sequences and repeated previews within one process, with extra memory
usage. GPU denoising is separate from GPU path tracing and requires supported hardware;
use_denoising=True alone does not select GPU denoising. Account for cache and denoising memory when running concurrent renders.

Use render_image for stills and render_sequence for animation frames; their setup scripts
configure the scene without calling bpy.ops.render.render themselves. render_sequence
returns a frames directory, sequence.json, FPS and frame count; it does not encode video.
After the frame jobs complete, call encode_video(frame_dirs=[...], fps=null) to create MP4.
Directory order defines video order. Within each directory the sequence.json order is used;
without a manifest PNG filenames are sorted numerically and an explicit fps is required.
null inherits the matching effective FPS from all manifests. Do not divide by frame_step
again. Explicit fps changes playback speed; encoding does not drop or interpolate frames.

Pass threads=4 to run_blender, render_image and render_sequence. Use --threads 4
for standalone Blender CLI commands, and retain FIXED/4 in the scene render settings.
Wait for script writes before launching jobs, and do not edit scripts or the shared scene
while jobs may still read them.

Wait for rendering to succeed before calling encode_video.
run_blender can also render PNG frames; provide explicit fps if there is no manifest.
All frames must have matching dimensions. encode_video snapshots and renumbers them into
one output package, preserves their source order in sequence.json and runs FFmpeg once.
Encoding can be retried using existing frame directories without repeating Blender work.

Inspect raw frames via view_image or view_video_frame(path=<returned sequence.json>, ...).
To inspect motion over an interval without sending every frame, use
view_video_clip(path, start_seconds, duration_seconds, fps). Its fps is preview sampling
only: start_seconds=0, duration_seconds=2, fps=2 selects indices 0,12,24,36 from a 24 FPS
sequence, at times 0,0.5,1,1.5 seconds. It uses the effective FPS already stored in the
manifest, not the original Blender timeline FPS, and never changes output timing.
Times are relative to this sequence.
Choose preview density for the motion; inspect suspect intervals and cut boundaries more
densely, using view_video_frame for exact boundary frames. Sparse previews cannot certify
smooth motion or absence of brief clipping. Returned timestamps distinguish sampling
times from the actual selected frame times; the requested end time is excluded.
After encoding, inspect the new package and use encode_video's render_uuid for finish;
render_sequence IDs identify frame-only outputs. Check total frame count, output FPS
and duration against the task. For camera timing and frame sampling, follow showcase-camera.
