
For the Blender geometry/motion reference, render a COLORED PROXY video.
The generated final video is a separate artifact. Subjects that have stable IDs in
the task brief and need cross-shot tracking must use visually distinct, solid
identity colors. Keep each assigned color on the same semantic entity in every
frame and camera view; never reuse one identity color for a different tracked
entity. Apply it to the entity's major visible proxy surfaces, not just one small
part. Keep each stable ID recoverable in Blender object/collection names or custom
properties so an external manifest exporter can associate geometry with semantics.
Use neutral gray/white clay materials for untracked environment geometry.

Identity colors are lookup markers, not requested final surface colors. Do not
add ID text, legends, guide lines, or other tracking graphics to the image. Avoid
final textures, decorative material detail, and stylized final lighting. Use
stable neutral illumination, visible diffuse form shading, and contact shadows
so silhouettes, depth, occlusion, and physical contact remain readable without
washing out the identity colors. Preserve any user-required transparency,
reflection, emission, or other appearance property that affects structure,
action, or visibility, and keep its source explicit in artifacts you produce.

These appearance constraints apply to the Blender proxy, not the final video_model output.
These requirements apply to child agents too, and take precedence over generic
white-clay preview or beauty-render suggestions in skills. Keep proxy geometry
detailed enough for the requested composition, contacts and motion.
Do not change IDs or identity colors between previews and the selected render.
The root owns the final shot_manifest; child agents provide relevant asset identities,
color mappings, shot plans and observed changes to their parent.
