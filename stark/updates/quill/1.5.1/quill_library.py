"""Stark Quill Library (opt-in, online only after the writer turns it on). Standard library only.

Sources, all public and used through their official, documented interfaces (no crawling, no scraping):
  * Project Gutenberg: the official OPDS catalogue search (www.gutenberg.org/ebooks/search.opds/) and the plain-text
    file of a book (www.gutenberg.org/cache/epub/<id>/pg<id>.txt). One request at a time, at most one a second,
    with an identifying User-Agent, as Gutenberg's robot policy asks. Books are public domain in the USA; the
    "Project Gutenberg" trademark header and licence footer are kept in the downloaded file and removed only from
    text the writer clips or re-shares (strip_gutenberg), as the Project Gutenberg licence allows.
  * Standard Ebooks: the public OPDS/Atom new-releases feed and their EPUB downloads (CC0 editions).
  * Wiktionary (CC BY-SA 4.0): definitions via the REST API, synonyms and etymology from the page's wikitext via the
    MediaWiki API.
  * Wikipedia (CC BY-SA 4.0): short summaries via the official REST API (page/summary).
  * Open Library: book metadata via search.json.
Every result carries source, licence and link. Nothing is ever put into a chapter: clips go to a per-book research
file (Research.md + research.json), with a citation (source, licence, URL, date).

Tests swap FETCH for a fake, so no test touches the internet.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "StarkQuill/1.2 (writing app; library panel; one request at a time)"
TIMEOUT = 15
MIN_GAP = 1.0                      # seconds between requests to the same host
MAX_BYTES = 30 * 1024 * 1024
RESEARCH_MD = "Research.md"
RESEARCH_JSON = "research.json"
ALLOWED_HOSTS = {"www.gutenberg.org", "gutenberg.org", "standardebooks.org", "en.wiktionary.org",
                 "en.wikipedia.org", "openlibrary.org", "covers.openlibrary.org"}
LICENCES = {"gutenberg": ("Public domain in the USA (Project Gutenberg)", "https://www.gutenberg.org/policy/license.html"),
            "standard": ("CC0 1.0 (Standard Ebooks)", "https://creativecommons.org/publicdomain/zero/1.0/"),
            "wiktionary": ("CC BY-SA 4.0 (Wiktionary)", "https://creativecommons.org/licenses/by-sa/4.0/"),
            "wikipedia": ("CC BY-SA 4.0 (Wikipedia)", "https://creativecommons.org/licenses/by-sa/4.0/"),
            "openlibrary": ("Open Library data (CC0 metadata)", "https://openlibrary.org/developers/licensing")}


class LibraryError(Exception):
    pass


_last = {}
_lock = threading.Lock()


def _real_fetch(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        raise LibraryError("not an allowed source: %s" % host)
    with _lock:
        wait = MIN_GAP - (time.time() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise LibraryError("file too large")
    return data


FETCH = _real_fetch            # tests replace this


def _get(url):
    try:
        return FETCH(url)
    except LibraryError:
        raise
    except Exception as e:
        raise LibraryError("could not reach %s (%s)" % (urllib.parse.urlsplit(url).hostname, type(e).__name__))


def _json(url):
    try:
        return json.loads(_get(url).decode("utf-8"))
    except ValueError:
        raise LibraryError("unexpected answer from %s" % urllib.parse.urlsplit(url).hostname)


def _q(s):
    return urllib.parse.quote(str(s).strip())


ATOM = "{http://www.w3.org/2005/Atom}"


def _atom_entries(data):
    root = ET.fromstring(data)
    return root.findall(ATOM + "entry")


# ---------------------------------------------------------------- books
def gutenberg_search(query):
    data = _get("https://www.gutenberg.org/ebooks/search.opds/?query=" + _q(query))
    out = []
    for e in _atom_entries(data):
        eid = (e.findtext(ATOM + "id") or "")
        m = re.search(r"(\d+)(?:\D*)$", eid)
        if not m:
            continue
        out.append({"source": "gutenberg", "id": m.group(1), "title": (e.findtext(ATOM + "title") or "").strip(),
                    "author": (e.findtext(ATOM + "author/" + ATOM + "name") or e.findtext(ATOM + "content") or "").strip(),
                    "url": "https://www.gutenberg.org/ebooks/" + m.group(1)})
    return [cite(x, "gutenberg") for x in out]


def standard_search(query):
    data = _get("https://standardebooks.org/feeds/atom/new-releases")
    words = [w for w in query.lower().split() if w]
    out = []
    for e in _atom_entries(data):
        title = (e.findtext(ATOM + "title") or "").strip()
        author = (e.findtext(ATOM + "author/" + ATOM + "name") or "").strip()
        if words and not all(w in (title + " " + author).lower() for w in words):
            continue
        link = next((l.get("href") for l in e.findall(ATOM + "link") if l.get("rel") in (None, "alternate")), "") or ""
        epub = next((l.get("href") for l in e.findall(ATOM + "link")
                     if (l.get("type") or "").startswith("application/epub")), "") or ""
        out.append({"source": "standard", "id": link.rstrip("/").split("/ebooks/")[-1], "title": title, "author": author,
                    "url": urllib.parse.urljoin("https://standardebooks.org/", link),
                    "epub": urllib.parse.urljoin("https://standardebooks.org/", epub) if epub else ""})
    return [cite(x, "standard") for x in out]


_PG_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)
_PG_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*", re.I | re.S)


def strip_gutenberg(text):
    """The work itself, without the Project Gutenberg header and licence footer (the trademark parts)."""
    m = _PG_START.search(text)
    if m:
        text = text[m.end():]
    m = _PG_END.search(text)
    if m:
        text = text[:m.start()]
    return text.strip("\r\n") + "\n"


def _safe_file(name):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", name).strip(" .")[:100]
    return s or "book"


def download(library_dir, item):
    """Save a public-domain book into the Library folder. Only Gutenberg and Standard Ebooks. -> path."""
    os.makedirs(library_dir, exist_ok=True)
    src = item.get("source")
    if src == "gutenberg":
        bid = str(item.get("id", ""))
        if not bid.isdigit():
            raise LibraryError("bad Gutenberg book number")
        data = _get("https://www.gutenberg.org/cache/epub/%s/pg%s.txt" % (bid, bid))
        name = "%s (Gutenberg %s).txt" % (_safe_file(item.get("title") or bid), bid)
    elif src == "standard":
        url = item.get("epub") or ""
        if urllib.parse.urlsplit(url).hostname != "standardebooks.org":
            raise LibraryError("Standard Ebooks downloads come from standardebooks.org only")
        data = _get(url)
        name = "%s (Standard Ebooks).epub" % _safe_file(item.get("title") or "book")
    else:
        raise LibraryError("only public-domain books from Project Gutenberg and Standard Ebooks can be downloaded")
    path = os.path.join(library_dir, name)
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    meta = os.path.join(library_dir, "library.json")
    try:
        with open(meta, encoding="utf-8") as f:
            idx = json.load(f)
    except (OSError, ValueError):
        idx = {}
    idx[name] = cite(dict(item, file=name), src)
    with open(meta + ".tmp", "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1, ensure_ascii=False)
    os.replace(meta + ".tmp", meta)
    return path


def read_local(library_dir, name, start=0, size=20000):
    """A page of a downloaded text book for the side pane (Gutenberg header/footer removed)."""
    if os.path.basename(name) != name or not name.lower().endswith(".txt"):
        raise LibraryError("only downloaded text books can be read here")
    with open(os.path.join(library_dir, name), "rb") as f:
        text = strip_gutenberg(f.read().decode("utf-8", "replace"))
    start = max(0, int(start))
    return {"text": text[start:start + size], "start": start, "total": len(text)}


# ---------------------------------------------------------------- reference
def _section(wikitext, names):
    m = re.search(r"^(=+)\s*(?:%s)(?:\s+\d+)?\s*\1\s*$" % "|".join(names), wikitext, re.M)
    if not m:
        return ""
    rest = wikitext[m.end():]
    n = re.search(r"^=+[^=].*?=+\s*$", rest, re.M)
    return rest[:n.start()] if n else rest


LANGS = {"en": "English", "enm": "Middle English", "ang": "Old English", "fro": "Old French", "frm": "Middle French",
         "fr": "French", "la": "Latin", "LL.": "Late Latin", "ML.": "Medieval Latin", "grc": "Ancient Greek",
         "non": "Old Norse", "gem-pro": "Proto-Germanic", "ine-pro": "Proto-Indo-European", "de": "German",
         "nl": "Dutch", "it": "Italian", "es": "Spanish", "ar": "Arabic", "goh": "Old High German"}


def _lang_term(m):
    """{{inh|en|enm|lampe}} -> "Middle English lampe" (the source language, then the word)."""
    lang = LANGS.get(m.group(1), "")
    return (lang + " " + m.group(2)).strip() if m.group(2) else lang


def _plain_wiki(s):
    s = re.sub(r"\{\{(?:inh|der|bor|lbor|uder)\+?\|[^|}]*\|([^|}]*)\|?([^|}]*)[^}]*\}\}", _lang_term, s)
    s = re.sub(r"\{\{(?:l|m|w|syn|cog)\|[^|}]*\|([^|}]*)[^}]*\}\}", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"'''?|<[^>]+>", "", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def wiktionary(word):
    w = word.strip()
    if not w:
        raise LibraryError("type a word")
    url = "https://en.wiktionary.org/wiki/" + _q(w.replace(" ", "_"))
    defs = []
    try:
        d = _json("https://en.wiktionary.org/api/rest_v1/page/definition/" + _q(w.replace(" ", "_")))
        for entry in d.get("en", [])[:4]:
            for dd in entry.get("definitions", [])[:4]:
                t = re.sub(r"<[^>]+>", "", dd.get("definition", "")).strip()
                if t:
                    defs.append({"pos": entry.get("partOfSpeech", ""), "text": t})
    except LibraryError:
        pass
    p = _json("https://en.wiktionary.org/w/api.php?action=parse&format=json&prop=wikitext&page=" + _q(w))
    wt = ((p.get("parse") or {}).get("wikitext") or {}).get("*", "")
    ety = _plain_wiki(_section(wt, ["Etymology"]))[:1200]
    syn = _section(wt, ["Synonyms"])
    synonyms = sorted(set(re.findall(r"\[\[([^\]|#]+)", syn) + re.findall(r"\{\{l\|en\|([^|}]+)", syn)))[:30]
    if not defs and not ety and not synonyms:
        raise LibraryError("Wiktionary has no entry for “%s”" % w)
    return cite({"source": "wiktionary", "word": w, "definitions": defs, "etymology": ety, "synonyms": synonyms,
                 "url": url}, "wiktionary")


def wikipedia(title):
    t = title.strip()
    if not t:
        raise LibraryError("type a subject")
    d = _json("https://en.wikipedia.org/api/rest_v1/page/summary/" + _q(t.replace(" ", "_")))
    if d.get("type") == "https://mediawiki.org/wiki/HyperSwitch/errors/not_found" or not d.get("extract"):
        raise LibraryError("Wikipedia has no article called “%s”" % t)
    url = ((d.get("content_urls") or {}).get("desktop") or {}).get("page") or \
        "https://en.wikipedia.org/wiki/" + _q(t.replace(" ", "_"))
    return cite({"source": "wikipedia", "title": d.get("title") or t, "extract": d.get("extract", ""),
                 "url": url}, "wikipedia")


def openlibrary(query):
    d = _json("https://openlibrary.org/search.json?limit=10&q=" + _q(query))
    out = []
    for doc in d.get("docs", [])[:10]:
        out.append(cite({"source": "openlibrary", "title": doc.get("title", ""),
                         "author": ", ".join(doc.get("author_name", [])[:3]),
                         "year": doc.get("first_publish_year"), "url": "https://openlibrary.org" + doc.get("key", ""),
                         "public_domain": bool(doc.get("public_scan_b") or doc.get("ebook_access") == "public")},
                        "openlibrary"))
    return out


def cite(item, source):
    lic, lic_url = LICENCES[source]
    item = dict(item)
    item.update(licence=lic, licence_url=lic_url,
                share_alike=source in ("wiktionary", "wikipedia"))
    return item


# ---------------------------------------------------------------- research notes (never the manuscript)
def _research(book_dir):
    try:
        with open(os.path.join(book_dir, RESEARCH_JSON), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {"clips": []}
    except (OSError, ValueError):
        return {"clips": []}


def citation(c):
    s = "%s. %s. %s. %s. Retrieved %s." % (c.get("title") or c.get("source_name", ""), c.get("source_name", ""),
                                           c.get("licence", ""), c.get("url", ""), c.get("date", ""))
    return re.sub(r"(\.\s*){2,}", ". ", s)


def add_clip(book_dir, text, source, title, url, licence, licence_url="", share_alike=False, now=None):
    text = (text or "").strip()
    if not text:
        raise LibraryError("nothing to clip")
    if len(text) > 4000:
        raise LibraryError("a clip can be at most 4,000 characters")
    names = {"gutenberg": "Project Gutenberg", "standard": "Standard Ebooks", "wiktionary": "Wiktionary",
             "wikipedia": "Wikipedia", "openlibrary": "Open Library"}
    clip = {"id": secrets.token_hex(4), "text": text, "source": source, "source_name": names.get(source, source),
            "title": title or "", "url": url or "", "licence": licence or "", "licence_url": licence_url or "",
            "share_alike": bool(share_alike),
            "date": (now or dt.date.today()).isoformat() if not isinstance(now, str) else now}
    d = _research(book_dir)
    d["clips"].append(clip)
    with open(os.path.join(book_dir, RESEARCH_JSON) + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(os.path.join(book_dir, RESEARCH_JSON) + ".tmp", os.path.join(book_dir, RESEARCH_JSON))
    _write_md(book_dir, d)
    return clip


def remove_clip(book_dir, clip_id):
    d = _research(book_dir)
    d["clips"] = [c for c in d["clips"] if c.get("id") != clip_id]
    with open(os.path.join(book_dir, RESEARCH_JSON), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    _write_md(book_dir, d)


def clips(book_dir):
    return _research(book_dir)["clips"]


def _write_md(book_dir, d):
    out = ["# Research", "", "Quotes clipped in Stark Quill's Library, with where they came from. This file is not "
           "part of the manuscript and is never exported with it.", ""]
    for c in d["clips"]:
        out += ["> " + c["text"].replace("\n", "\n> "), "", "Source: " + citation(c)]
        if c.get("share_alike"):
            out.append("Licence note: CC BY-SA. If you publish this text, credit the source and share it under the "
                       "same licence.")
        out += ["", "---", ""]
    with open(os.path.join(book_dir, RESEARCH_MD), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[“”\"‘’']", "", s)).strip().lower()


def quoted_in(chapters, book_dir, min_len=30):
    """Clips that appear (whole, or a stretch of at least min_len characters) in a chapter: lore flags them so the
    writer sees the attribution and share-alike duty. -> list of anomaly dicts."""
    out = []
    for c in clips(book_dir):
        t = _norm(c["text"])
        probe = t if len(t) <= 80 else t[:80]
        if len(probe) < min(min_len, len(t)) or not probe:
            continue
        for name, text in chapters:
            if probe in _norm(text):
                msg = "Quoted from %s: %s" % (c["source_name"], citation(c))
                if c.get("share_alike"):
                    msg += " CC BY-SA: credit the source and share alike if you publish it."
                out.append({"kind": "quoted_source", "chapter": name, "message": msg, "excerpt": c["text"][:160],
                            "url": c.get("url", "")})
    return out
