import math
import bpy
from mathutils import Vector


def active_camera(scene, frame):
    markers = sorted(
        (m for m in scene.timeline_markers if m.camera and m.frame <= frame), key=lambda m: m.frame
    )
    camera = markers[-1].camera if markers else scene.camera
    if camera is None:
        raise ValueError("Scene has no active camera")
    return camera


def frame_at(scene, seconds):
    fps = scene.render.fps / scene.render.fps_base
    if (
        not math.isfinite(seconds)
        or not 0 <= seconds < (scene.frame_end - scene.frame_start + 1) / fps
    ):
        raise ValueError("Time is outside the scene timeline")
    return scene.frame_start + seconds * fps


def curves_for(obj):
    if not obj.animation_data or not obj.animation_data.action:
        return []
    return [
        curve
        for layer in obj.animation_data.action.layers
        for strip in layer.strips
        for bag in strip.channelbags
        for curve in bag.fcurves
    ]


def animation(obj, frame):
    rows = []
    for curve in curves_for(obj):
        points = list(curve.keyframe_points)
        nearby = sorted(points, key=lambda key: abs(key.co.x - frame))[:3]
        rows.append(
            {
                "property": curve.data_path,
                "axis": curve.array_index,
                "key_count": len(points),
                "nearby_keys": [
                    {
                        "frame": float(k.co.x),
                        "value": float(k.co.y),
                        "interpolation": k.interpolation,
                    }
                    for k in sorted(nearby, key=lambda k: k.co.x)
                ],
            }
        )
    return rows


def describe(obj, frame):
    return {
        "name": obj.name,
        "type": obj.type,
        "source_script": obj.get("c2v_source_script"),
        "source_workspace": obj.get("c2v_source_workspace"),
        "collections": [c.name for c in obj.users_collection],
        "world_matrix": [list(row) for row in obj.matrix_world],
        "dimensions": list(obj.dimensions),
        "animation": animation(obj, frame),
        "constraints": [
            {
                "name": c.name,
                "type": c.type,
                "influence": c.influence,
                "target": getattr(c, "target", None).name if getattr(c, "target", None) else None,
            }
            for c in obj.constraints
        ],
    }


def inspect(request):
    scene = bpy.context.scene
    frame = frame_at(scene, request["time_seconds"])
    scene.frame_set(int(frame), subframe=frame % 1)
    camera = active_camera(scene, frame)
    for obj in scene.objects:
        if obj.hide_render:
            obj.hide_set(True)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = camera.evaluated_get(depsgraph)
    if camera.data.type not in {"PERSP", "ORTHO"}:
        raise ValueError("Point picking supports perspective and orthographic cameras")
    corners = evaluated.data.view_frame(scene=scene)
    (x, y) = (request["x"], request["y"])
    point = Vector(
        (
            min((v.x for v in corners))
            + x * (max((v.x for v in corners)) - min((v.x for v in corners))),
            max((v.y for v in corners))
            - y * (max((v.y for v in corners)) - min((v.y for v in corners))),
            corners[0].z,
        )
    )
    matrix = evaluated.matrix_world
    origin = (
        matrix.translation
        if camera.data.type == "PERSP"
        else matrix @ Vector((point.x, point.y, 0))
    )
    direction = (matrix @ point - origin).normalized()
    origin += direction * camera.data.clip_start
    (hit, location, normal, face, obj, hit_matrix) = scene.ray_cast(
        depsgraph, origin, direction, distance=camera.data.clip_end
    )
    result = {
        "time_seconds": request["time_seconds"],
        "scene_frame": frame,
        "image_point": [x, y],
        "camera": describe(camera, frame),
        "hit": bool(hit),
        "picking_mode": "first_geometric_surface",
        "source_note": "source_script records the construction entrypoint; older untagged objects return null",
    }
    result["camera"].update(
        lens_mm=evaluated.data.lens, lens_animation=animation(camera.data, frame)
    )
    if hit:
        (parents, parent) = ([], obj.parent)
        while parent:
            parents.append(describe(parent, frame))
            parent = parent.parent
        result.update(
            object=describe(obj, frame),
            parents=parents,
            location=list(location),
            normal=list(normal),
            face_index=face,
            instance_matrix=[list(row) for row in hit_matrix],
        )
    return result
