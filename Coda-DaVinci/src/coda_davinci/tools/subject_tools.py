"""Model-facing subject operations; state lives outside individual agent histories."""

import json
import uuid
from coda_davinci.tools.tool_results import ToolResult, ViewedImage
from coda_davinci.media.subjects import identity_reference, reference_layout
from coda_davinci.media.artifacts import file_hash


class SubjectTools:
    def note_scene_reference_views(self, images):
        index = self.meta / "scene_references/index.json"
        if not images or not index.is_file():
            return
        from coda_davinci.media.video_media import save

        viewed = {image.path.resolve() for image in images}
        with self.workspace_lock:
            records = json.loads(index.read_text())
            changed = False
            for item in records:
                if (self.root / item["path"]).resolve() in viewed and (not item.get("viewed")):
                    item["viewed"] = True
                    changed = True
            if changed:
                save(index, records)

    def generate_scene_reference(self, label, prompt, reference_path, regenerate):
        if self.context and self.context.is_child:
            raise ValueError("The root agent must create and approve scene appearance references")
        from coda_davinci.providers.images import generate_reference

        safe = "".join((ch if ch.isalnum() or ch in "-_" else "_" for ch in label)).strip("_")
        if not safe:
            raise ValueError("label must contain at least one letter or digit")
        directory = self.root / ".harness" / "scene_references"
        directory.mkdir(parents=True, exist_ok=True)
        index_path = directory / "index.json"
        records = json.loads(index_path.read_text()) if index_path.exists() else []
        existing = next((item for item in records if item["label"] == label), None)
        if existing and (not regenerate):
            image = self.root / existing["path"]
            if not image.is_file():
                raise ValueError(
                    "Cached scene reference is missing; inspect the scene reference ledger before regenerating"
                )
            return ToolResult(
                {"cached": True, "label": label, "path": existing["path"]},
                images=[ViewedImage(image, f"Scene reference: {label}")],
            )
        pending = directory / f"pending_{safe}.json"
        source = self.path(reference_path) if reference_path else None
        with pending.open("x") as stream:
            json.dump(
                {"label": label, "prompt": prompt, "reference_path": reference_path},
                stream,
                ensure_ascii=False,
                indent=2,
            )
        try:
            with self.model_request():
                (image, provenance) = generate_reference(
                    directory, "", "", prompt, source, layout="scene-board", subject_kind="scene"
                )
        except Exception as exc:
            if getattr(exc, "status_code", None) in (400, 401, 403, 404, 422, 429) or isinstance(
                exc, ValueError
            ):
                pending.rename(pending.with_name(f"failed_{uuid.uuid4().hex}.json"))
            raise
        relative = image.relative_to(self.root).as_posix()
        record = {
            "label": label,
            "path": relative,
            "prompt": prompt,
            "viewed": False,
            "reference_path": reference_path,
            "provenance": provenance,
        }
        from coda_davinci.media.video_media import save

        with self.workspace_lock:
            records = json.loads(index_path.read_text()) if index_path.exists() else []
            records = [item for item in records if item["label"] != label]
            records.append(record)
            save(index_path, records)
        pending.unlink()
        return ToolResult(
            {"status": "success", "label": label, "path": relative},
            images=[ViewedImage(image, f"Scene reference: {label}")],
        )

    def register_subject(
        self, subject_id, kind, identity_caption, outfit_id, outfit_caption, expected_revision
    ):
        if self.context and self.context.is_child:
            raise ValueError(
                "Ask the main agent to register/update identities; children may read and prepare reference images"
            )
        return self.subjects.materialize(
            self.subjects.register(
                subject_id, kind, identity_caption, outfit_id, outfit_caption, expected_revision
            ),
            self.root,
        )

    def list_subjects(self):
        return {"subjects": self.subjects.list()}

    def get_subject(self, subject_id):
        return self.subjects.materialize(self.subjects.get(subject_id), self.root)

    def select_subject_identity_reference(self, subject_id, outfit_id, expected_revision):
        if self.context and self.context.is_child:
            raise ValueError(
                "The main agent must review and select a replacement identity reference"
            )
        return self.subjects.materialize(
            self.subjects.select_identity(subject_id, outfit_id, expected_revision), self.root
        )

    def bind_subject_reference(self, subject_id, outfit_id, path, expected_revision, layout=None):
        source = self.path(path)
        item = self.subjects.bind(
            subject_id,
            outfit_id,
            source,
            expected_revision,
            {"source": "provided", "agent_path": path},
            layout=layout,
        )
        return self.subjects.materialize(item, self.root)

    def generate_subject_reference(
        self, subject_id, outfit_id, prompt, reference_path, regenerate, layout=None
    ):
        from coda_davinci.providers.images import generate_reference

        item = self.subjects.get(subject_id)
        outfit = item["outfits"][outfit_id]
        layout = reference_layout(
            layout or ("character-sheet" if item["kind"] == "person" else "single")
        )
        if outfit.get("reference") and (not regenerate):
            actual = outfit["reference"].get("layout", "single")
            return {
                "cached": True,
                "requested_layout": layout,
                "actual_layout": actual,
                "layout_matches": actual == layout,
                "subject": self.subjects.materialize(item, self.root),
            }
        source_reference = None
        if reference_path:
            source_reference = self.path(reference_path)
        else:
            known = outfit.get("reference") or identity_reference(item)
            if known:
                source_reference = self.subjects.root / known["path"]
        source_digest = file_hash(source_reference) if source_reference else None
        pending = self.subjects.root / f"pending_{subject_id}_{outfit_id}.json"
        try:
            with pending.open("x") as stream:
                json.dump(
                    {
                        "prompt": prompt,
                        "revision": item["revision"],
                        "layout": layout,
                        "source_reference_sha256": source_digest,
                    },
                    stream,
                )
        except FileExistsError:
            raise ValueError(
                "Reference generation already submitted or unresolved; inspect the existing tool job, do not resubmit"
            )
        try:
            with self.model_request():
                (source, provenance) = generate_reference(
                    self.subjects.root / "image_jobs",
                    item["identity_caption"],
                    outfit["caption"],
                    prompt,
                    source_reference,
                    layout=layout,
                    subject_kind=item["kind"],
                )
            if source_reference:
                provenance["source_reference_sha256"] = source_digest
            updated = self.subjects.bind(
                subject_id, outfit_id, source, item["revision"], provenance
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) in (400, 401, 403, 404, 422, 429) or isinstance(
                exc, ValueError
            ):
                pending.rename(pending.with_name("failed_" + uuid.uuid4().hex + ".json"))
            raise
        pending.unlink()
        local = self.subjects.materialize(updated, self.root)
        image = self.root / local["outfits"][outfit_id]["reference"]["path"]
        return ToolResult(
            {"subject": local},
            images=[ViewedImage(image, f"{subject_id}/{outfit_id} appearance reference")],
        )
