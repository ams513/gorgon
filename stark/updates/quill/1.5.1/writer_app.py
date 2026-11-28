#!/usr/bin/env python3
"""Stark Quill v1.0 - local-first novel writing app (writer_app.py).

Python standard library only (3.10 - 3.13), no external dependencies, no CDN: the whole editor UI is the
inline HTML/CSS/JS string at the bottom of this file. Run:  python writer_app.py [--root DIR] [--no-browser]

GUARANTEES (what this code really does - and the limits, stated plainly)
------------------------------------------------------------------------
1. Atomic ledger (class Ledger)
   * The editor posts the FULL chapter text after every ~800 ms typing pause (and on chapter switch,
     tab hide and page unload). Granularity is therefore "the last pause", NOT every keystroke: text typed
     in the final <800 ms before a crash/power loss can be lost (unless the browser's localStorage buffer
     survived - see 4).
   * Order of a save:  (a) append a JSON record with the full text + SHA-256 to <book>/.journal.log,
     flush + os.fsync;  (b) write <file>.tmp, flush + fsync;  (c) re-read the .tmp and verify byte length
     and SHA-256;  (d) os.replace() it over <file> (retried briefly if Windows reports a sharing violation);
     (e) fsync the directory where the OS supports it (POSIX; Windows cannot open directory handles from
     Python, NTFS journals the rename metadata itself);  (f) append a commit record;  (g) compact the
     journal (atomically rewritten to hold only uncommitted writes - normally empty).
   * Locked chapter file (a sync app such as OneDrive or Dropbox, or a virus scanner, holding it): after the
     brief retries in (d) the save answers HTTP 423 with a message that names the cause. The journal record
     from (a) stays, so the text is not lost; the page keeps its copy and tries again every few seconds. An
     uncommitted record is dropped from the journal once a later save of the same chapter commits.
   * Ledger.recover() (run for every book at start-up): replays the newest UNCOMMITTED journal write of each
     chapter whose file does not already carry that SHA, drops torn (half-written) journal lines, deletes
     stray .tmp files and compacts. A crash at any point therefore leaves either the previous pause's text
     (crash before the journal record reached disk) or the new pause's text (record on disk).
   * fsync is only as honest as the drive: a disk/controller that lies about its write cache can still
     lose data on power loss. Nothing in user space can fix that.
2. Sandbox (safe_join / validate_rel)
   * Every user-supplied book/chapter name is checked lexically (rejects '..', '.', empty parts,
     absolute paths, drive letters, UNC / device paths, any ':' so NTFS alternate data streams, Windows
     reserved names CON/PRN/AUX/NUL/COM0-9/LPT0-9 with any extension, trailing dots/spaces, control and
     <>"|?* characters, hidden '.' names) and then resolved with os.path.realpath and must stay inside the
     realpath of the books root, so symlink / junction escapes are refused too. Violations -> HTTP 403.
   * Snapshots: <book>/.backups/snapshot-<timestamp>.zip of all chapters, at most one per 120 s of
     saving (first save of a session always snapshots), 25 kept - the 26th deletes the oldest.
   * Deleted_Graveyard.md: paragraphs (blank-line separated) that disappear from a chapter and have no
     similar (>= 60 %) successor are appended with timestamp + chapter. Heuristic: it errs on keeping
     too much (an edited-beyond-recognition paragraph is also archived), never on keeping too little.
3. Server (QuillApp): binds 127.0.0.1 by default on the first free port 8525-8599. Every request runs
   inside a fault boundary: an exception is logged with traceback to <root>/.quill_health.log and answered
   with friendly JSON (HTTP 500); the server keeps running. A browser that goes away mid-answer (reload, tab
   closed: WinError 10053/10054, broken pipe) is normal and is logged as one line without a traceback. Socket timeout 20 s per connection, request
   body cap 8 MiB, Host header must be a loopback name / IP literal (DNS-rebinding guard), POSTs need
   Content-Type application/json and a same-origin (or absent) Origin, and every /api call needs the
   per-launch random token. A watchdog thread restarts the listener if its thread dies. The browser tab
   is opened once per process launch, never on a watchdog restart.
4. Mobile (PRO): opt-in LAN mode rebinds to 0.0.0.0; a phone pairs by scanning the QR (URL carrying a
   random pairing token) AND typing a 6-digit PIN (5 wrong PINs disable pairing until LAN is re-enabled).
   The QR is generated here in pure Python (byte mode, ECC level M, versions 1-10, all 8 masks scored
   with the standard penalty rules) and qr_decode_matrix() re-reads its structure for tests.
   Client side: Screen Wake Lock, Web Speech dictation (chunks queued in localStorage before they touch
   the editor), offline edits buffered in localStorage and re-sent on reconnect. If the server copy moved
   meanwhile the server performs merge_lines(base, pc, mobile): a line-level three-way merge that keeps
   every line either side added or changed; when both changed the same region BOTH versions are kept
   between <<<<<<< PC / ======= / >>>>>>> MOBILE markers. A deletion only applies when the other side left
   that region untouched.
   HTTPS (local_tls.py): browsers only grant Wake Lock, mic/camera and Chrome speech in a secure context, so
   LAN mode serves the phone over HTTPS on the same port (first byte 0x16 = TLS, else plain HTTP, which a LAN
   client may only use for the /trust onboarding page; everything else redirects to https). The cert comes
   from a per-install local CA (Windows: New-SelfSignedCertificate in CurrentUser, no admin; elsewhere the
   openssl CLI); keys live only in a user-only folder. Each phone trusts that CA once via /trust (iOS
   profile + Certificate Trust Settings, Android CA certificate). Without PowerShell/openssl, or with
   QUILL_LAN_TLS=0 / --lan-http, LAN stays plain HTTP and those APIs show "not available" (the keyboard's
   own voice typing still works). Chrome's Web Speech sends audio to Google's servers; nothing else
   leaves the PC.
5. Living Lore (quill_lore.py): heuristic, offline character/location/timeline index plus anomaly flags
   (alive after death, dates out of order, age inconsistencies). Optional Ollama at 127.0.0.1:11434 with
   a 1.5 s timeout for a short AI summary; when Ollama is absent the AI section is simply omitted.
6. Modes: FREE ($0) and PRO ($10/mo). PRO (docx/pdf/epub export, lore AI, mobile LAN) is decided by
   license_guard.check(); if license_guard is missing or fails, the app runs FREE. FREE never loses
   access to books. quill_export.export() is used when present, else a built-in txt/md/html exporter.
"""
from __future__ import annotations

import argparse
import difflib
import errno
import hashlib
import html
import http.server
import io
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
import zipfile
from datetime import datetime, timedelta as _timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import quill_export as _qexport
except Exception:  # absent or broken: built-in txt/md/html fallback
    _qexport = None
try:
    import license_guard as _lguard
except Exception:
    _lguard = None
try:
    import auto_updater as _updater
except Exception:
    _updater = None
try:
    import quill_lore as _lore
except Exception:
    _lore = None
try:
    import local_tls as _ltls  # HTTPS for phone / LAN mode (secure context: Wake Lock, mic, dictation)
except Exception:
    _ltls = None
try:
    import quill_spell as _spell  # local spell check (Windows' own checker) with a per-book dictionary
except Exception:
    _spell = None
try:
    import quill_library as _lib  # opt-in Library: public-domain books, dictionary, encyclopedia, research clips
except Exception:
    _lib = None

try:
    import quill_schools as _schools  # Schools mode: assignments, rubric self-check, submission package, classroom profile
except Exception:
    _schools = None

try:
    import quill_contests as _contests  # Stark Challenge writing contests: signed entries, anonymous judging, results
except Exception:
    _contests = None

try:
    import quill_craft as _craft  # goals and pace, plot outlines, submissions, series bible, revision checks
except Exception:
    _craft = None

try:
    import quill_ui as _ui  # the editor page (HTML/CSS/JS) lives in its own module
except Exception:
    _ui = None
try:
    import quill_epubcheck as _epubcheck  # structural EPUB checker run on every epub export
except Exception:
    _epubcheck = None
try:
    import quill_paste as _paste  # paste from Word / Google Docs: keeps italics and bold, drops the rest
except Exception:
    _paste = None
try:
    import quill_script as _script  # Fountain screenplays: script pages, scene list, .fountain / .fdx / script PDF
except Exception:
    _script = None
try:
    import quill_cite as _cite  # sources and citations: MLA, APA, Chicago; reference list in exports
except Exception:
    _cite = None
try:
    import quill_import as _import  # bring in a manuscript (.docx, .odt, .md, .txt, .fountain) split into chapters
except Exception:
    _import = None
try:
    import quill_studio as _studio  # comments and footnotes, compare with a backup, project kinds, sprints, plotlines
except Exception:
    _studio = None

APP_NAME = "Stark Quill"
VERSION = "1.5.1"
UPDATE_URL = "https://ams513.github.io/stark/updates/quill/latest.json"
UPDATE_EVERY_S = 20 * 3600       # at most one automatic update check a day (well, every 20 hours)
PORT_FIRST, PORT_LAST = 8525, 8599
MAX_BODY = 8 * 1024 * 1024
SOCKET_TIMEOUT = 20
SAVE_PAUSE_MS = 800
SNAPSHOT_KEEP = 25            # newest snapshots always kept; older ones thin out to one a day, then one a week
SNAPSHOT_DAILY_DAYS = 30
SNAPSHOT_WEEKLY_DAYS = 120
SNAPSHOT_MIN_INTERVAL = 120.0
JOURNAL_NAME = ".journal.log"
HEALTH_LOG = ".quill_health.log"
BACKUP_DIR = ".backups"
DELETED_DIR = "deleted"       # <book>/.backups/deleted/<stamp> <chapter file>: deleted chapters, restorable
GRAVEYARD = "Deleted_Graveyard.md"
BOOK_META = "book.json"       # title, author, language, chapter notes: plain JSON next to the chapters
RENAMES = ".quill_renames.json"
RENUM_JOURNAL = ".quill_renum.json"
STATS_FILE = ".quill_stats.json"
CHAPTER_EXTS = (".md", ".txt")
PRO_EXPORTS = ("docx", "pdf", "epub")
FREE_EXPORTS = ("txt", "md", "html")
SCRIPT_FREE = ("fountain",)          # screenplay books: the Fountain file is free ...
SCRIPT_PRO = ("fdx", "script")       # ... Final Draft and the script-page PDF are Pro, like the other typeset files
CHAPTER_STATUSES = ("idea", "draft", "revised", "final")
PAGE_SIZES = ("trade", "digest", "a5")


def channel():
    """'beta' or 'stable': the CHANNEL file the build puts next to the app (a beta build follows the beta feed)."""
    try:
        with open(os.path.join(_HERE, "CHANNEL"), encoding="ascii") as f:
            ch = f.read().strip().lower()
        if ch in ("beta", "stable"):
            return ch
    except OSError:
        pass
    return "beta" if "-beta" in VERSION else "stable"


def settings_path():
    if _lguard is not None:
        return os.path.join(_lguard.data_dir(), "settings.json")
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "StarkQuill", "settings.json")


def _old_data_dir():
    return os.path.join(_lguard._appdata(), _lguard.LEGACY_DATA_DIR) if _lguard is not None else ""


def running_from_moved_folder():
    """True when this copy runs from the pre-1.5.1 folder that has already been copied to the Stark one: that folder
    is left exactly as it is (no update is applied to it), and the copy is used from the next start."""
    old = _old_data_dir()
    return bool(old) and _within(os.path.realpath(old), os.path.realpath(_HERE)) and _lguard.data_dir() != old


# Windows: point the desktop shortcut that install_quill.bat made (under its pre-1.4 name too) at the copy.
_PS_SHORTCUT = r"""$d=$env:GQ_DESKTOP; if(-not $d){$d=[Environment]::GetFolderPath('Desktop')}
$w=New-Object -ComObject WScript.Shell; $o=$env:GQ_OLD.TrimEnd('\')+'\'; $n=$env:GQ_NEW.TrimEnd('\')
foreach($f in 'Stark Quill.lnk','Gorgon Quill.lnk'){ $p=Join-Path $d $f
 if(-not (Test-Path -LiteralPath $p)){continue}; $s=$w.CreateShortcut($p)
 if(-not $s.TargetPath.StartsWith($o,[StringComparison]::OrdinalIgnoreCase)){continue}
 $rel=$s.TargetPath.Substring($o.Length); if(Test-Path -LiteralPath (Join-Path $n 'START_STARK_QUILL.bat')){$rel='START_STARK_QUILL.bat'}
 $t=$w.CreateShortcut((Join-Path $d 'Stark Quill.lnk')); $t.TargetPath=Join-Path $n $rel; $t.WorkingDirectory=$n
 $t.WindowStyle=7; $t.Description='Stark Quill, a Stark product'; $i=Join-Path $n 'assets\quill.ico'
 if(Test-Path -LiteralPath $i){$t.IconLocation=$i+',0'}; $t.Save(); if($f -ne 'Stark Quill.lnk'){Remove-Item -LiteralPath $p}}"""


def move_data_folders():
    """Start of 1.5.1+: the per-user folders have Stark names. Each old one is copied across once
    (license_guard.move_dir: copied, checked file by file, the old folder left as it is). -> [(old, new, result)]"""
    if _lguard is None:
        return []
    base = _lguard._appdata()
    pairs = [(_old_data_dir(), os.path.join(base, _lguard.DATA_DIR))]
    if _ltls is not None:
        tls_base = os.path.dirname(_ltls.default_state_dir())
        pairs.append((os.path.join(tls_base, _ltls.LEGACY_NAME), os.path.join(tls_base, _ltls.DEFAULT_NAME)))
    out = [(old, new, _lguard.move_dir(old, new)) for old, new in pairs]
    if os.name == "nt" and out[0][2] == "moved" and running_from_moved_folder():
        import subprocess
        try:
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS_SHORTCUT],
                           env=dict(os.environ, GQ_OLD=os.path.realpath(out[0][0]), GQ_NEW=os.path.realpath(out[0][1])), capture_output=True,
                           timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass                     # the old folder keeps working; Quill moves over when the shortcut does
    return out


def moved_notice():
    """Once, after the move and from the new folder: where the settings went, and that the old folder can go."""
    if _lguard is None or running_from_moved_folder() or read_settings().get("moved_told"):
        return ""
    new = _lguard.data_dir()
    try:
        with open(os.path.join(new, _lguard.MOVED_MARK), encoding="utf-8") as f:
            old = f.read().strip()
    except OSError:
        return ""
    write_settings({"moved_told": True})
    return ("Stark Quill now keeps its settings and licence in %s. The old folder %s was copied there, checked file "
            "by file and left as it was. You can delete it." % (display_path(new), display_path(old)))


def read_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_settings(changes):
    d = read_settings()
    d.update(changes)
    p = settings_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.replace(tmp, p)
    return d


