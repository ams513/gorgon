#!/usr/bin/env python3
"""Build i18n/<locale>.json from a sectioned translation source (maintainer tool, standard library only).

Source format (UTF-8 text; one string per line, '\\n' for a line break inside a string):
    [app.vault]            <- section = key prefix
    opening=Ouverture...   <- key suffix = translation   (full key: app.vault.opening)
    # comment lines and blank lines are ignored

    python i18n/build_catalog.py export SECTIONED_OUT.txt          English source in this format (to translate)
    python i18n/build_catalog.py build fr SOURCE.txt [--drafted-by TEXT]
          -> i18n/fr.json with every key (missing ones are an error), _meta.machine_draft = true, no review records.
          A catalog built this way is a MACHINE DRAFT: status "needs native review" until import-review records a
          named reviewer for each string (python i18n.py import-review ...).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import i18n  # noqa: E402


def sections(keys):
    out = []
    for k in keys:
        pre, _, suf = k.rpartition(".")
        if not out or out[-1][0] != pre:
            out.append((pre, []))
        out[-1][1].append((suf, k))
    return out


def export(path):
    en = i18n.source()["messages"]
    lines = []
    for pre, items in sections(list(en)):
        lines.append("[%s]" % pre)
        for suf, k in items:
            lines.append("%s=%s" % (suf, en[k].replace("\n", "\\n")))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return len(en)


def parse(text):
    msgs, sec, errors = {}, None, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("[") and line.rstrip().endswith("]"):
            sec = line.strip()[1:-1].strip()
            continue
        if "=" not in line or sec is None:
            errors.append("line %d: expected key=text inside a [section]" % n)
            continue
        suf, _, val = line.partition("=")
        key = sec + "." + suf.strip()
        if key in msgs:
            errors.append("line %d: duplicate key %s" % (n, key))
        msgs[key] = val.strip().replace("\\n", "\n")
    return msgs, errors


def typography(loc, text):
    """Locale typography the translator should not have to type: French no-break spaces before : ; ? ! and
    inside guillemets."""
    if loc == "fr":
        text = re.sub(" ([:;?!\u00bb])", lambda m: "\u00a0" + m.group(1), text)
        text = text.replace("\u00ab ", "\u00ab\u00a0")
    return text


def build(locale, src_path, drafted_by="machine translation draft", root=None):
    loc = i18n.canonical(locale)
    if not loc or loc == i18n.SOURCE:
        raise SystemExit("choose a target locale")
    with open(src_path, encoding="utf-8-sig") as f:
        msgs, errors = parse(f.read())
    msgs = {k: typography(loc, v) for k, v in msgs.items()}
    en = i18n.source(root)["messages"]
    missing = [k for k in en if k not in msgs]
    extra = [k for k in msgs if k not in en]
    errors += ["missing key %s" % k for k in missing] + ["unknown key %s" % k for k in extra]
    for k, v in msgs.items():
        if k in en:
            errors += ["%s: %s" % (k, p) for p in i18n.check_message(v, en[k], loc)]
    keep = i18n.source(root).get("_meta", {}).get("keep", {})
    for k, words in keep.items():
        for w in words:
            if k in msgs and w not in msgs[k]:
                errors.append("%s: must keep %r (see en.json _meta.keep)" % (k, w))
    if errors:
        raise SystemExit("\n".join(errors[:80]) + ("\n... %d problems" % len(errors) if len(errors) > 80 else ""))
    cat = {"_meta": {"locale": loc, "language": i18n.LOCALES[loc]["name"], "native": i18n.LOCALES[loc]["native"],
                     "machine_draft": True, "status_note": "Machine draft: every string needs review by a qualified "
                     "native speaker (python i18n.py export-review %s ...). Never mark it reviewed by hand." % loc,
                     "drafted_by": drafted_by, "source_keys": len(en)},
           "messages": {k: msgs[k] for k in en}, "review": {}}
    path = i18n.catalog_path(loc, root)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return path, len(en)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "export":
        print("exported %d strings" % export(sys.argv[2]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "build":
        db = sys.argv[sys.argv.index("--drafted-by") + 1] if "--drafted-by" in sys.argv else "machine translation draft"
        p, n = build(sys.argv[2], sys.argv[3], db)
        print("wrote %s (%d strings, machine draft)" % (os.path.relpath(p), n))
    else:
        print(__doc__)
        sys.exit(2)
