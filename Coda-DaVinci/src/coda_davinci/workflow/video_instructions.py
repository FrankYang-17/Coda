from coda_davinci.runtime.prompts import load_prompt

"Keep full-video PE, generation, inspection and rework in the root's own session."


def adapt_video_instructions(prompt, settings, *, child):
    prompt += "\nWhen using minimax-h3 or minimax-h3-max, the complete submitted text must be at most 7000 characters, including spaces, newlines and automatically appended subject, shot and reference-binding descriptions. Budget for those descriptions while writing the prompt; keep them concise, preserve required events and reference mappings, and check the complete text length before submitting.\n"
    if settings.context_auto_compact:
        prompt += "\nContext compaction is enabled. Keep decisions, identities, paths, revision IDs and\nreview findings in files. Original goals and recent tool exchanges are retained;\nfull transcripts are archived under .harness/context/. Reopen relevant skills and\nspecific evidence as needed. Query existing jobs; do not duplicate uncertain submissions.\n"
    if not settings.proxy_mode:
        return prompt
    prompt = prompt.replace(
        "Inspect the encoded package and use its render_uuid for final video submission.",
        "Inspect the encoded Blender proxy. The root uses it as input to video_model, not as the final delivery.",
    )
    from coda_davinci.workflow.modeling_policy import system_guidance

    prompt += system_guidance(settings)
    prompt += "\nREFERENCE IMAGE INPUT REQUIREMENTS:\nBefore submitting to Seedance 2.5, check the actual saved dimensions of EVERY\nreference image, including original inputs, crops, scene references, identity/outfit\nimages attached through subject bindings, and continuation tail frames. Each image\nmust have width >= 300 px, height >= 300 px, width * height >= 90,000 pixels,\nand width / height within 0.39-2.50. Use PNG, JPEG or WebP. Check the final file\nafter every crop or resize; sufficient pixel area alone does not ensure a valid width\nor height (for example, 255x355 still fails the minimum width).\nFor an editable reference below these limits, make a separate copy and scale both\ndimensions by the same factor until BOTH sides and the area meet the limits.\nPreserve proportions, identity, content and reference roles; never stretch the image.\nIf its aspect ratio is invalid, use authorized context or padding without removing\nrequired subject features. Preserve originals, inspect the prepared copy and update\nthe explicit reference path or subject/outfit binding to the validated file.\nCorrect invalid image\ndimensions before resubmitting instead of repeating unchanged InvalidParameter requests.\nThese are input preparation checks, not prose to append to the final movie prompt.\n"
    if child:
        return (
            prompt
            + "\nThe parent owns final PE and video_model submission. Deliver your scoped Blender,\ncamera, reference or review work. The parent may resume you with generated-video\nfailures; correct the cause and return updated artifacts and inspected evidence.\n"
        )
    prompt += "\nFor a text-to-video task with no user-provided image or video, read scene-reference\nbefore constructing detailed Blender geometry. Generate and inspect a stable scene\nappearance reference, then use it while choosing visible structure in the proxy and\npass its workspace path in reference_images to video_model. The scene reference\nprovides finished appearance; the proxy provides layout, counts, camera, motion,\ncontacts and changing states. Reuse the same scene reference on revisits.\n\nWhen video_model supplies a proxy comparison, inspect_video on its returned video shows\nproxy-above/generated-below frame pairs at the same elapsed time. The receipt also links\na synchronized comparison page/video. Check camera movement, subject placement,\nocclusion/reappearance and action/cut timing against the original task. Preview pairing\nuses actual time without stretching either video; an ended source is marked explicitly.\n"
    if settings.interactive:
        from coda_davinci.workflow.interaction_instructions import interaction_rules

        return prompt + interaction_rules(settings)
    return prompt + load_prompt("video_generation_1.md")
