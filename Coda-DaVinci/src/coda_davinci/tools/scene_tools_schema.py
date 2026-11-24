from coda_davinci.tools.schema import PATH, STRING, tool

SCENE_TOOLS = [
    tool(
        "inspect_scene_at",
        "Pick the first geometric surface at a normalized image coordinate in a saved Blender scene. Use the checkpoint corresponding to the proxy view. Returns object, construction entrypoint when recorded, parents, animation and active camera. x runs left-to-right and y top-to-bottom. Transparent surfaces are geometric hits.",
        blend_file=PATH,
        time_seconds={"type": "number", "minimum": 0},
        x={"type": "number", "minimum": 0, "maximum": 1},
        y={"type": "number", "minimum": 0, "maximum": 1},
    ),
    tool(
        "preview_camera_variants",
        "Compare the current camera plan with up to three numeric variants in one Blender process. Returns a comparison page, low-resolution clips and labeled frame sheets. Geometry and subject animation remain shared. Each shot is retimed separately; distance_scale expands travel about its starting position. steady uses constant travel speed; short_ease adds brief acceleration/deceleration. Use apply_camera_variant to save a chosen plan for final rendering.",
        blend_file=PATH,
        variants={
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "distance_scale", "height_offset", "lens_mm", "pace"],
                "properties": {
                    "name": STRING,
                    "distance_scale": {"type": "number", "exclusiveMinimum": 0},
                    "height_offset": {"type": "number"},
                    "lens_mm": {"type": ["number", "null"], "exclusiveMinimum": 0},
                    "pace": {"type": "string", "enum": ["original", "steady", "short_ease"]},
                },
            },
        },
        timestamps_seconds={
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {"type": "number", "minimum": 0},
        },
        clip_start_seconds={"type": "number", "minimum": 0},
        clip_duration_seconds={"type": "number", "exclusiveMinimum": 0, "maximum": 4},
    ),
    tool(
        "apply_camera_variant",
        "Save a selected preview camera plan into a NEW .blend file using the preview scene snapshot. Returns the checkpoint to use in subsequent render_sequence/render_image. baseline is a valid selection.",
        preview_id=STRING,
        variant_name=STRING,
        output_blend=PATH,
    ),
]
