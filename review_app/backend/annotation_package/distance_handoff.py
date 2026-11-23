"""Distance orchestration behind the shared assignment interface."""

from pathlib import Path

from review_app.backend.annotation_package import distance, species
from review_app.backend.annotation_package.archive import decode_archive, encode_archive
from review_app.backend.annotation_package.identity import IdentityMap
from review_app.backend.annotation_package.scope import ReturnPreview, bound_videos
from review_app.backend.db.models import HandoffIdentityMap, Project, ProjectDir


def graph_ids(context, work):
    deps = context["distance"]
    return (
        ("distance_reference", [r["id"] for r in deps["references"]]),
        ("distance_frame", [r["id"] for r in deps["frames"]]),
        ("distance_import", [r["id"] for r in deps["imports"]]),
        ("distance_task", [u["task"]["id"] for u in work if u["task"]]),
        ("distance_individual", [r["id"] for u in work for r in u["individuals"]]),
        ("distance_sample", [r["id"] for u in work for r in u["samples"]]),
    )


def bind_origin(identities, context, work):
    for kind, ids in graph_ids(context, work):
        for value in ids:
            identities.bind(kind, value, value)


def portable_collection(session, binding):
    videos = bound_videos(session, binding)
    roots = session.query(ProjectDir).filter_by(project_id=binding.project_id).all()
    context, work = distance.collect(
        session,
        session.get(Project, binding.project_id),
        list(videos.values()),
        roots,
        binding.target_species,
    )
    identities = IdentityMap(session, binding.id)
    mapping = {
        r.local_id: r.portable_id
        for r in session.query(HandoffIdentityMap).filter_by(handoff_id=binding.id)
    }
    for kind, ids in graph_ids(context, work):
        for value in ids:
            mapping[value] = (
                identities.portable(kind, value)
                if binding.role == "working"
                else mapping.get(value, value)
            )
    # Discovery media may be collected under a synthetic ID for registered-only footage.
    for row in context["media"]:
        if row["video_id"] not in mapping:
            candidates = [
                m
                for m in binding.scope["media"]
                if m["video_path"] == row["video_path"]
                and m["root_id"] == mapping.get(row["root_id"], row["root_id"])
            ]
            if not candidates and binding.role != "origin":
                raise ValueError("Reference footage is outside the saved assignment media scope")
            mapping[row["video_id"]] = candidates[0]["video_id"] if candidates else row["video_id"]
    return distance.rewrite(context, mapping), distance.rewrite(work, mapping)


def export_return(session, binding):
    context, work = portable_collection(session, binding)
    manifest, original, _ = decode_archive(binding.setup_archive)
    manifest["direction"] = "return"
    # Species edits are local only. They never restore origin labels on acceptance.
    context["media"] = original["media"]
    context["predictions"] = []
    context["project_species"] = []
    context["distance"]["species_work"] = []
    return encode_archive(manifest, context, work)


def canonical(unit, context):
    return distance.canonical(
        unit,
        context["distance"]["frames"],
        context["distance"]["references"],
        context["distance"]["imports"],
    )


def scoped_context(context, work):
    """Only dependencies actually required by applied tasks can be imported."""
    import copy

    result = copy.deepcopy({key: value for key, value in context.items() if key != "deployments"})
    deps = result["distance"]
    frames = {
        s["reference_frame_id"] for u in work for s in u["samples"] if s["reference_frame_id"]
    }
    frames.update(
        s["ai_original"]["reference_frame_id"]
        for u in work
        for s in u["samples"]
        if s["ai_original"] and s["ai_original"].get("reference_frame_id")
    )
    imports = {
        r["import_id"]
        for u in work
        for r in ([u["task"]] if u["task"] else []) + u["individuals"]
        if r["import_id"]
    }
    deps["frames"] = [f for f in deps["frames"] if f["id"] in frames]
    imports.update(f["import_id"] for f in deps["frames"] if f["import_id"])
    refs = {f["reference_video_id"] for f in deps["frames"]}
    deps["references"] = [r for r in deps["references"] if r["id"] in refs]
    deps["imports"] = [r for r in deps["imports"] if r["id"] in imports]
    individuals = {r["id"] for u in work for r in u["individuals"]}
    rejected = {
        r["original"]["individual_id"]
        for u in work
        if u["task"]
        for r in u["task"]["rejected_ai"]
        if r.get("original") and r["original"].get("individual_id")
    }
    for source in deps["imports"]:
        payload = source["payload"]
        payload["references"] = [
            r for r in payload.get("references", []) if r["reference_frame_id"] in frames
        ]
        payload["individuals"] = [
            r
            for r in payload.get("individuals", [])
            if r["distance_individual_id"] in individuals or r.get("individual_id") in rejected
        ]
        payload["sampling"] = [
            r
            for r in payload.get("sampling", [])
            if r["video_id"] in {u["video_id"] for u in work}
        ]
        source_ids = {r["individual_id"] for r in payload["individuals"]}
        payload["predictions"] = [
            r for r in payload.get("predictions", []) if r["individual_id"] in source_ids
        ]
    return result