def default_root():
    env = os.environ.get("QUILL_BOOKS_ROOT")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), "Documents", "My_Books")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def clean_text(text):
    """Make text UTF-8 encodable (lone surrogates from a browser become U+FFFD)."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    return text.encode("utf-8", "replace").decode("utf-8")


# ======================================================================================================
# 2. Sandbox
# ======================================================================================================
class SandboxError(ValueError):
    pass


_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
_RESERVED |= {"COM%d" % i for i in range(10)} | {"LPT%d" % i for i in range(10)}
_RESERVED |= {"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
_BAD_CHARS = set('<>:"|?*')


def validate_rel(rel, allow_hidden=False):
    """Lexical check of a relative name. Returns its parts or raises SandboxError."""
    if not isinstance(rel, str) or not rel or len(rel) > 240:
        raise SandboxError("bad name")
    if "\x00" in rel:
        raise SandboxError("NUL byte")
    if rel[0] in "/\\" or os.path.isabs(rel):
        raise SandboxError("absolute / UNC path")
    if re.match(r"^[A-Za-z]:", rel):
        raise SandboxError("drive letter")
    if ":" in rel:
        raise SandboxError("alternate data stream / device syntax")
    parts = re.split(r"[\\/]", rel)
    for p in parts:
        if p in ("", ".", ".."):
            raise SandboxError("empty or dot path component")
        if any(c in _BAD_CHARS or ord(c) < 32 for c in p):
            raise SandboxError("illegal character")
        if p[-1] in ". ":
            raise SandboxError("trailing dot/space")
        if p.startswith(".") and not allow_hidden:
            raise SandboxError("hidden name")
        stem = p.split(".")[0].rstrip(" ").upper()
        if stem in _RESERVED:
            raise SandboxError("Windows reserved name")
    return parts


def _within(root_real, path_real):
    r = os.path.normcase(root_real)
    p = os.path.normcase(path_real)
    try:
        return os.path.commonpath([r, p]) == r
    except ValueError:  # different drives
        return False


def safe_join(root, rel, allow_hidden=False):
    """Resolve rel inside root (realpath on both, so links/junctions cannot escape)."""
    parts = validate_rel(rel, allow_hidden)
    root_real = os.path.realpath(root)
    cand = os.path.realpath(os.path.join(root_real, *parts))
    if not _within(root_real, cand) or os.path.normcase(cand) == os.path.normcase(root_real):
        raise SandboxError("path escapes the books folder")
    return cand


# ======================================================================================================
# Atomic file primitives
# ======================================================================================================
def fsync_dir(d):
    """fsync a directory. True if done; False where the OS does not allow it (Windows)."""
    if os.name == "nt":
        return False
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return False
    try:
        os.fsync(fd)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _replace(src, dst):
    for attempt in range(8):
        try:
            os.replace(src, dst)
            return
        except PermissionError:  # Windows: AV scanner / indexer holding the file for a moment
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def atomic_write_bytes(path, data):
    """tmp -> fsync -> re-read verify (length + SHA-256) -> os.replace -> dir fsync. Returns SHA-256."""
    sha = sha256_bytes(data)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    with open(tmp, "rb") as f:
        back = f.read()
    if len(back) != len(data) or sha256_bytes(back) != sha:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise IOError("verification of %s failed (length/SHA-256 mismatch)" % tmp)
    try:
        _replace(tmp, path)
    except OSError:
        try:  # the target is held by another program: leave no stray .tmp behind (the journal has the text)
            os.remove(tmp)
        except OSError:
            pass
        raise
    fsync_dir(os.path.dirname(path) or ".")
    return sha


# Windows error codes for "another program has this file open": access denied (what OneDrive / Dropbox
# placeholders give while they sync), sharing violation, lock violation. PermissionError is the POSIX form.
_LOCK_WINERRORS = (5, 32, 33)


def is_lock_error(exc):
    """True when an OSError means 'the file is held by another program' (sync app, scanner, editor)."""
    if not isinstance(exc, OSError):
        return False
    if getattr(exc, "winerror", None) in _LOCK_WINERRORS:
        return True
    return isinstance(exc, PermissionError)


def append_fsync(path, data):
    with open(path, "ab") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def read_text(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


# ======================================================================================================
# 1. Ledger
# ======================================================================================================
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def book_lock(book_dir):
    key = os.path.normcase(os.path.realpath(book_dir))
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = _LOCKS[key] = threading.RLock()
        return lk


class Ledger:
    """Append-only journal + verified atomic replace for the chapters of one book directory."""

    def __init__(self, book_dir):
        self.book_dir = os.path.realpath(book_dir)
        os.makedirs(self.book_dir, exist_ok=True)
        self.journal = os.path.join(self.book_dir, JOURNAL_NAME)
        self.lock = book_lock(self.book_dir)

    def path_for(self, name):
        return safe_join(self.book_dir, name)

    def _append(self, rec):
        line = json.dumps(rec, ensure_ascii=True, separators=(",", ":")) + "\n"
        append_fsync(self.journal, line.encode("ascii"))

    def read_journal(self):
        """-> (records, torn_count). Torn = undecodable line or write record whose SHA does not match."""
        try:
            with open(self.journal, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return [], 0
        recs, torn = [], 0
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line.decode("ascii"))
                if not isinstance(rec, dict):
                    raise ValueError
                if rec.get("op") == "write":
                    b = rec["text"].encode("utf-8")
                    if sha256_bytes(b) != rec["sha"] or len(b) != rec["len"]:
                        raise ValueError("sha")
            except Exception:
                torn += 1
                continue
            recs.append(rec)
        return recs, torn

    def save(self, name, text):
        """Journal first, then verified atomic replace. Returns the SHA-256 of the stored UTF-8 bytes."""
        text = clean_text(text)
        data = text.encode("utf-8")
        sha = sha256_bytes(data)
        path = self.path_for(name)
        with self.lock:
            seq = secrets.token_hex(8)
            self._append({"op": "write", "seq": seq, "ts": round(time.time(), 3), "name": name,
                          "sha": sha, "len": len(data), "text": text})
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                atomic_write_bytes(path, data)
            except OSError as e:
                if is_lock_error(e):  # the record above stays uncommitted: recover() or the next save keeps it
                    raise ChapterLocked(name, journaled=True, cause=e) from e
                raise
            self._append({"op": "commit", "seq": seq})
            self.compact()
        return sha

    def compact(self):
        """Rewrite the journal (atomically) keeping only write records that have no commit."""
        with self.lock:
            recs, _ = self.read_journal()
            commits = {r.get("seq") for r in recs if r.get("op") == "commit"}
            # An uncommitted write (e.g. a sync app held the file) is kept until a later write of the same
            # chapter commits; then it is older text and must not be replayed over the newer file at start-up.
            last_commit = {}
            for i, r in enumerate(recs):
                if r.get("op") == "write" and r.get("seq") in commits:
                    last_commit[r.get("name")] = i
            keep = [r for i, r in enumerate(recs) if r.get("op") == "write" and r.get("seq") not in commits
                    and last_commit.get(r.get("name"), -1) < i]
            data = b"".join((json.dumps(r, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
                            for r in keep)
            if not keep and not os.path.exists(self.journal):
                return 0
            atomic_write_bytes(self.journal, data)
            return len(keep)

    def recover(self):
        """Replay uncommitted journal writes past the last good file. Returns a report dict."""
        rep = {"book": self.book_dir, "replayed": [], "already_ok": [], "external_change": [],
               "discarded_tmp": [], "torn_records": 0, "errors": []}
        with self.lock:
            try:
                moved = recover_renumber(self.book_dir)
                if moved:
                    rep["renumber_finished"] = moved
            except Exception as e:  # never block the rest of the recovery
                rep["errors"].append("renumber journal: %s" % e)
            for dirpath, dirnames, filenames in os.walk(self.book_dir):
                dirnames[:] = [d for d in dirnames if d != BACKUP_DIR]
                for fn in filenames:
                    if fn.endswith(".tmp"):
                        p = os.path.join(dirpath, fn)
                        try:
                            os.remove(p)
                            rep["discarded_tmp"].append(os.path.relpath(p, self.book_dir))
                        except OSError as e:
                            rep["errors"].append("%s: %s" % (fn, e))
            recs, torn = self.read_journal()
            rep["torn_records"] = torn
            commits = {r.get("seq") for r in recs if r.get("op") == "commit"}
            latest = {}
            for r in recs:
                if r.get("op") == "write":
                    latest[r["name"]] = r
            for name, r in latest.items():
                try:
                    path = self.path_for(name)
                    cur = None
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            cur = sha256_bytes(f.read())
                    if cur == r["sha"]:
                        rep["already_ok"].append(name)
                    elif r["seq"] in commits:
                        rep["external_change"].append(name)  # edited outside the app after commit: keep
                    else:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        atomic_write_bytes(path, r["text"].encode("utf-8"))
                        rep["replayed"].append(name)
                    self._append({"op": "commit", "seq": r["seq"]})
                except Exception as e:  # one bad record never blocks the others
                    rep["errors"].append("%s: %s" % (name, e))
            if not rep["errors"]:
                self.compact()
        rep["ok"] = not rep["errors"]
        return rep

    def simulate_crash(self, name, text, stage="partial_tmp"):
        """TEST HOOK. Do the first steps of save() and stop as if the power went out.
        stage: 'journal_only' (record fsync'd, nothing else), 'partial_tmp' (record + half-written .tmp),
               'torn_journal' (only half a journal line reached the disk)."""
        text = clean_text(text)
        data = text.encode("utf-8")
        path = self.path_for(name)
        rec = {"op": "write", "seq": secrets.token_hex(8), "ts": round(time.time(), 3), "name": name,
               "sha": sha256_bytes(data), "len": len(data), "text": text}
        line = json.dumps(rec, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        with self.lock:
            if stage == "torn_journal":
                append_fsync(self.journal, line[: len(line) // 2])
                return {"stage": stage}
            append_fsync(self.journal, line + b"\n")
            if stage == "partial_tmp":
                with open(path + ".tmp", "wb") as f:
                    f.write(data[: len(data) // 2])
                    f.flush()
                    os.fsync(f.fileno())
        return {"stage": stage, "sha": rec["sha"]}


# ======================================================================================================
# Snapshots + graveyard
# ======================================================================================================
def list_chapters(book_dir):
    out = []
    try:
        names = os.listdir(book_dir)
    except FileNotFoundError:
        return out
    for n in names:
        if n.startswith(".") or n in (GRAVEYARD, "Research.md", "dictionary.txt") or not n.lower().endswith(CHAPTER_EXTS):
            continue
        if os.path.isfile(os.path.join(book_dir, n)):
            out.append(n)
    return sorted(out, key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)])


def list_snapshots(book_dir):
    bdir = os.path.join(book_dir, BACKUP_DIR)
    try:
        return sorted(n for n in os.listdir(bdir) if n.startswith("snapshot-") and n.endswith(".zip"))
    except FileNotFoundError:
        return []


def _snap_time(name):
    try:
        return datetime.strptime(name[9:31], "%Y%m%d-%H%M%S-%f").timestamp()
    except ValueError:
        return 0.0


def snapshot_book(book_dir, keep=SNAPSHOT_KEEP, min_interval=None, now=None):
    """Zip every chapter (and book.json) into .backups/snapshot-<ts>.zip. Returns path or None.
    Retention: the newest `keep` snapshots stay; of the older ones, the last snapshot of each day is kept for
    SNAPSHOT_DAILY_DAYS days and the last of each week up to SNAPSHOT_WEEKLY_DAYS days."""
    now = time.time() if now is None else now
    bdir = os.path.join(book_dir, BACKUP_DIR)
    os.makedirs(bdir, exist_ok=True)
    existing = list_snapshots(book_dir)
    if min_interval and existing and now - _snap_time(existing[-1]) < min_interval:
        return None
    stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S-%f")
    name = "snapshot-%s.zip" % stamp
    n = 1
    while name in existing:
        name = "snapshot-%s-%d.zip" % (stamp, n)
        n += 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for ch in list_chapters(book_dir):
            with open(os.path.join(book_dir, ch), "rb") as f:
                z.writestr(ch, f.read())
        meta = os.path.join(book_dir, BOOK_META)
        if os.path.isfile(meta):
            with open(meta, "rb") as f:
                z.writestr(BOOK_META, f.read())
    path = os.path.join(bdir, name)
    atomic_write_bytes(path, buf.getvalue())
    for old in snapshots_to_prune(list_snapshots(book_dir), keep, now):
        try:
            os.remove(os.path.join(bdir, old))
        except OSError:
            pass
    return path


def snapshots_to_prune(snaps, keep=SNAPSHOT_KEEP, now=None):
    """Pure retention rule (sorted oldest -> newest in, names to delete out)."""
    now = time.time() if now is None else now
    if len(snaps) <= keep:
        return []
    recent = set(snaps[-keep:])
    day_rep, week_rep = {}, {}
    for s in snaps:  # ascending: the last one of each day / week wins
        t = _snap_time(s)
        d = datetime.fromtimestamp(t) if t else None
        if d is None:
            continue
        day_rep[d.date()] = s
        week_rep[d.isocalendar()[:2]] = s
    keep_old = set()
    for s in set(day_rep.values()):
        if s not in recent and now - _snap_time(s) <= SNAPSHOT_DAILY_DAYS * 86400:
            keep_old.add(s)
    for s in set(week_rep.values()):
        if s not in recent and now - _snap_time(s) <= SNAPSHOT_WEEKLY_DAYS * 86400:
            keep_old.add(s)
    return [s for s in snaps if s not in recent and s not in keep_old]


def split_paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _similar(a, b, threshold):
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if m.real_quick_ratio() < threshold or m.quick_ratio() < threshold:
        return False
    return m.ratio() >= threshold


def deleted_paragraphs(old, new, threshold=0.6):
    """Paragraphs of old that vanished from new without a similar successor (pure function)."""
    a, b = split_paragraphs(old), split_paragraphs(new)
    bset = set(b)
    out = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag not in ("delete", "replace"):
            continue
        cands = b[j1:j2]
        joined = "\n\n".join(cands)
        for p in a[i1:i2]:
            if p in bset:
                continue
            if any(_similar(p, q, threshold) for q in cands):
                continue
            if joined and _similar(p, joined, threshold):  # paragraph was split in two
                continue
            out.append(p)
    return out


def append_graveyard(book_dir, chapter, paragraphs, now=None):
    if not paragraphs:
        return 0
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d %H:%M:%S")
    path = os.path.join(book_dir, GRAVEYARD)
    head = "" if os.path.exists(path) else "# Deleted Graveyard\n\nParagraphs removed from chapters.\n\n"
    body = "".join("## %s - %s\n\n%s\n\n---\n\n" % (stamp, chapter, p) for p in paragraphs)
    append_fsync(path, (head + body).encode("utf-8"))
    return len(paragraphs)


# ======================================================================================================
# 4. Three-way merge
# ======================================================================================================
def merge_lines(base, a, b, label_a="PC", label_b="MOBILE"):
    """Line-level diff3. Returns (merged_text, conflicts). Never drops a line that a or b added/changed:
    when both changed the same base region both versions are kept between markers."""
    bl, al, ml = base.splitlines(), a.splitlines(), b.splitlines()

    def matches(x, y):
        m = {}
        for blk in difflib.SequenceMatcher(None, x, y, autojunk=False).get_matching_blocks():
            for k in range(blk.size):
                m[blk.a + k] = blk.b + k
        return m

    ma, mb = matches(bl, al), matches(bl, ml)
    out, conflicts = [], []
    i = j = k = 0

    def resolve(bc, ac, mc):
        if ac == mc:
            out.extend(ac)
        elif ac == bc:
            out.extend(mc)
        elif mc == bc:
            out.extend(ac)
        elif len(bc) == len(ac) == len(mc) and len(bc) > 1:  # same shape: resolve line by line
            for x, y, z in zip(bc, ac, mc):
                resolve([x], [y], [z])
        else:
            conflicts.append({"line": len(out) + 1, "base": bc, label_a.lower(): ac, label_b.lower(): mc})
            out.append("<<<<<<< " + label_a)
            out.extend(ac)
            out.append("=======")
            out.extend(mc)
            out.append(">>>>>>> " + label_b)

    for p in range(len(bl)):
        if p < i or p not in ma or p not in mb or ma[p] < j or mb[p] < k:
            continue
        resolve(bl[i:p], al[j:ma[p]], ml[k:mb[p]])
        out.append(bl[p])
        i, j, k = p + 1, ma[p] + 1, mb[p] + 1
    resolve(bl[i:], al[j:], ml[k:])
    trail = "\n" if (a.endswith("\n") or b.endswith("\n")) and out else ""
    return "\n".join(out) + trail, conflicts


# ======================================================================================================
# 4b. QR code (byte mode, ECC M, versions 1-10) - pure Python
# ======================================================================================================
_QR_M = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
         5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]),
         9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)])}
_QR_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
             8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_ECL_M_BITS = 0  # L=01 M=00 Q=11 H=10

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator(n):
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _gf_mul(c, _GF_EXP[i])
        g = ng
    return g


def rs_remainder(data, n):
    gen = _rs_generator(n)
    res = list(data) + [0] * n
    for i in range(len(data)):
        c = res[i]
        if c:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], c)
    return res[len(data):]


def rs_syndromes_ok(block, n):
    for i in range(n):
        s = 0
        for byte in block:
            s = _gf_mul(s, _GF_EXP[i]) ^ byte
        if s:
            return False
    return True


def qr_data_capacity(v):
    return sum(c * d for c, d in _QR_M[v][1])


def qr_version_for(nbytes):
    for v in range(1, 11):
        cc = 8 if v < 10 else 16
        if 4 + cc + 8 * nbytes <= qr_data_capacity(v) * 8:
            return v
    raise ValueError("data too long for QR versions 1-10 at ECC M (max 213 bytes)")


def qr_format_bits(mask):
    data = (_ECL_M_BITS << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | (rem & 0x3FF)) ^ 0x5412


def qr_version_bits(v):
    rem = v
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (v << 12) | (rem & 0xFFF)


def _format_positions(size):
    c1 = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    c2 = [(size - 1 - i, 8) for i in range(8)] + [(8, size - 15 + i) for i in range(8, 15)]
    return c1, c2  # (x, y) for bit i


def _qr_function_map(v):
    size = 17 + 4 * v
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    al = _QR_ALIGN[v]
    n = len(al)
    for a in range(n):
        for b in range(n):
            if (a == 0 and b == 0) or (a == 0 and b == n - 1) or (a == n - 1 and b == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(al[a] + dx, al[b] + dy, max(abs(dx), abs(dy)) != 1)
    _draw_format(mod, fn, 0)
    if v >= 7:
        bits = qr_version_bits(v)
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, dark)
            setf(b, a, dark)
    return mod, fn


def _draw_format(mod, fn, mask):
    size = len(mod)
    bits = qr_format_bits(mask)
    c1, c2 = _format_positions(size)
    for i in range(15):
        dark = (bits >> i) & 1 == 1
        for (x, y) in (c1[i], c2[i]):
            mod[y][x] = dark
            fn[y][x] = True
    mod[size - 8][8] = True  # dark module
    fn[size - 8][8] = True


def _zigzag(size, fn):
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in range(size):
            y = size - 1 - vert if upward else vert
            for j in range(2):
                x = right - j
                if not fn[y][x]:
                    yield x, y
        right -= 2


def _mask_bit(m, x, y):
    if m == 0:
        return (x + y) % 2 == 0
    if m == 1:
        return y % 2 == 0
    if m == 2:
        return x % 3 == 0
    if m == 3:
        return (x + y) % 3 == 0
    if m == 4:
        return (x // 3 + y // 2) % 2 == 0
    if m == 5:
        return x * y % 2 + x * y % 3 == 0
    if m == 6:
        return (x * y % 2 + x * y % 3) % 2 == 0
    return ((x + y) % 2 + x * y % 3) % 2 == 0


def qr_penalty(mod):
    size = len(mod)
    score = 0
    lines = [row for row in mod] + [[mod[y][x] for y in range(size)] for x in range(size)]
    pat1 = [True, False, True, True, True, False, True, False, False, False, False]
    pat2 = pat1[::-1]
    for line in lines:
        run, prev = 0, None
        for c in line:
            if c == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run, prev = 1, c
        if run >= 5:
            score += 3 + run - 5
        for i in range(size - 10):
            seg = line[i:i + 11]
            if seg == pat1 or seg == pat2:
                score += 40
    for y in range(size - 1):
        for x in range(size - 1):
            c = mod[y][x]
            if c == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                score += 3
    dark = sum(sum(r) for r in mod)
    score += 10 * (abs(dark * 100 // (size * size) - 50) // 5)
    return score


def qr_encode(data, mask=None):
    """-> (matrix rows of bool, version, mask). data: str (UTF-8) or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    v = qr_version_for(len(data))
    ec, groups = _QR_M[v]
    cap = qr_data_capacity(v)
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)
    put(len(data), 8 if v < 10 else 16)
    for byte in data:
        put(byte, 8)
    put(0, min(4, cap * 8 - len(bits)))
    put(0, (-len(bits)) % 8)
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(cw) < cap:
        cw.append(pad)
        pad ^= 0xEC ^ 0x11
    blocks, pos = [], 0
    for count, dlen in groups:
        for _ in range(count):
            blocks.append(cw[pos:pos + dlen])
            pos += dlen
    ecs = [rs_remainder(b, ec) for b in blocks]
    stream = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                stream.append(b[i])
    for i in range(ec):
        for e in ecs:
            stream.append(e[i])
    base, fn = _qr_function_map(v)
    size = len(base)
    total = len(stream) * 8
    idx = 0
    for x, y in _zigzag(size, fn):
        if idx < total:
            base[y][x] = (stream[idx >> 3] >> (7 - (idx & 7))) & 1 == 1
        idx += 1
    best = None
    for m in ([mask] if mask is not None else range(8)):
        mod = [row[:] for row in base]
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and _mask_bit(m, x, y):
                    mod[y][x] = not mod[y][x]
        _draw_format(mod, [r[:] for r in fn], m)
        pen = qr_penalty(mod)
        if best is None or pen < best[0]:
            best = (pen, mod, m)
    return best[1], v, best[2]


def qr_decode_matrix(mod):
    """Structural decoder used to verify qr_encode: checks finders, timing, both format copies (BCH),
    version info, RS syndromes of every block, then parses the byte-mode payload. Raises ValueError."""
    size = len(mod)
    v = (size - 17) // 4
    if size != 17 + 4 * v or not 1 <= v <= 10:
        raise ValueError("bad size")
    for ox, oy in ((0, 0), (size - 7, 0), (0, size - 7)):
        for dy in range(7):
            for dx in range(7):
                want = max(abs(dx - 3), abs(dy - 3)) != 2
                if mod[oy + dy][ox + dx] != want:
                    raise ValueError("finder pattern broken")
    for i in range(8, size - 8):
        if mod[6][i] != (i % 2 == 0) or mod[i][6] != (i % 2 == 0):
            raise ValueError("timing pattern broken")
    if not mod[size - 8][8]:
        raise ValueError("dark module missing")
    c1, c2 = _format_positions(size)
    f1 = sum(1 << i for i in range(15) if mod[c1[i][1]][c1[i][0]])
    f2 = sum(1 << i for i in range(15) if mod[c2[i][1]][c2[i][0]])
    if f1 != f2:
        raise ValueError("format copies differ")
    valid = {}
    for ecl in range(4):
        for m in range(8):
            data = (ecl << 3) | m
            rem = data
            for _ in range(10):
                rem = (rem << 1) ^ ((rem >> 9) * 0x537)
            valid[((data << 10) | (rem & 0x3FF)) ^ 0x5412] = (ecl, m)
    if f1 not in valid:
        raise ValueError("format BCH check failed")
    ecl, mask = valid[f1]
    if ecl != _ECL_M_BITS:
        raise ValueError("not ECC level M")
    if v >= 7:
        vb = 0
        for i in range(18):
            if mod[i // 3][size - 11 + i % 3]:
                vb |= 1 << i
        if vb != qr_version_bits(v):
            raise ValueError("version info mismatch")
    _, fn = _qr_function_map(v)
    ec, groups = _QR_M[v]
    nblocks = sum(c for c, _ in groups)
    total = qr_data_capacity(v) + ec * nblocks
    bits = []
    for x, y in _zigzag(size, fn):
        bits.append(mod[y][x] ^ _mask_bit(mask, x, y))
    cws = [int("".join("1" if b else "0" for b in bits[i * 8:i * 8 + 8]), 2) for i in range(total)]
    lens = [d for c, d in groups for _ in range(c)]
    blocks = [[] for _ in lens]
    p = 0
    for i in range(max(lens)):
        for bi, ln in enumerate(lens):
            if i < ln:
                blocks[bi].append(cws[p])
                p += 1
    eccs = [[] for _ in lens]
    for i in range(ec):
        for bi in range(len(lens)):
            eccs[bi].append(cws[p])
            p += 1
    for bi in range(len(lens)):
        if not rs_syndromes_ok(blocks[bi] + eccs[bi], ec):
            raise ValueError("Reed-Solomon check failed in block %d" % bi)
    stream = [b for blk in blocks for b in blk]
    sbits = "".join(format(b, "08b") for b in stream)
    if sbits[:4] != "0100":
        raise ValueError("not byte mode")
    cc = 8 if v < 10 else 16
    n = int(sbits[4:4 + cc], 2)
    start = 4 + cc
    payload = bytes(int(sbits[start + 8 * i:start + 8 * i + 8], 2) for i in range(n))
    return {"version": v, "mask": mask, "ecl": "M", "format_bits": f1, "data": payload}


def qr_svg(data, scale=6, border=4):
    mod, v, m = qr_encode(data)
    size = len(mod)
    parts = []
    for y in range(size):
        for x in range(size):
            if mod[y][x]:
                parts.append("M%d %dh1v1h-1z" % (x + border, y + border))
    dim = size + 2 * border
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
            'shape-rendering="crispEdges" role="img" aria-label="QR code"><rect width="100%%" height="100%%" '
            'fill="#fff"/><path fill="#000" d="%s"/></svg>' % (dim, dim, dim * scale, dim * scale, "".join(parts)))


# ======================================================================================================
# 6. Modes / licence / export
# ======================================================================================================
_LIC_CACHE = {"t": 0.0, "v": None}


def license_status():
    """{'plan': 'FREE'|'PRO', 'message': str}. FREE whenever license_guard is missing or errors."""
    now = time.time()
    if _LIC_CACHE["v"] is not None and now - _LIC_CACHE["t"] < 60:
        return _LIC_CACHE["v"]
    res = {"plan": "FREE", "message": "Free plan (licence module not installed)."}
    if _lguard is not None:
        try:
            path = os.environ.get("QUILL_LICENSE_FILE") or os.path.join(_lguard.default_state_dir(),
                                                                         "license.json")
            try:  # a pre-activated store download carries key.txt next to the app
                st = _lguard.check(path, key_file=os.path.join(_HERE, "key.txt"))
            except TypeError:  # an older license_guard without key files
                st = _lguard.check(path)
            plan = "PRO" if (st.get("ok") and st.get("plan") == "PRO") else "FREE"
            res = {"plan": plan, "message": str(st.get("message") or "")}
        except Exception as e:
            res = {"plan": "FREE", "message": "Licence check failed (%s); Free plan active." % type(e).__name__}
    if os.environ.get("QUILL_FORCE_FREE") == "1":
        res = {"plan": "FREE", "message": "Free plan (forced by QUILL_FORCE_FREE)."}
    _LIC_CACHE.update(t=now, v=res)
    return res


def is_pro():
    return license_status()["plan"] == "PRO"


def _fallback_export(text, meta, fmt, out_path):
    if fmt in ("txt", "md"):
        data = text
    elif fmt == "html":
        body = []
        for title, ch in meta.get("chapters", []):
            body.append("<h2>%s</h2>" % html.escape(title))
            body.extend("<p>%s</p>" % html.escape(p).replace("\n", "<br>") for p in split_paragraphs(ch))
        data = ("<!doctype html><html><head><meta charset='utf-8'><title>%s</title><style>body{max-width:40em;"
                "margin:3em auto;font:1.1em/1.6 Georgia,serif;padding:0 1em}</style></head><body><h1>%s</h1>%s"
                "</body></html>" % (html.escape(meta["title"]), html.escape(meta["title"]), "".join(body)))
    else:
        raise ValueError("format %s needs quill_export.py" % fmt)
    atomic_write_bytes(out_path, data.encode("utf-8"))
    return out_path


_LEAD_HEADING = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*#*\s*$")


def export_chapters(book_dir):
    """[(title, body)] in reading order, ready for quill_export. The number prefix of the file name is
    dropped ("03 The Storm.md" -> "The Storm"); a '# Heading' typed as the first line of a chapter becomes its
    title; chapters with no text yet are left out. -> (chapters, skipped_titles)."""
    out, skipped = [], []
    for name in list_chapters(book_dir):
        body = read_text(os.path.join(book_dir, name)).lstrip("﻿")
        title = chapter_title(name)
        lines = body.split("\n")
        first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
        if first is not None:
            m = _LEAD_HEADING.match(lines[first])
            if m:
                title = m.group(1).strip()
                body = "\n".join(lines[first + 1:])
        if not body.strip():
            skipped.append(title)
            continue
        out.append((title, body))
    return out, skipped


def export_meta(book_dir):
    meta = read_meta(book_dir)
    if not meta.get("id"):
        with book_lock(book_dir):
            meta = read_meta(book_dir)
            if not meta.get("id"):  # a stable identifier, so e-readers treat a re-export as the same book
                meta["id"] = "urn:uuid:" + str(__import__("uuid").uuid4())
                write_meta(book_dir, meta)
    return {"title": meta["title"], "author": meta["author"], "lang": meta["language"], "subtitle": meta["subtitle"],
            "identifier": meta["id"], "year": datetime.now().strftime("%Y"), "app": "%s %s" % (APP_NAME, VERSION)}, meta


# ======================================================================================================
# Targets with a finish date (Q08)
# ======================================================================================================
# "80,000 words by 1 March" becomes a figure for today: the words still to write when the day began, shared
# over the days left (today and the finish date both count). The day is the writer's own local date, sent by
# the page, so a phone in another time zone and the PC each see their own today. Missed days need no special
# case: the words left are simply shared over fewer days, so the daily figure rises.
_DAY_RX = re.compile(r"^\d{4}-\d\d-\d\d$")


