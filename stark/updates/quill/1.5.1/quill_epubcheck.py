"""quill_epubcheck.py - a structural EPUB 3 checker (Python standard library only).

Stark Quill runs it on every EPUB it exports, and the tests run it on books built from awkward text. It is not
the official W3C EPUBCheck (that is a Java program); it checks the structure that e-readers and stores trip over:

  container   mimetype first, stored, exact bytes, no extra field; no duplicate, absolute, backslash or '..' names;
              META-INF/container.xml points at an existing package document
  package     OPF 3.0 namespace; unique-identifier -> a non-empty dc:identifier; dc:title, dc:language and exactly
              one well-formed dcterms:modified; manifest ids and hrefs unique, every href present in the zip with
              a media type that matches its extension; every file in the zip (apart from mimetype, META-INF and
              the OPF itself) declared in the manifest; exactly one nav item; spine not empty, idrefs known, no
              duplicates, only XHTML content documents; spine@toc -> an NCX item
  documents   every XHTML file well-formed, XHTML namespace, a non-empty <title>, a <body>, unique ids, a language;
              no remote resources and no scripts; every internal link and #fragment resolves
  navigation  a nav with epub:type="toc" holding an <ol> of <li><a href>; every target exists
  NCX         dtb:uid equals the package identifier; every navPoint has a label and an existing target;
              playOrder runs 1, 2, 3 ...
  CSS         braces balance; url() targets exist
Warnings (not errors): content documents over 260 KB (slow on older readers), no dc:creator, no landmarks.

    python quill_epubcheck.py book.epub      -> prints the report, exit code 1 on any error
    check(path)                               -> list of error strings ([] = passes)
    report(path)                              -> {"errors": [...], "warnings": [...], "stats": {...}}
"""
from __future__ import annotations

import posixpath
import re
import sys
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "x": "http://www.w3.org/1999/xhtml", "epub": "http://www.idpf.org/2007/ops",
      "c": "urn:oasis:names:tc:opendocument:xmlns:container", "ncx": "http://www.daisy.org/z3986/2005/ncx/"}
XHTML = "{%s}" % NS["x"]
EPUB_TYPE = "{%s}type" % NS["epub"]
MEDIA = {".xhtml": "application/xhtml+xml", ".html": "application/xhtml+xml", ".css": "text/css",
         ".ncx": "application/x-dtbncx+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp", ".otf": "font/otf",
         ".ttf": "font/ttf", ".woff": "font/woff", ".woff2": "font/woff2", ".js": "application/javascript"}
FONT_ALIASES = {"font/otf": {"application/vnd.ms-opentype", "application/font-sfnt"},
                "font/ttf": {"application/font-sfnt", "application/x-font-ttf"}, "font/woff": {"application/font-woff"}}
MAX_DOC = 260 * 1024


def _ext(name):
    return posixpath.splitext(name)[1].lower()


