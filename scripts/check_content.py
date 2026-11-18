#!/usr/bin/env python3
"""Offline length and diagram checks. Not a semantic or rendering verifier."""
import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path

RANGES = {"textbook": (5000, 10000), "post": (1500, 1500)}


def prose(text):
    # Strip code blocks, not their surrounding prose. Use plain text for exact counts.
    text = re.sub(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", "", text, flags=re.S)
    text = re.sub(r"<pre\b[^>]*>.*?</pre>", "", text, flags=re.S | re.I)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"</?(?:p|h[1-9]|b|strong|em|i|u|code|li|ul|ol|blockquote|br|span|title)(?:\s[^>]*)?/?>", "", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*+] |\d+[.)] |>[ ]?)", "", text, flags=re.M)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return html.unescape(text)


def measure(text, stage, over_reason=""):
    text = prose(text)
    chars = len(re.sub(r"\s", "", text))
    low, high = RANGES[stage]
    errors = []
    over_reason = (over_reason or "").strip()
    if chars < low:
        errors.append(f"below_min: {chars} < {low}")
    if chars > high:
        # 只有 textbook 允许带非空理由的超长；post/cover/diagram 与低于下限一律不绕过。
        if stage == "textbook" and over_reason:
            pass
        else:
            kind = "textbook_over_without_reason" if stage == "textbook" else "above_max"
            errors.append(f"{kind}: {chars} > {high}")
    result = {"ok": not errors, "stage": stage,
              "counting_rule": "visible_nonwhitespace_characters_including_punctuation",
              "characters": chars, "chinese_characters": len(re.findall(r"[\u3400-\u9fff]", text)),
              "english_words": len(re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", text)),
              "min": low, "max": high,
              "note": "Input must be body-only; title/tags/references separated by caller."}
    if stage == "textbook" and chars > high and over_reason:
        result["over_length_justified"] = True
        result["over_reason"] = over_reason
    return result


def check_diagram(data):
    errors = []
    if not isinstance(data, dict):
        return {"ok": False, "errors": ["manifest must be an object"]}
    nodes, edges = data.get("nodes", []), data.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        return {"ok": False, "errors": ["nodes must be a nonempty list"]}
    if not isinstance(edges, list):
        return {"ok": False, "errors": ["edges must be a list"]}
    if not data.get("source_revision"):
        errors.append("missing source_revision")
    if not data.get("title") or not data.get("legend"):
        errors.append("missing title or legend")
    valid_nodes = [n for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str) and n["id"]]
    if len(valid_nodes) != len(nodes):
        errors.append("each node requires a nonempty string id")
    ids = [n["id"] for n in valid_nodes]
    if len(ids) != len(set(ids)):
        errors.append("duplicate node id")
    idset, touched, adj, outgoing = set(ids), set(), {}, {}
    for n in valid_nodes:
        if not n.get("label"):
            errors.append(f"node {n['id']} has empty label")
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("edge must be object")
            continue
        a, b = edge.get("from"), edge.get("to")
        if not isinstance(a, str) or not isinstance(b, str) or a not in idset or b not in idset:
            errors.append("edge references missing node")
            continue
        touched.update((a, b))
        outgoing.setdefault(a, []).append(edge)
        if edge.get("kind") == "control":
            adj.setdefault(a, []).append(b)
        elif edge.get("kind") not in {"data", "dependency", "association"}:
            errors.append(f"edge {a}->{b} needs valid kind")
    for n in valid_nodes:
        nid = n["id"]
        if len(ids) > 1 and nid not in touched:
            errors.append(f"isolated node: {nid}")
        control = [e for e in outgoing.get(nid, []) if e.get("kind") == "control"]
        if n.get("kind") == "decision":
            if len(control) < 2 or any(not e.get("label") for e in control):
                errors.append(f"decision {nid} needs >=2 labelled control edges")
        if n.get("kind") == "terminal" and control:
            errors.append(f"terminal {nid} must not route into success automatically")
    if data.get("diagram_type") == "flow":
        starts = [n["id"] for n in valid_nodes if n.get("kind") == "start"]
        if not starts:
            errors.append("flow has no start")
        if not any(n.get("kind") == "terminal" for n in valid_nodes):
            errors.append("flow has no terminal")
        reached, queue = set(starts), list(starts)
        while queue:
            for target in adj.get(queue.pop(), []):
                if target not in reached:
                    reached.add(target)
                    queue.append(target)
        for n in valid_nodes:
            if not n.get("external", False) and n["id"] not in reached:
                errors.append(f"unreachable by control edges: {n['id']}")
    return {"ok": not errors, "node_count": len(nodes), "edge_count": len(edges),
            "errors": errors, "note": "Only structural checks; human semantic and rendered review still required."}


