#!/usr/bin/env python3
"""Independent V2 cover font check helper (staging copy, not part of V1).

Purpose
-------
Verify the bundled V2 cover type pair against a manifest:
  * manifest family / role / weight / file / license / sha256 integrity;
  * real glyph coverage via fontTools cmap (no silent fallback);
  * ordered fallback chain rules: Chinese has one bundled primary only,
    no sans/serif/system/generic auto fallback; English is primary ->
    secondary, and the secondary may enter the chain only when declared
    QA-approved; no generic cursive/system fallback.

This helper does NOT touch the V1 entry point or V1 frozen assets. It is a
standalone check; the skill CLI may call it from the V2 route only.

Exit codes
----------
  0  pass (coverage + declaration + bundle integrity all OK)
  1  check failure (missing glyph, illegal fallback, hash mismatch, ...)
  2  environment / dependency failure (e.g. fontTools unavailable) --
     reported clearly, never silently passed.

Usage
-----
  python3 check_v2_fonts.py --bundle <dir> [--manifest fonts-manifest.json] \
      [--text-file f.txt | --text "sample"] [--declaration decl.json] \
      [--selftest]
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Roles registered in the V2 manifest.
ROLE_ZH = "primary-zh"
ROLE_EN_PRIMARY = "primary-en"
ROLE_EN_SECONDARY = "secondary-en"

# Names that must never appear as an automatic fallback on the V2 route.
# Includes system CJK faces, generic categories, sans/serif/system-ui and
# generic cursive. Matching is case-insensitive, substring-based over the
# declared family token.
FORBIDDEN_FALLBACK_PATTERNS = [
    r"sans-serif", r"sans", r"serif", r"system-ui", r"generic",
    r"cursive", r"pingfang", r"kaiti", r"stkaiti", r"noto",
    r"arial", r"helvetica", r"songti", r"hei", r"黑体", r"楷体",
]

def try_import_fonttools():
    """Return (tt_module, None) on success, (None, reason) on failure."""
    try:
        from fontTools.ttLib import TTFont  # noqa: WPS433
        return TTFont, None
    except Exception as exc:  # pragma: no cover - depends on environment
        return None, f"fontTools unavailable: {type(exc).__name__}: {exc}"


def classify(ch):
    """Segment a character: ASCII 0x20-0x7E -> Latin stack; CJK/full-width
    (including circled digits ① ②) -> Chinese stack; anything else -> other."""
    o = ord(ch)
    if 0x20 <= o <= 0x7E:
        return "en"
    cjk = (
        0x3000 <= o <= 0x303F or 0x3400 <= o <= 0x9FFF
        or 0xFF00 <= o <= 0xFFEF or 0x2E80 <= o <= 0x2EFF
        or 0x3400 <= o <= 0x4DBF or 0x20000 <= o <= 0x2A6DF
        or 0x2460 <= o <= 0x2473
    )
    if cjk:
        return "zh"
    return "other"


def split_runs(text):
    """Split text into ordered (segment, part) runs. The join of all parts
    must equal the original text exactly (zero dropped characters)."""
    runs, cur, buf = [], None, ""
    for ch in text:
        k = classify(ch)
        if cur is None:
            cur = k
        if k != cur:
            runs.append((cur, buf))
            cur, buf = k, ""
        buf += ch
    if buf:
        runs.append((cur, buf))
    return runs


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_bundle(bundle_dir, manifest_name):
    """Validate manifest integrity. Returns (fonts_by_role, errors)."""
    bundle = Path(bundle_dir)
    manifest_path = bundle / manifest_name
    errors = []
    fonts_by_role = {}

    if not manifest_path.exists():
        return fonts_by_role, [f"manifest not found: {manifest_path}"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("fonts")
    if not isinstance(entries, list) or not entries:
        return fonts_by_role, ["manifest.fonts must be a non-empty list"]

    for entry in entries:
        role = entry.get("role")
        if role not in {ROLE_ZH, ROLE_EN_PRIMARY, ROLE_EN_SECONDARY}:
            errors.append(f"unknown role {role!r}")
            continue
        fpath = bundle / entry.get("file", "")
        licpath = bundle / entry.get("license_file", "")
        if not fpath.exists():
            errors.append(f"{role}: font file missing {entry.get('file')}")
            continue
        if not licpath.exists():
            errors.append(f"{role}: license file missing {entry.get('license_file')}")
            continue
        actual = sha256_file(fpath)
        if actual != entry.get("sha256", ""):
            errors.append(
                f"{role}: sha256 mismatch for {entry.get('file')} "
                f"(manifest {entry.get('sha256', '')[:12]}..., actual {actual[:12]}...)"
            )
        if entry.get("weight") != 400:
            errors.append(f"{role}: expected weight 400, got {entry.get('weight')}")
        fonts_by_role[role] = {
            "family": entry.get("family", ""),
            "file": str(fpath),
            "cmap": None,
        }
    return fonts_by_role, errors


def load_cmaps(fonts_by_role, TTFont):
    for role, info in fonts_by_role.items():
        try:
            info["cmap"] = TTFont(info["file"]).getBestCmap()
        except Exception as exc:
            return [f"{role}: cannot read cmap: {type(exc).__name__}: {exc}"]
    return []


def glyph_present(cmap, ch):
    return cmap is not None and ord(ch) in cmap


def coverage(text, fonts_by_role, use_secondary):
    """Return list of missing glyph records. English may fall back to the
    bundled secondary only when the caller declared use_secondary=True."""
    missing = []
    zh = fonts_by_role.get(ROLE_ZH, {})
    en = fonts_by_role.get(ROLE_EN_PRIMARY, {})
    sec = fonts_by_role.get(ROLE_EN_SECONDARY, {})
    for ch in text:
        if not ch.strip() and ch not in (" ",):
            # Keep whitespace out of the glyph report.
            continue
        kind = classify(ch)
        covered = False
        checked = []
        if kind == "zh":
            covered = glyph_present(zh.get("cmap"), ch)
            checked.append(ROLE_ZH)
        elif kind == "en":
            covered = glyph_present(en.get("cmap"), ch)
            checked.append(ROLE_EN_PRIMARY)
            if not covered and use_secondary and sec:
                covered = glyph_present(sec.get("cmap"), ch)
                checked.append(ROLE_EN_SECONDARY)
        else:
            # Unassigned / exotic codepoint: must be covered by zh stack,
            # then en stack; never an automatic system fallback.
            covered = glyph_present(zh.get("cmap"), ch) or glyph_present(en.get("cmap"), ch)
            checked += [ROLE_ZH, ROLE_EN_PRIMARY]
        if not covered:
            missing.append({
                "char": ch,
                "codepoint": f"U+{ord(ch):04X}",
                "segment": kind,
                "checked_roles": checked,
                "reason": "not in bundled cmap; preflight must fail instead of auto fallback",
            })
    return missing


def validate_declaration(decl, fonts_by_role):
    """Check the declared fallback chain against the bundle and rules."""
    errors = []
    family_by_role = {r: i["family"] for r, i in fonts_by_role.items()}
    zh_names = decl.get("zh_chain", [])
    en_names = decl.get("en_chain", [])
    secondary_qa = bool(decl.get("secondary_qa_approved", False))

    def forbidden(name):
        low = name.lower()
        return any(re.search(p, low) for p in FORBIDDEN_FALLBACK_PATTERNS)

    # Chinese: one bundled primary, nothing else.
    if zh_names != [family_by_role.get(ROLE_ZH, "")]:
        errors.append(
            "zh_chain must contain only the bundled primary "
            f"{family_by_role.get(ROLE_ZH)!r}; got {zh_names!r}"
        )
    for name in zh_names:
        if forbidden(name):
            errors.append(f"forbidden zh fallback token: {name!r}")

    expected_en = [family_by_role.get(ROLE_EN_PRIMARY, "")]
    if en_names and en_names[0] != expected_en[0]:
        errors.append(f"en_chain must start with primary {expected_en[0]!r}; got {en_names!r}")
    for name in en_names:
        if forbidden(name):
            errors.append(f"forbidden en fallback token: {name!r}")
    if len(en_names) > 1:
        secondary = family_by_role.get(ROLE_EN_SECONDARY, "")
        if en_names[1] != secondary:
            errors.append(f"en_chain secondary must be {secondary!r}; got {en_names[1]!r}")
        if not secondary_qa:
            errors.append(
                "secondary English font enters the chain without "
                "secondary_qa_approved=true"
            )
    for name in zh_names + en_names:
        if name and name not in family_by_role.values():
            errors.append(f"undeclared family in chain: {name!r}")
    return errors


def run_check(bundle_dir, manifest_name, text, decl, use_secondary, TTFont):
    fonts_by_role, errors = load_bundle(bundle_dir, manifest_name)
    errors += load_cmaps(fonts_by_role, TTFont)
    missing = []
    if not errors:
        missing = coverage(text, fonts_by_role, use_secondary)
        if decl is not None:
            errors += validate_declaration(decl, fonts_by_role)
    result = {
        "ok": not errors and not missing,
        "errors": errors,
        "missing_glyphs": missing,
        "bundled_roles": sorted(fonts_by_role.keys()),
        "checked_chars": len(text),
    }
    return result


def _selftest(bundle_dir, TTFont):
    """Build fixture variants in a temp dir; assert each expected outcome."""
    manifest_name = "fonts-manifest.json"
    src = Path(bundle_dir)
    passed, failed = [], []

    def case(name, condition, detail=""):
        (passed if condition else failed).append({"case": name, "detail": detail})

    base_manifest = json.loads((src / manifest_name).read_text(encoding="utf-8"))

    def make_fixture(tweak_manifest=None, override_decl=None, text=""):
        tmp = Path(tempfile.mkdtemp(prefix="v2font-"))
        for f in base_manifest["fonts"]:
            (tmp / f["file"]).write_bytes((src / f["file"]).read_bytes())
            (tmp / f["license_file"]).write_bytes((src / f["license_file"]).read_bytes())
        man = json.loads(json.dumps(base_manifest))
        if tweak_manifest:
            tweak_manifest(man)
        (tmp / manifest_name).write_text(json.dumps(man, ensure_ascii=False))
        decl = override_decl or {
            "zh_chain": [base_manifest["fonts"][0]["family"]],
            "en_chain": [base_manifest["fonts"][1]["family"]],
            "secondary_qa_approved": False,
        }
        return tmp, man, decl

    # 1. Safe ASCII + common Chinese, primary-only chain -> pass.
    tmp, _, decl = make_fixture(
        text="Agent 权限收口 重试 降级 1500 次 15% §3")
    res = run_check(str(tmp), manifest_name,
                    "Agent 权限收口 重试 降级 1500 次 15% §3", decl, False, TTFont)
    case("pass_safe_common", res["ok"], json.dumps(res, ensure_ascii=False))

    # 2. Probe an unassigned codepoint U+10FFFF -> missing, must fail.
    tmp, _, decl = make_fixture()
    res = run_check(str(tmp), manifest_name, "\U0010FFFF", decl, False, TTFont)
    case("fail_probe_unassigned",
         (not res["ok"]) and any(m["codepoint"] == "U+10FFFF" for m in res["missing_glyphs"]),
         json.dumps(res, ensure_ascii=False))

    # 3. A symbol absent from all three bundled cmaps -> missing, must fail.
    #    Basic Latin is fully covered by Caveat, so scan punctuation/symbol
    #    ranges for a codepoint missing on zh, primary-en and secondary-en.
    def _cmap(i):
        return TTFont(str(src / base_manifest["fonts"][i]["file"])).getBestCmap()
    zh_cmap, en_cmap, sec_cmap = _cmap(0), _cmap(1), _cmap(2)
    candidates = list(range(0xA0, 0xFF)) + list(range(0x2010, 0x2027)) + [0x2122]
    probe_cp = next((cp for cp in candidates
                     if cp not in zh_cmap and cp not in en_cmap and cp not in sec_cmap), None)
    assert probe_cp is not None, "no missing probe available"
    probe = chr(probe_cp)
    tmp, _, decl = make_fixture()
    res = run_check(str(tmp), manifest_name, probe, decl, False, TTFont)
    case("fail_missing_symbol_all_stacks",
         (not res["ok"]) and any(m["codepoint"] == f"U+{probe_cp:04X}" for m in res["missing_glyphs"]),
         f"probe=U+{probe_cp:04X} {probe!r} " + json.dumps(res, ensure_ascii=False))

    # 4. Forbidden PingFang token in zh chain -> reject.
    tmp, _, _ = make_fixture()
    bad_decl = {"zh_chain": ["LXGW WenKai", "PingFang SC"], "en_chain": ["Caveat"]}
    res = run_check(str(tmp), manifest_name, "测试", bad_decl, False, TTFont)
    case("reject_pingfang",
         (not res["ok"]) and any("PingFang" in e for e in res["errors"]),
         json.dumps(res, ensure_ascii=False))

    # 5. Generic sans-serif / cursive token -> reject.
    tmp, _, _ = make_fixture()
    bad_decl = {"zh_chain": ["LXGW WenKai"],
                "en_chain": ["Caveat", "cursive"]}
    res = run_check(str(tmp), manifest_name, "test", bad_decl, False, TTFont)
    case("reject_generic_fallback",
         (not res["ok"]) and any(("cursive" in e or "generic" in e) for e in res["errors"]),
         json.dumps(res, ensure_ascii=False))

    # 6. Secondary enters chain without QA approval -> reject.
    tmp, _, _ = make_fixture()
    bad_decl = {"zh_chain": ["LXGW WenKai"],
                "en_chain": ["Caveat", "Patrick Hand"],
                "secondary_qa_approved": False}
    res = run_check(str(tmp), manifest_name, "test", bad_decl, False, TTFont)
    case("reject_secondary_without_qa",
         (not res["ok"]) and any("secondary" in e.lower() for e in res["errors"]),
         json.dumps(res, ensure_ascii=False))

    # 7. Tampered manifest hash -> reject.
    def tamper(man):
        man["fonts"][0]["sha256"] = "0" * 64
    tmp, _, decl = make_fixture(tamper)
    res = run_check(str(tmp), manifest_name, "测试", decl, False, TTFont)
    case("reject_hash_tamper",
         (not res["ok"]) and any("sha256" in e for e in res["errors"]),
         json.dumps(res, ensure_ascii=False))

    # 8. Zero-drop: ASCII tail run must survive splitting.
    tail = "上下文 PDP"
    runs = split_runs(tail)
    case("zero_drop_ascii_tail",
         "".join(part for _, part in runs) == tail and runs[-1][0] == "en",
         json.dumps(runs, ensure_ascii=False))

    # 9. Zero-drop: trailing space and mixed CJK/Latin/circled digits.
    mixed = "Agent 权限①② 15% §3 "
    runs = split_runs(mixed)
    kept = "".join(part for _, part in runs)
    segs = {k for k, _ in runs}
    case("zero_drop_mixed_run",
         kept == mixed and {"zh", "en"} <= segs and classify("①") == "zh",
         json.dumps(runs, ensure_ascii=False))

    # 10. Zero-drop: empty and whitespace-only inputs do not lose length.
    case("zero_drop_empty", split_runs("") == [] and "".join(
        p for _, p in split_runs("   ")) == "   ", "")

    report = {"passed": passed, "failed": failed,
              "pass_count": len(passed), "fail_count": len(failed)}
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="V2 bundled font check")
    ap.add_argument("--bundle", default=".")
    ap.add_argument("--manifest", default="fonts-manifest.json")
    ap.add_argument("--text", default="")
    ap.add_argument("--text-file")
    ap.add_argument("--declaration", help="fallback chain declaration JSON file")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    TTFont, dep_error = try_import_fonttools()
    if dep_error:
        print(json.dumps({"ok": False, "environment_error": dep_error},
                         ensure_ascii=False, indent=2))
        return 2

    if args.selftest:
        report = _selftest(args.bundle, TTFont)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["fail_count"] == 0 else 1

    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    decl = None
    if args.declaration:
        decl = json.loads(Path(args.declaration).read_text(encoding="utf-8"))
    use_secondary = bool(decl and decl.get("secondary_qa_approved"))

    result = run_check(args.bundle, args.manifest, text, decl, use_secondary, TTFont)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
