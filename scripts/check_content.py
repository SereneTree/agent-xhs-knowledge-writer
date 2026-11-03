#!/usr/bin/env python3
"""Offline length and diagram checks. Not a semantic or rendering verifier."""
import argparse
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
REQUIRED_CATEGORIES = {"problem", "judgment", "module", "mechanism"}


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
    # Cover = a summary map of the whole text, not a poster/title decoration.
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
        missing = REQUIRED_CATEGORIES - cats
        if missing:
            errors.append(f"core_points missing required categories: {sorted(missing)}")
        for opt in ("failure", "tradeoff", "boundary"):
            if opt not in cats and not str(na.get(opt, "")).strip():
                errors.append(f"no {opt} point: add a core_point or a coverage_na.{opt} reason")
        # source_sections completeness: every listed chapter must be covered or excluded;
        # every core_point section must be listed; at least one textbook + one post listed.
        listed = data.get("source_sections", [])
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
    canvas = data.get("canvas") if isinstance(data.get("canvas"), dict) else {}
    return {"ok": not errors,
            "cover_count": len(units),
            "cover_units": units,
            "canvas_ratio": canvas.get("ratio"),
            "diagram_type": diagram_type,
            "decision_count": len(decision_nodes),
            "core_point_count": len(core_points) if isinstance(core_points, list) else 0,
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