CORE_CATEGORIES = {"problem", "judgment", "module", "mechanism", "tradeoff", "failure", "boundary"}
COVER_VERSIONS = ("v1", "v2")
# V1 (frozen legacy): problem/judgment/module/mechanism all required.
REQUIRED_CATEGORIES_V1 = {"problem", "judgment", "module", "mechanism"}
# V2: problem/judgment required; at least one of module/mechanism.
REQUIRED_CATEGORIES_V2 = {"problem", "judgment"}
REQUIRED_ANY_V2 = {"module", "mechanism"}

# V2 bundled handwritten pair (asset manifest: assets/fonts/fonts-manifest.json).
# Machine only verifies registration, bundled file presence and hash consistency;
# rendered legibility and stroke balance are human review duties. V1 keeps its
# frozen legacy font gate and is never touched by these checks.
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_MANIFEST_PATH = FONTS_DIR / "fonts-manifest.json"
V2_ALLOWED_ZH_FONTS = {"LXGW WenKai"}
V2_ALLOWED_EN_FONTS = {"Caveat", "Patrick Hand"}


COVER_MIN_UNITS, COVER_MAX_UNITS = 1, 2


def _parse_cover_units(data, errors):
    """Parse cover units. Returns list of {"source", "preview"} non-empty strings.

    New schema: covers = list of 1—2 {"source", "preview"} editable units.
    Legacy: cover = one {"source", "preview"} pair; covers = [one string file].
    Two units are parallel topic blocks (no separate overview page); the union
    of both pages must still cover every core_point.
    """
    units = []
    has_cover = "cover" in data
    has_covers = "covers" in data
    if has_cover and has_covers:
        errors.append("cover and covers are mutually exclusive")
    if has_cover:
        c = data["cover"]
        if not (isinstance(c, dict) and isinstance(c.get("source"), str) and c["source"].strip()
                and isinstance(c.get("preview"), str) and c["preview"].strip()):
            errors.append("cover must be {source, preview} editable pair = one cover unit")
        elif str(c.get("source", "")).strip() == str(c.get("preview", "")).strip():
            errors.append("cover source and preview must be different files (same file is not an editable pair)")
        else:
            units.append({"source": c["source"].strip(), "preview": c["preview"].strip()})
    elif has_covers:
        cov = data["covers"]
        if isinstance(cov, list) and cov and all(isinstance(x, str) and x.strip() for x in cov):
            # Legacy single-file finished asset list; keeps exactly-one semantics.
            if len(cov) != 1:
                errors.append("legacy covers:[file] accepts exactly one string file; "
                              "use covers:[{source,preview}, ...] for 1—2 editable units")
            else:
                units.append({"source": cov[0].strip(), "preview": cov[0].strip()})
        elif isinstance(cov, list) and all(isinstance(x, dict) for x in cov):
            if not (COVER_MIN_UNITS <= len(cov) <= COVER_MAX_UNITS):
                errors.append(f"covers must contain {COVER_MIN_UNITS}—{COVER_MAX_UNITS} editable units, "
                              f"got {len(cov)}; 0 units or 3+ units are rejected")
            for i, c in enumerate(cov):
                if not (isinstance(c.get("source"), str) and c["source"].strip()
                        and isinstance(c.get("preview"), str) and c["preview"].strip()):
                    errors.append(f"covers[{i}] must be {{source, preview}} non-empty strings")
                elif str(c.get("source", "")).strip() == str(c.get("preview", "")).strip():
                    errors.append(f"covers[{i}] source and preview must be different files")
                else:
                    units.append({"source": c["source"].strip(), "preview": c["preview"].strip()})
        else:
            errors.append("covers must be a list of 1—2 {source, preview} units "
                          "(or the legacy [one file string])")
    else:
        errors.append("cover manifest needs cover:{source,preview} or "
                      "covers:[{source,preview}, ...] with 1—2 units")
    return units


