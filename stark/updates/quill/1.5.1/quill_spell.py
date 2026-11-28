"""Stark Quill spell check: local only, standard library only.

On Windows it asks the spell checker built into Windows (the Windows Spell Checking API, the same one Notepad and
Office-style apps use) through COM. Nothing goes online. On other systems, or if Windows has no dictionary for the
language, available() is False and Quill leaves spelling to the browser as before.

Every book has its own dictionary, `dictionary.txt` in the book folder: one word per line, plain text. Quill adds
the character and place names Lore finds; the writer can add words too. Words in it are never flagged.
"""
from __future__ import annotations

import os
import queue
import re
import threading

DICT_NAME = "dictionary.txt"
DICT_HEADER = ("# Stark Quill book dictionary: words spell check accepts in this book, one per line.\n"
               "# Quill adds the character and place names Lore finds. You can add or remove words here.\n")
WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
MAX_WORDS = 20000


# ---------------------------------------------------------------- book dictionary
def dict_path(book_dir):
    return os.path.join(book_dir, DICT_NAME)


def read_dictionary(book_dir):
    try:
        with open(dict_path(book_dir), encoding="utf-8-sig") as f:
            return {ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")}
    except OSError:
        return set()


def add_words(book_dir, words):
    """Add words (whole names are split into words). -> the words that were new."""
    have = read_dictionary(book_dir)
    new = []
    for w in words:
        for t in WORD_RE.findall(str(w)):
            if len(t) > 1 and t not in have:
                have.add(t)
                new.append(t)
    if new:
        tmp = dict_path(book_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(DICT_HEADER + "\n".join(sorted(have, key=lambda s: (s.lower(), s))) + "\n")
        os.replace(tmp, dict_path(book_dir))
    return new


def remove_word(book_dir, word):
    have = read_dictionary(book_dir)
    if word in have:
        have.discard(word)
        tmp = dict_path(book_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(DICT_HEADER + "\n".join(sorted(have, key=lambda s: (s.lower(), s))) + ("\n" if have else ""))
        os.replace(tmp, dict_path(book_dir))
        return True
    return False


def lore_words(lore):
    """Words from Lore's character and place names (and their short forms)."""
    out = []
    for group in ("characters", "locations"):
        for e in lore.get(group, []) or []:
            out.append(e.get("name", ""))
            out.extend(e.get("aliases", []) or [])
    return out


def accepted(word, book_words):
    if word in book_words:
        return True
    base = re.sub(r"['’]s$", "", word)       # Wren's -> Wren
    return base in book_words or base.capitalize() in book_words


# ---------------------------------------------------------------- the Windows spell checker (COM, via ctypes)
class _WinSpell:
    """One thread owns the COM objects; requests go through a queue, so any server thread can ask."""

    def __init__(self, lang="en-US"):
        self.lang = lang
        self.q = queue.Queue()
        self.ready = threading.Event()
        self.ok = False
        self.error = ""
        t = threading.Thread(target=self._run, name="quill-spell", daemon=True)
        t.start()
        self.ready.wait(10)

    def _run(self):
        try:
            self._setup()
            self.ok = True
        except Exception as e:  # no COM, no dictionary, not Windows
            self.error = "%s: %s" % (type(e).__name__, e)
        self.ready.set()
        while self.ok:
            item = self.q.get()
            if item is None:
                break
            words, box, done = item
            try:
                box.append(self._misspelled(words))
            except Exception as e:
                box.append(e)
            done.set()

    def _setup(self):
        import ctypes
        from ctypes import wintypes, POINTER, byref, c_void_p, c_ulong, c_int

        self.ct = ctypes
        ole32 = ctypes.OleDLL("ole32")
        ole32.CoInitializeEx(None, 2)                                # COINIT_APARTMENTTHREADED
        self.ole32 = ole32

        class GUID(ctypes.Structure):
            _fields_ = [("a", c_ulong), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        def guid(s):
            g = GUID()
            ole32.CLSIDFromString(ctypes.c_wchar_p("{%s}" % s), byref(g))
            return g

        clsid = guid("7AB36653-1796-484B-BDFA-E74F1DB7C1DC")         # SpellCheckerFactory
        iid = guid("8E018A9D-2415-4677-BF08-794EA61F94BB")           # ISpellCheckerFactory
        factory = c_void_p()
        ole32.CoCreateInstance(byref(clsid), None, 1 | 4, byref(iid), byref(factory))   # INPROC | LOCAL server
        self._keep = [factory]

        def method(obj, index, *argtypes):
            vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
            proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
            return lambda *a: proto(vtbl[index])(obj, *a)

        self.method = method
        supported = c_int(0)
        method(factory, 4, ctypes.c_wchar_p, POINTER(c_int))(self.lang, byref(supported))   # IsSupported
        if not supported.value:
            raise OSError("Windows has no %s dictionary" % self.lang)
        checker = c_void_p()
        method(factory, 5, ctypes.c_wchar_p, POINTER(c_void_p))(self.lang, byref(checker))  # CreateSpellChecker
        self.checker = checker
        self._keep.append(checker)
        self._check = method(checker, 4, ctypes.c_wchar_p, POINTER(c_void_p))               # ISpellChecker::Check

    def _release(self, obj):
        if obj:
            self.method(obj, 2)()                                                           # IUnknown::Release

    def _misspelled(self, words):
        ct = self.ct
        text = " ".join(words)
        enum = ct.c_void_p()
        self._check(text, ct.byref(enum))
        bad = set()
        try:
            nxt = self.method(enum, 3, ct.POINTER(ct.c_void_p))                              # IEnumSpellingError::Next
            while True:
                err = ct.c_void_p()
                if nxt(ct.byref(err)) != 0 or not err:                                       # S_FALSE: no more
                    break
                start, length = ct.c_ulong(), ct.c_ulong()
                self.method(err, 3, ct.POINTER(ct.c_ulong))(ct.byref(start))                 # get_StartIndex
                self.method(err, 4, ct.POINTER(ct.c_ulong))(ct.byref(length))                # get_Length
                bad.add(text[start.value:start.value + length.value])
                self._release(err)
        except OSError:
            pass
        finally:
            self._release(enum)
        return bad

    def misspelled(self, words, timeout=10):
        if not self.ok:
            return set()
        box, done = [], threading.Event()
        self.q.put((list(words), box, done))
        if not done.wait(timeout):
            return set()
        if isinstance(box[0], Exception):
            raise box[0]
        return box[0]


_engines = {}
_engine_lock = threading.Lock()
_cache = {}            # (language, word) -> True when spelled right


def user_language():
    """This PC's own language tag (en-GB, en-US, ...) or ''."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
            return buf.value
    except Exception:
        pass
    return ""


def pick_language(book_lang=""):
    """The book's language when it names a region (en-GB), else this PC's language when it is the same language,
    else US English for English books."""
    book_lang = (book_lang or "en").strip()
    if "-" in book_lang:
        return book_lang
    mine = user_language()
    if mine.split("-")[0].lower() == book_lang.lower():
        return mine
    return {"en": "en-US"}.get(book_lang.lower(), book_lang)


def engine(lang="en-US"):
    with _engine_lock:
        if os.name != "nt" or os.environ.get("QUILL_NO_SPELL") == "1":
            return None
        if lang not in _engines:
            e = _WinSpell(lang)
            if not e.ok and lang != "en-US":
                e = _engines.get("en-US") or _WinSpell("en-US")
            _engines[lang] = e
        return _engines[lang]


CHECKER = None         # tests set a function(words) -> set of misspelled words


def available(lang="en-US"):
    if CHECKER is not None:
        return True
    e = engine(lang)
    return bool(e and e.ok)


def misspelled_words(text, book_words=(), lang="en-US"):
    """-> sorted list of the distinct words in text that are spelled wrong and are not in the book dictionary."""
    book_words = set(book_words)
    words = []
    seen = set()
    for w in WORD_RE.findall(text or ""):
        if w in seen or len(w) < 2 or accepted(w, book_words):
            continue
        seen.add(w)
        words.append(w)
        if len(words) >= MAX_WORDS:
            break
    todo = [w for w in words if (lang, w) not in _cache] if CHECKER is None else words
    if todo:
        if CHECKER is not None:
            bad = set(CHECKER(todo))
        else:
            bad = set()
            e = engine(lang)
            for i in range(0, len(todo), 400):
                bad |= e.misspelled(todo[i:i + 400])
        for w in todo:
            if CHECKER is None:
                _cache[(lang, w)] = w not in bad
        if CHECKER is not None:
            return sorted(w for w in words if w in bad)
    return sorted(w for w in words if _cache.get((lang, w)) is False)
