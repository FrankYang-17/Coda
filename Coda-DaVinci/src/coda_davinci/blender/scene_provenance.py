"""在 Blender 脚本执行和保存时记录新对象的构建入口。"""

import bpy


def begin(source_label, workspace):
    before = {obj.session_uid for obj in bpy.data.objects}

    @bpy.app.handlers.persistent
    def record(*_):
        for obj in bpy.data.objects:
            if obj.session_uid not in before and (not obj.get("c2v_source_script")):
                obj["c2v_source_script"] = source_label
                obj["c2v_source_workspace"] = workspace

    bpy.app.handlers.save_pre.append(record)
    return record


def end(record):
    record()
    if record in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(record)
