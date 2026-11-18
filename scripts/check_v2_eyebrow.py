#!/usr/bin/env python3
"""Independent V2 cover top-title eyebrow/kicker check helper (V2-only).

Purpose
-------
The V2 cover top band is reserved for the concrete main title (L1 problem
question). A separate small kicker/overline placed above that title -- a
column name, series name, issue number or topic tag (e.g. "面试总结",
"系列第 N 期", "知识卡片") -- is not allowed; the top space goes to the
main title's size and wrapping instead.

Scope and limits
----------------
* Deterministic source scan for text-based editable sources (.svg/.html/.htm).
  Non-text finished assets (.png/.jpg/...) are reported ``skipped``; a skipped
  source must not be claimed machine-checked -- the rendered review records
  the top-band check in style_check evidence.
* Legitimate in-body terms (e.g. "记忆", "Memory" on a mid-canvas node) are
  never forbidden keywords. Only a node positioned above the main title and
  matching an explicit column/series/issue/tag pattern is flagged.
* HTML/XML comments, <style>/<script>/<title>/<desc>/<metadata> and hidden
  elements are invisible and are never flagged.
* The main title itself may carry the topic word (e.g. "面试总结：Agent
  Memory 怎么设计？"); that is the title, not an eyebrow.
* This helper judges neither font legibility, stroke balance nor rendered
  layout. V1 (frozen route) never invokes it.

Exit codes
----------
  0  pass (clean, or non-text source skipped)
  1  check failure (eyebrow/kicker detected in the visible top band)
  2  environment / input failure that cannot be evaluated
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

# Explicit column / series / issue / topic-tag label patterns.
# Bare topic terms such as 记忆 / Memory are deliberately NOT listed: a node
# label in the body is legitimate; only a top-band kicker matching these
# explicit label words is rejected.
EYEBROW_PATTERNS = [
    r"面试总结", r"面试合集", r"面试题集",
    r"系列", r"专栏", r"合集", r"栏目", r"专题",
    r"知识卡片", r"速查手册",
    r"第\s*[0-9一二三四五六七八九十]+\s*期",
    r"ep\s*\d+", r"no\.\s*\d+",
]
_EYEBROW_RE = re.compile("|".join(f"(?:{p})" for p in EYEBROW_PATTERNS), re.I)

_SVG_TEXT_RE = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_HIDDEN_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden"
    r'|display\s*=\s*"[^"]*(?:none|hidden)"'
    r'|visibility\s*=\s*"[^"]*hidden"', re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_STYLE_SCRIPT_RE = re.compile(
    r"<(style|script|title|desc|metadata)\b[^>]*>.*?</\1>", re.S | re.I)

_HTML_H1_RE = re.compile(r"<h1\b([^>]*)>(.*?)</h1>", re.S | re.I)
_HTML_BLOCK_RE = re.compile(
    r"<(h1|h2|h3|p|div|span|small|header|a|figcaption)\b([^>]*)>(.*?)</\1>",
    re.S | re.I)
_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([^;]+)", re.I)

# A kicker sits clearly below the main title's tier: under 70% of its size.
_KICKER_SIZE_RATIO = 0.7


def _attr(attrs, name):
    m = re.search(rf"{re.escape(name)}\s*=\s*\"([^\"]*)\"", attrs or "", re.I)
    return m.group(1) if m else None


def _num(value):
    if value is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(m.group(0)) if m else None


def _visible_text(inner):
    return html.unescape(_TAG_RE.sub("", inner)).strip()


def _strip_invisible(text):
    text = _COMMENT_RE.sub("", text)
    text = _STYLE_SCRIPT_RE.sub("", text)
    return text


def _inline_font_size(attrs):
    m = _FONT_SIZE_RE.search(attrs or "")
    return _num(m.group(1)) if m else None


def _svg_nodes(text):
    nodes = []
    for m in _SVG_TEXT_RE.finditer(text):
        attrs, inner = m.group(1), m.group(2)
        if _HIDDEN_RE.search(attrs):
            continue
        label = _visible_text(inner)
        if not label:
            continue
        nodes.append({
            "text": label,
            "y": _num(_attr(attrs, "y")),
            "size": _num(_attr(attrs, "font-size")),
            "source": "svg<text>",
        })
    return nodes


def _svg_title(nodes, declared):
    if declared:
        for n in nodes:
            if declared in n["text"] or n["text"] in declared:
                return n
    sized = [n for n in nodes if n["size"] is not None]
    if sized:
        return max(sized, key=lambda n: (n["size"], -(n["y"] or 1e9)))
    return nodes[0] if nodes else None


def _svg_eyebrows(nodes, title):
    flags = []
    if title is None:
        return flags
    t_y, t_size = title.get("y"), title.get("size")
    for n in nodes:
        if n is title:
            continue
        if not _EYEBROW_RE.search(n["text"]):
            continue
        above = t_y is not None and n.get("y") is not None and n["y"] < t_y
        if not above:
            continue
        if n.get("size") is not None and t_size is not None:
            if n["size"] >= _KICKER_SIZE_RATIO * t_size:
                continue  # same heading tier, not a small kicker
        flags.append(n)
    return flags


def _html_blocks(text):
    blocks = []
    for m in _HTML_BLOCK_RE.finditer(text):
        tag, attrs, inner = m.group(1).lower(), m.group(2), m.group(3)
        if _HIDDEN_RE.search(attrs):
            continue
        label = _visible_text(inner)
        if not label:
            continue
        blocks.append({
            "tag": tag,
            "text": label,
            "size": _inline_font_size(attrs),
            "order": m.start(),
            "is_h1": tag == "h1",
        })
    return blocks


def _html_eyebrows(blocks):
    h1 = next((b for b in blocks if b["is_h1"]), None)
    if h1 is None:
        return [], None
    flags = []
    seen = set()
    h_size = h1["size"]
    for b in blocks:
        if b is h1:
            continue
        if b["order"] > h1["order"]:
            continue  # below the title, in-body node
        if b["text"] in seen:
            continue  # nested block reported once
        if not _EYEBROW_RE.search(b["text"]):
            continue
        if b.get("size") is not None and h_size is not None:
            if b["size"] >= _KICKER_SIZE_RATIO * h_size:
                continue
        seen.add(b["text"])
        flags.append(b)
    return flags, h1


def _flag_error(flag):
    return {
        "text": flag["text"],
        "reason": "V2 封面顶部大标题上方不设栏目/系列/期次/主题眉题；"
                  "顶部空间优先留给具体大标题",
    }


def run_check(source_text, ext, declared_title=""):
    """Scan one source. Returns a result dict; never raises on bad text."""
    ext = (ext or "").lower().lstrip(".")
    if ext == "svg":
        nodes = _svg_nodes(_strip_invisible(source_text))
        title = _svg_title(nodes, declared_title)
        flags = _svg_eyebrows(nodes, title)
        return {
            "ok": not flags, "skipped": False,
            "title": title["text"] if title else None,
            "errors": [_flag_error(f) for f in flags],
        }
    if ext in ("html", "htm"):
        blocks = _html_blocks(_strip_invisible(source_text))
        flags, h1 = _html_eyebrows(blocks)
        return {
            "ok": not flags, "skipped": False,
            "title": h1["text"] if h1 else None,
            "errors": [_flag_error(f) for f in flags],
        }
    return {
        "ok": True, "skipped": True,
        "reason": f"non-text source .{ext or '?'}: no source scan; "
                  "rendered top-band review must be recorded in style_check evidence",
        "errors": [],
    }


def _selftest():
    """Build in-memory fixtures; assert each expected outcome."""
    passed, failed = [], []

    def case(name, condition, detail=""):
        (passed if condition else failed).append({"case": name, "detail": detail})

    clean_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440">
<!-- 面试总结 系列第3期 草稿注释，不可见，不应被检出 -->
<text x="56" y="150" font-size="120">Agent Memory 怎么设计？</text>
<text x="56" y="900" font-size="76">短期记忆节点</text>
</svg>"""
    res = run_check(clean_svg, "svg")
    case("pass_clean_title_only", res["ok"], json.dumps(res, ensure_ascii=False))

    title_with_tag_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440">
