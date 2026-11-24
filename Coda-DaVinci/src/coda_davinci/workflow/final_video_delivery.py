"""Verify that the root delivered a reviewed generated movie, not its proxy."""

import json
from coda_davinci.media.artifacts import file_hash


def load_final_video_delivery(workspace, coding):
    value = coding.get("final_video")
    if not isinstance(value, str) or not value:
        raise ValueError("The root must finish with a reviewed video_model result")
    video = (workspace / value).resolve()
    if not video.is_relative_to(workspace.resolve()) or not video.is_file():
        raise ValueError("Final generated video must exist inside the workspace")
    receipt = workspace / ".harness/video_workflow/final_delivery.json"
    if not receipt.is_file():
        raise ValueError("Missing audited final generated-video delivery")
    delivery = json.loads(receipt.read_text())
    if (
        delivery.get("status") != "completed"
        or delivery.get("final_video") != value
        or delivery.get("render_uuid") != coding.get("render_uuid")
    ):
        raise ValueError("Final generated-video delivery does not match the root result")
    manifest = json.loads((video.parent / "video_manifest.json").read_text())
    approval_path = video.parent / "acceptance.json"
    approval = json.loads(approval_path.read_text()) if approval_path.is_file() else {}
    limit_delivery = (
        approval.get("delivery_reason") == "rework_limit_reached"
        and approval.get("rework_limit") == 3
        and (approval.get("render_uuid") == coding.get("render_uuid"))
    )
    if (
        manifest.get("kind") != "generated_video"
        or manifest.get("render_uuid") != coding.get("render_uuid")
        or manifest.get("technical", {}).get("status") != "passed"
        or (not manifest.get("review"))
        or (not manifest["review"].get("accepted") and (not limit_delivery))
        or (file_hash(video) != manifest.get("sha256"))
    ):
        raise ValueError("Final video has no matching technical and visual acceptance evidence")
    return delivery