def _load_font_manifest(errors):
    """Return {file: manifest entry} and verify bundled files, licenses and hashes.

    Integrity check only; it does not inspect rendered glyphs or legibility.
    """
    index = {}
    try:
        manifest = json.loads(FONT_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"[v2] bundled font manifest unreadable: {exc}")
        return index
    entries = manifest.get("fonts", []) if isinstance(manifest, dict) else []
    if not isinstance(entries, list):
        errors.append("[v2] bundled font manifest fonts must be a list")
        return index
    valid_roles = {"primary-zh", "primary-en", "secondary-en"}
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get("file", "")).strip():
            errors.append("[v2] bundled font manifest entry needs a file")
            continue
        file = entry["file"]
        index[file] = entry
        role = entry.get("role")
        if role not in valid_roles:
            errors.append(f"[v2] bundled font {file} role must be one of {sorted(valid_roles)}")
        if not str(entry.get("family", "")).strip():
            errors.append(f"[v2] bundled font {file} needs a family name")
        if entry.get("weight") != 400:
            errors.append(f"[v2] bundled font {file} must be weight 400")
        if not str(entry.get("version", "")).strip():
            errors.append(f"[v2] bundled font {file} needs a version")
        if not str(entry.get("license", "")).strip():
            errors.append(f"[v2] bundled font {file} needs a license field")
        path = FONTS_DIR / file
        if not path.is_file():
            errors.append(f"[v2] bundled font missing: {file}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry.get("sha256"):
            errors.append(f"[v2] bundled font sha256 mismatch: {file}")
        license_file = entry.get("license_file")
        if license_file and not (FONTS_DIR / license_file).is_file():
            errors.append(f"[v2] bundled font license missing: {license_file}")
    return index


def _check_v2_fonts(data, index, errors):
    """Validate an optional manifest font_pair and bundled file references.

    ``font_pair``: {"zh": "LXGW WenKai", "en": "Caveat" | "Patrick Hand"}.
    ``fonts``: optional list of {"file", "sha256"} entries referencing the
    bundled manifest. Rendered stroke balance and legibility stay human review.
    """
    pair = data.get("font_pair")
    if pair is not None:
        if not isinstance(pair, dict):
            errors.append("[v2] font_pair must be {zh, en} (or omitted)")
        else:
            if pair.get("zh") not in V2_ALLOWED_ZH_FONTS:
                errors.append(f"[v2] font_pair.zh must be one of {sorted(V2_ALLOWED_ZH_FONTS)}")
            if pair.get("en") not in V2_ALLOWED_EN_FONTS:
                errors.append(f"[v2] font_pair.en must be one of {sorted(V2_ALLOWED_EN_FONTS)}")
    files = data.get("fonts")
    if files is not None:
        if not isinstance(files, list):
            errors.append("[v2] fonts must be a list of bundled font entries (or omitted)")
            return
        for i, entry in enumerate(files):
            where = f"fonts[{i}]"
            if not isinstance(entry, dict):
                errors.append(f"[v2] {where} must be an object")
                continue
            file = str(entry.get("file", "")).strip()
            if file not in index:
                errors.append(f"[v2] {where} references unbundled font file: {file!r}")
                continue
            declared = entry.get("sha256")
            if declared and declared != index[file].get("sha256"):
                errors.append(f"[v2] {where} sha256 does not match bundled manifest: {file}")


def _check_v2_critical(data, valid_targets, units, excluded, listed_keys, errors):
    """V2-only critical-point registry gate.

    Critical failures/conditions can be marked either by ``"critical": true`` on a
    core_point, or listed in a top-level ``critical_points`` array (same shape).
    The machine only verifies registration, legal source fields, valid mapping targets and
    that a critical item is not NA-excluded. Whether the critical list is complete
    is a human semantic review duty (semantic_review / coverage_check evidence);
    keyword matching must not be used to claim semantic safety. V1 gets no gate
    here.
    """
    flagged = [cp for cp in (data.get("core_points") or [])
               if isinstance(cp, dict) and cp.get("critical") is True]
    extra = data.get("critical_points")
    if extra is not None and not isinstance(extra, list):
        errors.append("[v2] critical_points must be a list (or omitted)")
        extra = []
    elif extra is None:
        extra = []
    critical = list(extra) + flagged
    seen = set()
    critical_sections = set()
    critical_cats = set()
    core_cats = {cp.get("category") for cp in (data.get("core_points") or [])
                 if isinstance(cp, dict)}
    for i, c in enumerate(critical):
        where = f"critical_points[{i}]"
        if not isinstance(c, dict):
            errors.append(f"[v2] {where} must be an object")
            continue
        cid = c.get("id")
        if not cid or cid in seen:
            errors.append(f"[v2] {where} needs a unique non-empty id")
        seen.add(cid)
        cat = c.get("category")
        if cat not in CORE_CATEGORIES:
            errors.append(f"[v2] {where} category must be one of {sorted(CORE_CATEGORIES)}")
        critical_cats.add(cat)
        src = c.get("source")
        if not (isinstance(src, dict) and src.get("doc") in {"textbook", "post"}
                and str(src.get("section", "")).strip()):
            errors.append(f"[v2] {where} source must be {{doc: textbook|post, section: '...'}}")
        else:
            critical_sections.add((src["doc"], src["section"]))
        if not str(c.get("summary", "")).strip():
            errors.append(f"[v2] {where} needs a summary of the critical point")
        targets = c.get("maps_to")
        if not isinstance(targets, list) or not targets:
            errors.append(f"[v2] {where} needs non-empty maps_to")
        else:
            for t in targets:
                if t not in valid_targets:
                    errors.append(f"[v2] {where} maps_to {t!r} is not a node/region id")
        if len(units) >= 2:
            page = c.get("page")
            if page is None:
                errors.append(f"[v2] {where} on a {len(units)}-unit manifest needs page: 0 or {len(units)-1}")
            elif not (isinstance(page, int) and not isinstance(page, bool)
                      and 0 <= page < len(units)):
                errors.append(f"[v2] {where} page must be an integer in 0—{max(0, len(units)-1)}")
    for key in critical_sections:
        if key in excluded:
            errors.append(f"[v2] critical section {key[0]} {key[1]!r} is listed in "
                          "coverage_na.sections; a critical failure/condition cannot be NA-excluded")
        if listed_keys and key not in listed_keys:
            errors.append(f"[v2] critical source {key[0]} {key[1]!r} is not in source_sections")
    for cat in ("failure", "tradeoff", "boundary"):
        if cat in critical_cats and cat not in core_cats:
            errors.append(f"[v2] critical {cat} must be a real mapped core_point; "
                          f"it cannot be excused by a coverage_na.{cat} reason")
    return len(critical)


def check_canvas(data, errors):
    """Every cover unit must be portrait 3:4 (width:height = 3:4, e.g. 1080x1440)."""
    canvas = data.get("canvas")
    if not isinstance(canvas, dict):
        errors.append("canvas required: {ratio: '3:4', width, height}; every unit is portrait 3:4")
        return
    if canvas.get("ratio") != "3:4":
        errors.append("canvas.ratio must be '3:4' (portrait, width:height=3:4)")
    w, h = canvas.get("width"), canvas.get("height")
    if not (isinstance(w, int) and isinstance(h, int) and not isinstance(w, bool)
            and not isinstance(h, bool) and w > 0 and h > 0):
        errors.append("canvas.width/height must be positive integers")
        return
    if h <= w:
        errors.append("canvas must be portrait (height > width); 3:4 portrait e.g. 1080x1440")
    elif abs(h - w * 4 / 3) > max(1, w * 0.01):
        errors.append(f"canvas ratio must be 3:4 (height ≈ width*4/3, ±1%); got {w}x{h}")


def check_cover(data):
    # Cover = a structured summary map per the selected cover version,
    # not a poster/title decoration.
    # 1—2 cover units (default one; two only when one page gets unreadable).
    # diagram_type flow/structure/mixed; flow: full control branches incl.
    # >=1 decision with 2 labelled control edges.
    # structure/mixed: grouped regions, no forced start/terminal/decision.
    # core_points: coverage mapping from textbook/post points to nodes/regions;
    # on a 2-unit manifest each point carries page (0/1) marking its unit.
    errors = []
    if not isinstance(data, dict):
        return {"ok": False, "errors": ["cover manifest must be an object"]}
    units = _parse_cover_units(data, errors)
    check_canvas(data, errors)
    raw_version = data.get("cover_version")
    if raw_version is None:
        cover_version = "v1"  # legacy unversioned manifest: V1-compatible routing
    elif raw_version in COVER_VERSIONS:
        cover_version = raw_version
    else:
        errors.append(f"cover_version must be one of {COVER_VERSIONS}, got {raw_version!r}")
        cover_version = "v1"
    if cover_version == "v2":
        font_index = _load_font_manifest(errors)
        _check_v2_fonts(data, font_index, errors)
    if not data.get("source_revision"):
        errors.append("missing source_revision")
    if not data.get("title"):
        errors.append("missing title")
    diagram_type = data.get("diagram_type", "flow")
    if diagram_type not in {"flow", "structure", "mixed"}:
        errors.append("diagram_type must be flow/structure/mixed")
    nodes = data.get("nodes", [])
    embedded = {
        "title": data.get("title", ""),
        "diagram_type": diagram_type,
        "source_revision": data.get("source_revision", ""),
        "legend": data.get("legend"),
        "nodes": nodes,
        "edges": data.get("edges", []),
    }
    diag = check_diagram(embedded)
    errors.extend(diag["errors"])
    decision_nodes = [n["id"] for n in embedded["nodes"]
                      if isinstance(n, dict) and n.get("kind") == "decision"]
    raw_na = data.get("coverage_na")
    if raw_na is None:
        na = {}
    elif isinstance(raw_na, dict):
        na = raw_na
    else:
        na = {}
        errors.append("coverage_na must be an object (or omitted), not a string/other type")
    # flow: decision/failure branch required unless a real NA reason exists.
    if diag["ok"] and diagram_type == "flow" and not decision_nodes and not str(na.get("failure", "")).strip():
        errors.append("flow cover needs a decision node (failure branch) or coverage_na.failure reason")

    node_ids = {n.get("id") for n in nodes if isinstance(n, dict) and n.get("id")}
    regions = data.get("regions", [])
    region_ids = set()
    if not isinstance(regions, list):
        errors.append("regions must be a list")
        regions = []
    for i, r in enumerate(regions):
        where = f"regions[{i}]"
        if not isinstance(r, dict) or not str(r.get("id", "")).strip():
            errors.append(f"{where} needs a unique id"); continue
        if r["id"] in region_ids:
            errors.append(f"{where} duplicate region id {r['id']!r}")
        region_ids.add(r["id"])
        if not str(r.get("label", "")).strip():
            errors.append(f"{where} {r['id']!r} needs a label")
        members = r.get("members", [])
        if not isinstance(members, list) or not members:
            errors.append(f"{where} {r['id']!r} needs non-empty members (node ids)")
        else:
            for m in members:
                if m not in node_ids:
                    errors.append(f"{where} {r['id']!r} member {m!r} is not a node id")
    valid_targets = node_ids | region_ids
    critical_count = 0
    core_points = data.get("core_points")
    if not isinstance(core_points, list) or not core_points:
        errors.append("core_points required: map textbook/post points to nodes/regions")
    else:
        seen = set()
        cats = set()
        covered_sections = set()
        for i, cp in enumerate(core_points):
            where = f"core_points[{i}]"
            if not isinstance(cp, dict):
                errors.append(f"{where} must be an object"); continue
            cid = cp.get("id")
            if not cid or cid in seen:
                errors.append(f"{where} needs a unique id")
            seen.add(cid)
            cat = cp.get("category")
            if cat not in CORE_CATEGORIES:
                errors.append(f"{where} category must be one of {sorted(CORE_CATEGORIES)}")
            cats.add(cat)
            src = cp.get("source")
            if not (isinstance(src, dict) and src.get("doc") in {"textbook", "post"}
                    and str(src.get("section", "")).strip()):
                errors.append(f"{where} source must be {{doc: textbook|post, section: '...'}}")
            else:
                covered_sections.add((src["doc"], src["section"]))
            if not str(cp.get("summary", "")).strip():
                errors.append(f"{where} needs a summary of the point")
            targets = cp.get("maps_to")
            if not isinstance(targets, list) or not targets:
                errors.append(f"{where} needs non-empty maps_to")
            else:
                for t in targets:
                    if t not in valid_targets:
                        errors.append(f"{where} maps_to {t!r} is not a node/region id")
            page = cp.get("page")
            if page is None:
                if len(units) >= 2:
                    errors.append(f"{where} on a {len(units)}-unit manifest needs page: 0 or {len(units)-1}")
            elif not (isinstance(page, int) and not isinstance(page, bool)
                      and 0 <= page < len(units)):
                errors.append(f"{where} page must be an integer in 0—{max(0, len(units)-1)}")
        if cover_version == "v1":
            missing = REQUIRED_CATEGORIES_V1 - cats
            if missing:
                errors.append(f"[v1] core_points missing required categories: {sorted(missing)}")
        else:
            missing = REQUIRED_CATEGORIES_V2 - cats
            if missing:
                errors.append(f"[v2] core_points missing required categories: {sorted(missing)}")
            if not (REQUIRED_ANY_V2 & cats):
                errors.append("[v2] core_points need at least one of module or mechanism")
        for opt in ("failure", "tradeoff", "boundary"):
            if opt not in cats and not str(na.get(opt, "")).strip():
                errors.append(f"no {opt} point: add a core_point or a coverage_na.{opt} reason")
        # source_sections completeness: every listed chapter must be covered or excluded;
        # every core_point section must be listed; at least one textbook + one post listed.
        listed = data.get("source_sections", [])
        listed_keys = set()
        excluded = set()
        if not isinstance(listed, list) or not listed:
            errors.append("source_sections required: list textbook/post chapters used to build the summary")
        else:
            listed_keys = set()
            for j, sec in enumerate(listed):
                if not (isinstance(sec, dict) and sec.get("doc") in {"textbook", "post"}
                        and str(sec.get("section", "")).strip()):
                    errors.append(f"source_sections[{j}] must be {{doc: textbook|post, section: '...'}}"); continue
                listed_keys.add((sec["doc"], sec["section"]))
            if not any(k[0] == "textbook" for k in listed_keys):
                errors.append("source_sections must include at least one textbook chapter")
            if not any(k[0] == "post" for k in listed_keys):
                errors.append("source_sections must include at least one post chapter")
            for k in covered_sections - listed_keys:
                errors.append(f"core_point source {k[0]} {k[1]!r} is not in source_sections")
            excluded_raw = na.get("sections", [])
            if not isinstance(excluded_raw, list):
                errors.append("coverage_na.sections must be a list"); excluded_raw = []
            excluded = set()
            seen_ex = set()
            for k, e in enumerate(excluded_raw):
                where = f"coverage_na.sections[{k}]"
                if not (isinstance(e, dict) and e.get("doc") in {"textbook", "post"}
                        and str(e.get("section", "")).strip()
                        and str(e.get("reason", "")).strip()):
                    errors.append(f"{where} needs {{doc, section, reason}}"); continue
                key = (e["doc"], e["section"])
                if key in seen_ex:
                    errors.append(f"{where} duplicates {key[0]} {key[1]!r}")
                seen_ex.add(key)
                excluded.add(key)
                if key in covered_sections:
                    errors.append(f"{where} {key[0]} {key[1]!r} is both covered and excluded")
            for key in listed_keys - covered_sections - excluded:
                errors.append(f"source section {key[0]} {key[1]!r} has no core_point and no exclusion reason")
        if cover_version == "v2":
            critical_count = _check_v2_critical(data, valid_targets, units, excluded,
                                                listed_keys, errors)
        else:
            critical_count = 0
    canvas = data.get("canvas") if isinstance(data.get("canvas"), dict) else {}
    return {"ok": not errors,
            "cover_count": len(units),
            "cover_units": units,
            "cover_version": cover_version,
            "canvas_ratio": canvas.get("ratio"),
            "diagram_type": diagram_type,
            "decision_count": len(decision_nodes),
            "core_point_count": len(core_points) if isinstance(core_points, list) else 0,
            "critical_point_count": critical_count,
            "errors": errors,
            "note": "Structural coverage check only; independent semantic rendered review still required."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    count = commands.add_parser("count")
    count.add_argument("file", type=Path)
    count.add_argument("--stage", choices=RANGES, required=True)
    count.add_argument("--over-reason", default="",
                       help="仅 textbook 正文超过默认 10000 字时必填的非空理由；"
                            "对 post（严格 1500，1499/1501 均拒绝）与低于下限不生效，也不绕过 diagram/cover 检查。")
    diagram = commands.add_parser("diagram")
    diagram.add_argument("file", type=Path)
    cover = commands.add_parser("cover")
    cover.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        raw = args.file.read_text(encoding="utf-8")
        if args.command == "count":
            result = measure(raw, args.stage, args.over_reason)
        elif args.command == "diagram":
            result = check_diagram(json.loads(raw))
        else:
            result = check_cover(json.loads(raw))
    except (OSError, ValueError, TypeError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
