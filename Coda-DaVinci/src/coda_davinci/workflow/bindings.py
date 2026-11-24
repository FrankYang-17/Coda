"""Bind proxy identity colors to semantic subjects."""

import json


def color_prompt(settings, manifest):
    bindings = [
        {
            "entity_id": entity["entity_id"],
            "subject": entity["user_subject"],
            "proxy_color": entity["proxy_color"],
            "reference_bindings": entity.get("reference_bindings", []),
        }
        for entity in manifest["entity_map"]
    ]
    return (
        "Proxy identity-color control for 白模视频: each tracked subject has a stable, distinct color. Use the following color-to-subject bindings to identify the subjects throughout the video:\n"
        + json.dumps(bindings, ensure_ascii=False)
        + "\nIdentity colors are lookup markers only, not final clothing, skin, paint or material colors. Use the requested appearance and authorized references for final colors and materials. Do not render identity labels, color legends or debug markers."
    )