<text x="56" y="150" font-size="120">面试总结：Agent Memory 怎么设计？</text>
</svg>"""
    res = run_check(title_with_tag_svg, "svg")
    case("pass_title_self_carries_tag_word", res["ok"],
         json.dumps(res, ensure_ascii=False))

    kicker_column_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440">
<text x="56" y="40" font-size="34">面试总结</text>
<text x="56" y="150" font-size="120">Agent Memory 怎么设计？</text>
</svg>"""
    res = run_check(kicker_column_svg, "svg")
    case("reject_svg_kicker_column",
         (not res["ok"]) and any("面试总结" in e["text"] for e in res["errors"]),
         json.dumps(res, ensure_ascii=False))

    kicker_series_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440">
<text x="56" y="40" font-size="34">Agent 记忆 · 系列第 3 期</text>
<text x="56" y="150" font-size="120">Agent Memory 怎么设计？</text>
</svg>"""
    res = run_check(kicker_series_svg, "svg")
    case("reject_svg_kicker_series_issue",
         (not res["ok"]) and any("系列" in e["text"] for e in res["errors"]),
         json.dumps(res, ensure_ascii=False))

    body_term_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440">
<text x="56" y="150" font-size="120">Agent Memory 怎么设计？</text>
<text x="56" y="900" font-size="76">Memory 短期记忆</text>
</svg>"""
    res = run_check(body_term_svg, "svg")
    case("no_false_positive_body_memory_term", res["ok"],
         json.dumps(res, ensure_ascii=False))

    hidden_kicker_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440">