def report(path):
    errors, warnings, stats = [], [], {"files": 0, "documents": 0, "spine": 0, "toc_entries": 0}

    def err(msg):
        errors.append(msg)

    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        return {"errors": ["container: not a readable zip file (%s)" % e], "warnings": [], "stats": stats}
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        stats["files"] = len(names)
        bad = z.testzip()
        if bad is not None:
            err("container: corrupt member %s" % bad)
        # ---- container rules
        if not infos or infos[0].filename != "mimetype":
            err("container: 'mimetype' must be the first file in the zip")
        else:
            mi = infos[0]
            if mi.compress_type != zipfile.ZIP_STORED:
                err("container: 'mimetype' must be stored without compression")
            if mi.extra:
                err("container: 'mimetype' must not have an extra field")
            if z.read("mimetype") != b"application/epub+zip":
                err("container: 'mimetype' must contain exactly application/epub+zip")
        seen = set()
        for n in names:
            low = n.lower()
            if low in seen:
                err("container: duplicate file name %s" % n)
            seen.add(low)
            if n.startswith("/") or "\\" in n or ".." in n.split("/") or re.match(r"^[A-Za-z]:", n):
                err("container: unsafe file name %r" % n)
            if unicodedata.normalize("NFC", n) != n:
                err("container: file name not in Unicode NFC form: %r" % n)
        if "META-INF/container.xml" not in names:
            err("container: META-INF/container.xml is missing")
            return {"errors": errors, "warnings": warnings, "stats": stats}
        try:
            cont = ET.fromstring(z.read("META-INF/container.xml"))
            rf = cont.find(".//c:rootfile", NS)
            opf_path = rf.get("full-path") if rf is not None else None
            if rf is not None and rf.get("media-type") != "application/oebps-package+xml":
                err("container: rootfile media-type must be application/oebps-package+xml")
        except ET.ParseError as e:
            err("container: container.xml is not well-formed (%s)" % e)
            opf_path = None
        if not opf_path or opf_path not in names:
            err("container: the package document named in container.xml is missing")
            return {"errors": errors, "warnings": warnings, "stats": stats}
        # ---- package document
        try:
            opf = ET.fromstring(z.read(opf_path))
        except ET.ParseError as e:
            err("package: %s is not well-formed (%s)" % (opf_path, e))
            return {"errors": errors, "warnings": warnings, "stats": stats}
        base = posixpath.dirname(opf_path)
        if opf.tag != "{%s}package" % NS["opf"]:
            err("package: root element is not an OPF <package>")
        if opf.get("version") != "3.0":
            err("package: version must be 3.0 (found %r)" % opf.get("version"))
        uid_ref = opf.get("unique-identifier")
        idents = {e.get("id"): (e.text or "").strip() for e in opf.findall("opf:metadata/dc:identifier", NS)}
        uid = idents.get(uid_ref, "")
        if not uid_ref or not uid:
            err("package: unique-identifier must point at a non-empty dc:identifier")
        title = opf.find("opf:metadata/dc:title", NS)
        if title is None or not (title.text or "").strip():
            err("package: dc:title is missing or empty")
        lang = opf.find("opf:metadata/dc:language", NS)
        if lang is None or not re.match(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$", (lang.text or "").strip()):
            err("package: dc:language is missing or not a language tag")
        mods = [m for m in opf.findall("opf:metadata/opf:meta", NS) if m.get("property") == "dcterms:modified"]
        if len(mods) != 1 or not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", (mods[0].text or "").strip()):
            err("package: exactly one dcterms:modified (CCYY-MM-DDThh:mm:ssZ) is required")
        if opf.find("opf:metadata/dc:creator", NS) is None:
            warnings.append("package: no dc:creator (author) - stores show the book without an author")
        manifest, hrefs, nav_items = {}, {}, []
        for it in opf.findall("opf:manifest/opf:item", NS):
            iid, href, mt = it.get("id"), it.get("href"), it.get("media-type")
            if not iid or not href or not mt:
                err("package: manifest item without id, href or media-type")
                continue
            if iid in manifest:
                err("package: duplicate manifest id %s" % iid)
            full = posixpath.normpath(posixpath.join(base, urllib.parse.unquote(href))) if base else \
                posixpath.normpath(urllib.parse.unquote(href))
            if full in hrefs:
                err("package: %s is listed twice in the manifest" % href)
            manifest[iid] = (full, mt, (it.get("properties") or "").split())
            hrefs[full] = iid
            if full not in names:
                err("package: manifest item %s is not in the zip" % href)
            want = MEDIA.get(_ext(full))
            if want and mt != want and mt not in FONT_ALIASES.get(want, set()):
                err("package: %s has media-type %s, expected %s" % (href, mt, want))
            if "nav" in manifest[iid][2]:
                nav_items.append(iid)
        if len(nav_items) != 1:
            err("package: exactly one manifest item must have properties=\"nav\" (found %d)" % len(nav_items))
        for n in names:
            if n == "mimetype" or n.startswith("META-INF/") or n == opf_path or n.endswith("/"):
                continue
            if n not in hrefs:
                err("package: %s is in the zip but not in the manifest" % n)
        spine = opf.find("opf:spine", NS)
        refs = spine.findall("opf:itemref", NS) if spine is not None else []
        stats["spine"] = len(refs)
        if not refs:
            err("package: the spine is empty")
        used = set()
        for r in refs:
            idref = r.get("idref")
            if idref not in manifest:
                err("package: spine item %s is not in the manifest" % idref)
                continue
            if idref in used:
                err("package: spine item %s appears twice" % idref)
            used.add(idref)
            if manifest[idref][1] != "application/xhtml+xml":
                err("package: spine item %s is not an XHTML content document" % idref)
        toc_id = spine.get("toc") if spine is not None else None
        ncx_path = None
        if toc_id:
            if toc_id not in manifest or manifest[toc_id][1] != "application/x-dtbncx+xml":
                err("package: spine toc=%s does not name an NCX item" % toc_id)
            else:
                ncx_path = manifest[toc_id][0]
        # ---- content documents
        ids_by_doc = {}
        docs = [(iid, full) for iid, (full, mt, props) in manifest.items()
                if mt == "application/xhtml+xml" and full in names]
        stats["documents"] = len(docs)
        parsed = {}
        for iid, full in docs:
            data = z.read(full)
            if len(data) > MAX_DOC:
                warnings.append("documents: %s is %d KB; older readers prefer files under 260 KB"
                                % (full, len(data) // 1024))
            try:
                root = ET.fromstring(data)
            except ET.ParseError as e:
                err("documents: %s is not well-formed XHTML (%s)" % (full, e))
                continue
            parsed[full] = root
            if root.tag != XHTML + "html":
                err("documents: %s root is not an XHTML <html>" % full)
            t = root.find("x:head/x:title", NS)
            if t is None or not "".join(t.itertext()).strip():
                err("documents: %s has no <title>" % full)
            if root.find("x:body", NS) is None:
                err("documents: %s has no <body>" % full)
            if not (root.get("lang") or root.get("{http://www.w3.org/XML/1998/namespace}lang")):
                err("documents: %s does not declare its language" % full)
            ids = {}
            for el in root.iter():
                i = el.get("id")
                if i:
                    if i in ids:
                        err("documents: duplicate id %r in %s" % (i, full))
                    ids[i] = True
                if el.tag == XHTML + "script":
                    err("documents: %s contains a script (not declared, and not needed in a novel)" % full)
            ids_by_doc[full] = ids

        def resolve(src_doc, ref, what):
            if not ref or ref.startswith(("mailto:", "data:")):
                return
            if re.match(r"^[a-z][a-z0-9+.-]*:", ref, re.I):
                if what != "a":
                    err("documents: %s loads a remote resource %s" % (src_doc, ref))
                return
            path, _, frag = ref.partition("#")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(src_doc), urllib.parse.unquote(path))) \
                if path else src_doc
            if target not in names:
                err("documents: %s links to %s, which is not in the book" % (src_doc, ref))
                return
            if frag and target in ids_by_doc and frag not in ids_by_doc[target]:
                err("documents: %s links to #%s in %s, which has no such id" % (src_doc, frag, target))

        for full, root in parsed.items():
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                if tag == "a":
                    resolve(full, el.get("href"), "a")
                elif tag == "link":
                    resolve(full, el.get("href"), "link")
                elif tag in ("img", "audio", "video", "source", "iframe", "embed"):
                    resolve(full, el.get("src"), "img")
        # ---- navigation document
        if len(nav_items) == 1:
            nav_path = manifest[nav_items[0]][0]
            root = parsed.get(nav_path)
            if root is None:
                err("navigation: the nav document cannot be read")
            else:
                tocs = [e for e in root.iter(XHTML + "nav") if e.get(EPUB_TYPE) == "toc"]
                if not tocs:
                    err("navigation: no <nav epub:type=\"toc\">")
                else:
                    ol = tocs[0].find("x:ol", NS)
                    if ol is None:
                        err("navigation: the toc nav has no <ol>")
                    else:
                        lis = ol.findall("x:li", NS)
                        stats["toc_entries"] = len(lis)
                        if not lis:
                            err("navigation: the table of contents is empty")
                        for li in lis:
                            a = li.find("x:a", NS)
                            if a is None and li.find("x:span", NS) is None:
                                err("navigation: a toc entry has neither <a> nor <span>")
                            elif a is not None and not "".join(a.itertext()).strip():
                                err("navigation: a toc entry has no text")
                if not any(e.get(EPUB_TYPE) == "landmarks" for e in root.iter(XHTML + "nav")):
                    warnings.append("navigation: no landmarks nav (optional, helps some readers find the start)")
        # ---- NCX
        if ncx_path and ncx_path in names:
            try:
                ncx = ET.fromstring(z.read(ncx_path))
                mu = [m for m in ncx.findall("ncx:head/ncx:meta", NS) if m.get("name") == "dtb:uid"]
                if not mu or mu[0].get("content") != uid:
                    err("NCX: dtb:uid must equal the package identifier")
                orders = []
                for np_ in ncx.iter("{%s}navPoint" % NS["ncx"]):
                    lab = np_.find("ncx:navLabel/ncx:text", NS)
                    if lab is None or not (lab.text or "").strip():
                        err("NCX: a navPoint has no label")
                    c = np_.find("ncx:content", NS)
                    src = c.get("src") if c is not None else None
                    if not src:
                        err("NCX: a navPoint has no content src")
                    else:
                        tgt = posixpath.normpath(posixpath.join(posixpath.dirname(ncx_path),
                                                                urllib.parse.unquote(src.split("#")[0])))
                        if tgt not in names:
                            err("NCX: navPoint target %s is missing" % src)
                    po = np_.get("playOrder")
                    if po is not None:
                        orders.append(int(po) if po.isdigit() else -1)
                if orders and orders != list(range(1, len(orders) + 1)):
                    err("NCX: playOrder must run 1, 2, 3 ... in document order")
            except ET.ParseError as e:
                err("NCX: not well-formed (%s)" % e)
        # ---- CSS
        for iid, (full, mt, props) in manifest.items():
            if mt != "text/css" or full not in names:
                continue
            css = z.read(full).decode("utf-8", "replace")
            body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            if body.count("{") != body.count("}"):
                err("CSS: %s has unbalanced braces" % full)
            for u in re.findall(r"url\(\s*['\"]?([^'\")]+)", body):
                if re.match(r"^[a-z]+:", u, re.I):
                    err("CSS: %s loads a remote resource %s" % (full, u))
                elif posixpath.normpath(posixpath.join(posixpath.dirname(full), u)) not in names:
                    err("CSS: %s refers to missing %s" % (full, u))
    return {"errors": errors, "warnings": warnings, "stats": stats}


def check(path):
    return report(path)["errors"]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    bad = 0
    for p in argv:
        r = report(p)
        print("%s: %s (%d files, %d content documents, %d spine items, %d toc entries)"
              % (p, "PASS" if not r["errors"] else "FAIL", r["stats"]["files"], r["stats"]["documents"],
                 r["stats"]["spine"], r["stats"]["toc_entries"]))
        for e in r["errors"]:
            print("  error:", e)
        for w in r["warnings"]:
            print("  warning:", w)
        bad += bool(r["errors"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
