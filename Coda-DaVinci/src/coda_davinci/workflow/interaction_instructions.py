"""Practical sequential interaction guidance; separate from official benchmark scoring."""


def interaction_rules(settings):
    from coda_davinci.workflow.modeling_policy import system_guidance

    rules = (
        "\nSEQUENTIAL INTERACTION MODE (overrides whole-case and strict-delivery guidance):\nYou stay in the SAME case workspace and conversation across rounds. Only the current\ninteraction is new work; earlier interactions are completed history. Do not anticipate\nor read future turn instructions. Preserve changes already made to the scene.\nIn the first round build/save a reusable Blender scene. On later rounds load the existing\n.blend, modify the relevant camera/objects/action, and reuse assets and child task IDs;\ndo not reconstruct the environment. Use run_blender with blend_file pointing to the saved scene,\nthen apply only the current changes. Save the current endpoint state before finish and\nwrite scene_state.json with the .blend path, frame and useful camera/object state notes.\nYou decide the video-model inputs for each round: previous generated video plus an\nupdated proxy, previous video alone, or video_tail_frame(previous_video) as an image.\nA proxy upload is OPTIONAL here. Pass proxy_video=null and shot_manifest_path=null for\nreference-only generation, with duration_seconds for the current round. Do not create\nunnecessary geometry/camera tasks when a saved state or tail-frame reference is sufficient.\nRead video-pe once and reuse its guidance; ask existing children for targeted fixes only.\nCancel obsolete background work before finishing so it cannot modify the next round's scene.\nfinish completes THIS ROUND and preserves the scene/memory/team for the next interaction.\n"
        + (
            "\nPRACTICAL ACCEPTANCE: deliver a usable best effort, not a perfect benchmark score.\nPrioritize the requested subject/action/view change and basic continuation. Minor texture,\nlighting, clothing-detail, framing, resolution or timing differences are notes, not blockers.\nInspect a useful portion of the actual result; no fixed frame count or all-shot coverage is\nrequired. record_video_review may use accepted=true WITH issues; finish may retain\nremaining_issues. Empty/nonapplicable review fields are allowed. Do not fabricate scores.\nFix observed major defects when useful, then deliver the best usable version\nand record limitations. Do not repeatedly polish cosmetic differences. A missing/corrupt\nvideo or failed service call is a real failure; never claim those produced a deliverable.\n"
            if settings.review_policy == "practical"
            else ""
        )
    )
    if ("auto" if settings.generation_route == "direct" else "required") == "required":
        rules = rules.replace(
            "A proxy upload is OPTIONAL here.",
            "The current inspected proxy and manifest are REQUIRED here.",
        )
        rules = rules.replace(
            "Pass proxy_video=null and shot_manifest_path=null for\nreference-only generation, with duration_seconds for the current round.",
            "Provide the current proxy and manifest for this round.",
        )
        rules = rules.replace(
            "You decide the video-model inputs for each round: previous generated video plus an\nupdated proxy, previous video alone, or video_tail_frame(previous_video) as an image.",
            "Use the current inspected proxy and manifest together with authorized continuation references.",
        )
        rules = rules.replace(
            "Do not create\nunnecessary geometry/camera tasks when a saved state or tail-frame reference is sufficient.",
            "Reuse saved assets, but inspect and encode a current-round proxy before submission.",
        )
    return rules + system_guidance(settings)