def plan(session, binding, manifest, context, work, data, policy, original):
    current_context, current_work = portable_collection(session, binding)
    current = {u["video_id"]: u for u in current_work}
    videos = bound_videos(session, binding)
    units, selected, warnings = [], [], []
    totals = dict.fromkeys(
        ("added", "unchanged", "replaced", "unapplied", "annotations_removed"), 0
    )
    owners = {}
    for unit in current_work:
        for row in (
            ([unit["task"]] if unit["task"] else []) + unit["individuals"] + unit["samples"]
        ):
            owners[row["id"]] = unit["video_id"]
    current_refs = {r["id"]: r["video_id"] for r in current_context["distance"]["references"]}
    returned_refs = {r["id"]: r["video_id"] for r in context["distance"]["references"]}
    current_frames = [
        {**f, "reference_video_id": current_refs[f["reference_video_id"]]}
        for f in current_context["distance"]["frames"]
    ]
    returned_frames = [
        {**f, "reference_video_id": returned_refs[f["reference_video_id"]]}
        for f in context["distance"]["frames"]
    ]
    matches = distance.match_reference_frames(
        returned_frames, current_frames, {f["id"]: f["id"] for f in current_frames}
    )
    comparison_context = {
        **context,
        "distance": {
            **context["distance"],
            "frames": [
                {**f, "offset_ms": matches[f["id"]]["offset_ms"]} if f["id"] in matches else f
                for f in context["distance"]["frames"]
            ],
        },
    }
    for returned in sorted(work, key=lambda u: u["video_id"]):
        for row in (
            ([returned["task"]] if returned["task"] else [])
            + returned["individuals"]
            + returned["samples"]
        ):
            if row["id"] in owners and owners[row["id"]] != returned["video_id"]:
                raise ValueError("Returned Distance identity belongs to another task")
        local = current[returned["video_id"]]
        before, after = distance.counts(local), distance.counts(returned)
        old = canonical(local, current_context)
        new = canonical(returned, comparison_context)
        if old == new:
            classification, action = "identical", "unchanged"
        elif not any(before.values()):
            classification, action = "new", "added"
        else:
            classification, action = (
                "different",
                "replaced" if policy == "use_imported" else "unapplied",
            )
        # Report removed graph identities/links, not just a net count decrease.

        def keys(unit):
            return {
                "task_state": {"task"} if distance.counts(unit)["task_state"] else set(),
                "individuals": {r["id"] for r in unit["individuals"]},
                "samples": {r["id"] for r in unit["samples"]},
                "tags": {(r["id"], t["key"]) for r in unit["individuals"] for t in r["tags"]},
                "behaviors": {(r["id"], k) for r in unit["samples"] for k in r["behaviors"]},
                "progress": {r["annotator_name"] for r in unit["progress"]},
            }

        a, b = keys(old), keys(new)
        removed = {key: len(a[key] - b[key]) if action == "replaced" else 0 for key in a}
        totals[action] += 1
        totals["annotations_removed"] += sum(removed.values())
        units.append(
            {
                "video_id": returned["video_id"],
                "video_path": videos[returned["video_id"]].video_path,
                "classification": classification,
                "action": action,
                "existing": before,
                "returned": after,
                "removed": removed,
            }
        )
        if action in {"added", "replaced"}:
            selected.append((videos[returned["video_id"]].video_id, returned))
    # Current confirmed-label context is advisory; saved work scope is authoritative.
    original_labels = {
        u["video_id"]: species.canonical(u)
        for u in original["distance"]["species_work"]
        if u["video_id"] in manifest["work_keys"]
    }
    now_labels = {
        u["video_id"]: species.canonical(u)
        for u in current_context["distance"]["species_work"]
        if u["video_id"] in manifest["work_keys"]
    }
    if original_labels != now_labels:
        warnings.append(
            "Species context has changed since preparation; saved Distance scope remains eligible and labels will not be restored."
        )
    dependencies = scoped_context(context, [u for _, u in selected])
    substitutions = []
    for frame in dependencies["distance"]["frames"]:
        existing = matches.get(frame["id"])
        if existing and (
            existing["id"] != frame["id"]
            or any(existing[k] != frame[k] for k in ("offset_ms", "playback_offset_ms", "mask"))
        ):
            substitutions.append(
                {
                    "returned_frame_id": frame["id"],
                    "existing_frame_id": existing["id"],
                    "distance_m": frame["distance_m"],
                    "video_path": next(
                        row["video_path"]
                        for row in context["media"]
                        if row["video_id"] == returned_refs[frame["reference_video_id"]]
                    ),
                    "returned_offset_ms": frame["offset_ms"],
                    "existing_offset_ms": existing["offset_ms"],
                    "returned_playback_ms": frame["playback_offset_ms"],
                    "existing_playback_ms": existing["playback_offset_ms"],
                }
            )
    if substitutions:
        warnings.append(
            "Existing admin calibration will be reused for matching reference frames; returned sample distances are preserved."
        )
    if totals["unapplied"]:
        warnings.append("Keep existing leaves later annotator updates unapplied.")
    if totals["replaced"]:
        warnings.append(
            "Use imported replaces complete Distance task states, including admin corrections and unfinished/empty work."
        )
    state = {
        "payload": distance.digest(data.hex()),
        "policy": policy,
        "scope": binding.scope,
        "current": current_work,
        "dependencies": current_context,
        "substitutions": substitutions,
    }
    token = distance.digest(state)
    # Reference substitutions are structured preview data, not merely prose.
    preview = ReturnPreview(
        token,
        policy,
        tuple(units),
        totals,
        tuple(warnings),
        tuple(substitutions),
        stage="distance",
        target_species=manifest["target_species"],
        species_context_changed=original_labels != now_labels,
    )
    return binding, dependencies, selected, preview


