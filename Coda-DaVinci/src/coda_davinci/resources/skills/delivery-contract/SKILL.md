---
name: delivery-contract
description: Package versioned asset, camera or review deliveries with editable source, interface metadata and inspected previews.
---

Create delivery/manifest.json and all dependencies beneath delivery/. Every path in
the manifest is relative to its directory; list individual files, not directories.
No absolute paths, '..', symlinks or .harness contents. Do not list manifest.json itself.
The parent receives only the manifest and files explicitly listed in it. Include the
final construction source and every local helper, parameter file and resource required
to rebuild it. Intermediate work do not need to be delivered. Imports from the
original workspace, inputs/, imports/ or .skills/ cannot supply missing construction code.

For an asset delivery, read [the asset manifest template](assets/asset-manifest.json)
on demand with read_skill(name="delivery-contract", path="assets/asset-manifest.json").
Copy or write it into delivery/manifest.json and replace its example values.
Use a stable lowercase identifier for asset.id: start with a lowercase letter and
use only lowercase letters, digits and underscores, for example "main_bar" rather
than "MainBar". Keep this asset ID distinct from Blender Collection/object display names.

The bounds must come from the actual exported local geometry, not an estimate copied
from this example. Keep collection.instance_offset zero and include only component
geometry in the asset Collection. Build scripts use paths relative to __file__.
Include imported modules and external resources; external references in saved .blend
files must be packed or relative to files inside the package.

Design asset entrypoints to run from the package root in an empty Blender scene.
They should construct the asset and write the declared blend_file at that relative path.
For example, after building the Collection, bpy.data.libraries.write can export it.
For composite assets, include the child construction source so the parent can rebuild
and modify components. Keep the source consistent with the delivered .blend. Check its
usability yourself and document how to run it; submission does not execute the source.

For kind=camera: use the same common fields (schema_version, kind, summary, files,
previews, remaining_issues), plus entrypoint naming the camera setup .py. Include
shots.json among files. Declare validation_scene as the workspace-relative path of the
parent's packed scene snapshot, for example "inputs/scene.blend". That scene is an
explicit input, not a file to list in the camera package. It must have no unpacked
external references. The parent loads the scene before running the camera entrypoint;
the entrypoint must modify the current scene, not reload a hardcoded input path.
Preserve its geometry and inspect shot start/middle/end frames. shots.json is a list
(or an object with a "shots" list) whose integer "start"/"end" fields cover the output
timeline in order without gaps.
See [the camera manifest example](assets/camera-manifest.json) when preparing this kind.
For kind=review: use the common fields plus report_file naming a listed .md, .txt or
.json. previews may be empty. Reports identify affected objects/views, evidence,
severity, uncertainty and a suggested owner for repairs; do not claim unperformed checks.

Include at least one preview for asset/camera deliveries. Render with render_image or
render_sequence, inspect using view_image/view_video_frame, then copy those exact
image bytes into the delivery package. A short run_blender script can use
shutil.copyfile for packaging. If you received more asset/camera children after the
preview, render and inspect again before submitting. Choose previews that show the
delivered version and describe any unresolved problems honestly.

Call submit_task(manifest_path="delivery/manifest.json") alone. It ends only this
task, not the whole episode. Do not invoke finish inside a child. Submission reads the
JSON manifest and snapshots the listed files; it performs no automatic acceptance checks
on metadata, source syntax, rebuildability, geometry, cameras or preview evidence.
Malformed JSON, invalid file lists and inaccessible or unsafe file paths can still
prevent file transfer. The conventions above guide your work and the parent's review.
The parent must inspect source usability, interfaces and rendered integration, and can
resume you by task_id for corrections. Include known limitations in remaining_issues.

The submission receipt and parent result include your stable task_id and activation
number. After completion, your task becomes idle; its conversation and workspace remain
available while this episode is active. A parent can resume_task by that ID with feedback
and new inputs. Continue editing your writable source/delivery files when resumed, inspect
new previews and submit again. New parent input snapshots are normally under inputs/revisions/<activation>/;
use the exact input prefix in the feedback message, as existing names may require a unique revision directory.
The new frozen delivery is imported as another version, preserving earlier parent copies.
