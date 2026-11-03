#!/usr/bin/env python3
"""Record version-bound editorial gates under the permanent auto policy.

Authorization summary (key points, not a verbatim per-topic user quote):
  cancel per-topic human confirmation; run the original steps in order
  automatically; quality checks are NOT skipped.

Flow: new -> outline_shown -> textbook_done -> post_done -> done.
Quality gates are machine-checked evidence:
  - textbook record: real length check + structured review evidence bound to sha
  - post record: cloud read-back snapshot whose body files are actually read and
    hashed against the local post/textbook files
  - finish: cover review bound to the current manifest/source/preview hashes
Local audit aid only: it verifies evidence attachments, never real Feishu content.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATES = {"new", "outline_shown", "textbook_done", "post_done", "done"}
AUTHORIZATION = "取消逐篇人工确认，按原步骤顺序自动执行；不跳过质量检查。"
TB_SCOPES = ("sources", "code", "diagram", "render", "counts")
NEVER_NA = {"sources", "counts"}  # always required, never not_applicable


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_save(path, state):
    fd, temp = tempfile.mkstemp(prefix=".workflow-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def record(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("artifact must be a file")
    return {"path": str(path), "sha256": digest(path)}


def load_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {what}: {exc}")


def check_tb_evidence(evidence_path, textbook_sha):
    ev = load_json(evidence_path, "review evidence")
    if ev.get("textbook_sha256") != textbook_sha:
        raise ValueError("review evidence is not bound to the current textbook version")
    ranges = ev.get("ranges", {})
    if not isinstance(ranges, dict):
        raise ValueError("review evidence needs a ranges object")
    for scope in TB_SCOPES:
        r = ranges.get(scope)
        if not isinstance(r, dict):
            raise ValueError(f"review evidence missing scope: {scope}")
        status = r.get("status")
        if scope in NEVER_NA and status != "pass":
            raise ValueError(f"review scope {scope} must always be pass (never not_applicable)")
        if status not in ("pass", "not_applicable"):
            raise ValueError(f"review scope {scope} must be pass or not_applicable")
        if status == "pass" and not str(r.get("evidence", "")).strip():
            raise ValueError(f"review scope {scope}=pass needs non-empty evidence")
        if status == "not_applicable" and not str(r.get("reason", "")).strip():
            raise ValueError(f"review scope {scope}=not_applicable needs a reason")
    return ev


def check_snapshot(snap_path, doc_url, post_sha, tb_sha):
    """Read the actual read-back body files and hash them, do not trust declared strings."""
    snap_path = Path(snap_path).resolve()
    s = load_json(snap_path, "doc snapshot")
    if s.get("doc_url") != doc_url:
        raise ValueError("snapshot doc_url differs from the registered textbook doc")
    if not str(s.get("doc_version", "")).strip():
        raise ValueError("snapshot needs a non-empty doc_version from the read-back")
    mods = s.get("modules", [])
    if [m.get("role") for m in mods] != ["post", "textbook"]:
        raise ValueError("doc modules must be ordered: post (front) then textbook")
    expected = {"post": post_sha, "textbook": tb_sha}
    for m in mods:
        body = m.get("body_file")
        if not body:
            raise ValueError(f"snapshot module {m.get('role')} needs body_file")
        bp = Path(body) if Path(body).is_absolute() else snap_path.parent / body
        actual = digest(bp)
        if actual != expected[m["role"]]:
            raise ValueError(
                f"read-back body of module {m['role']} does not match current local file "
                f"(doc={actual[:12]} local={expected[m['role']][:12]})")
    return s


def check_cover_review(path, tb_sha, post_sha, manifest_path, cover_files):
    ev = load_json(path, "cover review")
    if ev.get("textbook_sha256") != tb_sha:
        raise ValueError("cover review not bound to the current textbook version")
    if ev.get("post_sha256") != post_sha:
        raise ValueError("cover review not bound to the current post version")
    manifest_path = Path(manifest_path).resolve()
    if ev.get("cover_manifest_sha256") != digest(manifest_path):
        raise ValueError("cover review not bound to the current cover manifest file")
    cf = ev.get("cover_files")
    # cover_files shapes:
    #  - list: one {"source":{path,sha256}, "preview":{...}} entry per cover unit;
    #    flattened order unit0.source, unit0.preview[, unit1.source, unit1.preview].
    #  - legacy dict {"source": {...}, "preview": {...}} for a single editable unit
    #    (2 files), or a legacy single finished file (1 file, keys may repeat).
    pairs = []
    if isinstance(cf, dict):
        if len(cover_files) == 2:
            pairs = [(cf.get("source", {}), cover_files[0]),
                     (cf.get("preview", {}), cover_files[1])]
        elif len(cover_files) == 1:
            decl = cf.get("source") or cf.get("preview") or {}
            pairs = [(decl, cover_files[0])]
        else:
            raise ValueError("dict cover_files only fits a single cover unit; use a list for 1—2 units")
    elif isinstance(cf, list):
        unit_count = len(cover_files) if len(cover_files) == 1 else len(cover_files) // 2
        if len(cf) != unit_count:
            raise ValueError(f"cover_files list must declare {unit_count} unit(s), got {len(cf)}")
        flat = []
        for i, unit in enumerate(cf):
            if not isinstance(unit, dict):
                raise ValueError(f"cover_files[{i}] must be {{source, preview}}")
            flat.append(unit.get("source", {}))
            if len(cover_files) > 1:
                flat.append(unit.get("preview", {}))
        pairs = list(zip(flat, cover_files))
    else:
        raise ValueError("cover_files must be a list of {source, preview} units "
                         "or the legacy {source, preview} object")
    for decl, actual_path in pairs:
        if not str(decl.get("path", "")).strip():
            raise ValueError("cover review cover_files entry missing path")
        if decl.get("sha256") != digest(actual_path):
            raise ValueError("cover review cover_files entry not bound to actual file hash")
    for field in ("source_editable", "preview_verified"):
        if not str(ev.get(field, "")).strip():
            raise ValueError(f"cover review {field} must explain in non-empty text")
    for field in ("node_edge", "render", "semantic_review",
                  "coverage_check", "render_mobile", "style_check"):
        item = ev.get(field)
        if not (isinstance(item, dict) and item.get("status") == "pass"
                and str(item.get("evidence", "")).strip()):
            raise ValueError(f"cover review {field} must be {{status: pass, evidence: '...'}} "
                             f"(fail or missing evidence blocks release)")
    return ev


def resolve_cover_files(manifest, manifest_dir):
    """Flatten 1—2 cover units to ordered paths:
    unit0.source, unit0.preview[, unit1.source, unit1.preview]."""
    out = []
    has_cover = "cover" in manifest
    has_covers = "covers" in manifest
    if has_cover and has_covers:
        raise ValueError("cover and covers are mutually exclusive")
    if has_cover:
        c = manifest["cover"]
        if not (isinstance(c, dict) and isinstance(c.get("source"), str) and c["source"].strip()
                and isinstance(c.get("preview"), str) and c["preview"].strip()):
            raise ValueError("cover must be {source, preview} editable pair for one cover unit")
        out += [c["source"], c["preview"]]
    elif has_covers:
        cov = manifest["covers"]
        if not isinstance(cov, list) or not (1 <= len(cov) <= 2):
            raise ValueError("covers must contain 1—2 units; 0 units or 3+ units rejected")
        if all(isinstance(x, str) and x.strip() for x in cov):
            if len(cov) != 1:
                raise ValueError("legacy covers:[file] accepts exactly one string file; "
                                 "use covers:[{source,preview}, ...] for multiple units")
            out.append(cov[0])
        elif all(isinstance(x, dict) for x in cov):
            for i, c in enumerate(cov):
                if not (isinstance(c.get("source"), str) and c["source"].strip()
                        and isinstance(c.get("preview"), str) and c["preview"].strip()):
                    raise ValueError(f"covers[{i}] must be {{source, preview}} non-empty strings")
                out += [c["source"], c["preview"]]
        else:
            raise ValueError("covers items must be {source, preview} (or the legacy one string file)")
    else:
        raise ValueError("cover manifest needs cover:{source,preview} or covers:[1—2 {source,preview}]")
    return [p if Path(p).is_absolute() else str(manifest_dir / p) for p in out]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--topic", required=True)
    p = sub.add_parser("record")
    p.add_argument("--stage", choices=["outline", "textbook", "post"], required=True)
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--url", default="")
    p.add_argument("--review", type=Path, help="structured review evidence JSON")
    p.add_argument("--snapshot", type=Path, help="post stage: cloud read-back snapshot JSON")
    p.add_argument("--over-reason", default="")
    p.add_argument("--new-doc", action="store_true",
                   help="explicitly request a new cloud doc when re-recording textbook")
    p = sub.add_parser("finish")
    p.add_argument("--post", type=Path, required=True)
    p.add_argument("--cover-manifest", type=Path, required=True)
    p.add_argument("--cover-review", type=Path, required=True)
    p.add_argument("--doc-url", required=True)
    p = sub.add_parser("cover-edit")
    p.add_argument("--cover-manifest", type=Path, required=True)
    p.add_argument("--cover-review", type=Path, required=True)
    sub.add_parser("migrate")
    sub.add_parser("status")
    args = parser.parse_args()
    path = args.state.expanduser().absolute()
    try:
        if args.cmd == "init":
            if path.exists():
                raise ValueError("state already exists; inspect it rather than overwrite")
            if not path.parent.is_dir() or not args.topic.strip():
                raise ValueError("existing parent and nonempty topic are required")
            state = {"schema_version": 2, "topic": args.topic.strip(), "stage": "new",
                     "auto_policy": True, "authorization": AUTHORIZATION,
                     "doc": None, "artifacts": {}, "history_files": [],
                     "approvals": {}, "history": []}
            event = {"action": "init", "at": stamp()}
        else:
            state = json.loads(path.read_text(encoding="utf-8"))
            state.setdefault("history_files", [])
            state.setdefault("doc", None)
            if state.get("schema_version") == 1 and args.cmd != "migrate":
                raise ValueError("old workflow; run migrate first")
            if args.cmd != "migrate" and (state.get("schema_version") != 2
                                           or state.get("stage") not in STATES):
                raise ValueError("invalid workflow state")
            event = {"action": args.cmd, "at": stamp()}

        if args.cmd == "record":
            item = record(args.file)
            if args.stage == "outline":
                if state["stage"] not in {"new", "outline_shown", "textbook_done", "post_done", "done"}:
                    raise ValueError("outline cannot be recorded here")
                for stage_name in ("outline", "textbook", "post", "post_snapshot", "release"):
                    old = state["artifacts"].pop(stage_name, None)
                    if old:
                        state["history_files"].append({"stage": f"{stage_name}_archived", **old})
                state["artifacts"]["outline"] = item
                state["stage"] = "outline_shown"
            elif args.stage == "textbook":
                outline = state["artifacts"].get("outline")
                if not outline:
                    raise ValueError("outline must be shown before textbook")
                if digest(outline["path"]) != outline["sha256"]:
                    raise ValueError("outline file changed since shown; re-record outline")
                if not args.review:
                    raise ValueError("textbook record requires structured review evidence JSON")
                if not args.url.strip():
                    raise ValueError("textbook record requires --url (the cloud doc)")
                if state.get("doc") and args.url.strip() != state["doc"] and not args.new_doc:
                    raise ValueError("doc URL must stay the same; pass --new-doc for an explicit new doc request")
                from check_content import measure
                measured = measure(args.file.read_text(encoding="utf-8"), "textbook", args.over_reason)
                if not measured["ok"]:
                    raise ValueError(f"textbook length not acceptable: {measured}")
                check_tb_evidence(args.review, item["sha256"])
                for stage_name in ("textbook", "post", "post_snapshot", "release"):
                    old = state["artifacts"].pop(stage_name, None)
                    if old:
                        state["history_files"].append({"stage": f"{stage_name}_archived", **old})
                item["url"] = args.url.strip()
                item["review"] = record(args.review)
                state["artifacts"]["textbook"] = item
                state["doc"] = args.url.strip()
                state["stage"] = "textbook_done"
            else:  # post
                outline = state["artifacts"].get("outline")
                tb = state["artifacts"].get("textbook")
                if not outline:
                    raise ValueError("outline must be shown before post")
                if digest(outline["path"]) != outline["sha256"]:
                    raise ValueError("outline file changed since shown; re-record outline")
                if not tb:
                    raise ValueError("post requires a reviewed textbook (A->B->C order enforced)")
                if digest(tb["path"]) != tb["sha256"]:
                    raise ValueError("textbook file changed; re-run textbook review")
                check_tb_evidence(tb["review"]["path"], tb["sha256"])
                for stale in ("post", "post_snapshot", "release"):
                    old = state["artifacts"].pop(stale, None)
                    if old:
                        state["history_files"].append({"stage": f"{stale}_archived", **old})
                if not args.url.strip() or args.url.strip() != tb["url"]:
                    raise ValueError("post must go into the same doc as the textbook")
                if not args.snapshot:
                    raise ValueError("post record requires --snapshot (cloud read-back evidence)")
                from check_content import measure
                measured = measure(args.file.read_text(encoding="utf-8"), "post")
                if not measured["ok"]:
                    raise ValueError(
                        f"post length must be exactly 1500 (got {measured['characters']}); "
                        "1499 and 1501 both fail"
                    )
                check_snapshot(args.snapshot, tb["url"], item["sha256"], tb["sha256"])
                state["artifacts"]["post"] = item
                state["artifacts"]["post_snapshot"] = record(args.snapshot)
                state["stage"] = "post_done"
            event.update({"artifact_stage": args.stage, "sha256": item["sha256"]})

        elif args.cmd == "finish":
            if state["stage"] != "post_done":
                raise ValueError("finish requires post registered after reviewed textbook (A->B->C->D order)")
            outline = state["artifacts"]["outline"]
            tb = state["artifacts"]["textbook"]
            post = state["artifacts"]["post"]
            if digest(outline["path"]) != outline["sha256"]:
                raise ValueError("outline file changed since shown; re-record outline")
            if digest(tb["path"]) != tb["sha256"]:
                raise ValueError("textbook file lost or changed; re-run textbook review")
            if digest(post["path"]) != post["sha256"]:
                raise ValueError("post file lost or changed; re-run post registration")
            check_tb_evidence(tb["review"]["path"], tb["sha256"])
            if args.doc_url.strip() != tb["url"]:
                raise ValueError("doc-url must equal the textbook cloud doc URL")
            snap = check_snapshot(state["artifacts"]["post_snapshot"]["path"], tb["url"],
                                  post["sha256"], tb["sha256"])
            manifest_path = args.cover_manifest.resolve()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            from check_content import check_cover
            cover_check = check_cover(manifest)
            if not cover_check["ok"]:
                raise ValueError(f"cover manifest invalid: {cover_check['errors']}")
            if manifest.get("source_revision") != tb["sha256"]:
                raise ValueError("cover source_revision must equal textbook.txt sha256")
            cover_files = resolve_cover_files(manifest, manifest_path.parent)
            if any(not Path(c).stat().st_size for c in cover_files):
                raise ValueError("cover source/preview files must not be empty")
            check_cover_review(args.cover_review, tb["sha256"], post["sha256"],
                               manifest_path, cover_files)
            state["artifacts"]["release"] = {
                "post": post,
                "cover_manifest": record(manifest_path),
                "covers": [record(c) for c in cover_files],
                "cover_review": record(args.cover_review),
                "snapshot": state["artifacts"]["post_snapshot"],
                "textbook_sha256": tb["sha256"],
                "doc_url": tb["url"],
                "doc_version": snap["doc_version"]}
            state["stage"] = "done"

        elif args.cmd == "cover-edit":
            if state["stage"] != "done":
                raise ValueError("cover-edit only after done")
            rel = state["artifacts"]["release"]
            outline = state["artifacts"]["outline"]
            tb = state["artifacts"]["textbook"]
            post = state["artifacts"]["post"]
            if digest(outline["path"]) != outline["sha256"]:
                raise ValueError("outline file changed since shown; re-record outline")
            if digest(tb["path"]) != tb["sha256"]:
                raise ValueError("textbook changed; re-run review before editing the cover")
            if digest(post["path"]) != post["sha256"]:
                raise ValueError("post changed; cover cannot be re-bound to a stale post")
            # Old upstream evidence must still match current local files.
            check_snapshot(rel["snapshot"]["path"], rel["doc_url"], post["sha256"], tb["sha256"])
            manifest_path = args.cover_manifest.resolve()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            from check_content import check_cover
            cover_check = check_cover(manifest)
            if not cover_check["ok"]:
                raise ValueError(f"cover manifest invalid: {cover_check['errors']}")
            if manifest.get("source_revision") != tb["sha256"]:
                raise ValueError("cover source_revision must equal current textbook sha256")
            cover_files = resolve_cover_files(manifest, manifest_path.parent)
            if any(not Path(c).stat().st_size for c in cover_files):
                raise ValueError("cover source/preview files must not be empty")
            check_cover_review(args.cover_review, tb["sha256"], post["sha256"],
                               manifest_path, cover_files)
            rel["cover_manifest"] = record(manifest_path)
            rel["covers"] = [record(c) for c in cover_files]
            rel["cover_review"] = record(args.cover_review)

        elif args.cmd == "migrate":
            if state.get("schema_version") == 2:
                event["note"] = "already migrated; no-op"
            else:
                old_stage = state.get("stage")
                tb = state.get("artifacts", {}).get("textbook")
                outline = state.get("artifacts", {}).get("outline")
                # Validate old files still exist, match hashes, and evidence passes.
                tb_ok = False
                if tb:
                    try:
                        if digest(tb["path"]) == tb["sha256"]:
                            from check_content import measure
                            m = measure(Path(tb["path"]).read_text(encoding="utf-8"), "textbook")
                            if m["ok"]:
                                check_tb_evidence(tb["review"]["path"], tb["sha256"])
                                tb_ok = True
                    except (KeyError, OSError, ValueError):
                        tb_ok = False
                outline_ok = bool(outline)
                if outline_ok:
                    try:
                        outline_ok = digest(outline["path"]) == outline["sha256"]
                    except OSError:
                        outline_ok = False
                if tb_ok and outline_ok:
                    new_stage = "textbook_done"
                elif outline_ok:
                    new_stage = "outline_shown"
                else:
                    new_stage = "new"
                old_release = state.get("artifacts", {}).pop("release", None)
                if old_release:
                    state["history_files"].append({"stage": "release_archived", **old_release})
                state.update({"schema_version": 2, "stage": new_stage,
                              "auto_policy": True, "authorization": AUTHORIZATION,
                              "doc": (tb or {}).get("url"),
                              "migrated_from": old_stage,
                              "old_approvals_note": "preserved as history; old approvals are not quality passes"})
                event.update({"migrated_from": old_stage, "new_stage": new_stage})

        elif args.cmd == "status":
            rel = state.get("artifacts", {}).get("release", {})
            print(json.dumps({"ok": True, "state": state,
                              "auto_policy": state.get("auto_policy", False),
                              "release_bound": {"doc_url": rel.get("doc_url"),
                                                "textbook_sha256": rel.get("textbook_sha256")}},
                             ensure_ascii=False, indent=2))
            return 0

        state["history"].append(event)
        atomic_save(path, state)
        print(json.dumps({"ok": True, "stage": state["stage"], "state_path": str(path)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