def accept(session, binding, context, selected, approved_substitutions):
    identities = IdentityMap(session, binding.id)
    maps = {
        r.portable_id: r.local_id
        for r in session.query(HandoffIdentityMap).filter_by(handoff_id=binding.id)
    }
    roots = {
        key: Path(session.get(ProjectDir, local).path)
        for key, local in maps.items()
        if session.get(ProjectDir, local) is not None
    }
    reference_media = {ref["video_id"] for ref in context["distance"]["references"]}
    media_paths = {
        row["video_id"]: (roots[row["root_id"]] / row["video_path"]).as_posix()
        for row in context["media"]
        if row["video_id"] in reference_media
    }
    work = [u for _, u in selected]
    used = distance.used_vocabulary(work, binding.target_species)
    definitions = {
        name: [r for r in context[name] if r[key] in used[name]]
        for name, key in (("species", "scientific_name"), ("behaviors", "key"), ("tags", "key"))
    }
    vocab = species.vocabulary(session, definitions)
    mapping = distance.restore_dependencies(
        session,
        binding.project_id,
        context,
        identities,
        maps,
        media_paths,
        work,
        approved_substitutions=approved_substitutions,
    )
    for video_id, unit in selected:
        distance.restore_work(
            session,
            binding.project_id,
            video_id,
            unit,
            vocab,
            identities,
            mapping,
            binding.target_species,
        )