def parse_day(s):
    """'YYYY-MM-DD' -> date, else None."""
    if isinstance(s, str) and _DAY_RX.match(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def client_today(s):
    """The writer's local date as the page sends it; this PC's local date when it is missing or odd."""
    return parse_day(s) or datetime.now().date()


def target_plan(target, deadline, words_now, today, day_start=None):
    """-> dict, or None when the book has no word target or no finish date.
    day_start: the book's words when today began (None = now, i.e. the first look of the day)."""
    try:
        target = int(target or 0)
    except (TypeError, ValueError):
        return None
    dl = deadline if hasattr(deadline, "toordinal") else parse_day(deadline)
    if target <= 0 or dl is None:
        return None
    words_now = max(0, int(words_now))
    start = words_now if day_start is None else max(0, int(day_start))
    days_left = (dl - today).days + 1
    left_at_start = max(0, target - start)
    remaining = max(0, target - words_now)
    written = max(0, words_now - start)
    if remaining == 0:
        status, per_day = "done", 0
    elif days_left <= 0:
        status, per_day = "past", left_at_start
    else:
        status, per_day = "on", -(-left_at_start // days_left)
    return {"target": target, "deadline": dl.isoformat(), "today": today.isoformat(), "days_left": max(0, days_left),
            "per_day": per_day, "day_start": start, "written_today": written,
            "today_left": max(0, per_day - written), "remaining": remaining, "status": status}


def _target_active(meta):
    return int(meta.get("target_words") or 0) > 0 and parse_day(meta.get("deadline") or "") is not None


def book_words(book_dir):
    return sum(count_words(read_text(os.path.join(book_dir, c))) for c in list_chapters(book_dir))


def note_day_start(book_dir, today, words=None):
    """Remember the book's words at the start of the writer's day (once a day, only with a dated target)."""
    meta = read_meta(book_dir)
    if not _target_active(meta) or (meta.get("target_day") or {}).get("date") == today.isoformat():
        return meta
    with book_lock(book_dir):
        meta = read_meta(book_dir)
        if (meta.get("target_day") or {}).get("date") != today.isoformat():
            meta["target_day"] = {"date": today.isoformat(),
                                  "start": book_words(book_dir) if words is None else int(words)}
            write_meta(book_dir, meta)
    return meta


def book_plan(book_dir, today, words=None):
    words = book_words(book_dir) if words is None else words
    meta = note_day_start(book_dir, today, words)
    td = meta.get("target_day") or {}
    start = td.get("start") if td.get("date") == today.isoformat() else None
    return target_plan(meta.get("target_words"), meta.get("deadline"), words, today, start)


# ======================================================================================================
# Back up the whole book to one zip (Q32)
# ======================================================================================================
BOOK_ZIP_FOLDER = "Stark Quill backups"


def _book_zip_members(book_dir):
    """Every file of the book except the Backups panel's own snapshots and half-written temp files."""
    out = []
    for dirpath, dirnames, filenames in os.walk(book_dir):
        dirnames[:] = sorted(d for d in dirnames if d != BACKUP_DIR and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            if fn.endswith((".tmp", ".tmp-quill", ".part")):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            out.append((p, os.path.relpath(p, book_dir).replace(os.sep, "/")))
    return out


def book_zip(book_dir, dest_dir, now=None):
    """Write '<book> backup <date time>.zip' into dest_dir, read it back and check every file's SHA-256.
    The zip holds one folder named like the book's, so unzipping it gives the book folder back."""
    book_dir = os.path.realpath(book_dir)
    folder = os.path.basename(book_dir)
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d %H%M")
    base = os.path.join(dest_dir, "%s backup %s" % (folder, stamp))
    out, n = base + ".zip", 1
    while os.path.exists(out):
        out, n = "%s (%d).zip" % (base, n), n + 1
    tmp = out + ".part"
    sums = {}
    try:
        with book_lock(book_dir):  # no save lands half-way through the copy
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for p, rel in _book_zip_members(book_dir):
                    try:
                        with open(p, "rb") as f:
                            data = f.read()
                    except OSError as e:
                        if is_lock_error(e):
                            raise HttpError(423, "Not backed up: another program has \"%s\" locked (often a "
                                                 "sync app while it uploads). Try again in a moment." % rel)
                        raise
                    when = max(time.localtime(os.path.getmtime(p))[:6], (1980, 1, 1, 0, 0, 0))
                    zi = zipfile.ZipInfo("%s/%s" % (folder, rel), date_time=when)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    z.writestr(zi, data)
                    sums[zi.filename] = sha256_bytes(data)
        with zipfile.ZipFile(tmp) as z:
            names = z.namelist()
            if z.testzip() is not None or sorted(names) != sorted(sums) or \
                    any(sha256_bytes(z.read(nm)) != sums[nm] for nm in names):
                raise IOError("The backup did not read back the same; nothing was kept. Try again.")
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        os.replace(tmp, out)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    fsync_dir(dest_dir)
    return {"path": out, "file": os.path.basename(out), "files": len(sums), "bytes": os.path.getsize(out)}


def removable_drives():
    """USB sticks and memory cards this computer can write to: [{id, label, root, free}].
    Windows: drives of type DRIVE_REMOVABLE (an external hard disk reports itself as fixed and is not listed).
    Elsewhere: folders mounted under /media, /run/media or /Volumes."""
    out = []
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        k.SetErrorMode(1)  # SEM_FAILCRITICALERRORS: an empty card reader never pops up a system dialog
        mask = k.GetLogicalDrives()
        for i in range(26):
            if not (mask >> i) & 1:
                continue
            root = "%s:\\" % chr(65 + i)
            if k.GetDriveTypeW(ctypes.c_wchar_p(root)) != 2:  # DRIVE_REMOVABLE
                continue
            buf = ctypes.create_unicode_buffer(261)
            if not k.GetVolumeInformationW(ctypes.c_wchar_p(root), buf, 261, None, None, None, None, 0):
                continue  # no card or stick in it
            out.append({"id": root[:2], "label": buf.value or "USB drive", "root": root})
    else:
        for base in ("/media", "/run/media", "/Volumes"):
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for nm in names:
                r = os.path.join(base, nm)
                if os.path.isdir(r) and os.path.realpath(r) != "/" and os.access(r, os.W_OK):
                    out.append({"id": r, "label": nm, "root": r})
    for d in out:
        try:
            d["free"] = __import__("shutil").disk_usage(d["root"]).free
        except OSError:
            d["free"] = None
    return out


def export_file_name(title, fmt):
    return "%s.%s" % (clean_title(title, "Book"), fmt)


_EXPORT_LOCK = threading.Lock()


def keep_previous_export(out):
    """If out exists, rename it to the first free '<stem> (n)<ext>' beside it and return that path."""
    if not os.path.exists(out):
        return None
    stem, ext = os.path.splitext(out)
    for n in range(1, 10000):
        cand = "%s (%d)%s" % (stem, n, ext)
        if os.path.exists(cand):
            continue
        try:
            os.rename(out, cand)  # never overwrites on Windows; the free name was checked just above
            return cand
        except FileExistsError:
            continue
    raise HttpError(409, "The exports folder already holds 9,999 copies of this file. Move some elsewhere first.")


def export_book(book_dir, title, fmt, pro, layout="book", page=None):
    """Write <book>/exports/<Title>.<fmt>. -> dict(path, file, skipped, problems, warnings)."""
    fmt = (fmt or "").lower()
    script = fmt in SCRIPT_FREE + SCRIPT_PRO
    if fmt not in FREE_EXPORTS + PRO_EXPORTS + SCRIPT_FREE + SCRIPT_PRO:
        raise HttpError(400, "Unknown export format.")
    if script and _script is None:
        raise HttpError(501, "The screenplay tools are not installed (quill_script.py is missing).")
    if (fmt in PRO_EXPORTS or fmt in SCRIPT_PRO) and not pro:
        names = {"fdx": "Final Draft", "script": "Screenplay PDF"}
        raise HttpError(402, "%s export is part of Stark Quill Pro." % names.get(fmt, fmt.upper()))
    if layout not in ("book", "manuscript"):
        raise HttpError(400, "Layout must be book or manuscript.")
    chapters, skipped = export_chapters(book_dir)
    if not chapters:
        raise HttpError(409, "There is nothing to export yet: every chapter is empty.")
    meta, bookmeta = export_meta(book_dir)
    if title:
        meta["title"] = title
    warnings = []
    if not script and _studio is not None:   # comments never leave the book; footnotes become chapter notes
        chapters = [(t, _studio.for_export(c)) for t, c in chapters]
        skipped += [t for t, c in chapters if not c.strip()]
        chapters = [(t, c) for t, c in chapters if c.strip()]
        if not chapters:
            raise HttpError(409, "There is nothing to export yet: the chapters hold only comments.")
    if not script and bookmeta.get("bibliography") and _cite is not None:
        srcs = _sources_load(book_dir)
        if srcs["sources"]:
            chapters.append(_cite.chapter(srcs["sources"], srcs["style"]))
    page = page if page in PAGE_SIZES else bookmeta.get("page", "trade")
    out_dir = os.path.join(book_dir, "exports")
    os.makedirs(out_dir, exist_ok=True)
    if script:
        fname = export_file_name(meta["title"] + (" (screenplay)" if fmt == "script" else ""),
                                 {"script": "pdf"}.get(fmt, fmt))
    else:
        fname = export_file_name(meta["title"] + (" (manuscript)" if layout == "manuscript" else ""), fmt)
    out = os.path.join(out_dir, fname)
    with _EXPORT_LOCK:  # two exports at once never pick the same copy name (saves do not wait for it)
        kept = keep_previous_export(out)  # an export never replaces an older one without keeping a copy
        try:
            if script:
                out = _write_script(chapters, meta, fmt, out, warnings)
            else:
                out = _write_export(chapters, meta, bookmeta, fmt, out, layout, page, warnings)
        except BaseException:
            if kept and not os.path.exists(out):  # nothing new was written: put the older export back
                try:
                    os.rename(kept, out)
                except OSError:
                    pass
            raise
    problems = []
    if fmt == "script":
        problems += _script.check_pdf(out)
    elif fmt == "fdx":
        problems += _script.check_fdx(read_text(out))
    elif fmt in ("epub", "docx", "pdf") and _qexport is not None and hasattr(_qexport, "validate"):
        problems += _qexport.validate(out)
    if fmt == "epub" and _epubcheck is not None:
        problems += _epubcheck.check(out)
    return {"path": out, "file": os.path.basename(out), "skipped": skipped, "problems": problems,
            "warnings": warnings, "size": os.path.getsize(out), "chapters": len(chapters),
            "kept_previous": os.path.basename(kept) if kept else None}


def _write_script(chapters, meta, fmt, out, warnings):
    """Screenplay files from Fountain chapters: .fountain (free), .fdx and the script-page PDF (Pro)."""
    if fmt == "fountain":
        atomic_write_bytes(out, _script.to_fountain(chapters, meta).encode("utf-8"))
    elif fmt == "fdx":
        atomic_write_bytes(out, _script.to_fdx(chapters, meta).encode("utf-8"))
    else:
        tmp = out + ".tmp"
        res = _script.to_pdf(chapters, meta, tmp)
        _replace(tmp, out)
        warnings.append("%s of script pages (about %s on screen)." % (
            "1 page" if res["pages"] == 1 else "%d pages" % res["pages"],
            "1 minute" if res["pages"] == 1 else "%d minutes" % res["pages"]))
        if res["missing"]:
            warnings.append("%d characters have no Courier letter and print as ?." % res["missing"])
    return out


def _write_export(chapters, meta, bookmeta, fmt, out, layout, page, warnings):
    if _qexport is not None and hasattr(_qexport, "export"):
        opts = {"warnings": warnings, "page": page, "subheads": True, "smart": fmt not in ("txt", "md"),
                "book": True, "chapter_numbers": bool(bookmeta.get("chapter_numbers")),
                "copyright": bool(bookmeta.get("copyright")) and bool(meta["author"]), "layout": layout,
                "embed_fonts": True}
        try:
            res = _qexport.export(chapters, meta, fmt, out, **opts)
        except TypeError:  # an older quill_export without the book options
            res = _qexport.export(chapters, meta, fmt, out)
        out = res if isinstance(res, str) else out
    elif fmt in PRO_EXPORTS:
        raise HttpError(501, "quill_export.py is not installed; only txt/md/html are available.")
    else:
        text = "\n\n".join("# %s\n\n%s" % (t, c.strip()) for t, c in chapters) + "\n"
        meta["chapters"] = chapters
        out = _fallback_export(text, meta, fmt, out)
    return out


# ======================================================================================================
# Book-level save (ledger + merge + graveyard + snapshot)
# ======================================================================================================
def save_chapter(book_dir, name, text, base_sha=None, base_text=None, snapshot_interval=SNAPSHOT_MIN_INTERVAL):
    """Save one chapter. If base_sha is given and the stored copy has moved on, three-way merge with
    base_text (returns need_base when base_text is missing). Returns a JSON-able dict."""
    led = Ledger(book_dir)
    text = clean_text(text)
    with led.lock:
        path = led.path_for(name)
        exists = os.path.exists(path)
        old = read_text(path) if exists else ""
        old_sha = sha256_bytes(old.encode("utf-8"))
        merged, conflicts = False, []
        if base_sha and base_sha != old_sha and text != old:
            if base_text is None:
                return {"ok": False, "need_base": True, "sha": old_sha}
            text, conflicts = merge_lines(clean_text(base_text), old, text)
            merged = True
        if exists and text == old:
            return {"ok": True, "sha": old_sha, "unchanged": True, "merged": merged, "text": text if merged else None,
                    "conflicts": conflicts, "delta": 0, "words": count_words(text)}
        sha = led.save(name, text)
        dead = deleted_paragraphs(old, text)
        try:
            append_graveyard(led.book_dir, name, dead)
        except OSError:
            pass
        snap = None
        try:
            snap = snapshot_book(led.book_dir, min_interval=snapshot_interval)
        except OSError:
            pass
    words = count_words(text)
    return {"ok": True, "sha": sha, "merged": merged, "text": text if merged else None, "conflicts": conflicts,
            "graveyard": len(dead), "snapshot": os.path.basename(snap) if snap else None,
            "words": words, "delta": words - count_words(old)}


_COMMENT_WORDS = re.compile(r"\{>>.*?<<\}", re.S)


def count_words(text):
    """Words as a writer counts them: runs of non-space characters, ignoring lone markup like *** or #.
    Comments ({>>a note<<}) are notes to self, not part of the book, so their words do not count."""
    text = text or ""
    if "{>>" in text:
        text = _COMMENT_WORDS.sub(" ", text)
    return sum(1 for w in text.split() if any(c.isalnum() for c in w))


# ======================================================================================================
# 7. Book structure: numbered chapter files, book.json, renames, deleted chapters
# ======================================================================================================
# Chapter order lives in the file names themselves ("01 Chapter One.md", "02 The Storm.md"), so any file
# manager or text editor shows the book in the same order the app does. The app strips the number when it
# shows a title or exports the book. Renumbering (reorder / insert) is journalled: a crash half-way is finished
# by Ledger.recover() at the next start, so no chapter is ever left under a temporary name.
_NUM_RX = re.compile(r"^(\d{1,4})[ ._-]+(.+)$")
_TITLE_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


# Windows file names cannot hold ':' or '?', but titles often do ("Part One: Salt", "Who Rang the Bell?"). The file
# name keeps a look-alike letter (U+A789 MODIFIER LETTER COLON, U+FF1F FULLWIDTH QUESTION MARK) and every title shown
# or exported turns it back into the real character.
_TITLE_TO_FILE = {":": "꞉", "?": "？"}
_FILE_TO_TITLE = {v: k for k, v in _TITLE_TO_FILE.items()}


def title_from_file(stem):
    return "".join(_FILE_TO_TITLE.get(c, c) for c in stem)


def chapter_title(name):
    stem = os.path.splitext(name)[0]
    m = _NUM_RX.match(stem)
    return title_from_file((m.group(2).strip() if m else stem) or stem)


def chapter_number(name):
    m = _NUM_RX.match(os.path.splitext(name)[0])
    return int(m.group(1)) if m else None


def clean_title(title, fallback="Untitled"):
    """A title that is also a safe Windows / macOS / Linux file name."""
    t = "".join(_TITLE_TO_FILE.get(c, c) for c in str(title or ""))
    t = _TITLE_BAD.sub("-", t)
    t = re.sub(r"\s+", " ", t).strip().lstrip(".").strip()
    t = t[:120].rstrip(". ")
    return t or fallback


def _read_json_file(path):
    try:
        with open(path, "rb") as f:
            obj = json.loads(f.read().decode("utf-8-sig"))
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None


def _write_json_file(path, obj, durable=True):
    data = json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    if durable:
        atomic_write_bytes(path, data)
        return
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    _replace(tmp, path)


META_DEFAULTS = {"title": "", "author": "", "language": "en", "subtitle": "", "target_words": 0, "deadline": "",
                 "chapter_numbers": False, "copyright": True, "page": "trade", "chapters": {}}


def read_meta(book_dir):
    data = _read_json_file(os.path.join(book_dir, BOOK_META)) or {}
    meta = dict(data)  # unknown keys are kept, so a newer version's settings survive an older one
    for k, v in META_DEFAULTS.items():
        if not isinstance(meta.get(k), type(v)):
            meta[k] = json.loads(json.dumps(v))
    meta["title"] = meta["title"].strip() or title_from_file(os.path.basename(os.path.normpath(book_dir)))
    if meta["page"] not in PAGE_SIZES:
        meta["page"] = "trade"
    return meta


def write_meta(book_dir, meta):
    _write_json_file(os.path.join(book_dir, BOOK_META), meta)


def update_meta(book_dir, changes):
    allowed = {"title": str, "author": str, "language": str, "subtitle": str, "target_words": int,
               "chapter_numbers": bool, "copyright": bool, "page": str, "deadline": str, "kind": str,
               "cite_style": str, "bibliography": bool}
    with book_lock(book_dir):
        meta = read_meta(book_dir)
        for k, typ in allowed.items():
            if k not in changes:
                continue
            v = changes[k]
            if typ is int:
                try:
                    v = max(0, min(10_000_000, int(v)))
                except (TypeError, ValueError):
                    raise HttpError(400, "%s must be a number" % k)
            elif typ is bool:
                v = bool(v)
            else:
                v = clean_text(str(v)).strip()[:200]
            if k == "language" and not re.match(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$", v):
                raise HttpError(400, "Language should be a code such as en, en-GB or de.")
            if k == "page" and v not in PAGE_SIZES:
                raise HttpError(400, "Unknown page size.")
            if k == "kind" and _studio is not None and v not in _studio.KINDS:
                raise HttpError(400, "Kind must be one of: %s." % ", ".join(_studio.KINDS))
            if k == "cite_style" and _cite is not None and v not in _cite.STYLES:
                raise HttpError(400, "Citation style must be MLA, APA or Chicago.")
            if k == "deadline" and v and parse_day(v) is None:
                raise HttpError(400, "The finish date should be a date such as 2027-03-01.")
            if k in ("deadline", "target_words") and meta.get(k) != v:
                meta.pop("target_day", None)  # a new target or date: today's figure is worked out again
            meta[k] = v
        write_meta(book_dir, meta)
        return meta


def chapter_note(book_dir, name, synopsis=None, status=None):
    with book_lock(book_dir):
        if name not in list_chapters(book_dir):
            raise HttpError(404, "No such chapter.")
        meta = read_meta(book_dir)
        note = dict(meta["chapters"].get(name) or {})
        if synopsis is not None:
            note["synopsis"] = clean_text(str(synopsis))[:4000]
        if status is not None:
            if status not in CHAPTER_STATUSES:
                raise HttpError(400, "Status must be one of: %s." % ", ".join(CHAPTER_STATUSES))
            note["status"] = status
        meta["chapters"][name] = note
        write_meta(book_dir, meta)
        return note


def _renames(book_dir):
    return _read_json_file(os.path.join(book_dir, RENAMES)) or {}


def resolve_renamed(book_dir, name):
    """A phone that was offline may still save to a chapter's old name: follow the rename trail."""
    if os.path.exists(os.path.join(book_dir, name)):
        return name
    trail = _renames(book_dir)
    seen = set()
    while name in trail and name not in seen and len(seen) < 50:
        seen.add(name)
        name = trail[name]
    return name


def _record_renames(book_dir, moves):
    if not moves:
        return
    trail = _renames(book_dir)
    for old, new in moves.items():
        trail[old] = new
        for k, v in list(trail.items()):  # keep chains short: a -> b -> c becomes a -> c
            if v == old:
                trail[k] = new
    if len(trail) > 400:
        trail = dict(list(trail.items())[-400:])
    _write_json_file(os.path.join(book_dir, RENAMES), trail, durable=False)


def _move_meta(book_dir, moves):
    if not moves:
        return
    meta = read_meta(book_dir)
    notes = meta["chapters"]
    moved = {new: notes.pop(old) for old, new in moves.items() if old in notes}
    notes.update(moved)
    write_meta(book_dir, meta)


def recover_renumber(book_dir):
    """Finish (or undo) a renumbering that a crash interrupted. -> number of files put back."""
    jpath = os.path.join(book_dir, RENUM_JOURNAL)
    job = _read_json_file(jpath)
    if not job:
        return 0
    n = 0
    for old, tmp, final in job.get("moves", []):
        tp = os.path.join(book_dir, tmp)
        if not os.path.exists(tp):
            continue
        for target in (final, old):
            if not os.path.exists(os.path.join(book_dir, target)):
                os.rename(tp, os.path.join(book_dir, target))
                break
        else:
            stem, ext = os.path.splitext(final)
            k = 1
            while os.path.exists(os.path.join(book_dir, "%s (recovered %d)%s" % (stem, k, ext))):
                k += 1
            os.rename(tp, os.path.join(book_dir, "%s (recovered %d)%s" % (stem, k, ext)))
        n += 1
    os.remove(jpath)
    return n


def apply_order(book_dir, entries):
    """entries: [{"old": existing chapter file or None (new chapter), "title": str, "ext": ".md"}] in reading
    order. Renames files to 'NN Title.ext' (journalled), creates the new ones. -> list of final names."""
    width = max(2, len(str(len(entries))))
    finals = []
    for i, e in enumerate(entries, 1):
        ext = e.get("ext") or (os.path.splitext(e["old"])[1] if e.get("old") else ".md")
        finals.append("%0*d %s%s" % (width, i, clean_title(e["title"]), ext.lower() if ext.lower() in CHAPTER_EXTS else ".md"))
    if len({f.lower() for f in finals}) != len(finals):
        raise HttpError(409, "Two chapters would get the same file name.")
    for f in finals:
        validate_rel(f)
    led = Ledger(book_dir)
    with led.lock:
        if led.compact():
            raise HttpError(409, "A save is still being finished. Try again in a moment.")
        moves = [(e["old"], f) for e, f in zip(entries, finals) if e.get("old") and e["old"] != f]
        if moves:
            tag = secrets.token_hex(4)
            job = {"moves": [[old, ".quill_mv_%s_%d" % (tag, k), new] for k, (old, new) in enumerate(moves)]}
            _write_json_file(os.path.join(book_dir, RENUM_JOURNAL), job)
            for old, tmp, new in job["moves"]:
                os.rename(os.path.join(book_dir, old), os.path.join(book_dir, tmp))
            for old, tmp, new in job["moves"]:
                os.rename(os.path.join(book_dir, tmp), os.path.join(book_dir, new))
            os.remove(os.path.join(book_dir, RENUM_JOURNAL))
            _record_renames(book_dir, dict(moves))
            _move_meta(book_dir, dict(moves))
        for e, f in zip(entries, finals):
            if not e.get("old"):
                led.save(f, e.get("text") or "")
    return finals


def _entries(book_dir, names=None):
    names = list_chapters(book_dir) if names is None else names
    return [{"old": n, "title": chapter_title(n), "ext": os.path.splitext(n)[1]} for n in names]


def new_chapter(book_dir, title, after=None, text=""):
    """Add a chapter at the end, or right after `after`. -> its file name."""
    title = clean_title(title, "Untitled")
    names = list_chapters(book_dir)
    if after is not None and after not in names:
        raise HttpError(404, "No such chapter.")
    numbered = all(chapter_number(n) is not None for n in names)
    if after is None and numbered:
        nums = [chapter_number(n) for n in names]
        width = max([2] + [len(_NUM_RX.match(os.path.splitext(n)[0]).group(1)) for n in names])
        name = "%0*d %s.md" % (width, (max(nums) if nums else 0) + 1, title)
        if os.path.exists(os.path.join(book_dir, name)):
            raise HttpError(409, "A chapter with that name exists.")
        validate_rel(name)
        Ledger(book_dir).save(name, text)
        return name
    entries = _entries(book_dir, names)
    pos = len(entries) if after is None else names.index(after) + 1
    entries.insert(pos, {"old": None, "title": title, "ext": ".md", "text": text})
    return apply_order(book_dir, entries)[pos]


def rename_chapter(book_dir, name, title):
    names = list_chapters(book_dir)
    if name not in names:
        raise HttpError(404, "No such chapter.")
    entries = _entries(book_dir, names)
    i = names.index(name)
    entries[i]["title"] = clean_title(title, chapter_title(name))
    if all(chapter_number(n) is not None for n in names):
        # keep everyone else's file name: only this one changes
        stem = os.path.splitext(name)[0]
        prefix = stem[: _NUM_RX.match(stem).start(2)]
        new = prefix + entries[i]["title"] + os.path.splitext(name)[1]
        if new == name:
            return name
        if os.path.exists(os.path.join(book_dir, new)) and new.lower() != name.lower():
            raise HttpError(409, "A chapter with that name exists.")
        validate_rel(new)
        led = Ledger(book_dir)
        with led.lock:
            if led.compact():
                raise HttpError(409, "A save is still being finished. Try again in a moment.")
            os.rename(os.path.join(book_dir, name), os.path.join(book_dir, new))
            _record_renames(book_dir, {name: new})
            _move_meta(book_dir, {name: new})
        return new
    return apply_order(book_dir, entries)[i]


def reorder_chapters(book_dir, order):
    names = list_chapters(book_dir)
    if sorted(order) != sorted(names):
        raise HttpError(409, "The chapter list changed. Reload and try again.")
    return apply_order(book_dir, _entries(book_dir, order))


def delete_chapter(book_dir, name):
    """Never destroys text: a backup snapshot is made, then the file moves to .backups/deleted/."""
    led = Ledger(book_dir)
    with led.lock:
        if name not in list_chapters(book_dir):
            raise HttpError(404, "No such chapter.")
        snapshot_book(book_dir)
        ddir = os.path.join(book_dir, BACKUP_DIR, DELETED_DIR)
        os.makedirs(ddir, exist_ok=True)
        dest = "%s %s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), name)
        os.replace(os.path.join(book_dir, name), os.path.join(ddir, dest))
        meta = read_meta(book_dir)
        note = meta["chapters"].pop(name, None)
        if note:
            meta.setdefault("deleted_notes", {})[dest] = note
        write_meta(book_dir, meta)
    return dest


def list_deleted(book_dir):
    ddir = os.path.join(book_dir, BACKUP_DIR, DELETED_DIR)
    out = []
    try:
        names = sorted(os.listdir(ddir), reverse=True)
    except FileNotFoundError:
        return out
    for fn in names:
        m = re.match(r"^(\d{8}-\d{6}) (.+)$", fn)
        if not m or not fn.lower().endswith(CHAPTER_EXTS):
            continue
        try:
            when = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M")
            words = count_words(read_text(os.path.join(ddir, fn)))
        except (OSError, ValueError):
            continue
        out.append({"file": fn, "title": chapter_title(m.group(2)), "when": when, "words": words})
    return out


def undelete_chapter(book_dir, file):
    validate_rel(file)
    src = os.path.join(book_dir, BACKUP_DIR, DELETED_DIR, file)
    if not os.path.isfile(src):
        raise HttpError(404, "That deleted chapter is not in the backups folder any more.")
    m = re.match(r"^\d{8}-\d{6} (.+)$", file)
    title = chapter_title(m.group(1)) if m else "Restored chapter"
    text = read_text(src)
    name = new_chapter(book_dir, title, text=text)
    os.remove(src)
    meta = read_meta(book_dir)
    note = meta.get("deleted_notes", {}).pop(file, None)
    if note:
        meta["chapters"][name] = note
    write_meta(book_dir, meta)
    return name


# ======================================================================================================
# 8. Search, graveyard reading, backups, writing stats
# ======================================================================================================
NL = chr(10)


def _utf16_len(s):
    return len(s.encode("utf-16-le")) // 2


def search_rx(q, case=False, word=False):
    if not isinstance(q, str) or not q.strip():
        raise HttpError(400, "Type something to search for.")
    pat = re.escape(q)
    if word:
        pat = r"(?<!\w)" + pat + r"(?!\w)"
    return re.compile(pat, 0 if case else re.IGNORECASE)


def search_book(book_dir, q, case=False, word=False, limit=500):
    """-> {"hits": [...], "total": n, "truncated": bool}. start/end are UTF-16 offsets (what a browser
    textarea uses), so the editor can select the match exactly even after an emoji."""
    rx = search_rx(q, case, word)
    hits, total = [], 0
    for name in list_chapters(book_dir):
        text = read_text(os.path.join(book_dir, name))
        pos16, last, line = 0, 0, 1
        for m in rx.finditer(text):
            total += 1
            if len(hits) >= limit:
                continue
            pos16 += _utf16_len(text[last:m.start()])
            line += text.count(NL, last, m.start())
            start16 = pos16
            end16 = start16 + _utf16_len(m.group(0))
            line_start = text.rfind(NL, 0, m.start()) + 1
            line_end = text.find(NL, m.end())
            line_end = len(text) if line_end < 0 else line_end
            hits.append({"name": name, "title": chapter_title(name), "start": start16, "end": end16, "line": line,
                         "before": text[max(line_start, m.start() - 70):m.start()], "match": m.group(0),
                         "after": text[m.end():min(line_end, m.end() + 70)]})
            line += m.group(0).count(NL)
            pos16, last = end16, m.end()
    return {"ok": True, "hits": hits, "total": total, "truncated": total > len(hits)}


def replace_in_book(book_dir, q, repl, case=False, word=False, names=None):
    """Replace everywhere (or in `names`). A backup snapshot is taken first, so it can be undone."""
    rx = search_rx(q, case, word)
    repl = clean_text(str(repl or ""))
    led = Ledger(book_dir)
    changed, count = [], 0
    with led.lock:
        targets = [n for n in list_chapters(book_dir) if names is None or n in names]
        todo = []
        for n in targets:
            text = read_text(os.path.join(book_dir, n))
            new, k = rx.subn(lambda m: repl, text)
            if k:
                todo.append((n, new))
                count += k
        snap = snapshot_book(book_dir) if todo else None
        for n, new in todo:
            led.save(n, new)
            changed.append(n)
    return {"ok": True, "replaced": count, "chapters": changed, "snapshot": os.path.basename(snap) if snap else None}


_GY_RX = re.compile(r"^## (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) - ([^\n]+?)\n\n(.*?)\n\n---\n", re.S | re.M)


def read_graveyard(book_dir, limit=400):
    path = os.path.join(book_dir, GRAVEYARD)
    try:
        text = read_text(path)
    except FileNotFoundError:
        return []
    out = [{"when": m.group(1)[:16], "chapter": m.group(2), "title": chapter_title(m.group(2)), "text": m.group(3)}
           for m in _GY_RX.finditer(text)]
    out.reverse()
    return out[:limit]


_SNAP_INFO = {}  # (path, size, mtime) -> (chapters, words): snapshots never change once written


def snapshot_info(book_dir):
    out = []
    bdir = os.path.join(book_dir, BACKUP_DIR)
    for s in reversed(list_snapshots(book_dir)):
        p = os.path.join(bdir, s)
        try:
            size = os.path.getsize(p)
            key = (os.path.normcase(p), size, os.path.getmtime(p))
            if key not in _SNAP_INFO:
                with zipfile.ZipFile(p) as z:
                    chs = [n for n in z.namelist() if n.lower().endswith(CHAPTER_EXTS) and n != GRAVEYARD]
                    _SNAP_INFO[key] = (chs, sum(count_words(z.read(n).decode("utf-8", "replace")) for n in chs))
            chs, words = _SNAP_INFO[key]
        except (OSError, zipfile.BadZipFile):
            continue
        t = _snap_time(s)
        out.append({"name": s, "when": datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "",
                    "chapters": len(chs), "words": words, "size": size})
    return out


def _open_snapshot(book_dir, snap):
    if snap not in list_snapshots(book_dir):
        raise HttpError(404, "That backup is not there any more.")
    return zipfile.ZipFile(os.path.join(book_dir, BACKUP_DIR, snap))


def snapshot_chapters(book_dir, snap):
    with _open_snapshot(book_dir, snap) as z:
        return [{"name": n, "title": chapter_title(n), "words": count_words(z.read(n).decode("utf-8", "replace"))}
                for n in sorted(z.namelist(), key=lambda s: [int(t) if t.isdigit() else t.lower()
                                                            for t in re.split(r"(\d+)", s)])
                if n.lower().endswith(CHAPTER_EXTS)]


def snapshot_text(book_dir, snap, name):
    with _open_snapshot(book_dir, snap) as z:
        if name not in z.namelist():
            raise HttpError(404, "That chapter is not in this backup.")
        return z.read(name).decode("utf-8", "replace")


def restore_chapter(book_dir, snap, name):
    """Put one chapter back as it was in a backup. The current text goes into a fresh backup first."""
    text = snapshot_text(book_dir, snap, name)
    led = Ledger(book_dir)
    with led.lock:
        names = list_chapters(book_dir)
        trail = resolve_renamed(book_dir, name)  # renamed or renumbered since the backup was taken
        target = name if name in names else trail if trail in names else \
            next((n for n in names if chapter_title(n) == chapter_title(name)), None)
        fresh = snapshot_book(book_dir)
        if target is None:
            target = new_chapter(book_dir, chapter_title(name), text=text)
        else:
            led.save(target, text)
    return {"ok": True, "name": target, "safety_backup": os.path.basename(fresh) if fresh else None}


class Stats:
    """Words written per day (net: new words minus deleted ones) and the daily goal, kept on this PC in
    <books>/.quill_stats.json so the PC and a paired phone share one count. Days are the PC's local dates."""

    def __init__(self, root):
        self.path = os.path.join(root, STATS_FILE)
        self.lock = threading.Lock()
        d = _read_json_file(self.path) or {}
        self.data = {"goal": int(d.get("goal") or 0) if str(d.get("goal", "0")).isdigit() else 0,
                     "days": {k: v for k, v in (d.get("days") or {}).items()
                              if re.match(r"^\d{4}-\d\d-\d\d$", str(k)) and isinstance(v, dict)}}

    @staticmethod
    def _day(now=None):
        return datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d")

    def _save(self):
        try:
            _write_json_file(self.path, self.data, durable=False)
        except OSError:
            pass

    def add(self, delta, now=None):
        if not delta:
            return
        with self.lock:
            rec = self.data["days"].setdefault(self._day(now), {"words": 0})
            rec["words"] = int(rec.get("words", 0)) + int(delta)
            if delta > 0 and _craft is not None:
                _craft.note_session(rec, time.time() if now is None else now)
            if len(self.data["days"]) > 800:
                for k in sorted(self.data["days"])[:-800]:
                    del self.data["days"][k]
            self._save()

    def set_goal(self, goal):
        with self.lock:
            self.data["goal"] = max(0, min(100000, int(goal)))
            self._save()

    def snapshot(self):
        with self.lock:
            return self.data["goal"], {k: dict(v) for k, v in self.data["days"].items()}

    def summary(self, now=None, span=14):
        now = time.time() if now is None else now
        with self.lock:
            goal, days = self.data["goal"], dict(self.data["days"])
        today = datetime.fromtimestamp(now).date()

        def words(d):
            return max(0, int((days.get(d.isoformat()) or {}).get("words", 0)))

        def met(d):
            w = words(d)
            return w >= goal if goal else w > 0

        streak = 0
        d = today if met(today) else today - _timedelta(days=1)
        while met(d) and streak < 3650:
            streak += 1
            d -= _timedelta(days=1)
        best, run, prev = 0, 0, None
        for k in sorted(days):
            try:
                dd = datetime.strptime(k, "%Y-%m-%d").date()
            except ValueError:
                continue
            run = run + 1 if (met(dd) and run and prev == dd - _timedelta(days=1)) else (1 if met(dd) else 0)
            prev = dd
            best = max(best, run)
        recent = []
        for i in range(span - 1, -1, -1):
            dd = today - _timedelta(days=i)
            recent.append({"date": dd.isoformat(), "words": words(dd), "met": met(dd)})
        return {"goal": goal, "today": words(today), "date": today.isoformat(), "streak": streak,
                "best_streak": max(best, streak), "days": recent}


# ======================================================================================================
# 3. Server
# ======================================================================================================
class HttpError(Exception):
    def __init__(self, status, msg, extra=None):
        Exception.__init__(self, msg)
        self.status = status
        self.msg = msg
        self.extra = extra or {}


class ChapterLocked(HttpError):
    """A chapter (or the book's journal) is held by another program, often a sync app mid-upload."""
    RETRY_MS = 4000

    def __init__(self, name, journaled=False, cause=None, retry=False):
        title = chapter_title(name) if name else "this chapter"
        code = getattr(cause, "winerror", None) or getattr(cause, "errno", None)
        msg = ("Not saved yet: another program has \"%s\" locked. This is usually OneDrive, Dropbox or another "
               "sync app while it uploads, or a virus scanner. " % title)
        if retry:  # the editor page keeps its copy and sends it again (quill_ui save())
            msg += "Your text is kept %s, and Quill tries again in a few seconds." % (
                "in Quill's journal and in this browser" if journaled else "in this browser")
        else:
            msg += "Try again in a moment; %s" % (
                "the change is kept in Quill's journal meanwhile." if journaled else "nothing was changed.")
        HttpError.__init__(self, 423, msg, {"locked": True, "journaled": bool(journaled),
                                            "retry_ms": self.RETRY_MS if retry else None, "code": code})
        self.name = name
        self.journaled = journaled
        self.cause = cause


def _is_loopback(addr):
    return addr in ("127.0.0.1", "::1") or addr.startswith("127.") or addr == "::ffff:127.0.0.1"


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET; UDP connect sends nothing, only picks the route
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)
# WinError 10053 (aborted by this host), 10054 (reset by the peer), 10058 (socket shut down); POSIX equivalents.
_GONE_WINERRORS = (10053, 10054, 10058)
_GONE_ERRNOS = tuple(getattr(errno, n) for n in ("EPIPE", "ECONNRESET", "ECONNABORTED", "ESHUTDOWN")
                     if hasattr(errno, n))


def client_gone(exc):
    """True when exc (or the OS error it wraps) only means the browser went away mid-answer."""
    for _ in range(6):
        if exc is None:
            return False
        if isinstance(exc, CLIENT_GONE) or isinstance(exc, ssl.SSLEOFError):
            return True
        if isinstance(exc, OSError) and (getattr(exc, "winerror", None) in _GONE_WINERRORS
                                         or exc.errno in _GONE_ERRNOS):
            return True
        exc = exc.__cause__ or (exc.__context__ if isinstance(exc, OSError) else None)
    return False


def gone_line(where, exc):
    """One quiet health-log line for a browser disconnect (no traceback)."""
    code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    return "browser disconnected during %s (%s%s); nothing lost" % (
        where, type(exc).__name__, (" %s" % code) if code else "")


class QuillHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # never share a port with another process (Windows semantics differ)

    def __init__(self, addr, handler, app):
        self.app = app
        http.server.ThreadingHTTPServer.__init__(self, addr, handler)

    def handle_error(self, request, client_address):
        # A browser that closes a tab or reloads mid-answer is normal, not a fault: one line, no traceback.
        exc = sys.exc_info()[1]
        if client_gone(exc):
            self.app.health(gone_line("a request", exc))
            return
        http.server.ThreadingHTTPServer.handle_error(self, request, client_address)

    def finish_request(self, request, client_address):
        # LAN mode with HTTPS: one port speaks TLS (first byte 0x16) and plain HTTP (local_tls.sniff_and_wrap)
        ctx = self.app.tls_context if (self.app.lan_enabled and _ltls is not None) else None
        conn = _ltls.sniff_and_wrap(request, ctx, SOCKET_TIMEOUT) if ctx is not None else request
        if conn is None:
            return
        try:
            http.server.ThreadingHTTPServer.finish_request(self, conn, client_address)
        finally:
            if conn is not request:
                for fn in (lambda: conn.shutdown(socket.SHUT_WR), conn.close):
                    try:
                        fn()
                    except OSError:
                        pass


class QuillHandler(http.server.BaseHTTPRequestHandler):
    server_version = "StarkQuill/" + VERSION
    sys_version = ""
    timeout = SOCKET_TIMEOUT

    def log_message(self, fmt, *args):  # quiet console
        pass

    @property
    def app(self):
        return self.server.app

    def do_GET(self):
        self._guarded("GET")

    def do_POST(self):
        self._guarded("POST")

    def _guarded(self, method):
        try:
            try:
                self._dispatch(method)
            except SandboxError as e:
                self._json(403, {"ok": False, "error": "That path is not allowed (%s)." % e})
            except HttpError as e:
                if isinstance(e, ChapterLocked):  # one line naming the cause, not a traceback
                    self.app.health("save waits: %s is locked by another program (%s)" % (
                        e.name, e.extra.get("code")))
                out = {"ok": False, "error": e.msg}
                out.update(e.extra)
                self._json(e.status, out)
        except Exception as e:
            if client_gone(e):                 # the browser went away mid-answer (tab closed, reload)
                self.close_connection = True
                self.app.health(gone_line("%s %s" % (method, self.path.split("?")[0]), e))
                return
            self.app.health("handler %s %s\n%s" % (method, self.path.split("?")[0], traceback.format_exc()))
            try:
                self._json(500, {"ok": False, "error": "Something went wrong, but your text is safe (journal + "
                                 "browser buffer). Details are in .quill_health.log."})
            except Exception:
                pass

    def _json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; "
                         "script-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    # --- security checks ------------------------------------------------------------------------
    def _check_host(self):
        host = (self.headers.get("Host") or "").strip().lower()
        m = re.match(r"^(\[[0-9a-f:.]+\]|[a-z0-9.\-]+)(?::(\d+))?$", host)
        if not m:
            raise HttpError(403, "Bad Host header.")
        name, port = m.group(1), m.group(2)
        if port and int(port) != self.server.server_address[1]:
            raise HttpError(403, "Bad Host port.")
        if name in ("127.0.0.1", "localhost", "[::1]"):
            return
        if self.app.lan_enabled and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", name):
            return
        if self.app.lan_enabled and self.app.tls_context is not None and name in self.app.tls_names:
            return  # this PC's own hostname / hostname.local (names in its certificate)
        raise HttpError(403, "Host not allowed (DNS-rebinding guard).")

    def _check_origin(self):
        origin = self.headers.get("Origin")
        scheme = "https://" if isinstance(self.connection, ssl.SSLSocket) else "http://"
        if origin and origin.lower() != scheme + (self.headers.get("Host") or "").lower():
            raise HttpError(403, "Cross-origin request refused.")

    def _auth(self):
        tok = self.headers.get("X-Quill-Token", "")
        loop = _is_loopback(self.client_address[0])
        if loop and tok and secrets.compare_digest(tok, self.app.local_token):
            return
        if self.app.lan_enabled and tok and any(secrets.compare_digest(tok, s) for s in list(self.app.mobile_sessions)):
            return
        if loop:   # this PC's own tab, opened before Quill restarted (every start makes a new key)
            raise HttpError(401, "Quill was restarted. Reload this page to keep writing; your text is kept.")
        raise HttpError(401, "This phone is not connected to Quill any more. Connect it again from Quill on your PC.")

    def _body(self):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HttpError(415, "JSON only.")
        try:
            n = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            n = -1
        if n < 0:
            raise HttpError(411, "Content-Length required.")
        if n > MAX_BODY:
            raise HttpError(413, "Request too large (max 8 MiB per chapter).")
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise HttpError(400, "Malformed JSON.")
        if not isinstance(obj, dict):
            raise HttpError(400, "JSON object expected.")
        return obj

    # --- routing ----------------------------------------------------------------------------------
    def _dispatch(self, method):
        self._check_host()
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path
        if not _is_loopback(self.client_address[0]) and not self.app.lan_enabled:
            raise HttpError(403, "LAN access is off.")
        if method == "GET" and path in TRUST_PATHS and self.app.tls_context is not None:
            return self._trust(path)
        if (self.app.tls_context is not None and not isinstance(self.connection, ssl.SSLSocket)
                and not _is_loopback(self.client_address[0])):
            if method != "GET":
                raise HttpError(403, "Use the https:// address for Phone mode.")
            self.send_response(301)  # plain HTTP from the LAN: only /trust, everything else moves to HTTPS
            self.send_header("Location", "https://%s%s" % (self.headers.get("Host"), self.path))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "GET" and path in ("/", "/index.html"):
            tok = self.app.local_token if _is_loopback(self.client_address[0]) else ""
            return self._html(render_page(tok))
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if not path.startswith("/api/"):
            raise HttpError(404, "Not found.")
        body = {}
        if method == "POST":
            self._check_origin()
            body = self._body()
        if path == "/api/pair" and method == "POST":
            return self._json(200, self.app.pair(body.get("pair", ""), body.get("pin", "")))
        self._auth()
        fn = ROUTES.get((method, path))
        if fn is None:
            if path == "/api/_selftest/raise" and self.app.test_hooks:
                raise RuntimeError("deliberate test failure")
            raise HttpError(404, "Unknown API.")
        local = _is_loopback(self.client_address[0])
        if path in LOCAL_ONLY and not local:
            raise HttpError(403, "Do this on the PC itself, not from the phone.")
        res = fn(self.app, q, body)
        if isinstance(res, FileResponse):
            return self._file(res)
        if not local and isinstance(res, dict):
            res.pop("path", None)  # a phone never needs the PC's full folder path
        return self._json(200, res)

    def _file(self, fr):
        size = os.path.getsize(fr.path)
        self.send_response(200)
        self.send_header("Content-Type", fr.ctype)
        self.send_header("Content-Length", str(size))
        quoted = urllib.parse.quote(fr.filename)
        ascii_name = re.sub(r'[^A-Za-z0-9 ._()-]', "_", fr.filename)
        self.send_header("Content-Disposition", "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quoted))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            with open(fr.path, "rb") as f:
                while True:
                    chunk = f.read(1 << 16)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        finally:
            if fr.remove_dir:
                __import__("shutil").rmtree(fr.remove_dir, ignore_errors=True)


    def _trust(self, path):
        tls = self.app.tls
        if path == "/trust":
            info = self.app.tls_info or {}
            url = "https://%s:%d/" % (lan_ip(), self.app.port)
            return self._html(_ltls.onboarding_html(APP_NAME, info.get("ca_sha256", ""), info.get("ca_cn", ""), url,
                                                    ca_not_after=info.get("ca_not_after", "")))
        if path == "/trust/ca.crt":
            data, ctype, fn = tls.ca_der(), "application/x-x509-ca-cert", "StarkLocalCA.crt"
        else:
            data, ctype, fn = tls.mobileconfig(), "application/x-apple-aspen-config", "StarkLocalCA.mobileconfig"
        if not data:
            raise HttpError(404, "No certificate yet.")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % fn)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


TRUST_PATHS = ("/trust", "/trust/ca.crt", "/trust/ca.mobileconfig")


def _book_dir(app, name):
    if not name:
        raise HttpError(400, "book required")
    return safe_join(app.root, name)


def _existing_book(app, name):
    d = _book_dir(app, name)
    if not os.path.isdir(d):
        raise HttpError(404, "No such book.")
    return d


def display_path(path):
    """How a folder is shown on screen: relative to the user's home ("Documents\\My_Books"), never with the
    Windows user name in it (screenshots and support emails get shared)."""
    home = os.path.realpath(os.path.expanduser("~"))
    p = os.path.realpath(path)
    if _within(home, p) and os.path.normcase(p) != os.path.normcase(home):
        return os.path.relpath(p, home)
    return p


class FileResponse:
    def __init__(self, path, filename, ctype, remove_dir=None):
        self.path, self.filename, self.ctype = path, filename, ctype
        self.remove_dir = remove_dir  # a temp folder made for this one download, removed once it is sent


def r_status(app, q, b):
    lic = license_status()
    books = [n for n in os.listdir(app.root) if not n.startswith(".") and os.path.isdir(os.path.join(app.root, n))]
    return {"ok": True, "app": APP_NAME, "version": VERSION, "plan": lic["plan"], "license": lic["message"],
            "lan": app.lan_enabled, "port": app.port, "restarts": app.restarts,
            "exports": list(FREE_EXPORTS + PRO_EXPORTS) if (_qexport or lic["plan"] == "PRO") else list(FREE_EXPORTS),
            "pro_exports": list(PRO_EXPORTS), "export_module": _qexport is not None, "lore": _lore is not None,
            "save_pause_ms": SAVE_PAUSE_MS, "recovery": app.recovery_summary, "first_run": not books,
            "books_folder": display_path(app.root), "stats": app.stats.summary(), "links": app_links(),
            "channel": channel(), "beta": channel() == "beta",
            "updates_consent": False if school_profile() == "classroom" else read_settings().get("updates"),
            "profile": school_profile(), "schools": _schools is not None, "contests": _contests is not None,
            "ui_scale": _schools.profile_defaults(school_profile())["ui_scale"] if _schools is not None else 1.0,
            "moved": moved_notice()}


def _book_summary(app, n):
    p = os.path.join(app.root, n)
    meta = read_meta(p)
    chs = list_chapters(p)
    return {"name": n, "title": meta["title"], "author": meta["author"], "chapters": len(chs)}


def r_books(app, q, b):
    out = []
    for n in sorted(os.listdir(app.root), key=str.lower):
        p = os.path.join(app.root, n)
        if not n.startswith(".") and os.path.isdir(p):
            out.append(_book_summary(app, n))
    return {"ok": True, "books": out}


def r_book_new(app, q, b):
    title = clean_text(str(b.get("title") or b.get("name") or "")).strip()
    if not title:
        raise HttpError(400, "Give the book a title (you can change it later).")
    folder = clean_title(b.get("name") or title, "My Book")
    d = _book_dir(app, folder)
    if os.path.isdir(d) and list_chapters(d) and b.get("title"):
        k = 2
        while os.path.isdir(_book_dir(app, "%s (%d)" % (folder, k))):
            k += 1
        folder = "%s (%d)" % (folder, k)
        d = _book_dir(app, folder)
    proj = None
    if b.get("project") and _studio is not None:
        try:
            proj = _studio.project(str(b.get("project")))
        except ValueError as e:
            raise HttpError(400, str(e))
    os.makedirs(d, exist_ok=True)
    meta = read_meta(d)
    meta["title"] = title[:200]
    if b.get("author") is not None:
        meta["author"] = clean_text(str(b.get("author"))).strip()[:200]
    write_meta(d, meta)
    if not list_chapters(d):
        if proj is not None:
            new_project_book(d, proj, client_today(b.get("today")))
        else:
            Ledger(d).save("01 Chapter One.md", str(b.get("text") or ""))
    return {"ok": True, "name": os.path.basename(d), "title": meta["title"]}


def _chapter_name(name):
    name = (name or "").strip()
    if name and not name.lower().endswith(CHAPTER_EXTS):
        name += ".md"
    if name == GRAVEYARD or "/" in name or "\\" in name:
        raise SandboxError("not a chapter name")
    return name


def _chapter_list(d):
    meta = read_meta(d)
    beat_of = _beat_titles(d, meta)
    out = []
    for c in list_chapters(d):
        t = read_text(os.path.join(d, c))
        note = meta["chapters"].get(c) or {}
        out.append({"name": c, "title": chapter_title(c), "number": chapter_number(c), "words": count_words(t),
                    "synopsis": note.get("synopsis", ""), "status": note.get("status", "draft"),
                    "opening": " ".join(t.split()[:40]), "beats": beat_of.get(c, []),
                    "pov": note.get("pov", ""), "setting": note.get("setting", ""), "when": note.get("when", ""),
                    "plotlines": note.get("plotlines") or []})
    return out


def r_chapters(app, q, b):
    d = _existing_book(app, q.get("book"))
    return {"ok": True, "chapters": _chapter_list(d), "snapshots": len(list_snapshots(d)), "meta": read_meta(d)}


def r_book(app, q, b):
    d = _existing_book(app, q.get("book"))
    chs = _chapter_list(d)
    words = sum(c["words"] for c in chs)
    plan = book_plan(d, client_today(q.get("today")), words)
    return {"ok": True, "meta": read_meta(d), "chapters": chs, "words": words,
            "folder": display_path(d), "snapshots": len(list_snapshots(d)), "target": plan}


def r_book_meta(app, q, b):
    d = _existing_book(app, b.get("book"))
    meta = update_meta(d, b.get("meta") or {})
    return {"ok": True, "meta": meta, "target": book_plan(d, client_today(b.get("today")))}


def r_target(app, q, b):
    d = _existing_book(app, q.get("book"))
    return {"ok": True, "target": book_plan(d, client_today(q.get("today")))}


def r_book_zip(app, q, b):
    """Download the whole book as one checked zip (the browser saves it; its Save As can pick a USB stick)."""
    d = _existing_book(app, q.get("book"))
    tmpdir = __import__("tempfile").mkdtemp(prefix="quill_zip_")
    try:
        res = book_zip(d, tmpdir)
    except BaseException:
        __import__("shutil").rmtree(tmpdir, ignore_errors=True)
        raise
    return FileResponse(res["path"], res["file"], "application/zip", remove_dir=tmpdir)


def r_drives(app, q, b):
    return {"ok": True, "drives": [{"id": x["id"], "label": x["label"], "free": x.get("free")}
                                   for x in removable_drives()]}


def r_book_zip_drive(app, q, b):
    d = _existing_book(app, b.get("book"))
    want = str(b.get("drive") or "")
    drive = next((x for x in removable_drives() if x["id"] == want), None)
    if drive is None:  # only a drive Quill itself listed as removable: never a path the request names
        raise HttpError(404, "That USB stick or card is not there any more. Plug it in and try again.")
    res = book_zip(d, os.path.join(drive["root"], BOOK_ZIP_FOLDER))
    return {"ok": True, "file": res["file"], "files": res["files"], "bytes": res["bytes"],
            "where": "%s (%s), folder %s" % (drive["label"], drive["id"], BOOK_ZIP_FOLDER)}


# ======================================================================================================
# Schools mode (Stark Education Suite): assignments, rubric self-check, submission package, classroom profile
# ======================================================================================================
ASSIGN_FOLDER = "Quill Assignments"
SUBMIT_FOLDER = "Quill Submissions"


def school_profile():
    """'classroom' or 'standard'. Classroom: larger page, spell check on, update checks off and not switchable on."""
    return _schools.profile_name(read_settings(), here=_HERE) if _schools is not None else "standard"


def _need_schools():
    if _schools is None:
        raise HttpError(501, "Schools mode is not installed (quill_schools.py is missing).")


def _schools_dest(app, drive_id, folder):
    """A folder beside the books folder, or a removable drive Quill itself listed (never a path the request names)."""
    if drive_id:
        drive = next((x for x in removable_drives() if x["id"] == str(drive_id)), None)
        if drive is None:
            raise HttpError(404, "That USB stick or card is not there any more. Plug it in and try again.")
        return os.path.join(drive["root"], folder), "%s (%s), folder %s" % (drive["label"], drive["id"], folder)
    dest = os.path.join(os.path.dirname(app.root), folder)
    return dest, display_path(dest)


def _book_assignment(d):
    a = read_meta(d).get("assignment")
    if not isinstance(a, dict):
        return None
    try:
        return _schools.read_assignment(json.dumps(a).encode("utf-8"))
    except ValueError:
        return None


def r_school_profile(app, q, b):
    name = school_profile()
    return {"ok": True, "profile": name, "settings": _schools.profile_defaults(name) if _schools else {}}


def r_school_profile_set(app, q, b):
    _need_schools()
    name = str(b.get("profile") or "")
    if name not in _schools.PROFILES:
        raise HttpError(400, "The profile is 'standard' or 'classroom'.")
    if name == "classroom":
        write_settings({"profile": "classroom", "updates": False})
    else:
        write_settings({"profile": "standard"})
    return r_school_profile(app, q, b)


def r_school_assignment_new(app, q, b):
    """A teacher makes the assignment file: it is saved in 'Quill Assignments' (or on a USB stick) to hand out."""
    _need_schools()
    try:
        a = _schools.make_assignment(b.get("title"), b.get("prompt"), b.get("word_target"), b.get("due"),
                                     b.get("rubric") or [], b.get("teacher"))
        dest, where = _schools_dest(app, b.get("drive"), ASSIGN_FOLDER)
        path = _schools.write_assignment(dest, a)
    except ValueError as e:
        raise HttpError(400, str(e))
    return {"ok": True, "file": os.path.basename(path), "where": where, "path": path, "assignment": a}


def r_school_assignment_open(app, q, b):
    """A student opens an assignment file as a new book with the prompt pinned (kept in book.json)."""
    _need_schools()
    try:
        a = _schools.read_assignment(str(b.get("text") or "").encode("utf-8"))
    except ValueError as e:
        raise HttpError(400, str(e))
    folder = clean_title(a["title"], "Assignment")
    d, k = _book_dir(app, folder), 2
    while os.path.isdir(d):
        d = _book_dir(app, "%s (%d)" % (folder, k))
        k += 1
    os.makedirs(d)
    meta = read_meta(d)
    meta["title"] = a["title"][:200]
    meta["author"] = clean_text(str(b.get("student") or "")).strip()[:100]
    meta["target_words"] = a["word_target"]
    meta["deadline"] = a["due"]
    meta["assignment"] = a
    meta["rubric_checks"] = []
    write_meta(d, meta)
    Ledger(d).save("01 Chapter One.md", "")
    return {"ok": True, "name": os.path.basename(d), "title": meta["title"]}


def r_school_rubric(app, q, b):
    _need_schools()
    d = _existing_book(app, q.get("book"))
    a = _book_assignment(d)
    return {"ok": True, "assignment": a, "rubric": _schools.rubric_status(a, read_meta(d).get("rubric_checks"))}


def r_school_rubric_set(app, q, b):
    _need_schools()
    d = _existing_book(app, b.get("book"))
    a = _book_assignment(d)
    if a is None:
        raise HttpError(400, "This book did not come from an assignment.")
    ticks = b.get("checks")
    if not isinstance(ticks, list):
        raise HttpError(400, "checks must be a list of criterion ids.")
    st = _schools.rubric_status(a, [str(t) for t in ticks])
    with book_lock(d):
        meta = read_meta(d)
        meta["rubric_checks"] = [c["id"] for c in st["criteria"] if c["done"]]
        write_meta(d, meta)
    return {"ok": True, "assignment": a, "rubric": st}


def revision_summary(d):
    snaps = list(reversed(snapshot_info(d)))   # oldest first
    return {"snapshots": len(snaps), "first": snaps[0]["when"] if snaps else "",
            "last": snaps[-1]["when"] if snaps else "",
            "words_by_snapshot": [{"when": s["when"], "words": s["words"]} for s in snaps[-30:]],
            "deleted_chapters": len(list_deleted(d))}


def submit_book(app, d, student, reflection, drive=None):
    _need_schools()
    student = clean_text(str(student or "")).strip()[:100]
    snapshot_book(d)                      # the history ends with the book exactly as it is handed in
    meta = read_meta(d)
    a = _book_assignment(d)
    info = {"student": student, "book_title": meta["title"], "words": book_words(d),
            "word_target": int(meta.get("target_words") or 0),
            "assignment": a and {"title": a["title"], "id": a["id"], "due": a["due"]},
            "rubric": _schools.rubric_status(a, meta.get("rubric_checks")) if a else None,
            "revisions": revision_summary(d), "self_checked_only": True}
    members = [(p, rel) for p, rel in _book_zip_members(d) if not os.path.basename(rel).startswith(".journal")]
    dest, where = _schools_dest(app, drive, SUBMIT_FOLDER)
    try:
        with book_lock(d):
            res = _schools.build_submission(dest, _schools.submission_name(meta["title"], student), members, info,
                                            reflection)
    except ValueError as e:
        raise HttpError(400, str(e))
    res["where"] = where
    res["words"] = info["words"]
    return res


def r_school_submit(app, q, b):
    d = _existing_book(app, b.get("book"))
    res = submit_book(app, d, b.get("student"), b.get("reflection"), b.get("drive"))
    return {"ok": True, "file": res["file"], "files": res["files"], "bytes": res["bytes"], "words": res["words"],
            "where": res["where"], "path": res["path"]}


# ---------------------------------------------------------------------------------------------- contests
CONTEST_FOLDER, ENTRY_FOLDER, JUDGE_FOLDER, RESULT_FOLDER = ("Quill Contests", "Quill Entries", "Quill Judging",
                                                             "Quill Results")


def _need_contests():
    if _contests is None or _schools is None:
        raise HttpError(501, "Contest mode is not installed (quill_contests.py is missing).")


def _contest_from(b):
    _need_contests()
    try:
        return _contests.read_contest(str(b.get("contest") or "").encode("utf-8"))
    except ValueError as e:
        raise HttpError(400, str(e))


def _files_in(dest, *exts):
    try:
        return [os.path.join(dest, n) for n in sorted(os.listdir(dest)) if n.lower().endswith(exts)
                and os.path.isfile(os.path.join(dest, n))]
    except OSError:
        return []


def r_contest_new(app, q, b):
    """An organiser makes the event pack: saved in 'Quill Contests' (or on a USB stick) to hand out."""
    _need_contests()
    try:
        c = _contests.make_contest(b.get("code"), b.get("title"), b.get("prompt"), b.get("word_limit"), b.get("deadline"),
                                   b.get("divisions") or None, b.get("rubric") or None, b.get("organiser"))
        dest, where = _schools_dest(app, b.get("drive"), CONTEST_FOLDER)
        path = _contests.write_contest(dest, c)
    except ValueError as e:
        raise HttpError(400, str(e))
    return {"ok": True, "file": os.path.basename(path), "where": where, "path": path, "contest": c}


def r_contest_open(app, q, b):
    """An entrant opens the event pack as a new book with the prompt pinned (kept in book.json)."""
    c = _contest_from(b)
    folder = clean_title(c["title"], "Contest entry")
    d, k = _book_dir(app, folder), 2
    while os.path.isdir(d):
        d = _book_dir(app, "%s (%d)" % (folder, k))
        k += 1
    os.makedirs(d)
    meta = read_meta(d)
    meta["title"] = c["title"][:200]
    meta["author"] = clean_text(str(b.get("entrant") or "")).strip()[:100]
    meta["target_words"] = c["word_limit"]
    meta["deadline"] = c["deadline"]
    meta["contest"] = c
    write_meta(d, meta)
    Ledger(d).save("01 Chapter One.md", "")
    return {"ok": True, "name": os.path.basename(d), "title": meta["title"], "contest": c}


def r_contest_enter(app, q, b):
    """Sign and pack the open book as a contest entry zip (hand-in packaging plus event code, name, division)."""
    _need_contests()
    d = _existing_book(app, b.get("book"))
    meta = read_meta(d)
    try:
        c = _contests.read_contest(json.dumps(meta.get("contest")).encode("utf-8"))
    except ValueError:
        raise HttpError(400, "This book did not come from a contest file.")
    members = b.get("members")
    if isinstance(members, str):
        members = re.split(r"[,\n]+", members)
    if not isinstance(members, list):
        members = []
    snapshot_book(d)
    mf = [(p, rel) for p, rel in _book_zip_members(d) if not os.path.basename(rel).startswith(".journal")]
    dest, where = _schools_dest(app, b.get("drive"), ENTRY_FOLDER)
    try:
        with book_lock(d):
            res = _contests.build_entry(dest, c, mf, b.get("division"), clean_text(str(b.get("entrant") or "")).strip(),
                                        members, book_words(d), meta["title"], b.get("statement") or "")
    except ValueError as e:
        raise HttpError(400, str(e))
    return {"ok": True, "file": res["file"], "entry_id": res["entry_id"], "where": where, "path": res["path"],
            "words": book_words(d)}


def _scan(app, c, drive, allow_late=False):
    dest, where = _schools_dest(app, drive, ENTRY_FOLDER)
    good, rej = _contests.scan_entries(c, _files_in(dest, ".zip"), count_words, allow_late)
    return good, rej, where


def r_contest_pack(app, q, b):
    """The organiser makes the anonymous judging pack from the entries folder (tampered, late and over-limit
    entries are listed, not packed)."""
    c = _contest_from(b)
    good, rej, where = _scan(app, c, b.get("drive"), bool(b.get("allow_late")))
    if not good:
        raise HttpError(400, "No valid entries for %s in %s." % (c["code"], where))
    dest, jwhere = _schools_dest(app, b.get("drive"), JUDGE_FOLDER)
    res = _contests.build_pack(dest, c, good)
    return {"ok": True, "file": res["file"], "entries": res["entries"], "where": jwhere, "rejected": rej, "path": res["path"]}


def r_contest_pack_read(app, q, b):
    """A judge opens a pack: the entries without names, and the rubric."""
    _need_contests()
    try:
        p = _contests.read_pack(str(b.get("pack") or "").encode("utf-8"))
    except ValueError as e:
        raise HttpError(400, str(e))
    return {"ok": True, "pack": p}


def r_contest_sheet(app, q, b):
    """A judge scores every entry against the rubric and saves a signed score sheet."""
    _need_contests()
    try:
        p = _contests.read_pack(str(b.get("pack") or "").encode("utf-8"))
        dest, where = _schools_dest(app, b.get("drive"), JUDGE_FOLDER)
        res = _contests.make_sheet(dest, p, b.get("judge"), b.get("scores") or {}, b.get("comments") or {})
    except ValueError as e:
        raise HttpError(400, str(e))
    return {"ok": True, "file": res["file"], "entries": res["entries"], "where": where, "path": res["path"]}


def r_contest_results(app, q, b):
    """The organiser imports entries and score sheets, then gets ranked results per division and certificates."""
    c = _contest_from(b)
    good, rej, where = _scan(app, c, b.get("drive"), bool(b.get("allow_late")))
    jdest, _w = _schools_dest(app, b.get("drive"), JUDGE_FOLDER)
    sheets, bad = [], []
    for p in _files_in(jdest, ".quillscores"):
        try:
            sheets.append(_contests.read_sheet(p, c))
        except ValueError as e:
            bad.append({"file": os.path.basename(p), "problems": [str(e)]})
    try:
        res = _contests.rank(c, good, sheets)
        rdest, rwhere = _schools_dest(app, b.get("drive"), RESULT_FOLDER)
        paths = _contests.write_results(rdest, c, res, int(b.get("places") or 3), bool(b.get("participation")))
    except ValueError as e:
        raise HttpError(400, str(e))
    return {"ok": True, "results": res, "text": _contests.results_text(res), "where": rwhere,
            "files": {k: os.path.basename(v) for k, v in paths.items()}, "rejected": rej + bad}


def r_paste(app, q, b):
    html_in = b.get("html")
    if not isinstance(html_in, str):
        raise HttpError(400, "html required")
    if _paste is None:
        raise HttpError(501, "quill_paste.py is not installed; the paste stays plain text.")
    return {"ok": True, "text": _paste.html_to_text(html_in)}


def r_chapter(app, q, b):
    d = _existing_book(app, q.get("book"))
    name = resolve_renamed(d, _chapter_name(q.get("name")))
    p = safe_join(d, name)
    if not os.path.isfile(p):
        raise HttpError(404, "No such chapter.")
    t = read_text(p)
    return {"ok": True, "name": name, "title": chapter_title(name), "text": t, "sha": sha256_bytes(t.encode("utf-8"))}


def r_chapter_new(app, q, b):
    d = _existing_book(app, b.get("book"))
    if b.get("title") is None and b.get("name"):  # v1.0 clients: an exact file name
        name = _chapter_name(b.get("name"))
        if os.path.exists(safe_join(d, name)):
            raise HttpError(409, "A chapter with that name exists.")
        return {"ok": True, "name": name, "title": chapter_title(name), "sha": Ledger(d).save(name, "")}
    title = str(b.get("title") or "").strip()
    if not title:
        raise HttpError(400, "Give the chapter a title.")
    after = b.get("after")
    name = new_chapter(d, title, after=_chapter_name(after) if after else None)
    return {"ok": True, "name": name, "title": chapter_title(name), "sha": sha256_bytes(b"")}


def r_chapter_rename(app, q, b):
    d = _existing_book(app, b.get("book"))
    name = _chapter_name(b.get("name"))
    new = rename_chapter(d, name, b.get("title") or "")
    return {"ok": True, "name": new, "title": chapter_title(new), "chapters": _chapter_list(d)}


def r_chapter_order(app, q, b):
    d = _existing_book(app, b.get("book"))
    order = b.get("order")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise HttpError(400, "order must be a list of chapter names")
    finals = reorder_chapters(d, [_chapter_name(x) for x in order])
    return {"ok": True, "renamed": dict(zip(order, finals)), "chapters": _chapter_list(d)}


def r_chapter_delete(app, q, b):
    d = _existing_book(app, b.get("book"))
    dest = delete_chapter(d, _chapter_name(b.get("name")))
    return {"ok": True, "kept_as": dest, "chapters": _chapter_list(d)}


def r_chapter_undelete(app, q, b):
    d = _existing_book(app, b.get("book"))
    name = undelete_chapter(d, str(b.get("file") or ""))
    return {"ok": True, "name": name, "chapters": _chapter_list(d)}


def r_chapter_note(app, q, b):
    d = _existing_book(app, b.get("book"))
    note = chapter_note(d, _chapter_name(b.get("name")), b.get("synopsis"), b.get("status"))
    return {"ok": True, "note": note}


def r_save(app, q, b):
    d = _book_dir(app, b.get("book"))
    name = _chapter_name(b.get("name"))
    if os.path.isdir(d):
        name = resolve_renamed(d, name)
    text = b.get("text")
    if not isinstance(text, str):
        raise HttpError(400, "text required")
    if b.get("today") and os.path.isdir(d):  # first save of the writer's day: note the book's words before it
        try:
            note_day_start(d, client_today(b.get("today")))
        except OSError:
            pass  # the target figure is a convenience; never let it stop a save
    try:
        res = save_chapter(d, name, text, b.get("base_sha") or None, b.get("base"))
    except ChapterLocked as e:  # the journal has the text; the page sends it again by itself
        raise ChapterLocked(name, journaled=e.journaled, cause=e.cause, retry=True) from e.cause
    except OSError as e:
        if is_lock_error(e):  # held before the journal record was written: the page still has the text
            raise ChapterLocked(name, journaled=False, cause=e, retry=True) from e
        raise
    res["name"] = name
    if res.get("ok") and res.get("delta"):
        app.stats.add(res["delta"])
    if res.get("ok"):
        res["stats"] = app.stats.summary()
    app.lore_dirty.add(os.path.normcase(d))
    return res


def r_search(app, q, b):
    d = _existing_book(app, q.get("book"))
    return search_book(d, q.get("q", ""), q.get("case") == "1", q.get("word") == "1")


def r_replace(app, q, b):
    d = _existing_book(app, b.get("book"))
    names = b.get("names")
    if names is not None and not (isinstance(names, list) and all(isinstance(x, str) for x in names)):
        raise HttpError(400, "names must be a list")
    return replace_in_book(d, b.get("q", ""), b.get("with", ""), bool(b.get("case")), bool(b.get("word")), names)


def r_graveyard(app, q, b):
    d = _existing_book(app, q.get("book"))
    return {"ok": True, "entries": read_graveyard(d)}


def r_backups(app, q, b):
    d = _existing_book(app, q.get("book"))
    return {"ok": True, "snapshots": snapshot_info(d), "deleted": list_deleted(d)}


def r_backup(app, q, b):
    d = _existing_book(app, q.get("book"))
    return {"ok": True, "chapters": snapshot_chapters(d, q.get("snap", ""))}


def r_backup_chapter(app, q, b):
    d = _existing_book(app, q.get("book"))
    return {"ok": True, "text": snapshot_text(d, q.get("snap", ""), q.get("name", ""))}


def r_backup_restore(app, q, b):
    d = _existing_book(app, b.get("book"))
    res = restore_chapter(d, str(b.get("snap") or ""), str(b.get("name") or ""))
    res["chapters"] = _chapter_list(d)
    return res


def r_backup_now(app, q, b):
    d = _existing_book(app, b.get("book"))
    p = snapshot_book(d)
    return {"ok": True, "name": os.path.basename(p)}


def r_backup_file(app, q, b):
    d = _existing_book(app, q.get("book"))
    snap = q.get("snap", "")
    if snap not in list_snapshots(d):
        raise HttpError(404, "That backup is not there any more.")
    meta = read_meta(d)
    return FileResponse(os.path.join(d, BACKUP_DIR, snap), "%s backup %s" % (clean_title(meta["title"], "Book"),
                                                                            snap[9:24] + ".zip"), "application/zip")


def r_stats(app, q, b):
    return dict(app.stats.summary(), ok=True)


def r_stats_goal(app, q, b):
    try:
        app.stats.set_goal(int(b.get("goal") or 0))
    except (TypeError, ValueError):
        raise HttpError(400, "The goal should be a number of words.")
    return dict(app.stats.summary(), ok=True)


def book_lore(app, d):
    """Lore for the saved chapters of a book, cached until a chapter changes. -> (chapters, lore)."""
    key = os.path.normcase(d)
    chapters = [(chapter_title(c), read_text(os.path.join(d, c))) for c in list_chapters(d)]
    sig = sha256_bytes("".join(c + sha256_bytes(t.encode("utf-8")) for c, t in chapters).encode("utf-8"))
    cached = app.lore_cache.get(key)
    if cached and cached[0] == sig:
        return chapters, cached[1]
    res = _lore.analyze(chapters)
    app.lore_cache[key] = (sig, res)
    if _spell is not None:   # names Lore finds, and invented words in the book's title, go into its dictionary
        title_words = []
        lang = _spell_lang(d)
        if _spell.available(lang):
            title_words = [w for w in _spell.misspelled_words(read_meta(d)["title"], (), lang) if w[:1].isupper()]
        _spell.add_words(d, _spell.lore_words(res) + title_words)
    return chapters, res


def r_lore(app, q, b):
    d = _existing_book(app, q.get("book"))
    if _lore is None:
        return {"ok": True, "available": False}
    chapters, res = book_lore(app, d)
    res = dict(res)
    if _lib is not None:
        quoted = _lib.quoted_in(chapters, d)
        if quoted:  # clipped research quotes found in the manuscript: show the citation and licence duty
            res["anomalies"] = list(res.get("anomalies", [])) + quoted
    res["ok"] = True
    res["available"] = True
    if q.get("ai") == "1":
        if not is_pro():
            raise HttpError(402, "Continuity notes from a local AI model are part of Stark Quill Pro.")
        fn = getattr(_lore, "ai_continuity", None)
        current = q.get("chapter")
        text = None
        if current:
            try:
                text = read_text(safe_join(d, resolve_renamed(d, _chapter_name(current))))
            except (OSError, SandboxError):
                text = None
        if callable(fn):
            res["ai"] = fn(res, chapter_text=text, chapter_title=chapter_title(current) if current else None,
                           model=q.get("model") or None)
        else:
            summary = _lore.ai_summary(res)
            res["ai"] = {"ok": bool(summary), "notes": summary or "", "model": ""}
    return res


def r_lore_ai(app, q, b):
    if _lore is None or not hasattr(_lore, "ai_status"):
        return {"ok": True, "running": False, "models": [], "message": "The lore module is not installed."}
    st = _lore.ai_status()
    st["ok"] = True
    st["pro"] = is_pro()
    return st


def r_export(app, q, b):
    d = _existing_book(app, b.get("book"))
    res = export_book(d, None, b.get("fmt"), is_pro(), layout=b.get("layout") or "book", page=b.get("page"))
    res["ok"] = True
    res["folder"] = display_path(os.path.dirname(res["path"]))
    return res


def r_export_file(app, q, b):
    d = _existing_book(app, q.get("book"))
    fn = q.get("file", "")
    validate_rel(fn)
    if "/" in fn or "\\" in fn:
        raise SandboxError("not an export file")
    p = safe_join(os.path.join(d, "exports"), fn)
    if not os.path.isfile(p):
        raise HttpError(404, "Export it first.")
    ext = os.path.splitext(fn)[1].lower()
    ctype = {".epub": "application/epub+zip", ".pdf": "application/pdf", ".html": "text/html; charset=utf-8",
             ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
             ".fountain": "text/plain; charset=utf-8", ".fdx": "application/xml"}.get(ext,
                                                                                         "application/octet-stream")
    return FileResponse(p, fn, ctype)


def r_reveal(app, q, b):
    d = _existing_book(app, b.get("book"))
    target = os.path.join(d, "exports") if b.get("what") == "exports" else d
    os.makedirs(target, exist_ok=True)
    opened = False
    if not app.test_hooks and os.environ.get("QUILL_NO_REVEAL") != "1":
        try:
            if os.name == "nt":
                os.startfile(target)  # noqa: S606 - opens Explorer on the writer's own folder
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target])
            opened = True
        except Exception:
            opened = False
    return {"ok": True, "opened": opened, "folder": display_path(target)}


def r_updates(app, q, b):
    st = read_settings()
    url = (_updater.configured_url(_HERE) if _updater is not None and hasattr(_updater, "configured_url")
           else "") or UPDATE_URL
    return {"ok": True, "consent": st.get("updates"), "channel": channel(), "version": VERSION,
            "beta": channel() == "beta", "last_check": st.get("update_last_check"),
            "status": st.get("update_status"), "detail": st.get("update_detail"),
            "host": urllib.parse.urlsplit(url).netloc}


def r_updates_set(app, q, b):
    if school_profile() == "classroom":   # the classroom profile keeps update checks off; the school updates Quill itself
        write_settings({"updates": False})
        return dict(r_updates(app, q, b), consent=False, locked=True)
    if "consent" in b:
        write_settings({"updates": bool(b.get("consent"))})
    if b.get("check_now") and not app.test_hooks:   # pressing Check now is itself the writer's go-ahead
        threading.Thread(target=app._update_check, kwargs={"force": True}, daemon=True).start()
    return r_updates(app, q, b)


def library_dir(app):
    """Downloaded public-domain books live beside the books folder, in their own 'Quill Library' folder."""
    return os.path.join(os.path.dirname(app.root), "Quill Library")


def _library_on(app):
    if _lib is None:
        raise HttpError(501, "The library module is not installed.")
    if read_settings().get("library") is not True:
        raise HttpError(409, "The Library is off. Turn it on first: it is the only part of Quill that goes online.")


def _lib_call(fn, *a):
    try:
        return fn(*a)
    except _lib.LibraryError as e:
        raise HttpError(502, str(e))


def r_library(app, q, b):
    on = read_settings().get("library") is True
    d = library_dir(app)
    files = []
    if os.path.isdir(d):
        files = sorted(n for n in os.listdir(d) if n.lower().endswith((".txt", ".epub")))
    return {"ok": True, "available": _lib is not None, "enabled": on, "folder": display_path(d), "files": files}


def r_library_enable(app, q, b):
    write_settings({"library": bool(b.get("on"))})
    return r_library(app, q, b)


def r_library_search(app, q, b):
    _library_on(app)
    src, text = q.get("src", "gutenberg"), q.get("q", "")
    if not text.strip():
        raise HttpError(400, "Type a title or an author.")
    fn = {"gutenberg": _lib.gutenberg_search, "standard": _lib.standard_search,
          "openlibrary": _lib.openlibrary}.get(src)
    if fn is None:
        raise HttpError(400, "Unknown source.")
    return {"ok": True, "results": _lib_call(fn, text)}


def r_library_word(app, q, b):
    _library_on(app)
    return {"ok": True, "entry": _lib_call(_lib.wiktionary, q.get("q", ""))}


def r_library_wiki(app, q, b):
    _library_on(app)
    return {"ok": True, "entry": _lib_call(_lib.wikipedia, q.get("q", ""))}


def r_library_download(app, q, b):
    _library_on(app)
    item = b.get("item") if isinstance(b.get("item"), dict) else {}
    path = _lib_call(_lib.download, library_dir(app), item)
    return {"ok": True, "file": os.path.basename(path), "folder": display_path(library_dir(app))}


def r_library_read(app, q, b):
    if _lib is None:
        raise HttpError(501, "The library module is not installed.")
    try:
        return dict(_lib.read_local(library_dir(app), q.get("name", ""), q.get("start", 0)), ok=True)
    except (_lib.LibraryError, OSError) as e:
        raise HttpError(404, str(e))


def r_research(app, q, b):
    d = _existing_book(app, q.get("book"))
    if _lib is None:
        return {"ok": True, "clips": []}
    return {"ok": True, "clips": _lib.clips(d), "file": "Research.md"}


def r_research_clip(app, q, b):
    d = _existing_book(app, b.get("book"))
    if _lib is None:
        raise HttpError(501, "The library module is not installed.")
    c = _lib_call(_lib.add_clip, d, b.get("text", ""), str(b.get("source", "")), str(b.get("title", "")),
                  str(b.get("url", "")), str(b.get("licence", "")), str(b.get("licence_url", "")),
                  bool(b.get("share_alike")))
    app.lore_cache.pop(os.path.normcase(d), None)
    return {"ok": True, "clip": c}


def r_research_remove(app, q, b):
    d = _existing_book(app, b.get("book"))
    _lib.remove_clip(d, str(b.get("id", "")))
    return {"ok": True, "clips": _lib.clips(d)}


def _spell_lang(d):
    return _spell.pick_language(str(read_meta(d).get("language") or "en"))


def r_spell(app, q, b):
    """POST {book, text}: the words in text that are spelled wrong, leaving out the book dictionary. Local only."""
    d = _existing_book(app, b.get("book"))
    lang = _spell_lang(d) if _spell is not None else ""
    if _spell is None or not _spell.available(lang):
        return {"ok": True, "available": False, "words": []}
    if _lore is not None:
        book_lore(app, d)                            # keeps the dictionary up to date with Lore's names
    text = str(b.get("text") or "")[:2000000]
    words = _spell.misspelled_words(text, _spell.read_dictionary(d), lang)
    return {"ok": True, "available": True, "words": words, "language": lang, "dictionary": len(_spell.read_dictionary(d)),
            "file": _spell.DICT_NAME}


def r_spell_add(app, q, b):
    d = _existing_book(app, b.get("book"))
    if _spell is None:
        raise HttpError(501, "Spell check is not installed.")
    word = clean_text(str(b.get("word") or "")).strip()
    if not word or len(word) > 60:
        raise HttpError(400, "Pick one word to add.")
    return {"ok": True, "added": _spell.add_words(d, [word]), "dictionary": len(_spell.read_dictionary(d))}


def r_spell_remove(app, q, b):
    d = _existing_book(app, b.get("book"))
    if _spell is None:
        raise HttpError(501, "Spell check is not installed.")
    return {"ok": True, "removed": _spell.remove_word(d, str(b.get("word") or "").strip())}


def r_recover(app, q, b):
    d = _book_dir(app, q.get("book"))
    return {"ok": True, "report": Ledger(d).recover()}


def r_lan(app, q, b):
    if b.get("enable"):
        if not is_pro() and not app.test_hooks:
            raise HttpError(402, "Phone mode is part of Stark Quill Pro.")
        return app.enable_lan()
    app.disable_lan()
    return {"ok": True, "lan": False}


def _license_path():
    if _lguard is None:
        return None
    return os.environ.get("QUILL_LICENSE_FILE") or os.path.join(_lguard.default_state_dir(), "license.json")


def r_license(app, q, b):
    lic = license_status()
    out = {"ok": True, "plan": lic["plan"], "message": lic["message"], "available": _lguard is not None}
    if _lguard is not None:
        try:
            try:
                st = _lguard.check(_license_path(), key_file=os.path.join(_HERE, "key.txt"))
            except TypeError:
                st = _lguard.check(_license_path())
            out.update(code=st.get("code"), expires=st.get("expires"), days_left=st.get("days_left"),
                       devices=st.get("devices"), device_code=_lguard.device_code(),
                       term_days=st.get("term_days"), term_start=st.get("term_start"),
                       has_key=bool((_read_json_file(_license_path()) or {}).get("token")))
        except Exception as e:
            out["error"] = type(e).__name__
    return out


def r_license_activate(app, q, b):
    if _lguard is None:
        raise HttpError(501, "The licence module is not installed.")
    key = re.sub(r"\s+", "", str(b.get("key") or ""))
    if not key:
        raise HttpError(400, "Paste the whole key: one line that starts with GQ1.")
    st = _lguard.activate(key, _license_path())
    _LIC_CACHE.update(t=0.0, v=None)
    res = r_license(app, q, b)
    res["activated"] = bool(st.get("ok") and st.get("plan") == "PRO")
    res["message"] = str(st.get("message") or "")
    return res


def r_license_deactivate(app, q, b):
    if _lguard is None:
        raise HttpError(501, "The licence module is not installed.")
    data = _read_json_file(_license_path()) or {}
    left = None
    if data.get("token"):
        left = _lguard.deactivate(data["token"], _license_path())
    _LIC_CACHE.update(t=0.0, v=None)
    res = r_license(app, q, b)
    res["seats_left"] = left
    return res


def r_sample(app, q, b):
    """A short sample book (original text) so a new writer can try every panel before starting their own."""
    if _ui is None or not hasattr(_ui, "SAMPLE_BOOK"):
        raise HttpError(501, "No sample book in this version.")
    sample = _ui.SAMPLE_BOOK
    folder = clean_title(sample["folder"])
    d = _book_dir(app, folder)
    if not os.path.isdir(d) or not list_chapters(d):
        os.makedirs(d, exist_ok=True)
        meta = read_meta(d)
        meta.update(title=sample["title"], author=sample["author"], copyright=False, language="en-GB")
        write_meta(d, meta)
        led = Ledger(d)
        for i, (title, text) in enumerate(sample["chapters"], 1):
            led.save("%02d %s.md" % (i, clean_title(title)), text)
        if _spell is not None and _spell.available(_spell_lang(d)):
            # The sample is written in British English. If this PC's checker has no British dictionary, its few
            # British spellings ("harbour") go into the sample's own dictionary, which also shows how that works.
            text = "\n".join(t for _, t in sample["chapters"])
            _spell.add_words(d, _spell.misspelled_words(text, (), _spell_lang(d)))
    return {"ok": True, "name": folder}


# ======================================================================================================
# Craft tools: progress, plot outline, submissions, series bible, revision (quill_craft.py)
# ======================================================================================================
SUBMISSIONS_FILE = "Submissions.json"
SERIES_FILE = ".quill_series.json"
_SERIES_LOCK = threading.Lock()


def _need_craft():
    if _craft is None:
        raise HttpError(501, "The writing tools are not installed (quill_craft.py is missing).")


def _craft_err(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HttpError(400, str(e))


def _beat_titles(d, meta):
    """{chapter file: [beat titles]} for the book's outline (empty without one)."""
    ol = meta.get("outline")
    if _craft is None or not isinstance(ol, dict) or ol.get("template") not in _craft.TEMPLATES:
        return {}
    out = {}
    for b in _craft.TEMPLATES[ol["template"]]["beats"]:
        for c in ((ol.get("beats") or {}).get(b["id"]) or {}).get("chapters") or []:
            if isinstance(c, str):
                out.setdefault(resolve_renamed(d, c), []).append(b["title"])
    return out


def r_progress(app, q, b):
    _need_craft()
    today = client_today(q.get("today"))
    d = _existing_book(app, q.get("book")) if q.get("book") else None
    goal, days = app.stats.snapshot()
    res = dict(app.stats.summary(), ok=True)
    res["heat"] = _craft.heatmap(days, today, goal)
    res["pace"] = int(round(_craft.pace(days, today)))
    res.update(_craft.session_stats(days, today))
    if d:
        words = book_words(d)
        meta = read_meta(d)
        res["book"] = {"words": words, "target": meta.get("target_words", 0), "deadline": meta.get("deadline", ""),
                       "projection": _craft.project_finish(meta.get("target_words", 0), words, days, today)}
    return res


def _outline_state(app, d):
    meta = read_meta(d)
    chs = _chapter_list(d)
    view = _craft.outline_view(meta.get("outline"), chs, meta.get("target_words", 0), lambda n: resolve_renamed(d, n))
    return {"ok": True, "templates": _craft.template_list(), "outline": view, "chapters": [
        {"name": c["name"], "title": c["title"], "words": c["words"]} for c in chs]}


def r_outline(app, q, b):
    _need_craft()
    return _outline_state(app, _existing_book(app, q.get("book")))


def r_outline_set(app, q, b):
    _need_craft()
    d = _existing_book(app, b.get("book"))
    with book_lock(d):
        meta = read_meta(d)
        meta["outline"] = _craft_err(_craft.apply_template, meta.get("outline"), str(b.get("template") or ""),
                                     bool(b.get("replace")))
        write_meta(d, meta)
    return _outline_state(app, d)


def r_outline_beat(app, q, b):
    _need_craft()
    d = _existing_book(app, b.get("book"))
    with book_lock(d):
        meta = read_meta(d)
        chs = b.get("chapters")
        if isinstance(chs, list):
            chs = [c for c in chs if isinstance(c, str)]
        meta["outline"] = _craft_err(_craft.set_beat, meta.get("outline"), str(b.get("beat") or ""), chs,
                                     b.get("notes"), list_chapters(d))
        write_meta(d, meta)
    return _outline_state(app, d)


def r_outline_clear(app, q, b):
    _need_craft()
    d = _existing_book(app, b.get("book"))
    with book_lock(d):
        meta = read_meta(d)
        meta.pop("outline", None)
        write_meta(d, meta)
    return _outline_state(app, d)


def _subs_load(d):
    data = _read_json_file(os.path.join(d, SUBMISSIONS_FILE)) or {}
    items = []
    for it in data.get("items") or []:
        try:
            items.append(_craft.clean_submission(it))
        except ValueError:
            continue
    letters = data.get("letters") if isinstance(data.get("letters"), dict) else {}
    return {"items": items, "letters": letters}


def _subs_state(d, today):
    data = _subs_load(d)
    return {"ok": True, "items": data["items"], "summary": _craft.submissions_summary(data["items"], today),
            "kinds": list(_craft.SUB_KINDS),
            "statuses": [{"id": s, "label": _craft.SUB_LABEL[s]} for s in _craft.SUB_STATUS]}


def r_submissions(app, q, b):
    _need_craft()
    return _subs_state(_existing_book(app, q.get("book")), client_today(q.get("today")))


def r_submission_save(app, q, b):
    _need_craft()
    d = _existing_book(app, b.get("book"))
    item = b.get("item") if isinstance(b.get("item"), dict) else {}
    with book_lock(d):
        data = _subs_load(d)
        old = next((x for x in data["items"] if x["id"] == item.get("id")), None)
        new = _craft_err(_craft.clean_submission, item, old)
        if old is None and len(data["items"]) >= 500:
            raise HttpError(400, "That is 500 entries already: delete some that are done.")
        data["items"] = [new if x["id"] == new["id"] else x for x in data["items"]] if old else data["items"] + [new]
        _write_json_file(os.path.join(d, SUBMISSIONS_FILE), data)
    return _subs_state(d, client_today(b.get("today")))


def r_submission_delete(app, q, b):
    _need_craft()
    d = _existing_book(app, b.get("book"))
    with book_lock(d):
        data = _subs_load(d)
        data["items"] = [x for x in data["items"] if x["id"] != b.get("id")]
        _write_json_file(os.path.join(d, SUBMISSIONS_FILE), data)
    return _subs_state(d, client_today(b.get("today")))


def _letter_state(d, kind, fields=None):
    meta = read_meta(d)
    data = _subs_load(d)
    saved = dict(data["letters"].get(kind) or {})
    if fields is not None:
        saved = {k: str(fields.get(k, ""))[:4000] for k in (f["id"] for f in _craft.letter_fields(kind))}
    pre = {"title": meta["title"], "author": meta["author"], "word_count": "{:,}".format(book_words(d))}
    merged = dict({k: v for k, v in pre.items() if v}, **{k: v for k, v in saved.items() if v})
    text, missing = _craft.render_letter(kind, merged)
    return data, saved, {"ok": True, "kind": kind, "fields": _craft.letter_fields(kind), "values": merged,
                         "text": text, "missing": missing, "words": count_words(text)}


def r_letter(app, q, b):
    _need_craft()
    d = _existing_book(app, q.get("book"))
    return _craft_err(_letter_state, d, str(q.get("kind") or ""))[2]


def r_letter_save(app, q, b):
    _need_craft()
    d = _existing_book(app, b.get("book"))
    kind = str(b.get("kind") or "")
    fields = b.get("fields") if isinstance(b.get("fields"), dict) else {}
    with book_lock(d):
        data, saved, res = _craft_err(_letter_state, d, kind, fields)
        data["letters"][kind] = saved
        _write_json_file(os.path.join(d, SUBMISSIONS_FILE), data)
    return res


def r_revise(app, q, b):
    _need_craft()
    off = [x for x in b["off"] if x in _craft.CHECKS] if isinstance(b.get("off"), list) else []
    text = b.get("text")
    whole = False
    if not isinstance(text, str):
        d = _existing_book(app, b.get("book"))
        text = "\n\n".join(read_text(os.path.join(d, c)) for c in list_chapters(d))
        whole = True
    if len(text) > 3_000_000:
        raise HttpError(400, "That is too long to check at once: check one chapter at a time.")
    res = _craft.analyze_text(text, off)
    if whole:
        res["spans"] = []   # positions mean nothing across a joined book
    res["ok"] = True
    res["labels"] = _craft.CHECK_LABEL
    return res


def _series_books(app):
    return [n for n in sorted(os.listdir(app.root), key=str.lower)
            if not n.startswith(".") and os.path.isdir(os.path.join(app.root, n))]


def _series_state(app):
    books = {n: read_meta(os.path.join(app.root, n))["title"] for n in _series_books(app)}
    data = _read_json_file(os.path.join(app.root, SERIES_FILE)) or {}
    out = []
    for s in data.get("series") or []:
        try:
            out.append(_craft.clean_series(s, books, s))
        except ValueError:
            continue
    return {"ok": True, "series": out, "books": [{"name": k, "title": v} for k, v in books.items()]}


def _series_write(app, fn):
    with _SERIES_LOCK:
        lst = _series_state(app)["series"]
        fn(lst, _series_books(app))
        _write_json_file(os.path.join(app.root, SERIES_FILE), {"series": lst})
    return _series_state(app)


def r_series(app, q, b):
    _need_craft()
    return _series_state(app)


def r_series_save(app, q, b):
    _need_craft()
    obj = b.get("series")
    saved = []

    def go(lst, books):
        old = next((s for s in lst if isinstance(obj, dict) and s["id"] == obj.get("id")), None)
        new = _craft_err(_craft.clean_series, obj, books, old)
        if old is None and len(lst) >= 50:
            raise HttpError(400, "That is 50 series already.")
        lst[:] = [new if s["id"] == new["id"] else s for s in lst] if old else lst + [new]
        saved.append(new["id"])
    res = _series_write(app, go)
    res["saved"] = saved[0]
    return res


def r_series_delete(app, q, b):
    _need_craft()

    def go(lst, books):
        lst[:] = [s for s in lst if s["id"] != b.get("id")]
    return _series_write(app, go)


def r_series_import(app, q, b):
    """Add what Lore finds in each book of the series to the bible (nothing already there is changed)."""
    _need_craft()
    if _lore is None:
        raise HttpError(501, "The lore module is not installed.")
    added = [0]

    def go(lst, books):
        s = next((x for x in lst if x["id"] == b.get("id")), None)
        if s is None:
            raise HttpError(404, "No such series.")
        per = [(f, book_lore(app, os.path.join(app.root, f))[1]) for f in s["books"]]
        added[0] = _craft.merge_lore(s, per)
    res = _series_write(app, go)
    res["added"] = added[0]
    return res


def r_series_check(app, q, b):
    """Continuity across the whole series: Lore's checks run over every book in reading order as one story."""
    _need_craft()
    if _lore is None:
        raise HttpError(501, "The lore module is not installed.")
    s = next((x for x in _series_state(app)["series"] if x["id"] == q.get("id")), None)
    if s is None:
        raise HttpError(404, "No such series.")
    chapters, appear, texts = [], {}, {}
    for f in s["books"]:
        d = os.path.join(app.root, f)
        chs, lore = book_lore(app, d)
        title = read_meta(d)["title"]
        chapters += [("%s: %s" % (title, name), text) for name, text in chs]
        texts[title] = "\n".join(text for _, text in chs)
        for e in lore.get("characters") or []:
            for n in [e["name"]] + e.get("aliases", []):
                appear.setdefault(_craft.name_key(n), []).append(title)
    res = _lore.analyze(chapters) if chapters else {"anomalies": []}
    people = []
    for c in s["characters"]:
        seen = []
        for n in [c["name"]] + c.get("aliases", []):
            for t in appear.get(_craft.name_key(n), []):
                if t not in seen:
                    seen.append(t)
            for t, body in texts.items():   # a name the writer typed counts even where Lore did not detect it
                if t not in seen and n and re.search(r"(?<!\w)%s(?!\w)" % re.escape(n), body, re.I):
                    seen.append(t)
        people.append({"name": c["name"], "books": seen})
    return {"ok": True, "anomalies": res.get("anomalies", []), "appearances": people, "books": len(s["books"])}


# ======================================================================================================
# 1.5 studio: screenplays, sources and citations, import, comments, compare, projects, sprints, plotlines
# ======================================================================================================
SOURCES_FILE = "Sources.json"
SPRINTS_FILE = ".quill_sprints.json"
_SPRINT_LOCK = threading.Lock()


def _need(mod, what):
    if mod is None:
        raise HttpError(501, "The %s tools are not installed." % what)


def _bad_input(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HttpError(400, str(e))


def _book_chapters(d):
    """[(file name, title, text)] in reading order."""
    return [(c, chapter_title(c), read_text(os.path.join(d, c))) for c in list_chapters(d)]


def r_projects(app, q, b):
    _need(_studio, "project")
    return {"ok": True, "projects": _studio.project_list(), "kinds": _studio.KINDS}


def new_project_book(d, p, today):
    """Fill a new, empty book folder with a project template's chapters and settings."""
    with book_lock(d):
        meta = read_meta(d)
        meta.update(_studio.project_meta(p, today))
        write_meta(d, meta)
    names = []
    for i, (title, text) in enumerate(p["chapters"]):
        if text is None:
            text = _script.STARTER if _script is not None else ""
        if i == 0:
            name = "01 %s.md" % clean_title(title)
            Ledger(d).save(name, text)
        else:
            name = new_chapter(d, title, text=text)
        names.append(name)
    if p.get("outline") and _craft is not None:
        beats = ("setup", "plot1", "rising", "midpoint", "plot2", "climax")
        with book_lock(d):
            meta = read_meta(d)
            ol = _craft.apply_template(None, p["outline"], True)
            for beat, name in zip(beats, names):
                ol = _craft.set_beat(ol, beat, [name], None, names)
            meta["outline"] = ol
            write_meta(d, meta)
    return names


# ---- screenplays
def r_script(app, q, b):
    _need(_script, "screenplay")
    d = _existing_book(app, q.get("book"))
    res = _script.analyze(_book_chapters(d))
    res.update(ok=True, cheatsheet=_script.CHEATSHEET)
    return res


def r_script_preview(app, q, b):
    _need(_script, "screenplay")
    text = b.get("text")
    if not isinstance(text, str):
        raise HttpError(400, "No text to preview.")
    if len(text) > 2_000_000:
        raise HttpError(400, "That chapter is too long to preview at once.")
    return {"ok": True, "html": _script.preview_html(text)}


# ---- sources and citations
def _sources_load(d):
    data = _read_json_file(os.path.join(d, SOURCES_FILE)) or {}
    meta = read_meta(d)
    style = data.get("style") if _cite is not None and data.get("style") in _cite.STYLES else \
        meta.get("cite_style") if _cite is not None and meta.get("cite_style") in _cite.STYLES else "mla"
    out = []
    for s in data.get("sources") or []:
        try:
            out.append(_cite.clean_source(s, s))
        except (ValueError, AttributeError):
            continue
    return {"style": style, "sources": out}


def _sources_state(d):
    data = _sources_load(d)
    st = data["style"]
    items = [dict(s, entry=_cite.entry(s, st), cite=_cite.in_text(s, st, "")) for s in data["sources"]]
    items.sort(key=lambda s: s["entry"].lower())
    return {"ok": True, "style": st, "styles": _cite.STYLES, "kinds": _cite.KINDS, "list_title": _cite.LIST_TITLE[st],
            "bibliography": bool(read_meta(d).get("bibliography")), "sources": items,
            "list": _cite.bibliography(data["sources"], st)}


def _sources_write(d, data):
    _write_json_file(os.path.join(d, SOURCES_FILE), {"style": data["style"], "sources": data["sources"]})


def r_sources(app, q, b):
    _need(_cite, "citation")
    return _sources_state(_existing_book(app, q.get("book")))


def r_source_save(app, q, b):
    _need(_cite, "citation")
    d = _existing_book(app, b.get("book"))
    src = b.get("source") if isinstance(b.get("source"), dict) else {}
    with book_lock(d):
        data = _sources_load(d)
        old = next((x for x in data["sources"] if x["id"] == src.get("id")), None)
        new = _bad_input(_cite.clean_source, src, old)
        if old is None and len(data["sources"]) >= _cite.MAX_SOURCES:
            raise HttpError(400, "That is %d sources already." % _cite.MAX_SOURCES)
        data["sources"] = [new if x["id"] == new["id"] else x for x in data["sources"]] if old else data["sources"] + [new]
        _sources_write(d, data)
    res = _sources_state(d)
    res["saved"] = new["id"]
    return res


def r_source_delete(app, q, b):
    _need(_cite, "citation")
    d = _existing_book(app, b.get("book"))
    with book_lock(d):
        data = _sources_load(d)
        data["sources"] = [x for x in data["sources"] if x["id"] != b.get("id")]
        _sources_write(d, data)
    return _sources_state(d)


def r_source_style(app, q, b):
    _need(_cite, "citation")
    d = _existing_book(app, b.get("book"))
    style = b.get("style")
    if style is not None and style not in _cite.STYLES:
        raise HttpError(400, "Citation style must be MLA, APA or Chicago.")
    with book_lock(d):
        data = _sources_load(d)
        if style:
            data["style"] = style
        _sources_write(d, data)
        meta = read_meta(d)
        if style:
            meta["cite_style"] = style
        if "bibliography" in b:
            meta["bibliography"] = bool(b.get("bibliography"))
        write_meta(d, meta)
    return _sources_state(d)


def r_source_cite(app, q, b):
    """The text to put at the cursor: an in-text citation (MLA, APA) or a footnote (Chicago)."""
    _need(_cite, "citation")
    d = _existing_book(app, b.get("book"))
    data = _sources_load(d)
    src = next((x for x in data["sources"] if x["id"] == b.get("id")), None)
    if src is None:
        raise HttpError(404, "That source is not in the list any more.")
    loc = str(b.get("locator") or "")
    if data["style"] == "chicago":
        return {"ok": True, "kind": "note", "text": _cite.note(src, loc, short=bool(b.get("short")))}
    return {"ok": True, "kind": "inline", "text": _cite.in_text(src, data["style"], loc)}


def r_source_import(app, q, b):
    """Add a source for every page or book clipped into the Library's research notes (none twice)."""
    _need(_cite, "citation")
    d = _existing_book(app, b.get("book"))
    clips = _lib.clips(d) if _lib is not None else []
    with book_lock(d):
        data = _sources_load(d)
        have = {(x.get("title", "").lower(), x.get("url", "")) for x in data["sources"]}
        new = [x for x in _cite.from_clips(clips) if (x["title"].lower(), x.get("url", "")) not in have]
        data["sources"] += new[:max(0, _cite.MAX_SOURCES - len(data["sources"]))]
        _sources_write(d, data)
    res = _sources_state(d)
    res["added"] = len(new)
    return res


# ---- import a manuscript
def _import_read(b):
    _need(_import, "import")
    name = str(b.get("filename") or "")
    try:
        raw = __import__("base64").b64decode(str(b.get("data") or ""), validate=True)
    except ValueError:
        raise HttpError(400, "The file did not arrive whole. Try again.")
    if not raw:
        raise HttpError(400, "That file is empty.")
    try:
        return _import.read(name, raw)
    except _import.ImportProblem as e:
        raise HttpError(400, str(e))


def r_import_preview(app, q, b):
    res = _import_read(b)
    return {"ok": True, "title": res["title"], "author": res["author"], "kind": res["kind"], "how": res["how"],
            "words": res["words"], "notes": res["notes"],
            "chapters": [{"title": c["title"], "words": c["words"], "opening": " ".join(c["text"].split()[:24])}
                         for c in res["chapters"]]}


def r_import(app, q, b):
    """Make a new book from the file: one chapter file per chapter, nothing else touched."""
    res = _import_read(b)
    title = clean_text(str(b.get("title") or res["title"])).strip()[:200] or "Imported book"
    author = clean_text(str(b.get("author") if b.get("author") is not None else res["author"])).strip()[:200]
    folder = clean_title(title, "Imported book")
    k = 2
    base = folder
    while os.path.exists(_book_dir(app, folder)):
        folder = "%s (%d)" % (base, k)
        k += 1
    d = _book_dir(app, folder)
    os.makedirs(d)
    meta = read_meta(d)
    meta.update(title=title, author=author)
    if res["kind"] == "screenplay":
        meta["kind"] = "screenplay"
    write_meta(d, meta)
    width = max(2, len(str(len(res["chapters"]))))
    used = set()
    for i, c in enumerate(res["chapters"], 1):
        t = clean_title(c["title"], "Chapter %d" % i)
        name = "%0*d %s.md" % (width, i, t)
        if name.lower() in used:
            name = "%0*d %s (%d).md" % (width, i, t, i)
        used.add(name.lower())
        validate_rel(name)
        Ledger(d).save(name, c["text"].rstrip("\n") + "\n")
    return {"ok": True, "name": os.path.basename(d), "title": title, "chapters": len(res["chapters"]),
            "words": book_words(d)}


# ---- comments
def r_comments(app, q, b):
    _need(_studio, "comment")
    d = _existing_book(app, q.get("book"))
    out, total = [], 0
    for name, title, text in _book_chapters(d):
        cs = _studio.comments(text)
        _, defs, order = _studio.footnotes(text)
        if cs or defs:
            out.append({"name": name, "title": title, "comments": cs, "footnotes": len(defs)})
            total += len(cs)
    return {"ok": True, "chapters": out, "total": total}


# ---- compare with a backup
def r_backup_compare(app, q, b):
    _need(_studio, "compare")
    d = _existing_book(app, q.get("book"))
    name = q.get("name", "")
    old = snapshot_text(d, q.get("snap", ""), name)
    cur = resolve_renamed(d, name)
    p = os.path.join(d, cur) if cur in list_chapters(d) else None
    new = read_text(p) if p else ""
    res = _studio.compare(old, new)
    res.update(ok=True, exists=bool(p), name=cur, title=chapter_title(cur))
    return res


# ---- writing sprints
def _sprints(app):
    data = _read_json_file(os.path.join(app.root, SPRINTS_FILE)) or {}
    return [x for x in data.get("sprints") or [] if isinstance(x, dict) and isinstance(x.get("minutes"), (int, float))
            and isinstance(x.get("words"), int) and isinstance(x.get("day"), str)]


def r_sprints(app, q, b):
    _need(_studio, "sprint")
    return dict(_studio.sprint_summary(_sprints(app), client_today(q.get("today")).isoformat()), ok=True)


def r_sprint_save(app, q, b):
    _need(_studio, "sprint")
    sp = _bad_input(_studio.clean_sprint, b.get("sprint"))
    with _SPRINT_LOCK:
        items = (_sprints(app) + [sp])[-1000:]
        _write_json_file(os.path.join(app.root, SPRINTS_FILE), {"sprints": items})
    return dict(_studio.sprint_summary(items, sp["day"]), ok=True, saved=sp)


# ---- scene details, plotlines and the plot grid
def _plot_state(d):
    meta = read_meta(d)
    pls = meta.get("plotlines") if isinstance(meta.get("plotlines"), list) else []
    chs = _chapter_list(d)
    return {"ok": True, "plotlines": pls, "colors": list(_studio.PLOT_COLORS), "chapters": chs,
            "grid": _studio.plot_grid(chs, pls)}


def r_plotgrid(app, q, b):
    _need(_studio, "plotline")
    return _plot_state(_existing_book(app, q.get("book")))


def r_plotlines(app, q, b):
    _need(_studio, "plotline")
    d = _existing_book(app, b.get("book"))
    with book_lock(d):
        meta = read_meta(d)
        old = meta.get("plotlines") if isinstance(meta.get("plotlines"), list) else []
        meta["plotlines"] = _bad_input(_studio.clean_plotlines, b.get("plotlines"), old)
        keep = {p["id"] for p in meta["plotlines"]}
        for note in meta["chapters"].values():   # a removed plotline leaves no stale tags behind
            if isinstance(note, dict) and note.get("plotlines"):
                note["plotlines"] = [x for x in note["plotlines"] if x in keep]
        write_meta(d, meta)
    return _plot_state(d)


def r_chapter_details(app, q, b):
    _need(_studio, "plotline")
    d = _existing_book(app, b.get("book"))
    name = _chapter_name(b.get("name"))
    with book_lock(d):
        if name not in list_chapters(d):
            raise HttpError(404, "No such chapter.")
        meta = read_meta(d)
        ids = {p.get("id") for p in meta.get("plotlines") or [] if isinstance(p, dict)}
        note = dict(meta["chapters"].get(name) or {})
        note.update(_studio.clean_details(b, ids))
        meta["chapters"][name] = note
        write_meta(d, meta)
    return {"ok": True, "note": note}


ROUTES = {
    ("GET", "/api/status"): r_status, ("GET", "/api/books"): r_books, ("POST", "/api/books"): r_book_new,
    ("GET", "/api/chapters"): r_chapters, ("GET", "/api/chapter"): r_chapter, ("GET", "/api/book"): r_book,
    ("POST", "/api/book/meta"): r_book_meta, ("POST", "/api/book/sample"): r_sample,
    ("POST", "/api/chapter/new"): r_chapter_new, ("POST", "/api/chapter/rename"): r_chapter_rename,
    ("POST", "/api/chapter/order"): r_chapter_order, ("POST", "/api/chapter/delete"): r_chapter_delete,
    ("POST", "/api/chapter/undelete"): r_chapter_undelete, ("POST", "/api/chapter/note"): r_chapter_note,
    ("POST", "/api/save"): r_save, ("GET", "/api/search"): r_search, ("POST", "/api/replace"): r_replace,
    ("GET", "/api/graveyard"): r_graveyard, ("GET", "/api/backups"): r_backups, ("GET", "/api/backup"): r_backup,
    ("GET", "/api/backup/chapter"): r_backup_chapter, ("POST", "/api/backup/restore"): r_backup_restore,
    ("POST", "/api/backup/now"): r_backup_now, ("GET", "/api/backup/file"): r_backup_file,
    ("GET", "/api/stats"): r_stats, ("POST", "/api/stats/goal"): r_stats_goal,
    ("GET", "/api/lore"): r_lore, ("GET", "/api/lore/ai"): r_lore_ai,
    ("POST", "/api/export"): r_export, ("GET", "/api/export/file"): r_export_file, ("POST", "/api/reveal"): r_reveal,
    ("GET", "/api/recover"): r_recover, ("POST", "/api/lan"): r_lan,
    ("GET", "/api/license"): r_license, ("POST", "/api/license/activate"): r_license_activate,
    ("POST", "/api/license/deactivate"): r_license_deactivate,
    ("GET", "/api/updates"): r_updates, ("POST", "/api/updates"): r_updates_set,
    ("POST", "/api/spell"): r_spell, ("POST", "/api/spell/add"): r_spell_add,
    ("POST", "/api/spell/remove"): r_spell_remove,
    ("GET", "/api/library"): r_library, ("POST", "/api/library/enable"): r_library_enable,
    ("GET", "/api/library/search"): r_library_search, ("GET", "/api/library/word"): r_library_word,
    ("GET", "/api/library/wiki"): r_library_wiki, ("POST", "/api/library/download"): r_library_download,
    ("GET", "/api/library/read"): r_library_read, ("GET", "/api/research"): r_research,
    ("POST", "/api/research/clip"): r_research_clip, ("POST", "/api/research/remove"): r_research_remove,
    ("GET", "/api/progress"): r_progress, ("GET", "/api/outline"): r_outline, ("POST", "/api/outline"): r_outline_set,
    ("POST", "/api/outline/beat"): r_outline_beat, ("POST", "/api/outline/clear"): r_outline_clear,
    ("GET", "/api/submissions"): r_submissions, ("POST", "/api/submissions/save"): r_submission_save,
    ("POST", "/api/submissions/delete"): r_submission_delete, ("GET", "/api/letter"): r_letter,
    ("POST", "/api/letter"): r_letter_save, ("POST", "/api/revise"): r_revise,
    ("GET", "/api/series"): r_series, ("POST", "/api/series/save"): r_series_save,
    ("POST", "/api/series/delete"): r_series_delete, ("POST", "/api/series/import"): r_series_import,
    ("GET", "/api/series/check"): r_series_check,
    ("GET", "/api/target"): r_target, ("GET", "/api/book/zip"): r_book_zip, ("GET", "/api/drives"): r_drives,
    ("POST", "/api/book/zip/drive"): r_book_zip_drive, ("POST", "/api/paste"): r_paste,
    ("GET", "/api/schools/profile"): r_school_profile, ("POST", "/api/schools/profile"): r_school_profile_set,
    ("POST", "/api/schools/assignment/new"): r_school_assignment_new,
    ("POST", "/api/schools/assignment/open"): r_school_assignment_open,
    ("GET", "/api/schools/rubric"): r_school_rubric, ("POST", "/api/schools/rubric"): r_school_rubric_set,
    ("POST", "/api/schools/submit"): r_school_submit,
    ("POST", "/api/contests/new"): r_contest_new, ("POST", "/api/contests/open"): r_contest_open,
    ("POST", "/api/contests/enter"): r_contest_enter, ("POST", "/api/contests/pack"): r_contest_pack,
    ("POST", "/api/contests/judge/open"): r_contest_pack_read, ("POST", "/api/contests/judge/sheet"): r_contest_sheet,
    ("POST", "/api/contests/results"): r_contest_results,
    ("GET", "/api/projects"): r_projects, ("GET", "/api/script"): r_script,
    ("POST", "/api/script/preview"): r_script_preview, ("GET", "/api/sources"): r_sources,
    ("POST", "/api/sources/save"): r_source_save, ("POST", "/api/sources/delete"): r_source_delete,
    ("POST", "/api/sources/style"): r_source_style, ("POST", "/api/sources/cite"): r_source_cite,
    ("POST", "/api/sources/import"): r_source_import, ("POST", "/api/import/preview"): r_import_preview,
    ("POST", "/api/import"): r_import, ("GET", "/api/comments"): r_comments,
    ("GET", "/api/backup/compare"): r_backup_compare, ("GET", "/api/sprints"): r_sprints,
    ("POST", "/api/sprints"): r_sprint_save, ("GET", "/api/plotgrid"): r_plotgrid,
    ("POST", "/api/plotlines"): r_plotlines, ("POST", "/api/chapter/details"): r_chapter_details,
}
# Only the PC itself (never a paired phone) may change the licence, open folders or switch Phone mode.
LOCAL_ONLY = {"/api/license", "/api/license/activate", "/api/license/deactivate", "/api/reveal", "/api/lan",
              "/api/updates", "/api/library/enable", "/api/drives", "/api/book/zip/drive", "/api/schools/profile"}


class QuillApp:
    def __init__(self, root=None, port=None, open_browser=True, test_hooks=False, tls=None):
        self.root = os.path.realpath(root or default_root())
        os.makedirs(self.root, exist_ok=True)
        self.fixed_port = port
        self.open_browser = open_browser
        self.test_hooks = test_hooks
        self.local_token = secrets.token_urlsafe(24)
        self.lan_enabled = False
        self.lan_token = None
        self.lan_pin = None
        self.pin_failures = 0
        self.mobile_sessions = set()
        # LAN HTTPS: tls=None -> automatic (off under test_hooks or QUILL_LAN_TLS=0), False -> off,
        # or a local_tls.LocalTLS instance. tls_context stays None until Phone mode is enabled.
        if tls is None:
            tls = None if (test_hooks or _ltls is None or os.environ.get("QUILL_LAN_TLS", "1") == "0") \
                else _ltls.LocalTLS()
        self.tls = tls or None
        self.tls_context = None
        self.tls_info = None
        self.tls_names = ()
        self.bind_host = "127.0.0.1"
        self.port = None
        self.httpd = None
        self.serve_thread = None
        self.restarts = 0
        self.recovery_summary = {}
        self.lore_cache = {}
        self.lore_dirty = set()
        self.stats = Stats(self.root)
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._browser_opened = False
        self._lock = threading.RLock()
        self._health_lock = threading.Lock()

    def health(self, msg):
        line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg.rstrip())
        with self._health_lock:
            try:
                append_fsync(os.path.join(self.root, HEALTH_LOG), line.encode("utf-8", "replace"))
            except OSError:
                pass

    def recover_all(self):
        summary = {}
        for n in os.listdir(self.root):
            p = os.path.join(self.root, n)
            if n.startswith(".") or not os.path.isdir(p):
                continue
            try:
                rep = Ledger(p).recover()
                if rep["replayed"] or rep["discarded_tmp"] or rep["torn_records"] or rep["errors"] \
                        or rep.get("renumber_finished"):
                    summary[n] = rep
                    self.health("recovery %s: %s" % (n, json.dumps(rep)))
            except Exception:
                self.health("recovery failed for %s\n%s" % (n, traceback.format_exc()))
        self.recovery_summary = summary
        return summary

    def _bind(self):
        ports = [self.port or self.fixed_port] if (self.port or self.fixed_port) else []
        ports += [p for p in range(PORT_FIRST, PORT_LAST + 1) if p not in ports]
        last = None
        for p in ports:
            try:
                httpd = QuillHTTPServer((self.bind_host, p), QuillHandler, self)
                httpd.timeout = SOCKET_TIMEOUT
                return httpd
            except OSError as e:
                last = e
        raise OSError("no free port in %d-%d (%s)" % (PORT_FIRST, PORT_LAST, last))

    def _listen(self):
        httpd = self._bind()
        self.httpd = httpd
        self.port = httpd.server_address[1]
        t = threading.Thread(target=self._serve, args=(httpd,), name="quill-listener", daemon=True)
        self.serve_thread = t
        t.start()

    def _serve(self, httpd):
        try:
            httpd.serve_forever(poll_interval=0.25)
        except Exception:
            self.health("listener crashed\n" + traceback.format_exc())

    def start(self):
        self.recover_all()
        with self._lock:
            self._listen()
        threading.Thread(target=self._watchdog, name="quill-watchdog", daemon=True).start()
        if _updater is not None:
            try:  # tell the updater this version booted (it rolls back an update that never confirms)
                fn = getattr(_updater, "confirm_healthy", None)
                if callable(fn):
                    fn(_HERE)
            except Exception:
                self.health("auto_updater confirm_healthy failed\n" + traceback.format_exc())
            if (not self.test_hooks and read_settings().get("updates") is True and school_profile() != "classroom"
                    and not running_from_moved_folder()):
                threading.Thread(target=self._update_check, daemon=True).start()
        if self.open_browser and not self._browser_opened:
            self._browser_opened = True
            try:
                webbrowser.open("http://127.0.0.1:%d/" % self.port)
            except Exception:
                pass
        return self

    def _update_check(self, force=False):
        """Ask the signed update feed for a newer build of this channel. Only runs after the writer said yes to
        automatic checks, and at most once every 20 hours unless they press Check now."""
        try:
            if school_profile() == "classroom":
                return
            st = read_settings()
            if not force and time.time() - float(st.get("update_last_check") or 0) < UPDATE_EVERY_S:
                return
            fn = getattr(_updater, "check_for_updates", None) or getattr(_updater, "check", None)
            res = fn(_HERE, VERSION) if callable(fn) else ("disabled", "No updater installed.")
            status, detail = (res if isinstance(res, tuple) and len(res) == 2 else ("done", str(res)))
            write_settings({"update_last_check": int(time.time()), "update_status": status, "update_detail": detail})
        except Exception:
            self.health("auto_updater check failed\n" + traceback.format_exc())

    def _watchdog(self):
        while not self._stop.wait(0.5):
            dead = self.serve_thread is None or not self.serve_thread.is_alive()
            if dead or self._restart.is_set():
                try:
                    self._restart.clear()
                    with self._lock:
                        old = self.httpd
                        if old is not None:
                            if not dead:
                                old.shutdown()
                            old.server_close()
                        self._listen()
                        self.restarts += 1
                    self.health("listener (re)started on %s:%d" % (self.bind_host, self.port))
                except Exception:
                    self.health("watchdog restart failed\n" + traceback.format_exc())

    def kill_listener_for_test(self):
        """TEST HOOK: stop the listener thread as if it had died; the watchdog must bring it back."""
        self.httpd.shutdown()

    def wait_until_listening(self, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.serve_thread is not None and self.serve_thread.is_alive() and not self._restart.is_set():
                try:
                    socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                    return True
                except OSError:
                    pass
            time.sleep(0.05)
        return False

    def _tls_ready(self):
        if self.tls is None:
            return None
        try:
            info = self.tls.ensure()
            ctx = self.tls.server_context() if info.get("https") else None
        except Exception:
            self.health("local TLS failed\n" + traceback.format_exc())
            info, ctx = {"https": False, "message": "HTTPS setup failed; see .quill_health.log."}, None
        if not info.get("https"):
            self.health("LAN HTTPS unavailable: %s" % info.get("message"))
        self.tls_info = info
        self.tls_names = tuple(n for n in info.get("dns", ()) if n != "localhost")
        self.tls_context = ctx
        return info

    def enable_lan(self):
        tls = self._tls_ready()  # may run PowerShell for a few seconds the first time: outside the lock
        with self._lock:
            self.lan_enabled = True
            self.lan_token = secrets.token_urlsafe(16)
            self.lan_pin = "%06d" % secrets.randbelow(10 ** 6)
            self.pin_failures = 0
            if self.bind_host != "0.0.0.0":
                self.bind_host = "0.0.0.0"
                self._restart.set()
        ip = lan_ip()
        if self.tls_context is not None:
            url = "https://%s:%d/?pair=%s" % (ip, self.port, self.lan_token)
            trust = "http://%s:%d/trust" % (ip, self.port)
            return {"ok": True, "lan": True, "https": True, "url": url, "pin": self.lan_pin, "qr_svg": qr_svg(url),
                    "trust_url": trust, "trust_qr_svg": qr_svg(trust), "ca_sha256": tls.get("ca_sha256", ""),
                    "ca_created": bool(tls.get("ca_created")),
                    "note": "Phone must be on the same Wi-Fi. HTTPS is on, so Wake Lock and dictation work once the "
                            "phone trusts this PC's certificate (one time per phone, step 1). Chrome's dictation "
                            "sends audio to Google."}
        url = "http://%s:%d/?pair=%s" % (ip, self.port, self.lan_token)
        why = (tls or {}).get("message", "HTTPS is off.") if tls is not None else "HTTPS is off."
        return {"ok": True, "lan": True, "https": False, "url": url, "pin": self.lan_pin, "qr_svg": qr_svg(url),
                "note": "Phone must be on the same Wi-Fi. Wake Lock and Chrome dictation need HTTPS, so they "
                        "may be unavailable over plain-HTTP LAN; the keyboard's own voice typing still works. "
                        + why}

    def disable_lan(self):
        with self._lock:
            self.lan_enabled = False
            self.lan_token = None
            self.lan_pin = None
            self.mobile_sessions.clear()
            if self.bind_host != "127.0.0.1":
                self.bind_host = "127.0.0.1"
                self._restart.set()

    def pair(self, token, pin):
        with self._lock:
            if not self.lan_enabled or not self.lan_token:
                raise HttpError(403, "Pairing is off. Turn on Phone mode on the PC.")
            ok_t = secrets.compare_digest(str(token), self.lan_token)
            ok_p = secrets.compare_digest(str(pin), self.lan_pin or "")
            if not (ok_t and ok_p):
                self.pin_failures += 1
                if self.pin_failures >= 5:
                    self.lan_token = None
                    raise HttpError(403, "Too many wrong PINs. Re-enable Phone mode on the PC.")
                raise HttpError(403, "Wrong PIN.")
            s = secrets.token_urlsafe(24)
            self.mobile_sessions.add(s)
            return {"ok": True, "session": s}

    def stop(self):
        self._stop.set()
        with self._lock:
            if self.httpd is not None:
                try:
                    self.httpd.shutdown()
                except Exception:
                    pass
                self.httpd.server_close()


def render_page(token):
    if _ui is not None:
        return _ui.render(token, SAVE_PAUSE_MS, VERSION)
    return PAGE.replace("__QUILL_TOKEN__", json.dumps(token)).replace("__PAUSE__", str(SAVE_PAUSE_MS)) \
               .replace("__VERSION__", VERSION)


def app_links():
    """Optional quill_links.json next to the app: {"pro_url": "https://...", "support_email": "..."}.
    Only https links and plain addresses are passed to the page."""
    d = _read_json_file(os.path.join(_HERE, "quill_links.json")) or {}
    out = {}
    url = str(d.get("pro_url") or "")
    if re.match(r"^https://[^\s\"'<>]+$", url):
        out["pro_url"] = url
    mail = str(d.get("support_email") or "")
    if re.match(r"^[^@\s\"'<>]+@[^@\s\"'<>]+\.[A-Za-z]{2,}$", mail):
        out["support_email"] = mail
    return out


# The v1.0 page, used only when quill_ui.py is missing from an install (the API still serves it).
LEGACY_PAGE_NOTE = "quill_ui.py not found: serving the built-in v1.0 page"


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Stark Quill</title>
<style>
:root{--bg:#f7f4ee;--panel:#efebe3;--ink:#2a2622;--muted:#8a8178;--line:#ddd6ca;--accent:#4f7a5a;--warn:#b0603a;
--font:Georgia,'Iowan Old Style','Palatino Linotype',serif;--ui:system-ui,-apple-system,'Segoe UI',sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#1b1d1c;--panel:#232625;--ink:#e6e1d8;
--muted:#8d918c;--line:#343836;--accent:#86b393;--warn:#e09a72}}
:root[data-theme=dark]{--bg:#1b1d1c;--panel:#232625;--ink:#e6e1d8;--muted:#8d918c;--line:#343836;--accent:#86b393;--warn:#e09a72}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font-family:var(--ui);display:flex;overflow:hidden}
aside{width:250px;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:14px;gap:10px;overflow:auto}
aside h1{font:600 17px var(--font);margin:0 0 4px;letter-spacing:.02em}
aside h1 small{font:11px var(--ui);color:var(--muted);margin-left:6px}
select,input,button{font:13px var(--ui);color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
button{cursor:pointer}button:hover{border-color:var(--accent)}
.row{display:flex;gap:6px}.row>*{flex:1}
#chapters{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
#chapters li{padding:7px 9px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;font-size:14px}
#chapters li:hover{background:var(--bg)}#chapters li.on{background:var(--bg);box-shadow:inset 3px 0 var(--accent)}
#chapters li span{color:var(--muted);font-size:12px}
main{flex:1;display:flex;flex-direction:column;min-width:0}
header{display:flex;align-items:center;gap:8px;padding:8px 14px;border-bottom:1px solid var(--line);font-size:13px;color:var(--muted);flex-wrap:wrap}
header .sp{flex:1}#status.err{color:var(--warn)}#status.ok{color:var(--accent)}
#goalbar{height:3px;background:var(--line);width:90px;border-radius:2px;overflow:hidden}#goalbar i{display:block;height:100%;background:var(--accent);width:0}
#wrap{flex:1;overflow:auto;display:flex;justify-content:center}
#ed{width:100%;max-width:44em;padding:48px 28px 40vh;border:0;outline:0;resize:none;background:transparent;color:var(--ink);font:19px/1.75 var(--font);min-height:100%}
#lore{width:290px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:14px;font-size:13px;display:none}
body.lore #lore{display:block}#lore h3{font:600 13px var(--ui);text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:14px 0 6px}
#lore .an{border-left:3px solid var(--warn);padding:4px 8px;margin:6px 0;background:var(--bg);border-radius:4px}
#lore .tag{display:inline-block;padding:2px 7px;margin:2px;border-radius:10px;background:var(--bg);border:1px solid var(--line)}
body.focus aside,body.focus #lore,body.focus header{display:none}body.focus #ed{padding-top:22vh}
#banner{display:none;padding:8px 14px;background:var(--warn);color:#fff;font-size:13px}
dialog{border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--ink);max-width:92vw}
dialog svg{width:240px;height:240px;display:block;margin:8px auto;background:#fff}
#pin{font:600 28px monospace;letter-spacing:.2em;text-align:center}
#pairview{display:none;margin:auto;max-width:320px;text-align:center;padding:20px}
.mic.on{background:var(--warn);color:#fff}
@media (max-width:760px){aside{position:fixed;inset:0 auto 0 0;z-index:5;transform:translateX(-100%);transition:transform .2s}
body.side aside{transform:none}#lore{position:fixed;inset:0 0 0 auto;z-index:5;width:86vw}#ed{padding:24px 16px 40vh;font-size:18px}}
</style></head><body>
<div id="pairview"><h2 style="font-family:var(--font)">Stark Quill</h2><p id="pairmsg">Enter the PIN shown on your PC.</p>
<input id="pairpin" inputmode="numeric" maxlength="6" style="font-size:24px;text-align:center;width:100%"><p><button id="pairbtn">Pair</button></p></div>
<aside id="side"><h1>Stark Quill<small>v__VERSION__</small></h1>
<div class="row"><select id="book"></select><button id="newbook" title="New book">+</button></div>
<ul id="chapters"></ul><button id="newch">+ Chapter</button>
<div style="margin-top:auto;display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--muted)">
<label>Daily goal <input id="goal" type="number" min="0" step="100" style="width:80px"> words</label>
<div class="row"><select id="fmt"></select><button id="export">Export</button></div>
<div id="plan"></div></div></aside>
<main><div id="banner"></div><header>
<button id="menu" title="Chapters">&#9776;</button><span id="title"></span><span class="sp"></span>
<span id="words">0 words</span><span id="goalbar"><i></i></span><span id="status">Ready</span>
<button id="mic" class="mic" title="Dictation">Dictate</button><button id="lorebtn">Lore</button>
<button id="phone">Phone</button><button id="focus" title="Focus mode (Esc exits)">Focus</button><button id="theme">Theme</button>
</header><div id="wrap"><textarea id="ed" spellcheck="true" placeholder="Begin..."></textarea></div></main>
<section id="lore"></section>
<dialog id="lan"><div id="lanbody"></div><p style="text-align:right"><button id="lanoff">Turn off</button> <button id="lanclose">Close</button></p></dialog>
<script>
"use strict";
const PAUSE=__PAUSE__;let TOKEN=__QUILL_TOKEN__;const $=id=>document.getElementById(id);const ed=$("ed");
const LS={get(k,d){try{const v=localStorage.getItem(k);return v===null?d:JSON.parse(v)}catch(e){return d}},
set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}},del(k){try{localStorage.removeItem(k)}catch(e){}}};
if(!TOKEN)TOKEN=LS.get("quill.session","");
let S={book:null,ch:null,baseText:"",baseSha:"",timer:null,saving:false,plan:"FREE",lastLore:0};
function status(t,c){const s=$("status");s.textContent=t;s.className=c||""}
function banner(t){const b=$("banner");b.textContent=t||"";b.style.display=t?"block":"none"}
async function api(path,body){const o={headers:{"X-Quill-Token":TOKEN}};
 if(body!==undefined){o.method="POST";o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body)}
 const r=await fetch(path,o);let j={};try{j=await r.json()}catch(e){}
 if(r.status===401&&!TOKEN){showPair();throw new Error("pair")} if(!r.ok&&!j.need_base){const e=new Error(j.error||("HTTP "+r.status));e.status=r.status;throw e}return j}
const pkey=()=>"quill.pending."+S.book+"/"+S.ch;
function words(t){const m=t.match(/\S+/g);return m?m.length:0}
function updWords(){const w=words(ed.value);$("words").textContent=w.toLocaleString()+" words";
 const day=new Date().toISOString().slice(0,10),k="quill.day."+S.book;let d=LS.get(k,null);
 if(!d||d.day!==day){d={day,start:bookWords()};LS.set(k,d)} const goal=+LS.get("quill.goal",0);
 const done=Math.max(0,bookWords()-d.start);$("goalbar").firstChild.style.width=goal?Math.min(100,done*100/goal)+"%":"0";
 $("goalbar").title=goal?done+" / "+goal+" words today":"Set a daily goal in the sidebar"}
let chWords={};function bookWords(){chWords[S.ch]=words(ed.value);return Object.values(chWords).reduce((a,b)=>a+b,0)}
ed.addEventListener("input",()=>{status("Editing...");updWords();clearTimeout(S.timer);S.timer=setTimeout(save,PAUSE)});
function buffer(){if(S.book&&S.ch)LS.set(pkey(),{book:S.book,name:S.ch,text:ed.value,base_sha:S.baseSha,base:S.baseText})}
async function save(force){clearTimeout(S.timer);if(!S.book||!S.ch)return;if(S.saving){S.timer=setTimeout(save,PAUSE);return}
 const sent=ed.value;if(!force&&sent===S.baseText&&S.baseSha)return;buffer();S.saving=true;
 try{let p={book:S.book,name:S.ch,text:sent,base_sha:S.baseSha};let j=await api("/api/save",p);
  if(j.need_base){p.base=S.baseText;j=await api("/api/save",p)}
  if(j.merged){if(ed.value===sent){keepCaret(j.text);S.baseText=j.text;S.baseSha=j.sha}else{S.baseText=sent;S.baseSha="resync";setTimeout(save,10)}
   if(j.conflicts&&j.conflicts.length)banner(j.conflicts.length+" overlapping edit(s) merged: both versions kept between <<<<<<< and >>>>>>> markers.")}
  else{S.baseText=sent;S.baseSha=j.sha}
  if(ed.value===sent)LS.del(pkey());status("Saved","ok");if(Date.now()-S.lastLore>4000)loadLore();
 }catch(e){status(e.status?("Not saved: "+e.message):"Offline - kept on this device","err");if(!e.status)setTimeout(()=>save(true),3000)}
 finally{S.saving=false}}
function keepCaret(t){const a=ed.selectionStart,b=ed.selectionEnd;ed.value=t;ed.setSelectionRange(Math.min(a,t.length),Math.min(b,t.length));updWords()}
window.addEventListener("online",()=>flushPending());
document.addEventListener("visibilitychange",()=>{if(document.hidden){buffer();save()}else wake()});
window.addEventListener("beforeunload",buffer);
async function flushPending(){for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(!k||!k.startsWith("quill.pending."))continue;
 const p=LS.get(k,null);if(!p)continue;if(p.book===S.book&&p.name===S.ch)continue;
 try{let j=await api("/api/save",p);if(j.need_base){p.base=p.base||"";j=await api("/api/save",p)}LS.del(k);i--}catch(e){}}}
async function loadBooks(){const j=await api("/api/books");let bs=j.books;if(!bs.length){await api("/api/books",{name:"My First Book"});bs=(await api("/api/books")).books}
 const sel=$("book");sel.innerHTML="";bs.forEach(b=>{const o=document.createElement("option");o.textContent=b.name;sel.appendChild(o)});
 const last=LS.get("quill.book",bs[0].name);sel.value=bs.some(b=>b.name===last)?last:bs[0].name;await openBook(sel.value)}
async function openBook(name){await save();S.book=name;LS.set("quill.book",name);const j=await api("/api/chapters?book="+encodeURIComponent(name));
 chWords={};j.chapters.forEach(c=>chWords[c.name]=c.words);renderChapters(j.chapters);
 const last=LS.get("quill.ch."+name,null);const c=j.chapters.find(c=>c.name===last)||j.chapters[0];if(c)await openChapter(c.name);loadLore()}
function renderChapters(list){const ul=$("chapters");ul.innerHTML="";list.forEach(c=>{const li=document.createElement("li");
 li.innerHTML="<b></b><span></span>";li.firstChild.textContent=c.name.replace(/\.(md|txt)$/,"");li.lastChild.textContent=c.words;
 li.onclick=()=>openChapter(c.name);if(c.name===S.ch)li.className="on";ul.appendChild(li)})}
async function openChapter(name){if(S.ch&&S.ch!==name)await save();const j=await api("/api/chapter?book="+encodeURIComponent(S.book)+"&name="+encodeURIComponent(name));
 S.ch=name;S.baseText=j.text;S.baseSha=j.sha;LS.set("quill.ch."+S.book,name);const pend=LS.get(pkey(),null);
 ed.value=j.text;if(pend&&pend.text!==j.text){ed.value=pend.text;S.baseText=pend.base!=null?pend.base:j.text;S.baseSha=pend.base_sha||j.sha;status("Restoring unsaved text...");save(true)}
 $("title").textContent=name.replace(/\.(md|txt)$/,"");document.querySelectorAll("#chapters li").forEach(li=>li.className=li.firstChild.textContent===$("title").textContent?"on":"");
 updWords();flushDictation();document.body.classList.remove("side")}
$("book").onchange=e=>openBook(e.target.value);
$("newbook").onclick=async()=>{const n=prompt("Book title");if(!n)return;try{await api("/api/books",{name:n});LS.set("quill.book",n);loadBooks()}catch(e){alert(e.message)}};
$("newch").onclick=async()=>{const n=prompt("Chapter title");if(!n)return;try{const j=await api("/api/chapter/new",{book:S.book,name:n});await openBook(S.book);openChapter(j.name)}catch(e){alert(e.message)}};
$("focus").onclick=()=>{document.body.classList.toggle("focus");ed.focus()};
$("menu").onclick=()=>document.body.classList.toggle("side");
document.addEventListener("keydown",e=>{if(e.key==="Escape")document.body.classList.remove("focus");if((e.ctrlKey||e.metaKey)&&e.key==="s"){e.preventDefault();save(true)}});
function applyTheme(t){if(t)document.documentElement.setAttribute("data-theme",t);else document.documentElement.removeAttribute("data-theme")}
applyTheme(LS.get("quill.theme",""));$("theme").onclick=()=>{const dark=matchMedia("(prefers-color-scheme: dark)").matches;
 const cur=document.documentElement.getAttribute("data-theme")||(dark?"dark":"light");const t=cur==="dark"?"light":"dark";applyTheme(t);LS.set("quill.theme",t)};
$("goal").value=LS.get("quill.goal","")||"";$("goal").oninput=e=>{LS.set("quill.goal",e.target.value);updWords()};
$("lorebtn").onclick=()=>{document.body.classList.toggle("lore");loadLore()};
function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
async function loadLore(ai){if(!document.body.classList.contains("lore")||!S.book)return;S.lastLore=Date.now();
 try{const j=await api("/api/lore?book="+encodeURIComponent(S.book)+(ai?"&ai=1":""));const L=$("lore");if(!j.available){L.innerHTML="<p>Lore engine not installed.</p>";return}
 let h="<h3>Timeline warnings</h3>"+(j.anomalies.length?j.anomalies.map(a=>"<div class=an><b>"+esc(a.chapter)+"</b>: "+esc(a.message)+"</div>").join(""):"<p>None found.</p>");
 h+="<h3>Characters</h3>"+j.characters.map(c=>"<span class=tag title='"+esc(c.mentions+" mentions")+"'>"+esc(c.name)+"</span>").join("");
 h+="<h3>Places</h3>"+j.locations.map(c=>"<span class=tag>"+esc(c.name)+"</span>").join("");
 h+="<h3>Dates</h3>"+j.timeline.map(t=>"<div>"+esc(t.chapter)+": "+esc(t.date)+"</div>").join("");
 if(S.plan==="PRO")h+=j.ai?"<h3>AI summary</h3><p>"+esc(j.ai)+"</p>":"<p><button id=aibtn>AI summary (local Ollama)</button></p>";
 L.innerHTML=h;const b=$("aibtn");if(b)b.onclick=()=>loadLore(true)}catch(e){}}
$("export").onclick=async()=>{await save(true);try{const j=await api("/api/export",{book:S.book,fmt:$("fmt").value});status("Exported: "+j.path,"ok")}catch(e){alert(e.message)}};
// --- wake lock --------------------------------------------------------------------------------
let wl=null;async function wake(){try{if("wakeLock" in navigator&&!document.hidden&&!wl){wl=await navigator.wakeLock.request("screen");wl.addEventListener("release",()=>wl=null)}}catch(e){}}
ed.addEventListener("focus",wake);
// --- dictation: chunks go to localStorage first, then into the editor -----------------------
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;let rec=null,dictating=false;
function flushDictation(){const q=LS.get("quill.dict",[]);if(!q.length||!S.ch)return;const a=ed.selectionStart;const ins=q.join(" ")+" ";
 ed.setRangeText(ins,a,ed.selectionEnd,"end");LS.del("quill.dict");ed.dispatchEvent(new Event("input"))}
$("mic").onclick=()=>{if(!SR){alert("Dictation is not available in this browser here (it needs Chrome/Edge/Safari and, on a phone, HTTPS). Your keyboard's voice typing still works.");return}
 if(dictating){dictating=false;rec&&rec.stop();$("mic").classList.remove("on");return}
 rec=new SR();rec.continuous=true;rec.interimResults=false;rec.lang=navigator.language||"en-US";
 rec.onresult=e=>{for(let i=e.resultIndex;i<e.results.length;i++)if(e.results[i].isFinal){const q=LS.get("quill.dict",[]);q.push(e.results[i][0].transcript.trim());LS.set("quill.dict",q)}flushDictation()};
 rec.onend=()=>{if(dictating)try{rec.start()}catch(e){}};rec.onerror=e=>{if(e.error==="not-allowed"){dictating=false;$("mic").classList.remove("on")}};
 dictating=true;$("mic").classList.add("on");ed.focus();rec.start()};
// --- phone / LAN ----------------------------------------------------------------------------
$("phone").onclick=async()=>{try{const j=await api("/api/lan",{enable:true});$("lanbody").innerHTML="<h3>Write on your phone</h3>"+
 (j.https?"<p><b>1. First time on this phone:</b> scan to install this PC's certificate"+(j.ca_created?" (new certificate: every phone again)":"")+"</p>"+j.trust_qr_svg+
 "<p style='font-size:11px;max-width:300px;word-break:break-all'>"+esc(j.trust_url)+"<br>SHA-256 "+esc(j.ca_sha256)+"</p><p><b>2. Scan to pair</b>, then enter PIN</p>":"<p>Scan, then enter PIN</p>")+
 j.qr_svg+"<div id=pin>"+esc(j.pin)+"</div><p style='font-size:12px;max-width:300px'>"+esc(j.url)+"<br>"+esc(j.note)+"</p>";$("lan").showModal()}catch(e){alert(e.message)}};
$("lanclose").onclick=()=>$("lan").close();$("lanoff").onclick=async()=>{try{await api("/api/lan",{enable:false})}catch(e){}$("lan").close()};
function showPair(){$("side").style.display="none";document.querySelector("main").style.display="none";$("pairview").style.display="block"}
$("pairbtn").onclick=async()=>{const pair=new URLSearchParams(location.search).get("pair")||"";
 try{const r=await fetch("/api/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pair,pin:$("pairpin").value})});const j=await r.json();
 if(!r.ok)throw new Error(j.error);LS.set("quill.session",j.session);location.href="/"}catch(e){$("pairmsg").textContent=e.message}};
async function boot(){if(!TOKEN){if(new URLSearchParams(location.search).get("pair"))showPair();else{showPair();$("pairmsg").textContent="Scan the QR code shown on your PC to pair this device."}return}
 try{const st=await api("/api/status");S.plan=st.plan;$("plan").textContent=st.plan==="PRO"?"PRO plan":"FREE plan - PRO adds docx/pdf/epub, lore AI, phone mode";
 const fm=$("fmt");st.exports.forEach(f=>{const o=document.createElement("option");o.textContent=f;fm.appendChild(o)});
 if(st.recovery&&Object.keys(st.recovery).length)banner("Recovered unsaved work after an unexpected shutdown: "+Object.keys(st.recovery).join(", "));
 await loadBooks();flushPending();setInterval(()=>{if(ed.value!==S.baseText&&!S.saving)save()},15000)}catch(e){if(e.message!=="pair")status("Cannot reach Stark Quill: "+e.message,"err")}}
boot();
</script></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stark Quill - local-first novel writing app")
    ap.add_argument("--root", help="books folder (default %%USERPROFILE%%\\Documents\\My_Books)")
    ap.add_argument("--port", type=int, help="fixed port (default: first free in 8525-8599)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--lan-http", action="store_true", help="Phone mode over plain HTTP (no local certificate)")
    a = ap.parse_args(argv)
    for old, new, res in move_data_folders():
        if res:
            print("%s -> %s: %s" % (old, new, res), flush=True)
    app = QuillApp(root=a.root, port=a.port, open_browser=not a.no_browser, tls=False if a.lan_http else None).start()
    print("%s %s on http://127.0.0.1:%d/  books: %s  (Ctrl+C quits)" % (APP_NAME, VERSION, app.port, app.root), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    main()