<text x="56" y="40" font-size="34" display="none">面试总结</text>
<text x="56" y="150" font-size="120">Agent Memory 怎么设计？</text>
</svg>"""
    res = run_check(hidden_kicker_svg, "svg")
    case("no_false_positive_hidden_element", res["ok"],
         json.dumps(res, ensure_ascii=False))

    clean_html = """<html><body>
<!-- 面试总结 专栏 草稿注释，不可见 -->
<h1>Agent Memory 怎么设计？</h1>
<p>短期记忆节点</p>
</body></html>"""
    res = run_check(clean_html, "html")
    case("pass_html_comment_clean_h1", res["ok"],
         json.dumps(res, ensure_ascii=False))

    kicker_html = """<html><body>
<header><p style="font-size:28px">面试总结 · 知识卡片</p></header>
<h1 style="font-size:60px">Agent Memory 怎么设计？</h1>
</body></html>"""
    res = run_check(kicker_html, "html")
    case("reject_html_kicker_above_h1",
         (not res["ok"]) and any("知识卡片" in e["text"] for e in res["errors"]),
         json.dumps(res, ensure_ascii=False))

    res = run_check(b"<svg/>".decode(), "png")
    case("skip_non_text_source", res["ok"] and res["skipped"],
         json.dumps(res, ensure_ascii=False))

    return {"passed": passed, "failed": failed,
            "pass_count": len(passed), "fail_count": len(failed)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="V2 cover top-title eyebrow check")
    ap.add_argument("--source", type=Path)
    ap.add_argument("--title", default="",
                    help="manifest title used to locate the main heading")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        report = _selftest()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["fail_count"] == 0 else 1

    if not args.source:
        print(json.dumps({"ok": False, "error": "--source required"},
                         ensure_ascii=False))
        return 2
    try:
        text = args.source.read_text(encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"ok": False, "environment_error": str(exc)},
                         ensure_ascii=False))
        return 2
    result = run_check(text, args.source.suffix, args.title)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
