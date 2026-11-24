"""Episode-wide identity records and immutable reference-image versions."""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
from PIL import Image


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch("[a-z][a-z0-9_]{0,63}", value):
        raise ValueError("Subject/outfit IDs must be lowercase identifiers, e.g. person_a")
    return value


def reference_layout(value):
    if value not in ("single", "character-sheet"):
        raise ValueError("Reference layout must be single or character-sheet")
    return value


def identity_reference(item):
    """Read older records without requiring a destructive registry migration."""
    return item.get("identity_reference") or next(
        (outfit["reference"] for outfit in item["outfits"].values() if outfit.get("reference")),
        None,
    )


class SubjectStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "subjects.json"

    @contextmanager
    def transaction(self):
        with (self.root / "subjects.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = (
                json.loads(self.path.read_text())
                if self.path.exists()
                else {"version": 1, "subjects": {}}
            )
            yield data
            temp = self.root / ("registry_" + uuid.uuid4().hex + ".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            temp.replace(self.path)

    def list(self):
        with self.transaction() as data:
            return deepcopy(list(data["subjects"].values()))

    def get(self, subject_id):
        with self.transaction() as data:
            return deepcopy(data["subjects"][identifier(subject_id)])

    def register(
        self, subject_id, kind, identity_caption, outfit_id, outfit_caption, expected_revision
    ):
        identifier(subject_id)
        identifier(outfit_id)
        if (
            kind not in ("person", "animal")
            or not identity_caption.strip()
            or (not outfit_caption.strip())
        ):
            raise ValueError(
                "Supply person/animal, identity_caption and outfit_caption (use natural coat for animals)"
            )
        with self.transaction() as data:
            old = data["subjects"].get(subject_id)
            if old and old["revision"] != expected_revision:
                raise ValueError("Subject revision changed; get_subject before updating")
            if not old and expected_revision not in (None, 0):
                raise ValueError("New subject requires expected_revision=null or 0")
            if (
                old
                and old["identity_caption"] != identity_caption
                and any((o.get("reference") for o in old["outfits"].values()))
            ):
                raise ValueError(
                    "Identity has bound references; create a new subject/version instead of silently changing identity"
                )
            item = old or {"subject_id": subject_id, "outfits": {}, "revision": 0}
            if old and old["kind"] != kind:
                raise ValueError("A subject cannot change species/kind")
            outfit = item["outfits"].get(outfit_id, {})
            if outfit.get("reference") and outfit.get("caption") != outfit_caption:
                raise ValueError(
                    "Use a new outfit_id for changed clothing with an existing reference"
                )
            item.update(kind=kind, identity_caption=identity_caption, revision=item["revision"] + 1)
            item["outfits"][outfit_id] = {**outfit, "caption": outfit_caption}
            data["subjects"][subject_id] = item
            return deepcopy(item)

    def bind(self, subject_id, outfit_id, source, expected_revision, provenance, layout=None):
        source = Path(source)
        layout = reference_layout(layout or provenance.get("layout", "single"))
        with Image.open(source) as image:
            image.verify()
        with self.transaction() as data:
            item = data["subjects"][identifier(subject_id)]
            if item["revision"] != expected_revision:
                raise ValueError(
                    "Subject changed during image preparation; inspect before binding the image"
                )
            outfit = item["outfits"][identifier(outfit_id)]
            anchor = deepcopy(identity_reference(item))
            folder = self.root / "references" / subject_id / outfit_id
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (uuid.uuid4().hex + source.suffix.lower())
            shutil.copyfile(source, target)
            reference = {
                "path": str(target.relative_to(self.root)),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "layout": layout,
                "provenance": deepcopy(provenance),
            }
            if outfit.get("reference"):
                outfit.setdefault("reference_history", []).append(outfit["reference"])
            outfit["reference"] = reference
            item["revision"] += 1
            item["identity_reference"] = anchor or deepcopy(reference)
            item.setdefault("identity_outfit_id", outfit_id)
            return deepcopy(item)

    def select_identity(self, subject_id, outfit_id, expected_revision):
        with self.transaction() as data:
            item = data["subjects"][identifier(subject_id)]
            if item["revision"] != expected_revision:
                raise ValueError(
                    "Subject revision changed; get_subject before selecting an identity reference"
                )
            selected = item["outfits"][identifier(outfit_id)].get("reference")
            if not selected:
                raise ValueError("Selected outfit has no reference image")
            old = identity_reference(item)
            if old and old["sha256"] != selected["sha256"]:
                item.setdefault("identity_reference_history", []).append(deepcopy(old))
            item["identity_reference"] = deepcopy(selected)
            item["identity_outfit_id"] = outfit_id
            item["revision"] += 1
            return deepcopy(item)

    def materialize(self, item, workspace):
        """Expose read-only input copies inside each agent's own workspace."""
        item = deepcopy(item)
        anchor = identity_reference(item)
        if anchor:
            source = self.root / anchor["path"]
            relative = Path("inputs/subjects") / item["subject_id"] / "identity" / source.name
            target = Path(workspace) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(source, target)
            item["identity_reference"] = {**deepcopy(anchor), "path": relative.as_posix()}
        for outfit_id, outfit in item["outfits"].items():
            ref = outfit.get("reference")
            if ref:
                source = self.root / ref["path"]
                relative = Path("inputs/subjects") / item["subject_id"] / outfit_id / source.name
                target = Path(workspace) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copyfile(source, target)
                ref["path"] = relative.as_posix()
        return item


def bind_manifest(store, manifest, references, *, preserve_reference_set=False):
    """Resolve IDs/outfit states and assign actual request image positions centrally."""
    from coda_davinci.workflow.references import ReferenceImage

    manifest = deepcopy(manifest)
    records = {s["subject_id"]: s for s in store.list()}
    if not records:
        return (manifest, list(references), "")
    entities = {e["entity_id"]: e for e in manifest["entity_map"]}
    unused = set(records) - set(entities)
    if unused:
        raise ValueError(f"Registered subjects absent from final entity_map: {sorted(unused)}")
    managed_hashes = set()
    active_hashes = set()
    for item in records.values():
        all_refs = [identity_reference(item), *item.get("identity_reference_history", [])]
        for outfit in item["outfits"].values():
            all_refs += [outfit.get("reference"), *outfit.get("reference_history", [])]
        managed_hashes.update((ref["sha256"] for ref in all_refs if ref))
    for shot in manifest["shots"]:
        states = shot.get("subject_states", {})
        if not isinstance(states, dict):
            raise ValueError("shot.subject_states must be an object")
        for subject_id in shot["visible_entity_ids"]:
            if subject_id not in records:
                continue
            item = records[subject_id]
            anchor = identity_reference(item)
            if anchor:
                active_hashes.add(anchor["sha256"])
            state = states.get(subject_id, {})
            if not isinstance(state, dict):
                raise ValueError("Each subject state must be an object")
            outfit_id = state.get("outfit_id")
            if outfit_id is None and len(item["outfits"]) == 1:
                outfit_id = next(iter(item["outfits"]))
            ref = item["outfits"].get(outfit_id, {}).get("reference")
            if ref:
                active_hashes.add(ref["sha256"])
    preserve_labels = {
        b["media_label"]
        for entity in entities.values()
        for b in entity.get("reference_bindings", [])
        if entity["entity_id"] not in records
        or set(b.get("attributes", [])) - {"identity", "appearance", "face", "clothing"}
    }
    refs = [
        r
        for r in references
        if preserve_reference_set
        or r.label in preserve_labels
        or (digest := hashlib.sha256(r.path.read_bytes()).hexdigest()) not in managed_hashes
        or (digest in active_hashes)
    ]
    hashes = {hashlib.sha256(r.path.read_bytes()).hexdigest(): i for (i, r) in enumerate(refs)}
    for subject_id in records:
        extra = [
            b
            for b in entities[subject_id].get("reference_bindings", [])
            if set(b.get("attributes", [])) - {"identity", "appearance", "face", "clothing"}
        ]
        if extra:
            entities[subject_id]["reference_bindings"] = extra
        else:
            entities[subject_id].pop("reference_bindings", None)

    def attach(subject_id, reference, suffix, attributes):
        if reference is None:
            return None
        source = (store.root / reference["path"]).resolve()
        if (
            not source.is_relative_to(store.root.resolve())
            or hashlib.sha256(source.read_bytes()).hexdigest() != reference["sha256"]
        ):
            raise ValueError(f"{subject_id}: registered reference changed or escaped the registry")
        if reference["sha256"] not in hashes:
            label = f"subject_{subject_id}_{suffix}"
            if any((r.label == label for r in refs)):
                raise ValueError(f"Reference label collision: {label}")
            hashes[reference["sha256"]] = len(refs)
            refs.append(ReferenceImage(label, source))
        index = hashes[reference["sha256"]]
        binding = {
            "media_label": refs[index].label,
            "subject_selector": subject_id,
            "attributes": attributes,
        }
        entity_bindings = entities[subject_id].setdefault("reference_bindings", [])
        previous = next(
            (b for b in entity_bindings if b["media_label"] == binding["media_label"]), None
        )
        if previous:
            previous["attributes"] = list(dict.fromkeys([*previous["attributes"], *attributes]))
        else:
            entity_bindings.append(binding)
        return index + 1

    lines = [
        "Subject identity contract: preserve these identities across cuts. Proxy geometry/colors are not final appearance."
    ]
    for shot in manifest["shots"]:
        states = shot.get("subject_states", {})
        if not isinstance(states, dict):
            raise ValueError("shot.subject_states must map subject_id to outfit_id/state objects")
        bindings = []
        for subject_id in shot["visible_entity_ids"]:
            if subject_id not in records:
                continue
            item = records[subject_id]
            state = states.get(subject_id, {})
            if not isinstance(state, dict):
                raise ValueError("Each subject state must be an object")
            outfits = item["outfits"]
            outfit_id = state.get("outfit_id")
            if outfit_id is None:
                if len(outfits) != 1:
                    raise ValueError(f"{shot['shot_id']}: specify outfit_id for {subject_id}")
                outfit_id = next(iter(outfits))
            if outfit_id not in outfits:
                raise ValueError(f"{shot['shot_id']}: unknown outfit {outfit_id} for {subject_id}")
            outfit = outfits[outfit_id]
            ref = outfit.get("reference")
            anchor = identity_reference(item)
            identity_number = attach(subject_id, anchor, "identity", ["identity"])
            image_number = attach(subject_id, ref, outfit_id, ["identity", "appearance"])
            entities[subject_id]["identity_caption"] = item["identity_caption"]
            descriptor = {
                "subject_id": subject_id,
                "revision": item["revision"],
                "outfit_id": outfit_id,
                "identity_caption": item["identity_caption"],
                "outfit_caption": outfit["caption"],
                "state": state.get("state", ""),
                "image_number": image_number,
                "identity_image_number": identity_number,
                "reference_sha256": ref["sha256"] if ref else None,
                "identity_reference_sha256": anchor["sha256"] if anchor else None,
                "reference_layout": ref.get("layout", "single") if ref else None,
                "identity_reference_layout": anchor.get("layout", "single") if anchor else None,
            }
            bindings.append(descriptor)
            lines.append(
                f"{shot['shot_id']} frames [{shot['start_frame']},{shot['end_frame']}): "
                + json.dumps(descriptor, ensure_ascii=False)
            )
        shot["subject_bindings"] = bindings
    lines.append(
        "Image number N means 图片N / ImageN in THIS request. identity_image_number supplies stable facial/body identity; image_number supplies the current outfit appearance. The outfit caption controls clothing when they differ. A character-sheet contains multiple views/expressions of ONE subject, not multiple cast members. Never reproduce sheet panels, labels, swatches, or the neutral reference background in the video. Do not copy the reference pose or expression over the requested action. References do not determine whether scene backgrounds should be realistic or stylized; follow the user task."
    )
    return (manifest, refs, "\n".join(lines))
