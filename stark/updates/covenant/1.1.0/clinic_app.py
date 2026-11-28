#!/usr/bin/env python3
"""Stark Covenant - local-first clinic records app (clinic_app.py).

Python standard library only (3.10+), no external dependencies, no CDN: the whole UI is the inline
HTML/CSS/JS string PAGE at the bottom of this file.
Run:  python clinic_app.py [--data DIR] [--no-browser] [--demo] [--lan] [--port N]

IMPORTANT - WHAT THIS SOFTWARE IS NOT
  Stark Covenant is NOT a certified EHR and NOT a medical device. It makes NO HIPAA, GDPR or other
  regulatory compliance claim. The buyer must complete their own legal, privacy, security and compliance
  review before any real patient data is entered. It performs NO automated diagnosis and NO automated
  treatment: every clinical entry is made and signed by a person.

GUARANTEES (what the code really does - and the limits, stated plainly)
1. Durable writes (class Store). Every change is ONE transaction:
     (a) a JSON record with the (already encrypted) bytes of every file it touches + SHA-256 is appended
         to <store>/.journal.log, flush + os.fsync;
     (b) each file: <file>.tmp written + fsync, re-read and verified (length + SHA-256), os.replace();
     (c) one hash-chained audit entry is appended (fsync) and the audit head is updated atomically;
     (d) a commit record is appended and the journal compacted.
   Store.recover() (run at every start) replays uncommitted transactions (files whose SHA differs, and the
   audit entry if it is missing), drops torn journal lines and stray .tmp files, and cuts a torn final
   audit line (kept aside as audit.torn-*.frag for inspection). fsync is only as honest as the drive.
2. Audit trail (class AuditTrail): append-only JSON lines, each carrying seq, prev and an HMAC-SHA256 over
   the entry with a per-installation 32-byte key (stored encrypted by the vault in normal mode). verify()
   detects edited, inserted, deleted, reordered and truncated entries (the head file records the last
   seq/hash). Limit: someone holding the decrypted key AND write access can forge a whole new chain; keep
   backups elsewhere. Audit entries contain ids, actions and SHA-256 values only - never names or note text.
3. Sign-off lock: a note is a draft until its author signs it with a PIN (scrypt-hashed, separate from the
   password). The signature is an HMAC over the content hash, signer and time. After signing the note
   cannot be edited; only addenda, each signed with the PIN at creation, can be added. Suggestions from
   self_learner (or any AI) never enter a record by themselves: /api/ai/accept requires the clinician's PIN
   (explicit sign-off) and only inserts into the clinician's own DRAFT, recorded with provenance; the draft
   itself must still be signed.
4. Data at rest: every record, user file, document and backup is sealed with security_vault
   (encrypt/decrypt). If the vault module is missing or unusable the app runs in DEMO mode: a separate
   folder, synthetic patients only, a red banner on every screen and "synthetic" on every patient.
5. Modes from license_guard (missing/failing -> Baby): Baby $0 (one user, all permissions), Clinic $79/mo
   and Humanitarian $50 one-time (roles clinician / nurse / front desk / admin, least privilege, LAN opt-in),
   Hospital $249 and $999 tiers (more users). A downgrade never locks anyone out of existing records.
6. Server: 127.0.0.1 only on the first free port 8600-8699 (LAN only with --lan AND a plan that allows
   it; plain HTTP - use a trusted network). Per-handler fault boundary (traceback -> .covenant_health.log,
   friendly JSON 500, server keeps running), watchdog restarts a dead listener, Host check (DNS
   rebinding), Origin check, CSRF token on every POST, HttpOnly SameSite=Strict session cookie, per-user
   login with hashlib.scrypt, lockout after 5 failures (15 min), idle auto-lock (server and browser).
7. Paths: every on-disk name goes through safe_join (rejects '..', absolute, drive, UNC/device, ':' ADS,
   reserved names, control chars, trailing dot/space) and realpath containment (junction/symlink escapes).
8. Rolling encrypted backups of the store (start-up + every 30 min when changed + on demand), 20 kept.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import hmac
import http.cookies
import http.server
import importlib
import io
import json
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
import zipfile
from datetime import date, datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

APP_NAME = "Stark Covenant"
VERSION = "1.1.0"
BUILD_CHANNEL = "stable"    # full release (1.0.2+); older test installs are promoted by auto_updater
PORT_FIRST, PORT_LAST = 8600, 8699
MAX_BODY = 12 * 1024 * 1024
MAX_DOC = 8 * 1024 * 1024
SOCKET_TIMEOUT = 20
IDLE_SECONDS = 600
LOCK_FAILS = 5
LOCK_SECONDS = 900
BACKUP_KEEP = 20
BACKUP_INTERVAL = 1800
JOURNAL_NAME = ".journal.log"
HEALTH_LOG = ".covenant_health.log"
MAGIC_ENC = b"GCVE1\n"
MAGIC_DEMO = b"GCVD1\n"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
GENESIS = "0" * 64

DISCLAIMER = ("Stark Covenant is not a certified EHR and not a medical device. It makes no HIPAA, GDPR or "
              "other regulatory compliance claim. The buyer must complete their own compliance, privacy and "
              "security review before entering real patient data. It performs no automated diagnosis or "
              "treatment; every clinical entry is made and signed by a person.")
DEMO_BANNER = ("DEMO MODE - synthetic patients only. Nothing here is encrypted. Do not enter real patient data.")
ABOUT_LINE = "People and AI, working together, remember that God is the original Designer and Healer."
AI_LABEL = "Suggestion - requires clinician sign-off"
EXAMPLE_USER = "example"     # "Try with an example clinic": its account in the DEMO store only (synthetic patients)
EXAMPLE_PIN = "1234"         # shown on screen; signs notes in the example clinic only

OPTIONAL_MODULES = ("security_vault", "license_guard", "auto_updater", "polyglot_engine", "interop_bridge",
                    "optical_airgap", "self_learner", "emergency_comms", "consent_center", "persona_ai",
                    "nutrition_planner", "coverage_navigator", "research_desk", "i18n", "regional", "practice_suite",
                    "care_suite")
_MODS = {}


def release_channel(app_dir=None):
    """beta | stable: what this install follows (auto_updater settings / the CHANNEL file, else this build)."""
    app_dir = app_dir or _HERE
    up = optional("auto_updater")
    if up is not None:
        try:
            cfg = up._read_json(os.path.join(app_dir, up.CONFIG_NAME)) or {}
            if cfg.get("channel") in up.CHANNELS or os.path.exists(os.path.join(app_dir, up.CHANNEL_NAME)):
                return up.installed_channel(app_dir)
        except Exception:
            pass
    return BUILD_CHANNEL


def optional(name):
    """Import a sibling module owned by another team; None if missing or broken (never fatal)."""
    if name not in _MODS:
        try:
            _MODS[name] = importlib.import_module(name)
        except Exception:
            _MODS[name] = None
    return _MODS[name]


def documents_dir():
    """The user's real Documents folder (follows Windows folder redirection), else ~/Documents."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # CSIDL_PERSONAL
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


DATA_DIR_NAME = "Stark_Covenant_Data"            # Documents\Stark_Covenant_Data (1.1.0+)
LEGACY_DATA_DIR_NAME = "Gorgon_Covenant_Data"    # its name up to 1.1.0: copied once (move_data_root), never deleted
MOVED_MARK = ".moved_from"                       # inside the new folder: where it was copied from, when, how many files
MOVED_TOLD = ".moved_told"                       # the person has been told once (r_status)


def env_setting(name):
    """STARK_COVENANT_<name>, else the pre-1.1.0 GORGON_COVENANT_<name> (both stay supported)."""
    return os.environ.get("STARK_COVENANT_" + name) or os.environ.get("GORGON_COVENANT_" + name)


def default_data_root():
    """The data folder: an env override, else Documents/Stark_Covenant_Data, or the pre-1.1.0
    Documents/Gorgon_Covenant_Data while the Stark one does not exist yet (not copied yet, or the copy failed and
    nothing changed). Never creates or moves anything; main() calls move_data_root() first."""
    env = env_setting("DATA")
    if env:
        return env
    docs = documents_dir()
    new, old = os.path.join(docs, DATA_DIR_NAME), os.path.join(docs, LEGACY_DATA_DIR_NAME)
    return old if not os.path.isdir(new) and os.path.isdir(old) else new


def _tree_hashes(root):
    """{relative path: SHA-256} of every file under root."""
    out = {}
    for d, _dirs, files in os.walk(root):
        for fn in files:
            path = os.path.join(d, fn)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            out[os.path.relpath(path, root)] = h.hexdigest()
    return out


def covenant_running():
    """True when a copy of the app (any version) answers on this computer, so its data folder is in use. Copies take
    the first free port from PORT_FIRST, so the search stops at the first free port (bind test: instant)."""
    import http.client
    for p in range(PORT_FIRST, PORT_LAST + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return False
        except OSError:
            pass
        finally:
            s.close()
        try:
            c = http.client.HTTPConnection("127.0.0.1", p, timeout=3)
            c.request("GET", "/api/status")
            body = c.getresponse().read(1 << 16)
            c.close()
            if b"Covenant" in body:
                return True
        except (OSError, http.client.HTTPException):
            pass
    return False


def move_data_root(docs=None):
    """1.1.0: copy Documents/Gorgon_Covenant_Data to Documents/Stark_Covenant_Data once, verify, then switch.
    Only when the Stark folder is missing, the old one exists, no env override is set and no copy of the app is
    running. The copy goes to <new>.moving, must match the old folder in file count and every SHA-256, gets MOVED_MARK
    and is then renamed to <new>; from then on default_data_root() is the new folder. The old folder is only read:
    never changed, never deleted. -> "moved" | None (nothing to do) | "busy" | "failed: <reason>" (the new folder is
    still missing, so the app keeps using the old one exactly as before)."""
    if env_setting("DATA"):
        return None
    docs = docs or documents_dir()
    new, old = os.path.join(docs, DATA_DIR_NAME), os.path.join(docs, LEGACY_DATA_DIR_NAME)
    if os.path.isdir(new) or not os.path.isdir(old):
        return None
    if covenant_running():
        return "busy"
    tmp = new + ".moving"
    try:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)                       # an earlier attempt that never finished: only ever our own copy
        shutil.copytree(old, tmp)
        src, dst = _tree_hashes(old), _tree_hashes(tmp)
        if src != dst:
            raise OSError("the copy does not match the old folder (%d files, %d copied)" % (len(src), len(dst)))
        _write_fsync(os.path.join(tmp, MOVED_MARK), json.dumps(
            {"from": old, "at": now_iso(), "files": len(src)}).encode("utf-8"))
        os.rename(tmp, new)
        fsync_dir(docs)
        return "moved"
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return "failed: %s" % e


def moved_notice(data_root):
    """Once, after the copy: {"from": old, "to": new} for the start screen; None otherwise (or already told)."""
    if os.path.exists(os.path.join(data_root, MOVED_TOLD)):
        return None
    try:
        with open(os.path.join(data_root, MOVED_MARK), encoding="utf-8") as f:
            old = json.load(f)["from"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        _write_fsync(os.path.join(data_root, MOVED_TOLD), now_iso().encode("ascii"))
    except OSError:
        pass
    return {"from": old, "to": data_root}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def new_id(prefix):
    return prefix + secrets.token_hex(6)


ID_RE = re.compile(r"^[A-Z][0-9a-f]{12}$")


class HttpError(Exception):
    def __init__(self, status, msg):
        Exception.__init__(self, msg)
        self.status = status
        self.msg = msg


# ======================================================================================================
# Sandbox
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
        raise SandboxError("path escapes the data folder")
    return cand


# ======================================================================================================
# Atomic file primitives
# ======================================================================================================
def fsync_dir(d):
    if os.name == "nt":
        return False  # Windows cannot open directory handles from Python; NTFS journals the rename
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
        except PermissionError:  # AV scanner / indexer holding the file for a moment
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _write_fsync(path, data, mode="wb"):
    with open(path, mode) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def atomic_write_bytes(path, data):
    """tmp -> fsync -> re-read verify (length + SHA-256) -> os.replace -> dir fsync. Returns SHA-256."""
    sha = sha256_bytes(data)
    tmp = path + ".tmp"
    _write_fsync(tmp, data)
    with open(tmp, "rb") as f:
        back = f.read()
    if len(back) != len(data) or sha256_bytes(back) != sha:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise IOError("verification of %s failed (length/SHA-256 mismatch)" % tmp)
    _replace(tmp, path)
    fsync_dir(os.path.dirname(path) or ".")
    return sha


def append_fsync(path, data):
    _write_fsync(path, data, "ab")


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


# ======================================================================================================
# Vault (security_vault) + record codec
# ======================================================================================================
class VaultAdapter:
    """Wraps any object offering an encrypt/decrypt pair; proven by a round-trip probe."""
    PAIRS = (("encrypt", "decrypt"), ("encrypt_bytes", "decrypt_bytes"), ("encrypt_blob", "decrypt_blob"),
             ("seal", "unseal"), ("protect", "unprotect"))

    def __init__(self, target, name="security_vault"):
        self.name = name
        self.target = target
        for e, d in self.PAIRS:
            fe, fd = getattr(target, e, None), getattr(target, d, None)
            if not (callable(fe) and callable(fd)):
                continue
            probe = b"GorgonCovenant-probe-" + os.urandom(16)
            try:
                out = fe(probe)
                textual = isinstance(out, str)
                blob = out.encode("utf-8") if textual else bytes(out)
                if probe in blob:
                    continue  # pass-through is not encryption
                back = fd(blob.decode("utf-8") if textual else blob)
                if isinstance(back, str):
                    back = back.encode("latin-1")
                if bytes(back) != probe:
                    continue
            except Exception:
                continue
            self._e, self._d, self._textual, self.pair = fe, fd, textual, e
            return
        raise LookupError("no usable encrypt/decrypt pair")

    def encrypt(self, b):
        out = self._e(bytes(b))
        return out.encode("utf-8") if self._textual else bytes(out)

    def decrypt(self, b):
        back = self._d(b.decode("utf-8") if self._textual else bytes(b))
        return back.encode("latin-1") if isinstance(back, str) else bytes(back)


def find_vault():
    """-> (VaultAdapter | None, reason)."""
    sv = optional("security_vault")
    if sv is None:
        return None, "security_vault module not installed"
    for fn in ("available", "is_available"):
        f = getattr(sv, fn, None)
        if callable(f):
            try:
                if f() is False:
                    return None, "security_vault reports it is unavailable"
            except Exception:
                pass
    targets, why = [], ""
    for factory in ("default_vault", "get_vault", "open_vault", "local_vault"):
        f = getattr(sv, factory, None)
        if callable(f):
            try:
                targets.append(f())
            except Exception as e:
                why = why or str(e)[:200]
    targets.append(sv)
    for cls in ("Vault", "SecurityVault", "LocalVault"):
        C = getattr(sv, cls, None)
        if isinstance(C, type):
            try:
                targets.append(C())
            except Exception:
                pass
    for t in targets:
        try:
            return VaultAdapter(t), "security_vault"
        except Exception:
            continue
    return None, "security vault not open" + (": " + why if why else "")


class Codec:
    def __init__(self, vault):
        self.vault = vault

    @property
    def demo(self):
        return self.vault is None

    def seal(self, raw):
        if self.vault is None:
            return MAGIC_DEMO + raw
        return MAGIC_ENC + self.vault.encrypt(raw)

    def open(self, data):
        if self.vault is None:
            if not data.startswith(MAGIC_DEMO):
                raise IOError("not a demo-mode file")
            return data[len(MAGIC_DEMO):]
        if not data.startswith(MAGIC_ENC):
            raise IOError("file is not vault-encrypted")
        return self.vault.decrypt(data[len(MAGIC_ENC):])

    def dump(self, obj):
        return self.seal(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def load(self, data):
        return json.loads(self.open(data).decode("utf-8"))


# ======================================================================================================
# Audit trail
# ======================================================================================================
class AuditTrail:
    def __init__(self, dirpath, key):
        self.dir = dirpath
        os.makedirs(dirpath, exist_ok=True)
        self.log = os.path.join(dirpath, "audit.log")
        self.head = os.path.join(dirpath, "audit.head")
        self.key = key
        self._last = (0, GENESIS)
        self._load_last()

    def _mac(self, body):
        return hmac.new(self.key, canon(body), hashlib.sha256).hexdigest()

    def _lines(self):
        try:
            raw = read_bytes(self.log)
        except FileNotFoundError:
            return []
        return [ln for ln in raw.split(b"\n") if ln.strip()]

    def _load_last(self):
        last = (0, GENESIS)
        for ln in reversed(self._lines()):
            try:
                e = json.loads(ln.decode("utf-8"))
                last = (int(e["seq"]), str(e["hash"]))
                break
            except Exception:
                continue
        self._last = last

    def read_head(self):
        try:
            h = json.loads(read_bytes(self.head).decode("ascii"))
            return int(h["seq"]), str(h["hash"])
        except Exception:
            return None

    def append(self, event, txn=None):
        seq, prev = self._last
        body = {"seq": seq + 1, "ts": now_iso(), "txn": txn, "actor": event.get("actor", "system"),
                "action": event.get("action", "?"), "target": event.get("target", ""),
                "detail": event.get("detail") or {}, "files": event.get("files") or {}, "prev": prev}
        body["hash"] = self._mac(body)
        append_fsync(self.log, json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        self._last = (body["seq"], body["hash"])
        atomic_write_bytes(self.head, json.dumps({"seq": body["seq"], "hash": body["hash"]}).encode("ascii"))
        return body

    def has_txn(self, txn):
        needle = ('"txn":"%s"' % txn).encode("ascii")
        try:
            return needle in read_bytes(self.log)
        except FileNotFoundError:
            return False

    def entries(self, limit=200, offset=0):
        out = []
        for ln in self._lines():
            try:
                out.append(json.loads(ln.decode("utf-8")))
            except Exception:
                out.append({"unreadable": True})
        out.reverse()
        return out[offset:offset + limit], len(out)

    def recover(self):
        rep = {"torn_tail_bytes": 0, "head_advanced": False}
        try:
            raw = read_bytes(self.log)
        except FileNotFoundError:
            return rep
        if raw and not raw.endswith(b"\n"):
            cut = raw.rfind(b"\n") + 1
            frag = raw[cut:]
            try:
                json.loads(frag.decode("utf-8"))
                complete = True  # a full entry missing only its newline: keep it
            except Exception:
                complete = False
            if complete:
                append_fsync(self.log, b"\n")
            else:
                _write_fsync(os.path.join(self.dir, "audit.torn-%d.frag" % int(time.time() * 1000)), frag)
                with open(self.log, "r+b") as f:
                    f.truncate(cut)
                    f.flush()
                    os.fsync(f.fileno())
                rep["torn_tail_bytes"] = len(frag)
        self._load_last()
        head = self.read_head()
        if head is None or head[0] < self._last[0]:
            # crash between the log append and the head update: advance the head only if the chain is valid
            if self.verify(check_head=False)["ok"]:
                atomic_write_bytes(self.head, json.dumps({"seq": self._last[0], "hash": self._last[1]}).encode("ascii"))
                rep["head_advanced"] = True
        return rep

    def verify(self, check_head=True):
        problems = []
        prev, expect, n = GENESIS, 1, 0
        for i, ln in enumerate(self._lines(), 1):
            n += 1
            try:
                e = json.loads(ln.decode("utf-8"))
                if not isinstance(e, dict):
                    raise ValueError
            except Exception:
                problems.append("line %d is unreadable (edited or corrupted)" % i)
                continue
            seq = e.get("seq")
            try:
                if seq != expect or isinstance(seq, bool):
                    problems.append("line %d: sequence %r where %s was expected (entry deleted, inserted or "
                                    "reordered)" % (i, seq, expect))
                if e.get("prev") != prev:
                    problems.append("line %d (seq %r): chain link broken" % (i, seq))
                body = {k: v for k, v in e.items() if k != "hash"}
                got = str(e.get("hash", "")).encode("utf-8", "replace")
                if not hmac.compare_digest(self._mac(body).encode("ascii"), got):
                    problems.append("line %d (seq %r): content does not match its seal (entry altered)" % (i, seq))
            except Exception:  # anything malformed is tampering: report it, never crash the check
                problems.append("line %d: malformed entry (entry altered)" % i)
            prev = str(e.get("hash", ""))
            expect = (seq + 1) if (isinstance(seq, int) and not isinstance(seq, bool)) else expect + 1
        if check_head:
            head = self.read_head()
            if n and head is None:
                problems.append("audit head file missing")
            elif head is not None and (head[0] != expect - 1 or head[1] != prev):
                problems.append("log does not end where the head says (seq %d expected) - entries were "
                                "removed or appended outside the app" % head[0])
        return {"ok": not problems, "entries": n, "problems": problems[:50], "last_hash": prev}


# ======================================================================================================
# Store: journaled transactions
# ======================================================================================================
class Store:
    def __init__(self, root, codec, key, health=None):
        self.root = os.path.realpath(root)
        for sub in ("charts", "docs", "sys", "audit"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        self.codec = codec
        self.health = health or (lambda m: None)
        self.journal = os.path.join(self.root, JOURNAL_NAME)
        self.lock = threading.RLock()
        self.broken = None
        self.audit = AuditTrail(os.path.join(self.root, "audit"), key)

    def path(self, rel):
        return safe_join(self.root, rel)

    def exists(self, rel):
        return os.path.isfile(self.path(rel))

    def read_raw(self, rel):
        with self.lock:  # Windows: an open reader handle would make a concurrent os.replace fail
            return read_bytes(self.path(rel))

    def read_obj(self, rel, default=None):
        p = self.path(rel)
        with self.lock:
            if not os.path.isfile(p):
                return default
            data = read_bytes(p)
        return self.codec.load(data)

    # --- journal --------------------------------------------------------------------------------
    def _jappend(self, rec):
        """Append one record. If the journal does not end with a newline (a torn line from a crash or a failed
        write), a newline is written first, so this record can never be glued onto the broken one."""
        line = json.dumps(rec, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        try:
            with open(self.journal, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() > 0:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        line = b"\n" + line
        except FileNotFoundError:
            pass
        append_fsync(self.journal, line)

    def _cut_torn_tail(self):
        """Truncate the journal to its last complete newline-terminated line (fsync). -> bytes removed.
        A final record that is complete and verifies (only its newline was lost to the power cut) is kept and
        terminated instead: its transaction is then replayed, like any other complete journal record."""
        try:
            raw = read_bytes(self.journal)
        except FileNotFoundError:
            return 0
        if not raw or raw.endswith(b"\n"):
            return 0
        cut = raw.rfind(b"\n") + 1
        if self._parse_record(raw[cut:]) is not None:
            append_fsync(self.journal, b"\n")
            return 0
        with open(self.journal, "r+b") as f:
            f.truncate(cut)
            f.flush()
            os.fsync(f.fileno())
        return len(raw) - cut

    def _txn_record(self, files, event):
        flist = []
        for rel, data in files.items():
            self.path(rel)  # sandbox check before anything is written
            flist.append({"rel": rel, "sha": sha256_bytes(data), "len": len(data),
                          "b64": base64.b64encode(data).decode("ascii")})
        return {"op": "txn", "seq": secrets.token_hex(8), "ts": round(time.time(), 3), "files": flist,
                "event": event}

    def read_journal(self):
        try:
            raw = read_bytes(self.journal)
        except FileNotFoundError:
            return [], 0
        recs, torn = [], 0
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            rec = self._parse_record(line)
            if rec is None:
                torn += 1
                continue
            recs.append(rec)
        return recs, torn

    @staticmethod
    def _parse_record(line):
        """One journal line -> record dict, or None when it is torn or damaged (a txn's files must verify)."""
        try:
            rec = json.loads(line.decode("ascii"))
            if not isinstance(rec, dict):
                return None
            if rec.get("op") == "txn":
                for f in rec["files"]:
                    b = base64.b64decode(f["b64"])
                    if sha256_bytes(b) != f["sha"] or len(b) != f["len"]:
                        return None
            return rec
        except Exception:
            return None

    def compact(self):
        with self.lock:
            recs, _ = self.read_journal()
            commits = {r.get("seq") for r in recs if r.get("op") == "commit"}
            keep = [r for r in recs if r.get("op") == "txn" and r.get("seq") not in commits]
            if not keep and not os.path.exists(self.journal):
                return 0
            data = b"".join(json.dumps(r, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
                            for r in keep)
            atomic_write_bytes(self.journal, data)
            return len(keep)

    def commit(self, files, event):
        """files: {rel: bytes}. event: {actor, action, target, detail}. Returns the audit entry.
        If a step fails after the journal record is on disk, the store refuses further writes until restart:
        recover() then finishes that transaction, so no newer write can be overwritten by a late replay."""
        with self.lock:
            if self.broken:
                raise HttpError(503, "A save did not finish (%s). Restart Stark Covenant to complete it from the "
                                     "journal; nothing is lost." % self.broken)
            event = dict(event)
            event["files"] = {rel: sha256_bytes(d) for rel, d in files.items()}
            rec = self._txn_record(files, event)
            self._jappend(rec)
            try:
                for rel, data in files.items():
                    atomic_write_bytes(self.path(rel), data)
                entry = self.audit.append(event, txn=rec["seq"])
                self._jappend({"op": "commit", "seq": rec["seq"]})
            except BaseException as e:
                self.broken = type(e).__name__
                self.health("commit %s did not finish; store read-only until restart\n%s"
                            % (rec["seq"], traceback.format_exc()))
                raise
            try:
                self.compact()
            except Exception:  # the commit marker is durable; compaction is only housekeeping
                self.health("journal compaction failed\n" + traceback.format_exc())
            return entry

    def recover(self):
        rep = {"replayed": [], "audit_added": 0, "discarded_tmp": [], "torn_records": 0, "errors": [],
               "journal_torn_tail_bytes": 0}
        with self.lock:
            # FIRST: drop a torn final journal line, so no record appended below can be glued onto it
            rep["journal_torn_tail_bytes"] = self._cut_torn_tail()
            for dirpath, dirnames, filenames in os.walk(self.root):
                for fn in filenames:
                    if fn.endswith(".tmp"):
                        p = os.path.join(dirpath, fn)
                        try:
                            os.remove(p)
                            rep["discarded_tmp"].append(os.path.relpath(p, self.root))
                        except OSError as e:
                            rep["errors"].append("%s: %s" % (fn, e))
            rep["audit"] = self.audit.recover()
            recs, torn = self.read_journal()
            rep["torn_records"] = torn
            commits = {r.get("seq") for r in recs if r.get("op") == "commit"}
            for r in recs:
                if r.get("op") != "txn" or r["seq"] in commits:
                    continue
                try:
                    for f in r["files"]:
                        p = self.path(f["rel"])
                        cur = sha256_bytes(read_bytes(p)) if os.path.isfile(p) else None
                        if cur != f["sha"]:
                            atomic_write_bytes(p, base64.b64decode(f["b64"]))
                            rep["replayed"].append(f["rel"])
                    if not self.audit.has_txn(r["seq"]):
                        ev = dict(r["event"])
                        ev["detail"] = dict(ev.get("detail") or {}, recovered=True)
                        self.audit.append(ev, txn=r["seq"])
                        rep["audit_added"] += 1
                    self._jappend({"op": "commit", "seq": r["seq"]})
                except Exception as e:  # one bad record never blocks the others
                    rep["errors"].append("%s: %s" % (r.get("seq"), e))
            if not rep["errors"]:
                self.compact()
        rep["ok"] = not rep["errors"]
        return rep

    def simulate_crash(self, files, event, stage="partial_tmp"):
        """TEST HOOK: start commit() and stop as if the power went out.
        stages: journal_only | partial_tmp | torn_journal | after_files (files written, no audit/commit).
        Like a real crash, the store refuses further writes afterwards (restart to recover)."""
        with self.lock:
            self.broken = "simulated crash"
            event = dict(event)
            event["files"] = {rel: sha256_bytes(d) for rel, d in files.items()}
            rec = self._txn_record(files, event)
            line = json.dumps(rec, ensure_ascii=True, separators=(",", ":")).encode("ascii")
            if stage == "torn_journal":
                append_fsync(self.journal, line[: len(line) // 2])
                return {"stage": stage}
            append_fsync(self.journal, line + b"\n")
            if stage == "partial_tmp":
                rel, data = next(iter(files.items()))
                _write_fsync(self.path(rel) + ".tmp", data[: len(data) // 2])
            elif stage == "after_files":
                for rel, data in files.items():
                    atomic_write_bytes(self.path(rel), data)
            return {"stage": stage, "seq": rec["seq"]}

    def integrity(self):
        """Audit chain + every file's current SHA-256 against the last SHA the audit trail recorded."""
        res = self.audit.verify()
        latest = {}
        for ln in self.audit._lines():
            try:
                e = json.loads(ln.decode("utf-8"))
                latest.update(e.get("files") or {})
            except Exception:
                continue
        changed = []
        for rel, sha in sorted(latest.items()):
            try:
                p = self.path(rel)
                cur = sha256_bytes(read_bytes(p)) if os.path.isfile(p) else None
            except Exception:
                cur = None
            if cur != sha:
                changed.append(rel)
        res["files_checked"] = len(latest)
        res["files_changed_outside_app"] = changed[:50]
        if changed:
            res["ok"] = False
            res["problems"] = res["problems"] + ["%d record file(s) differ from what the audit trail sealed"
                                                 % len(changed)]
        return res


# ======================================================================================================
# Plans / licensing
# ======================================================================================================
PLANS = {
    "baby": {"label": "Baby", "price": "$0", "max_users": 1, "roles": False, "lan": False},
    "clinic": {"label": "Clinic", "price": "$79/mo", "max_users": 15, "roles": True, "lan": True},
    "humanitarian": {"label": "Humanitarian", "price": "$50 one-time", "max_users": 15, "roles": True, "lan": True},
    "hospital": {"label": "Hospital", "price": "$249/mo", "max_users": 100, "roles": True, "lan": True},
    "hospital_plus": {"label": "Hospital Plus", "price": "$999/mo", "max_users": 1000, "roles": True,
                      "lan": True},
    # one practitioner, full single-user features (encrypted vault, exports, interop, comms), $30/mo
    "individual": {"label": "Individual", "price": "$30/mo", "max_users": 1, "roles": False, "lan": False},
    # large-scale operations: per-seat price falls as size grows (see license_guard.FAIRNESS_RULES)
    "health_network": {"label": "Health Network", "price": "$3,499/mo", "max_users": 5000, "roles": True,
                       "lan": True},
    "regional": {"label": "Regional Health System", "price": "$8,999/mo", "max_users": 20000, "roles": True,
                 "lan": True},
    "national": {"label": "National", "price": "$19,999/mo", "max_users": 100000, "roles": True,
                 "lan": True},
}
# Pricing 2026-12, one-time licences (approved by the owner 2026-11-19). These plans exist in the app while
# license_guard.pricing_proposal_active() is true (license_guard.PRICING_PROPOSAL_APPROVED). Every plan above
# stays: existing keys keep working.
PROPOSED_PLANS = {
    "solo": {"label": "Solo", "price": "$50 one-time", "max_users": 1, "roles": False, "lan": False},
    "practice": {"label": "Practice", "price": "$200 one-time", "max_users": 4, "roles": True,
                 "lan": True, "per_clinician": True},
}
PRACTICE_EXTRA_USERS = 2     # Practice: one account per clinician paid for, plus an admin and a front-desk account


def pricing_switch_on():
    lg = optional("license_guard")
    try:
        return bool(lg is not None and lg.pricing_proposal_active())
    except Exception:
        return False


def apply_pricing_switch():
    """Add the proposed plans when the switch is on (idempotent). -> True when they are available."""
    if pricing_switch_on():
        for k, v in PROPOSED_PLANS.items():
            PLANS.setdefault(k, dict(v))
        PLANS["humanitarian"]["price"] = "$50 one-time"
    return "solo" in PLANS


apply_pricing_switch()


def features_for_plan(plan):
    """Feature gates of a plan (any spelling, e.g. license_guard's 'CLINIC'). Humanitarian == Clinic."""
    p = PLANS[normalize_plan(plan)]
    return {"max_users": p["max_users"], "roles": p["roles"], "lan": p["lan"]}


def normalize_plan(v):
    s = re.sub(r"[^a-z0-9]", "", str(v or "").lower())
    if not s:
        return "baby"
    if "humanit" in s:
        return "humanitarian"
    if "national" in s:
        return "national"
    if "regional" in s:
        return "regional"
    if "network" in s:
        return "health_network"
    if "practice" in s and "practice" in PLANS:      # proposed plans: only while the pricing switch is on
        return "practice"
    if "solo" in s and "solo" in PLANS:
        return "solo"
    if "individual" in s or s == "30":
        return "individual"
    if "hospital" in s or "enterprise" in s:
        if "999" in s or "plus" in s or "enterprise" in s or "2" in s.replace("hospital", ""):
            return "hospital_plus"
        return "hospital"
    if "clinic" in s or s in ("pro", "79"):
        return "clinic"
    return "baby"


def _plan_from_result(res):
    if res is None:
        return None
    if isinstance(res, str):
        return res
    get = (lambda k: res.get(k)) if isinstance(res, dict) else (lambda k: getattr(res, k, None))
    if get("ok") is False or get("valid") is False:
        return "baby"
    for k in ("plan", "tier", "mode", "edition", "product"):
        v = get(k)
        if v:
            return str(v)
    return None


def resolve_plan(explicit=None):
    key, source, message = "baby", "default", "Baby plan (no licence module installed)."
    clinicians = seats = None
    if explicit:
        key, source, message = normalize_plan(explicit), "explicit", "Plan set by the launcher."
    else:
        lg = optional("license_guard")
        if lg is not None:
            message = "Baby plan (no valid licence found)."
            for fn in ("check", "license_status", "current_plan", "get_plan", "status"):
                f = getattr(lg, fn, None)
                if not callable(f):
                    continue
                try:
                    res = f()
                    p = _plan_from_result(res)
                    if p:
                        key, source = normalize_plan(p), "license_guard"
                        clinicians = res.get("clinicians") if isinstance(res, dict) else None
                        seats = res.get("seats") if isinstance(res, dict) else None
                        msg = res.get("message") if isinstance(res, dict) else None
                        message = str(msg or "%s plan (licence verified)." % PLANS[key]["label"])
                        break
                except Exception as e:
                    message = "Licence check failed (%s); Baby plan active." % type(e).__name__
                    break
    info = dict(PLANS[key])
    if info.get("per_clinician") and isinstance(clinicians, int) and clinicians > 0:
        info["clinicians"] = clinicians                  # per-clinician licence: users follow the clinicians paid for
        info["max_users"] = clinicians + PRACTICE_EXTRA_USERS
    elif info.get("per_clinician") and isinstance(seats, int) and seats > 0:
        info["max_users"] = seats                        # flat Practice (4) or Clinic (10) licence: users = seats
    info.update(key=key, source=source, message=message)
    return info


# ======================================================================================================
# Users, roles, secrets
# ======================================================================================================
ROLES = ("clinician", "nurse", "front_desk", "admin")
ROLE_PERMS = {
    "clinician": {"demo.read", "demo.write", "clin.read", "encounter.write", "vitals.write", "note.write",
                  "note.sign", "allergy.write", "meds.write", "problems.write", "doc.read", "doc.write",
                  "ai.use", "sched.read", "sched.write", "bill.read", "bill.write", "measure.write", "plan.write",
                  "note.cosign", "form.write", "form.design", "psynote.write", "release.write"},
    "nurse": {"demo.read", "demo.write", "clin.read", "encounter.write", "vitals.write", "note.write",
              "note.sign", "allergy.write", "doc.read", "doc.write", "ai.use", "sched.read", "sched.write",
              "measure.write", "form.write", "release.write"},
    # front desk: scheduling, billing and intake forms (billing shows diagnosis codes on invoices, never notes or
    # vitals). form.design: clinicians and admins design form templates; form.write: complete and read client forms.
    # psynote.write: psychotherapy notes (clinicians, author-only); release.write: releases of information and the
    # disclosure log (care_suite)
    "front_desk": {"demo.read", "demo.write", "encounter.write", "doc.write", "sched.read", "sched.write",
                   "bill.read", "bill.write", "form.write", "release.write"},
    "admin": {"demo.read", "users.manage", "audit.read", "backup.run", "form.design"},
}
ROLE_PERMS["owner"] = set().union(*ROLE_PERMS.values())
ITEM_KINDS = {
    "allergies": {"perm": "allergy.write", "required": "substance",
                  "fields": {"substance": 200, "reaction": 500, "severity": 20, "status": 30, "noted": 10},
                  "choices": {"severity": ("unknown", "mild", "moderate", "severe"),
                              "status": ("active", "inactive", "entered_in_error")}},
    "medications": {"perm": "meds.write", "required": "name",
                    "fields": {"name": 200, "dose": 100, "route": 60, "frequency": 100, "status": 30,
                               "start": 10, "end": 10, "notes": 1000},
                    "choices": {"status": ("active", "stopped", "entered_in_error")}},
    "problems": {"perm": "problems.write", "required": "description",
                 "fields": {"description": 300, "code": 40, "onset": 10, "status": 30, "notes": 1000},
                 "choices": {"status": ("active", "resolved", "inactive", "entered_in_error")}},
}
VITAL_RANGES = {"bp_sys": (40, 300), "bp_dia": (20, 200), "pulse": (20, 250), "temp_c": (25, 45),
                "resp": (4, 80), "spo2": (50, 100), "weight_kg": (0.3, 500), "height_cm": (20, 260),
                "glucose_mmol": (0.5, 50)}
VITAL_NAMES = {"bp_sys": "Systolic blood pressure (mmHg)", "bp_dia": "Diastolic blood pressure (mmHg)",
               "pulse": "Pulse (per minute)", "temp_c": "Temperature (°C)", "resp": "Breathing rate (per minute)",
               "spo2": "Oxygen saturation (%)", "weight_kg": "Weight (kg)", "height_cm": "Height (cm)",
               "glucose_mmol": "Blood glucose (mmol/L)"}      # plain names in error messages (stored values are metric)
ENCOUNTER_TYPES = ("visit", "telehealth", "phone", "home", "inpatient", "other")
NOTE_TEMPLATES = ("soap", "dap", "birp", "progress", "intake", "mse")  # the browser builds the sections into the text
DOC_MIME = {"application/pdf", "image/png", "image/jpeg", "image/gif", "text/plain", "text/csv",
            "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def hash_secret(secret, salt=None):
    salt = salt or os.urandom(16)
    h = hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                       maxmem=64 * 1024 * 1024, dklen=32)
    return {"alg": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": salt.hex(), "hash": h.hex()}


def check_secret(secret, rec):
    try:
        h = hashlib.scrypt(str(secret).encode("utf-8"), salt=bytes.fromhex(rec["salt"]), n=int(rec["n"]),
                           r=int(rec["r"]), p=int(rec["p"]), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(h.hex(), rec["hash"])
    except Exception:
        return False


_DUMMY = None


def _dummy_check():
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_secret("dummy-password-for-timing")
    check_secret("x", _DUMMY)


def _clean(v, maxlen=200, multiline=False, field="text"):
    if v is None:
        return ""
    if not isinstance(v, (str, int, float)):
        raise HttpError(400, "%s must be text." % field)
    s = str(v).encode("utf-8", "replace").decode("utf-8")
    if multiline:
        s = "".join(c for c in s if c in "\n\t" or ord(c) >= 32).replace("\r", "")
    else:
        s = "".join(c if ord(c) >= 32 else " " for c in s)
    s = s.strip()
    if len(s) > maxlen:
        raise HttpError(400, "%s is too long (max %d characters)." % (field, maxlen))
    return s


def _date(v, field="date", required=False, not_future=False):
    s = _clean(v, 10, field=field)
    if not s:
        if required:
            raise HttpError(400, "%s is required (YYYY-MM-DD)." % field)
        return ""
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HttpError(400, "%s must be a date YYYY-MM-DD." % field)
    if not_future and d > date.today():
        raise HttpError(400, "%s cannot be in the future." % field)
    if d.year < 1880:
        raise HttpError(400, "%s is too far in the past." % field)
    return s


def _check_password(pw):
    if not isinstance(pw, str) or len(pw) < 10 or len(pw) > 200:
        raise HttpError(400, "Password must be 10 to 200 characters.")


def _check_pin(pin):
    if not isinstance(pin, str) or not re.match(r"^\d{4,12}$", pin):
        raise HttpError(400, "PIN must be 4 to 12 digits.")


def _username(v):
    s = _clean(v, 40, field="username").lower()
    if not re.match(r"^[a-z0-9][a-z0-9._-]{1,39}$", s):
        raise HttpError(400, "Username: 2-40 letters, digits, dot, dash or underscore.")
    return s


def safe_doc_name(name):
    n = _clean(name, 300, field="file name")
    n = re.split(r"[\\/]", n)[-1]
    n = "".join(c for c in n if c not in '<>:"|?*' and ord(c) >= 32).strip(" .")
    return (n or "document")[:120]


# ======================================================================================================
# Clinic: records, users, permissions (no HTTP here; the server wraps it)
# ======================================================================================================
class _ClosedStore:
    """Stands in for the Store until the secure vault is open: any use is a clean 503, never a crash."""

    def __bool__(self):
        return False

    def __getattr__(self, name):
        raise HttpError(503, "Open the secure vault first.")


CLOSED_STORE = _ClosedStore()


class Clinic:
    CHARTS, DOCS, USERS, INDEX, KEY = "charts", "docs", "sys/users.bin", "sys/index.bin", "sys/audit.key"
    SETTINGS = "sys/settings.bin"
    WORKLIST = "sys/worklist.bin"     # the Today index (practice_suite builds it from the charts on first use)

    def __init__(self, data_root=None, vault="auto", plan=None, demo=False, test_hooks=False,
                 idle_seconds=None, seed_demo=True, vault_root=None):
        """vault: "auto" (security_vault: auto-unlock with this PC's share + a plugged-in USB stick, otherwise
        wait for the unlock / create screen), an object with encrypt/decrypt, or None (DEMO).
        state: "ready" | "locked" (vault exists, needs USB / recovery code) | "no_vault" (first run) |
               "confirm_code" (vault just created, recovery code not yet confirmed)."""
        self.data_root = os.path.realpath(data_root or default_data_root())
        os.makedirs(self.data_root, exist_ok=True)
        self.moved = moved_notice(self.data_root)     # the data folder was copied to its Stark name: say so once
        self.test_hooks = test_hooks
        self.idle_seconds = int(idle_seconds or env_setting("IDLE") or IDLE_SECONDS)
        self._health_lock = threading.Lock()
        self._open_lock = threading.RLock()
        self.plan = resolve_plan(plan)
        self._plan_at = time.time()
        self._plan_explicit = plan
        self.fatal = None
        self.state = "starting"
        self.store = CLOSED_STORE
        self.vault = None
        self.vault_obj = None
        self.vault_reason = ""
        self.vault_root = vault_root or env_setting("VAULT") or None
        self.demo = False
        self.demo_chosen = False
        self.store_name = ""
        self.recovery = {}
        self.backup_dir = None
        self._seed = seed_demo
        self._pending_codes = None
        self._unlock_fails = 0
        self._unlock_block_until = 0.0
        self.sessions = {}
        self._sess_lock = threading.Lock()
        self._unknown_fails = {}
        self._learners = {}
        self.dirty = False
        self.on_ready = []  # callables run once the store is open (e.g. re-mount emergency_comms with the vault)
        if demo:
            self.open_store(None, "demo mode requested")
        elif vault is None:
            self.open_store(None, "no vault supplied")
        elif vault == "auto":
            self._auto_vault()
        else:
            adapter = vault if isinstance(vault, VaultAdapter) else VaultAdapter(vault, "injected")
            self.open_store(adapter, adapter.name)

    # --- secure vault: auto-unlock, unlock screen, first-run creation ---------------------------------
    def _auto_vault(self):
        sv = optional("security_vault")
        if sv is None or not isinstance(getattr(sv, "Vault", None), type):
            adapter, reason = find_vault()  # some other vault module shape
            self.open_store(adapter, reason)
            return
        try:
            backend = sv.get_backend() if callable(getattr(sv, "get_backend", None)) else True
        except Exception:
            backend = None
        if backend is None:
            self.open_store(None, "this computer offers no operating-system encryption (AES-GCM), so only DEMO "
                                  "mode is possible")
            return
        try:
            v = sv.Vault(self.vault_root) if self.vault_root else sv.Vault()
        except Exception as e:
            self.open_store(None, "security vault could not start: %s" % str(e)[:200])
            return
        self.vault_obj = v
        if not v.exists():
            self.state, self.vault_reason = "no_vault", "No secure vault has been created on this computer yet."
            return
        try:
            v.unlock()
            self.open_store(VaultAdapter(v), "security_vault")
        except Exception as e:
            self.state = "locked"
            self.vault_reason = "The secure vault is locked: %s" % str(e)[:300]

    def _need_vault(self, *states):
        if self.vault_obj is None or self.state not in states:
            raise HttpError(409, "That step is not available right now. Reload the page to see the current step.")

    def vault_status(self):
        sv = optional("security_vault")
        drives = []
        try:
            drives = [str(d) for d in sv.removable_drives()] if sv is not None else []
        except Exception:
            pass
        usb_share = None
        if self.vault_obj is not None and self.state == "locked":
            try:
                usb_share = self.vault_obj.find_usb_share()
            except Exception:
                usb_share = None
        return {"state": self.state, "reason": self.vault_reason, "drives": drives, "usb_key_found": bool(usb_share),
                "vault_folder": getattr(self.vault_obj, "root", None), "demo": self.demo,
                "encrypted_records_exist": os.path.isdir(os.path.join(self.data_root, "records", "audit"))}

    def vault_unlock(self, recovery_code="", passphrase="", usb=""):
        """Open an existing vault with any two keys: this PC's share, the USB key, the recovery code."""
        self._need_vault("locked")
        if time.time() < self._unlock_block_until:
            raise HttpError(429, "Too many attempts. Wait a minute and try again.")
        codes = [c for c in [str(recovery_code or "").strip()] if c]
        try:
            self.vault_obj.unlock(usb=str(usb) or None, recovery_codes=codes, passphrase=str(passphrase) or None)
        except Exception as e:
            self._unlock_fails += 1
            if self._unlock_fails >= 10:
                self._unlock_fails, self._unlock_block_until = 0, time.time() + 60
            raise HttpError(403, "The vault did not open: %s" % str(e)[:300])
        self._unlock_fails = 0
        self.open_store(VaultAdapter(self.vault_obj), "security_vault")
        return self.vault_status()

    def vault_create(self, usb, passphrase=""):
        """First run: make the vault (2 of 3 keys: this PC, the USB stick, a printed recovery code)."""
        self._need_vault("no_vault")
        if not usb:
            raise HttpError(400, "Choose the USB stick to hold the second key.")
        try:
            codes = self.vault_obj.create(str(usb), k=2, n=3, passphrase=str(passphrase) or None)
        except Exception as e:
            raise HttpError(400, "The vault could not be created: %s" % str(e)[:300])
        self._pending_codes = list(codes)
        self.state = "confirm_code"
        return {"recovery_codes": self._pending_codes}

    def vault_confirm(self, code):
        """The person types the recovery code back; only then are records opened."""
        self._need_vault("confirm_code")
        sv = optional("security_vault")
        try:
            typed = sv.parse_recovery_code(code)
            want = sv.parse_recovery_code(self._pending_codes[0])
        except Exception as e:
            raise HttpError(400, "That is not the recovery code shown: %s" % str(e)[:200])
        if typed[:2] != want[:2]:
            raise HttpError(400, "That is not the recovery code shown. Compare each group and try again.")
        self._pending_codes = None
        self.open_store(VaultAdapter(self.vault_obj), "security_vault")
        return self.vault_status()

    def choose_demo(self):
        """The person explicitly chose DEMO mode (synthetic patients, separate folder, nothing encrypted)."""
        if self.state == "ready":
            raise HttpError(409, "Records are already open.")
        self.demo_chosen = True
        self.open_store(None, "DEMO mode chosen")
        return self.vault_status()

    def open_store(self, adapter, reason):
        with self._open_lock:
            if self.state == "ready":
                return
            self._open_store(adapter, reason)
            self.state = "ready"
        for cb in list(self.on_ready):
            try:
                cb()
            except Exception:
                self.health("on_ready callback failed\n" + traceback.format_exc())

    def _open_store(self, adapter, reason):
        seed_demo = self._seed
        self.vault, self.vault_reason = adapter, reason
        self.demo = adapter is None
        self.store_name = "demo_synthetic" if self.demo else "records"
        store_dir = os.path.join(self.data_root, self.store_name)
        os.makedirs(os.path.join(store_dir, "sys"), exist_ok=True)
        self.codec = Codec(adapter)
        key = self._load_key(store_dir)
        self.sig_key = hmac.new(key, b"gorgon-covenant-signature-v1", hashlib.sha256).digest()
        self.store = Store(store_dir, self.codec, key, self.health)
        self.recovery = self.store.recover()
        if self.recovery["replayed"] or self.recovery["audit_added"] or self.recovery["torn_records"] or \
                self.recovery["errors"] or self.recovery["audit"]["torn_tail_bytes"] or \
                self.recovery["journal_torn_tail_bytes"]:
            self.health("recovery: %s" % json.dumps(self.recovery))
        try:
            self._users()
            self._index()
        except Exception as e:
            self.fatal = ("The records cannot be opened with the current security vault (%s). Nothing was "
                          "changed. Restore the vault key or a backup." % type(e).__name__)
            self.health("startup: %s\n%s" % (self.fatal, traceback.format_exc()))
        if self.demo and seed_demo and not self.fatal and not self.store.exists(self.INDEX):
            self._seed_demo()
        self.backup_dir = os.path.join(self.data_root, "backups", self.store_name)

    # --- infrastructure --------------------------------------------------------------------------
    def health(self, msg):
        line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(msg).rstrip())
        with self._health_lock:
            try:
                append_fsync(os.path.join(self.data_root, HEALTH_LOG), line.encode("utf-8", "replace"))
            except OSError:
                pass

    def _load_key(self, store_dir):
        p = safe_join(store_dir, self.KEY)
        if os.path.isfile(p):
            try:
                return self.codec.open(read_bytes(p))
            except Exception:
                self.fatal = "The audit key cannot be opened with the current security vault."
                self.health("audit key unreadable\n" + traceback.format_exc())
                return os.urandom(32)  # verification will then fail loudly; nothing is overwritten
        if os.path.isfile(os.path.join(store_dir, "audit", "audit.log")):
            self.health("audit key missing while an audit log exists - a new key is made; the old log will "
                        "fail verification")
        key = os.urandom(32)
        atomic_write_bytes(p, self.codec.seal(key))
        return key

    def refresh_plan(self):
        if time.time() - self._plan_at > 300:
            self.plan = resolve_plan(self._plan_explicit)
            self._plan_at = time.time()
        return self.plan

    def _commit(self, files, actor, action, target="", detail=None):
        if self.fatal:
            raise HttpError(503, self.fatal)
        entry = self.store.commit(files, {"actor": actor, "action": action, "target": target,
                                          "detail": detail or {}})
        self.dirty = True
        return entry

    def _users(self):
        return self.store.read_obj(self.USERS, {"users": {}})

    def _index(self):
        return self.store.read_obj(self.INDEX, {"patients": {}})

    @staticmethod
    def _chart_rel(pid):
        if not isinstance(pid, str) or not ID_RE.match(pid) or pid[0] != "P":
            raise HttpError(400, "Bad patient id.")
        return "charts/%s.bin" % pid

    def _load_chart(self, pid):
        rel = self._chart_rel(pid)
        chart = self.store.read_obj(rel)
        if chart is None:
            raise HttpError(404, "No such patient.")
        return chart

    # --- permissions ----------------------------------------------------------------------------
    def perms(self, user):
        out = set()
        for r in user.get("roles", ()):
            out |= ROLE_PERMS.get(r, set())
        return out

    def require(self, user, perm):
        if perm not in self.perms(user):
            raise HttpError(403, "Your role does not allow this (%s)." % perm)

    @staticmethod
    def public_user(u, perms=None):
        d = {"id": u["id"], "username": u["username"], "display": u.get("display") or u["username"],
             "roles": list(u.get("roles", [])), "active": u.get("active", True),
             "locked": u.get("locked_until", 0) > time.time(), "has_pin": bool(u.get("pin")),
             "prefs": dict(u.get("prefs") or {})}
        if u.get("example"):
            d["example"] = True
        if perms is not None:
            d["perms"] = sorted(perms)
        return d

    # --- setup / users --------------------------------------------------------------------------
    def needs_setup(self):
        return not self._users()["users"]

    def setup(self, username, password, pin, display=""):
        with self.store.lock:
            users = self._users()
            if users["users"]:
                raise HttpError(409, "Setup is already done.")
            roles = ["owner"] if self.plan["key"] == "baby" else ["admin"]
            u = self._new_user(username, password, pin, display, roles)
            users["users"][u["id"]] = u
            self._commit({self.USERS: self.codec.dump(users)}, u["id"], "setup.first_user", u["id"],
                         {"roles": roles})
            return self.public_user(u)

    def _new_user(self, username, password, pin, display, roles):
        _check_password(password)
        _check_pin(pin)
        return {"id": new_id("U"), "username": _username(username),
                "display": _clean(display, 80, field="display name") or _username(username),
                "roles": roles, "pw": hash_secret(password), "pin": hash_secret(pin), "active": True,
                "failed": 0, "locked_until": 0, "pin_failed": 0, "pin_locked_until": 0, "created": now_iso()}

    def create_user(self, actor, username, password, pin, roles, display=""):
        self.require(actor, "users.manage")
        roles = self._roles(roles)
        with self.store.lock:
            users = self._users()
            plan = self.refresh_plan()
            if len(users["users"]) >= plan["max_users"]:
                raise HttpError(402, "The %s plan allows %d user(s). Upgrade to add more."
                                % (plan["label"], plan["max_users"]))
            uname = _username(username)
            if any(x["username"] == uname for x in users["users"].values()):
                raise HttpError(409, "That username exists.")
            u = self._new_user(uname, password, pin, display, roles)
            users["users"][u["id"]] = u
            self._commit({self.USERS: self.codec.dump(users)}, actor["id"], "user.create", u["id"],
                         {"roles": roles})
            return self.public_user(u)

    def _roles(self, roles):
        if not isinstance(roles, list) or not roles:
            raise HttpError(400, "Choose at least one role.")
        if not self.plan["roles"]:
            raise HttpError(402, "Roles need the Clinic, Humanitarian or Hospital plan.")
        bad = [r for r in roles if r not in ROLES]
        if bad:
            raise HttpError(400, "Unknown role.")
        return sorted(set(roles))

    def update_user(self, actor, uid, roles=None, active=None, password=None, unlock=False, pin=None):
        self.require(actor, "users.manage")
        with self.store.lock:
            users = self._users()
            u = users["users"].get(uid)
            if u is None:
                raise HttpError(404, "No such user.")
            changed = []
            if roles is not None:
                u["roles"] = self._roles(roles)
                changed.append("roles")
            if active is not None:
                u["active"] = bool(active)
                changed.append("active")
            if password:
                _check_password(password)
                u["pw"] = hash_secret(password)
                changed.append("password_reset")
            if pin:
                _check_pin(pin)
                u["pin"] = hash_secret(pin)
                changed.append("pin_reset")
            if unlock:
                u.update(failed=0, locked_until=0, pin_failed=0, pin_locked_until=0)
                changed.append("unlock")
            admins = [x for x in users["users"].values() if x.get("active", True)
                      and ({"admin", "owner"} & set(x.get("roles", [])))]
            if not admins:
                raise HttpError(409, "At least one active admin must remain.")
            self._commit({self.USERS: self.codec.dump(users)}, actor["id"], "user.update", uid,
                         {"changed": changed, "roles": u["roles"]})
            if active is False or password:
                self.end_sessions_for(uid)
            return self.public_user(u)

    def list_users(self, actor):
        self.require(actor, "users.manage")
        return [self.public_user(u) for u in self._users()["users"].values()]

    def change_own_secret(self, actor, current_password, new_password=None, new_pin=None):
        with self.store.lock:
            users = self._users()
            u = users["users"][actor["id"]]
            if not check_secret(current_password or "", u["pw"]):
                raise HttpError(403, "Current password is wrong.")
            changed = []
            if new_password:
                _check_password(new_password)
                u["pw"] = hash_secret(new_password)
                changed.append("password")
            if new_pin:
                _check_pin(new_pin)
                u["pin"] = hash_secret(new_pin)
                changed.append("pin")
            if not changed:
                raise HttpError(400, "Nothing to change.")
            self._commit({self.USERS: self.codec.dump(users)}, u["id"], "user.self_change", u["id"],
                         {"changed": changed})
            return {"changed": changed}

    # --- language + region preferences (display only; stored values never change) --------------------
    # --- updates (signed auto_updater; checks only when the clinic allowed them) ----------------------
    @property
    def app_dir(self):
        if getattr(self, "_app_dir", None) is None:
            # the install folder holds the update settings; a test run or a source checkout (it has the build
            # script, which never ships) keeps them in its own data folder instead
            dev = self.test_hooks or os.path.exists(os.path.join(_HERE, "deploy_chart.ps1"))
            self._app_dir = os.path.join(self.data_root, "app_dir_for_tests") if dev else _HERE
            os.makedirs(self._app_dir, exist_ok=True)
        return self._app_dir

    def update_info(self, actor):
        self.require(actor, "users.manage")
        up = optional("auto_updater")
        if up is None:
            return {"available": False, "version": VERSION, "channel": release_channel(self.app_dir)}
        st = up.read_settings(self.app_dir)
        return {"available": True, "version": VERSION, "channel": release_channel(self.app_dir),
                "checks": st["checks"], "feed": st["url"]}

    def update_settings_set(self, actor, checks):
        self.require(actor, "users.manage")
        up = optional("auto_updater")
        if up is None:
            raise HttpError(501, "The updater is not installed.")
        if not isinstance(checks, bool):
            raise HttpError(400, "checks must be true or false.")
        cfg = up._read_json(os.path.join(self.app_dir, up.CONFIG_NAME)) or {}
        channel = None if cfg.get("channel") in up.CHANNELS else release_channel(self.app_dir)
        up.write_settings(self.app_dir, checks=checks, channel=channel)
        self._commit({}, actor["id"], "updates.settings", "updates", {"checks": checks})
        return self.update_info(actor)

    def update_check_now(self, actor):
        self.require(actor, "users.manage")
        up = optional("auto_updater")
        if up is None:
            raise HttpError(501, "The updater is not installed.")
        if not up.read_settings(self.app_dir)["checks"]:
            raise HttpError(409, "Update checks are off. Turn them on first.")
        status, detail = up.check_for_updates(self.app_dir, VERSION, timeout=5.0)
        self._commit({}, actor["id"], "updates.check", "updates", {"status": status})
        out = self.update_info(actor)
        out.update(status=status, detail=detail)
        return out

    def _clean_locale_prefs(self, prefs):
        if not isinstance(prefs, dict):
            raise HttpError(400, "Preferences must be an object.")
        out = {}
        i18n, rg = optional("i18n"), optional("regional")
        if prefs.get("locale") not in (None, ""):
            loc = i18n.canonical(prefs["locale"]) if i18n else None
            if not loc or loc not in i18n.LOCALES:
                raise HttpError(400, "Unknown language.")
            out["locale"] = loc
        if rg is not None:
            try:
                out.update(rg.clean_prefs({k: v for k, v in prefs.items() if k != "locale"}))
            except ValueError as e:
                raise HttpError(400, "Region settings: %s." % e)
        return out

    def locale_settings(self):
        """Clinic default language + region (used before sign-in and for users without their own choice)."""
        if self.state != "ready":
            return {}
        try:
            return dict((self.store.read_obj(self.SETTINGS, {}) or {}).get("locale") or {})
        except Exception:
            return {}

    def set_locale_settings(self, actor, prefs):
        self.require(actor, "users.manage")
        clean = self._clean_locale_prefs(prefs)
        with self.store.lock:
            cur = self.store.read_obj(self.SETTINGS, {}) or {}
            cur["locale"] = clean
            self._commit({self.SETTINGS: self.codec.dump(cur)}, actor["id"], "settings.locale", "clinic",
                         {"keys": sorted(clean)})
        return {"prefs": clean, "region": self.effective_region(clean)}

    def effective_region(self, prefs=None, locale=None):
        rg, i18n = optional("regional"), optional("i18n")
        if rg is None:
            return None
        merged = dict(self.locale_settings())
        merged.update({k: v for k, v in (prefs or {}).items()})
        loc = (merged.get("locale") or locale or "en")
        loc_region = (i18n.LOCALES.get(loc, {}).get("region") if i18n else None)
        return rg.client_region({k: v for k, v in merged.items() if k != "locale"}, loc_region)

    def set_prefs(self, actor, prefs):
        clean = self._clean_locale_prefs(prefs)
        with self.store.lock:
            users = self._users()
            u = users["users"][actor["id"]]
            cur = dict(u.get("prefs") or {})
            for k in list(prefs.keys() if isinstance(prefs, dict) else []):
                if prefs.get(k) in (None, "") and k in cur:
                    cur.pop(k)
            cur.update(clean)
            u["prefs"] = cur
            self._commit({self.USERS: self.codec.dump(users)}, u["id"], "user.prefs", u["id"],
                         {"keys": sorted(cur)})
        return {"prefs": cur, "region": self.effective_region(cur)}

    def get_prefs(self, actor):
        p = dict(actor.get("prefs") or {})
        return {"prefs": p, "region": self.effective_region(p)}

    # --- licence: the Licence screen (a licence problem never locks anyone out of records) -----------
    @staticmethod
    def _lic_public(st):
        st = st or {}
        return {k: st.get(k) for k in ("ok", "plan", "plan_name", "code", "message", "expires", "days_left",
                                        "devices", "seats", "scale")}

    def licence_info(self, actor):
        self.require(actor, "users.manage")
        lg = optional("license_guard")
        if lg is None:
            return {"available": False, "plan": self.refresh_plan()}
        st = lg.check()
        info = getattr(lg, "PLAN_INFO", {})
        plans = [{"plan": k, "name": v.get("name", k), "price_usd": v.get("price_usd", 0),
                  "period": v.get("period", ""), "seats": v.get("seats", 0), "max_scale": v.get("max_scale", "")}
                 for k, v in info.items()]
        return {"available": True, "status": self._lic_public(st), "device_code": lg.device_code(),
                "plans": plans, "fairness": list(getattr(lg, "FAIRNESS_RULES", ())), "plan": self.refresh_plan()}

    def licence_activate(self, actor, token):
        self.require(actor, "users.manage")
        lg = optional("license_guard")
        if lg is None:
            raise HttpError(501, "The licence module is not installed.")
        token = re.sub(r"\s+", "", str(token or ""))
        if not token:
            raise HttpError(400, "Paste the licence key first.")
        if len(token) > 8192 or not re.fullmatch(r"GC1\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", token):
            raise HttpError(400, "That is not a Stark Covenant licence key (it starts with GC1.).")
        st = lg.activate(token, lg.default_license_path())
        self._plan_at = 0
        self._commit({}, actor["id"], "licence.activate", "licence",
                     {"ok": bool(st.get("ok")), "code": st.get("code"), "plan": st.get("plan"),
                      "token_sha": lg.token_hash(token)[:16]})
        return {"status": self._lic_public(st), "plan": self.refresh_plan()}

    def licence_deactivate(self, actor):
        """Give this device's seat back and stop using the key here (Baby plan until a key is activated again;
        the released licence file is kept next to the old one so it can be moved to another device)."""
        self.require(actor, "users.manage")
        lg = optional("license_guard")
        if lg is None:
            raise HttpError(501, "The licence module is not installed.")
        path = lg.default_license_path()
        try:
            with open(path, encoding="utf-8") as f:
                token = str((json.load(f) or {}).get("token") or "")
        except (OSError, ValueError):
            token = ""
        if not token:
            raise HttpError(409, "No licence key is active on this device.")
        left = lg.deactivate(token, path)
        try:
            os.replace(path, path + ".released")
        except OSError:
            pass
        self._plan_at = 0
        self._commit({}, actor["id"], "licence.deactivate", "licence", {"devices_left": left})
        return {"devices_left": left, "plan": self.refresh_plan()}

    # --- login / sessions -------------------------------------------------------------------------
    def login(self, username, password):
        if self.fatal:
            raise HttpError(503, self.fatal)
        uname = str(username or "").strip().lower()[:40]
        now = time.time()
        with self.store.lock:
            users = self._users()
            u = next((x for x in users["users"].values() if x["username"] == uname), None)
            if u is None:
                _dummy_check()
                n = self._unknown_fails.get(uname, 0) + 1
                self._unknown_fails[uname] = n
                if len(self._unknown_fails) > 5000:
                    self._unknown_fails.clear()
                self._commit({}, "anonymous", "login.fail", "u:" + sha256_bytes(uname.encode("utf-8"))[:12])
                raise HttpError(401, "Wrong username or password.")
            if u.get("locked_until", 0) > now:
                mins = int((u["locked_until"] - now) // 60) + 1
                raise HttpError(423, "This account is locked after repeated failures. Try again in %d "
                                     "minute(s) or ask an admin to unlock it." % mins)
            if not check_secret(password or "", u["pw"]):
                u["failed"] = u.get("failed", 0) + 1
                action = "login.fail"
                if u["failed"] >= LOCK_FAILS:
                    u["failed"], u["locked_until"], action = 0, now + LOCK_SECONDS, "account.locked"
                self._commit({self.USERS: self.codec.dump(users)}, u["id"], action, u["id"])
                if action == "account.locked":
                    raise HttpError(423, "Too many failed attempts. The account is locked for %d minutes."
                                    % (LOCK_SECONDS // 60))
                raise HttpError(401, "Wrong username or password.")
            if not u.get("active", True):
                raise HttpError(403, "This account is disabled.")
            files = {}
            if u.get("failed"):
                u["failed"] = 0
                files[self.USERS] = self.codec.dump(users)
            self._commit(files, u["id"], "login.ok", u["id"])
        return self._new_session(u)

    def _new_session(self, u):
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(24), time.time()
        with self._sess_lock:
            self.sessions[token] = {"uid": u["id"], "csrf": csrf, "last": now, "created": now}
        return token, csrf, self.public_user(u, self.perms(u))

    # --- "Try with an example clinic" (first minute): DEMO practice area + its own example account ------------
    def example_user(self):
        """The example clinic's account, or None. Only ever in the DEMO store (synthetic patients, own folder)."""
        if self.state != "ready" or not self.demo or self.fatal:
            return None
        return next((u for u in self._users()["users"].values() if u.get("example")), None)

    def example_signin(self):
        """One click from the start screen: open the DEMO practice area (separate folder, synthetic patients,
        nothing encrypted) and sign in to its example account. Refused while encrypted records are open; never
        reads or changes them. The example account's password is random and never shown (this button is the way
        back in); its signing PIN is EXAMPLE_PIN, shown on screen, and signs synthetic notes only."""
        if self.state == "ready" and not self.demo:
            raise HttpError(409, "Your clinic's records are open, so the example clinic is not available now.")
        if self.state != "ready":
            self.demo_chosen = True
            self.open_store(None, "example clinic chosen")
        if self.fatal:
            raise HttpError(503, self.fatal)
        with self.store.lock:
            users = self._users()
            u = next((x for x in users["users"].values() if x.get("example")), None)
            if u is None:
                if users["users"]:
                    raise HttpError(409, "This practice area already has its own accounts. Sign in with one of them.")
                u = self._new_user(EXAMPLE_USER, secrets.token_urlsafe(24), EXAMPLE_PIN, "Example clinician", ["owner"])
                u["example"] = True
                users["users"][u["id"]] = u
                self._commit({self.USERS: self.codec.dump(users)}, u["id"], "setup.example_user", u["id"],
                             {"roles": u["roles"]})
            if not u.get("active", True):
                raise HttpError(403, "This account is disabled.")
            self._commit({}, u["id"], "login.example", u["id"])
        return self._new_session(u)

    def session(self, token, touch=True):
        """-> (session dict, user dict) or raises 401 (missing, expired or idle-locked)."""
        if not token:
            raise HttpError(401, "Please sign in.")
        now = time.time()
        with self._sess_lock:
            s = self.sessions.get(token)
            if s is None:
                raise HttpError(401, "Please sign in.")
            if now - s["last"] > self.idle_seconds:
                del self.sessions[token]
                raise HttpError(401, "Locked after inactivity. Please sign in again.")
            if touch:
                s["last"] = now
        u = self._users()["users"].get(s["uid"])
        if u is None or not u.get("active", True):
            self.logout(token)
            raise HttpError(401, "Please sign in.")
        return s, u

    def logout(self, token):
        with self._sess_lock:
            return self.sessions.pop(token, None) is not None

    def end_sessions_for(self, uid):
        with self._sess_lock:
            for t in [t for t, s in self.sessions.items() if s["uid"] == uid]:
                del self.sessions[t]

    def verify_pin(self, user, pin):
        """E-signature check. 5 wrong PINs lock signing for 15 minutes."""
        now = time.time()
        with self.store.lock:
            users = self._users()
            u = users["users"][user["id"]]
            if u.get("pin_locked_until", 0) > now:
                raise HttpError(423, "Signing is locked after repeated wrong PINs. Try again later.")
            if check_secret(str(pin or ""), u["pin"]):
                if u.get("pin_failed"):
                    u["pin_failed"] = 0
                    self._commit({self.USERS: self.codec.dump(users)}, u["id"], "pin.ok", u["id"])
                return True
            u["pin_failed"] = u.get("pin_failed", 0) + 1
            if u["pin_failed"] >= LOCK_FAILS:
                u["pin_failed"], u["pin_locked_until"] = 0, now + LOCK_SECONDS
            self._commit({self.USERS: self.codec.dump(users)}, u["id"], "pin.fail", u["id"])
        raise HttpError(403, "Wrong PIN - nothing was signed.")

    # --- patients ---------------------------------------------------------------------------------
    DEMO_FIELDS = {"name": 120, "dob": 10, "sex": 30, "mrn": 40, "phone": 40, "email": 120, "address": 300,
                   "emergency_contact": 200, "notes": 1000, "given_name": 80, "family_name": 80,
                   "addr_line1": 120, "addr_line2": 120, "addr_city": 80, "addr_region": 80, "addr_postal": 20,
                   "addr_country": 2}

    def _demographics(self, d, base=None):
        if not isinstance(d, dict):
            raise HttpError(400, "demographics required")
        out = dict(base or {})
        for k, n in self.DEMO_FIELDS.items():
            if k in d:
                out[k] = _date(d[k], "date of birth", required=True, not_future=True) if k == "dob" else \
                    _clean(d[k], n, multiline=(k == "notes"), field=k)
        rg = optional("regional")
        if out.get("addr_country"):
            out["addr_country"] = out["addr_country"].upper()
            if rg is not None and out["addr_country"] not in rg.REGIONS:
                raise HttpError(400, "Unknown country code.")
        if rg is not None:
            region = out.get("addr_country") or self.locale_settings().get("region") or ""
            prof = rg.region_profile(region) or {}
            if not out.get("name") and out.get("given_name") and out.get("family_name"):
                out["name"] = rg.display_name(out, prof.get("name_order", "given"))
            if not out.get("address") and any(out.get("addr_" + p) for p in rg.ADDRESS_PARTS):
                parts = {p: out.get("addr_" + p, "") for p in rg.ADDRESS_PARTS}
                out["address"] = ", ".join(rg.format_address(parts, prof.get("address", "generic")))[:300]
            # E.164 kept next to what was typed (the typed text is never rewritten)
            out.pop("phone_e164", None)
            e164 = rg.to_e164(out.get("phone"), region or "") if out.get("phone") else None
            if e164:
                out["phone_e164"] = e164
        if not out.get("name"):
            raise HttpError(400, "Patient name is required.")
        if not out.get("dob"):
            raise HttpError(400, "Date of birth is required (YYYY-MM-DD).")
        return out

    def _save_chart(self, chart, user_id, action, detail, extra_files=None, index=True):
        chart["rev"] = chart.get("rev", 0) + 1
        chart["updated"] = now_iso()
        files = {self._chart_rel(chart["id"]): self.codec.dump(chart)}
        if index:
            idx = self._index()
            d = chart["demographics"]
            idx["patients"][chart["id"]] = {"name": d.get("name", ""), "dob": d.get("dob", ""),
                                            "mrn": d.get("mrn", ""), "synthetic": chart.get("synthetic", False)}
            files[self.INDEX] = self.codec.dump(idx)
        files.update(extra_files or {})
        return self._commit(files, user_id, action, chart["id"], detail)

    def create_patient(self, user, demographics, _actor=None):
        if _actor is None:
            self.require(user, "demo.write")
        demo = self._demographics(demographics)
        if self.demo and "(synthetic)" not in demo["name"].lower():
            demo["name"] += " (synthetic)"
        chart = {"id": new_id("P"), "created": now_iso(), "created_by": _actor or user["id"], "rev": 0,
                 "synthetic": self.demo, "demographics": demo, "encounters": [], "notes": [], "vitals": [],
                 "allergies": [], "medications": [], "problems": [], "documents": []}
        with self.store.lock:
            self._save_chart(chart, _actor or user["id"], "patient.create", {})
        return chart["id"]

    def update_patient(self, user, pid, demographics):
        self.require(user, "demo.write")
        with self.store.lock:
            chart = self._load_chart(pid)
            old = chart["demographics"]
            new = self._demographics(demographics, base=old)
            if chart.get("synthetic") and "(synthetic)" not in new["name"].lower():
                new["name"] += " (synthetic)"
            chart.setdefault("demographics_history", []).append({"at": now_iso(), "by": user["id"], "old": old})
            chart["demographics"] = new
            self._save_chart(chart, user["id"], "patient.update",
                             {"fields": sorted(k for k in new if new.get(k) != old.get(k))})
        return self.view(user, pid, audit=False)

    def search(self, user, q=""):
        self.require(user, "demo.read")
        q = str(q or "").strip().lower()
        out = []
        for pid, p in self._index()["patients"].items():
            hay = " ".join([p.get("name", ""), p.get("dob", ""), p.get("mrn", "")]).lower()
            if not q or q in hay:
                out.append(dict(p, id=pid))
        out.sort(key=lambda p: p["name"].lower())
        return out[:500]

    def note_signature(self, pid, note):
        content = sha256_bytes(canon({"title": note.get("title", ""), "text": note.get("text", ""),
                                      "encounter_id": note.get("encounter_id"),
                                      "ai_provenance": note.get("ai_provenance", [])}))
        sig = hmac.new(self.sig_key, canon({"patient": pid, "note": note["id"], "content": content,
                                            "signer": note.get("signed_by"), "at": note.get("signed_at")}),
                       hashlib.sha256).hexdigest()
        return content, sig

    def addendum_signature(self, pid, note_id, a):
        content = sha256_bytes(a["text"].encode("utf-8"))
        sig = hmac.new(self.sig_key, canon({"patient": pid, "note": note_id, "addendum": a["id"],
                                            "content": content, "signer": a["author"], "at": a["at"]}),
                       hashlib.sha256).hexdigest()
        return content, sig

    def view(self, user, pid, audit=True):
        perms = self.perms(user)
        if "demo.read" not in perms:
            raise HttpError(403, "Your role cannot open charts.")
        chart = self._load_chart(pid)
        out = {"id": chart["id"], "created": chart["created"], "rev": chart["rev"],
               "synthetic": chart.get("synthetic", False), "demographics": chart["demographics"]}
        if perms & {"clin.read", "encounter.write"}:
            out["encounters"] = chart["encounters"]
        if "clin.read" in perms:
            notes = []
            for n in chart["notes"]:
                n = dict(n)
                if n["status"] == "signed":
                    content, sig = self.note_signature(pid, n)
                    n["signature_valid"] = (content == n.get("content_sha")
                                            and hmac.compare_digest(sig, n.get("signature", "")))
                    adds = []
                    for a in n.get("addenda", []):
                        a = dict(a)
                        c2, s2 = self.addendum_signature(pid, n["id"], a)
                        a["signature_valid"] = c2 == a.get("content_sha") and hmac.compare_digest(s2, a.get("signature", ""))
                        adds.append(a)
                    n["addenda"] = adds
                    cs = n.get("cosign")
                    if cs and cs.get("status") == "done":
                        cs = dict(cs)
                        cs["signature_valid"] = (cs.get("content_sha") == n.get("content_sha") and n["signature_valid"]
                                                 and hmac.compare_digest(self.cosign_signature(
                                                     pid, n["id"], cs.get("content_sha"), cs), cs.get("signature", "")))
                        n["cosign"] = cs
                notes.append(n)
            plans = []
            for p in chart.get("treatment_plans", []):
                p = dict(p)
                if p.get("signature"):
                    content, sig = self.plan_signature(pid, p)
                    p["signature_valid"] = content == p.get("content_sha") and hmac.compare_digest(sig, p["signature"])
                plans.append(p)
            out.update(notes=notes, vitals=chart["vitals"], allergies=chart["allergies"],
                       medications=chart["medications"], problems=chart["problems"],
                       measures=chart.get("measures", []), treatment_plans=plans)
        if "doc.read" in perms:
            out["documents"] = chart["documents"]
        if audit:
            self._commit({}, user["id"], "chart.view", pid)
        return out

    # --- encounters, vitals ------------------------------------------------------------------------
    def add_encounter(self, user, pid, etype="visit", reason="", when=None):
        self.require(user, "encounter.write")
        if etype not in ENCOUNTER_TYPES:
            raise HttpError(400, "Unknown encounter type.")
        with self.store.lock:
            chart = self._load_chart(pid)
            e = {"id": new_id("E"), "date": _date(when, "encounter date") or date.today().isoformat(),
                 "type": etype, "reason": _clean(reason, 300, field="reason"), "created_by": user["id"],
                 "created_by_name": user.get("display", ""), "created": now_iso()}
            chart["encounters"].append(e)
            self._save_chart(chart, user["id"], "encounter.create", {"encounter": e["id"]}, index=False)
        return e

    def _encounter(self, chart, eid):
        e = next((x for x in chart["encounters"] if x["id"] == eid), None)
        if e is None:
            raise HttpError(404, "No such encounter.")
        return e

    def add_vitals(self, user, pid, eid, values):
        self.require(user, "vitals.write")
        if not isinstance(values, dict):
            raise HttpError(400, "vitals required")
        rec = {}
        for k, (lo, hi) in VITAL_RANGES.items():
            v = values.get(k)
            if v in (None, ""):
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise HttpError(400, "%s must be a number." % VITAL_NAMES[k])
            if not lo <= f <= hi:
                raise HttpError(400, "%s %s is outside the accepted entry range %s-%s (check units)." % (VITAL_NAMES[k], v, lo, hi))
            rec[k] = f
        if not rec:
            raise HttpError(400, "Enter at least one vital sign.")
        with self.store.lock:
            chart = self._load_chart(pid)
            self._encounter(chart, eid)
            v = {"id": new_id("V"), "encounter_id": eid, "at": now_iso(), "by": user["id"],
                 "by_name": user.get("display", ""), "values": rec, "status": "active",
                 "comment": _clean(values.get("comment"), 300, field="comment")}
            chart["vitals"].append(v)
            self._save_chart(chart, user["id"], "vitals.add", {"encounter": eid, "vitals": v["id"]}, index=False)
        return v

    def vitals_error(self, user, pid, vid, reason):
        """Vitals are never edited: a wrong entry is marked entered-in-error with a reason."""
        self.require(user, "vitals.write")
        reason = _clean(reason, 300, field="reason")
        if not reason:
            raise HttpError(400, "A reason is required.")
        with self.store.lock:
            chart = self._load_chart(pid)
            v = next((x for x in chart["vitals"] if x["id"] == vid), None)
            if v is None:
                raise HttpError(404, "No such vitals entry.")
            if v["status"] != "active":
                raise HttpError(409, "Already marked entered-in-error.")
            v.update(status="entered_in_error", error_reason=reason, error_by=user["id"], error_at=now_iso())
            self._save_chart(chart, user["id"], "vitals.entered_in_error", {"vitals": vid}, index=False)
        return v

    # --- notes + sign-off lock --------------------------------------------------------------------
    def _note(self, chart, nid):
        n = next((x for x in chart["notes"] if x["id"] == nid), None)
        if n is None:
            raise HttpError(404, "No such note.")
        return n

    def _wl(self, change):
        """Apply change(worklist) to the Today index when it exists -> extra files for the same transaction."""
        if not self.store.exists(self.WORKLIST):
            return {}
        w = self.store.read_obj(self.WORKLIST, {}) or {}
        change(w)
        return {self.WORKLIST: self.codec.dump(w)}

    def add_note(self, user, pid, eid, text, title="Progress note", template=None):
        self.require(user, "note.write")
        if template not in (None, "") and template not in NOTE_TEMPLATES:
            raise HttpError(400, "Unknown note template.")
        with self.store.lock:
            chart = self._load_chart(pid)
            self._encounter(chart, eid)
            n = {"id": new_id("N"), "encounter_id": eid, "title": _clean(title, 120, field="title") or "Note",
                 "text": _clean(text, 100000, multiline=True, field="note"), "author": user["id"],
                 "author_name": user.get("display", ""), "created": now_iso(), "updated": now_iso(),
                 "status": "draft", "ai_provenance": [], "addenda": [], "signed_by": None, "signed_at": None}
            if template:
                n["template"] = template
            chart["notes"].append(n)
            wl = self._wl(lambda w: w.setdefault("drafts", {}).__setitem__(n["id"], {
                "patient": pid, "author": user["id"], "title": n["title"], "created": n["created"]}))
            self._save_chart(chart, user["id"], "note.create", {"note": n["id"], "encounter": eid}, extra_files=wl,
                             index=False)
        return n

    def _editable(self, user, n):
        if n["status"] == "signed":
            raise HttpError(423, "This note is signed and locked. Add a signed addendum instead.")
        if n["author"] != user["id"]:
            raise HttpError(403, "Only the author can change a draft note.")

    def update_note(self, user, pid, nid, text=None, title=None):
        self.require(user, "note.write")
        with self.store.lock:
            chart = self._load_chart(pid)
            n = self._note(chart, nid)
            self._editable(user, n)
            if text is not None:
                n["text"] = _clean(text, 100000, multiline=True, field="note")
            if title is not None:
                n["title"] = _clean(title, 120, field="title") or n["title"]
            n["updated"] = now_iso()
            self._save_chart(chart, user["id"], "note.update", {"note": nid,
                                                                  "text_sha": sha256_bytes(n["text"].encode("utf-8"))},
                             index=False)
        return n

    def sign_note(self, user, pid, nid, pin):
        self.require(user, "note.sign")
        with self.store.lock:
            chart = self._load_chart(pid)
            n = self._note(chart, nid)
            self._editable(user, n)
            if not n["text"].strip():
                raise HttpError(400, "An empty note cannot be signed.")
            self.verify_pin(user, pin)
            chart = self._load_chart(pid)  # re-read: verify_pin may have committed the users file
            n = self._note(chart, nid)
            n["status"] = "signed"
            n["signed_by"] = user["id"]
            n["signed_by_name"] = user.get("display", "")
            n["signed_at"] = now_iso()
            n["content_sha"], n["signature"] = self.note_signature(pid, n)
            users = self._users()["users"]
            sup = users.get((users.get(user["id"]) or {}).get("supervisor") or "")
            if sup is not None and sup.get("active", True) and sup["id"] != user["id"]:
                # supervised clinician: the note is signed and locked, and also waits for the supervisor's PIN
                n["cosign"] = {"status": "pending", "required": sup["id"],
                               "required_name": sup.get("display") or sup["username"]}

            def wl(w):
                w.setdefault("drafts", {}).pop(nid, None)
                if n.get("cosign"):
                    w.setdefault("cosign", {})[nid] = {"patient": pid, "author": user["id"], "title": n["title"],
                                                       "supervisor": n["cosign"]["required"], "signed_at": n["signed_at"]}
            self._save_chart(chart, user["id"], "note.sign", {"note": nid, "content_sha": n["content_sha"],
                                                              "cosign": bool(n.get("cosign"))},
                             extra_files=self._wl(wl), index=False)
        self._learn(user, chart, n)
        return n

    def cosign_signature(self, pid, nid, content_sha, c):
        return hmac.new(self.sig_key, canon({"patient": pid, "note": nid, "cosign": True, "content": content_sha,
                                             "signer": c["by"], "at": c["at"]}), hashlib.sha256).hexdigest()

    def cosign_note(self, user, pid, nid, pin):
        """Supervision: the supervisor named on a signed note co-signs it with their own PIN. The note text stays
        locked; the co-signature seals the note's content hash, the co-signer and the time."""
        self.require(user, "note.cosign")
        with self.store.lock:
            chart = self._load_chart(pid)
            n = self._note(chart, nid)
            cs = n.get("cosign") or {}
            if n["status"] != "signed" or cs.get("status") != "pending":
                raise HttpError(409, "This note is not waiting for a co-signature.")
            if cs.get("required") != user["id"] or n["author"] == user["id"]:
                raise HttpError(403, "Only the supervisor named on this note can co-sign it.")
            self.verify_pin(user, pin)
            chart = self._load_chart(pid)
            n = self._note(chart, nid)
            cs = n["cosign"]
            cs.update(status="done", by=user["id"], by_name=user.get("display", ""), at=now_iso(),
                      content_sha=n["content_sha"])
            cs["signature"] = self.cosign_signature(pid, nid, n["content_sha"], cs)
            self._save_chart(chart, user["id"], "note.cosign", {"note": nid, "content_sha": n["content_sha"]},
                             extra_files=self._wl(lambda w: w.setdefault("cosign", {}).pop(nid, None)), index=False)
        return n

    def plan_signature(self, pid, p):
        """Treatment plan version seal (practice_suite): content hash + HMAC over the signer and the time."""
        content = sha256_bytes(canon({k: p.get(k) for k in ("title", "problems", "strengths", "start", "review",
                                                            "status", "goals", "version")}))
        sig = hmac.new(self.sig_key, canon({"patient": pid, "plan": p["id"], "content": content,
                                            "signer": p.get("signed_by"), "at": p.get("signed_at")}),
                       hashlib.sha256).hexdigest()
        return content, sig

    def add_addendum(self, user, pid, nid, text, pin):
        self.require(user, "note.sign")
        text = _clean(text, 50000, multiline=True, field="addendum")
        if not text:
            raise HttpError(400, "The addendum is empty.")
        with self.store.lock:
            chart = self._load_chart(pid)
            n = self._note(chart, nid)
            if n["status"] != "signed":
                raise HttpError(409, "Addenda are for signed notes. Edit the draft instead.")
            self.verify_pin(user, pin)
            chart = self._load_chart(pid)
            n = self._note(chart, nid)
            a = {"id": new_id("A"), "text": text, "author": user["id"], "author_name": user.get("display", ""),
                 "at": now_iso()}
            a["content_sha"], a["signature"] = self.addendum_signature(pid, nid, a)
            n.setdefault("addenda", []).append(a)
            self._save_chart(chart, user["id"], "note.addendum", {"note": nid, "addendum": a["id"],
                                                                  "content_sha": a["content_sha"]}, index=False)
        return a

    # --- allergies / medications / problems ------------------------------------------------------
    def _item_fields(self, kind, data, base=None):
        spec = ITEM_KINDS[kind]
        if not isinstance(data, dict):
            raise HttpError(400, "item required")
        out = dict(base or {})
        for k, n in spec["fields"].items():
            if k in data:
                if n == 10:
                    out[k] = _date(data[k], k)
                else:
                    out[k] = _clean(data[k], n, multiline=(k == "notes"), field=k)
                ch = spec.get("choices", {}).get(k)
                if ch and out[k] and out[k] not in ch:
                    raise HttpError(400, "%s must be one of: %s" % (k, ", ".join(ch)))
        for k, ch in spec.get("choices", {}).items():
            if not out.get(k):
                out[k] = ch[0]
        if not out.get(spec["required"]):
            raise HttpError(400, "%s is required." % spec["required"])
        return out

    def add_item(self, user, pid, kind, data):
        if kind not in ITEM_KINDS:
            raise HttpError(400, "Unknown list.")
        self.require(user, ITEM_KINDS[kind]["perm"])
        fields = self._item_fields(kind, data)
        with self.store.lock:
            chart = self._load_chart(pid)
            item = dict(fields, id=new_id("I"), recorded_by=user["id"], recorded_by_name=user.get("display", ""),
                        recorded_at=now_iso(), history=[])
            chart[kind].append(item)
            self._save_chart(chart, user["id"], "%s.add" % kind, {"item": item["id"]}, index=False)
        return item

    def update_item(self, user, pid, kind, iid, data):
        """Items are versioned, never deleted: the previous version is kept in history."""
        if kind not in ITEM_KINDS:
            raise HttpError(400, "Unknown list.")
        self.require(user, ITEM_KINDS[kind]["perm"])
        with self.store.lock:
            chart = self._load_chart(pid)
            item = next((x for x in chart[kind] if x["id"] == iid), None)
            if item is None:
                raise HttpError(404, "No such entry.")
            old = {k: item.get(k) for k in ITEM_KINDS[kind]["fields"]}
            new = self._item_fields(kind, data, base=old)
            item["history"].append(dict(old, changed_at=now_iso(), changed_by=user["id"]))
            item.update(new)
            self._save_chart(chart, user["id"], "%s.update" % kind, {"item": iid}, index=False)
        return item

    # --- documents --------------------------------------------------------------------------------
    def add_document(self, user, pid, name, mime, b64, eid=None):
        self.require(user, "doc.write")
        try:
            data = base64.b64decode(str(b64 or ""), validate=True)
        except Exception:
            raise HttpError(400, "File data must be base64.")
        if not data:
            raise HttpError(400, "The file is empty.")
        if len(data) > MAX_DOC:
            raise HttpError(413, "Documents are limited to %d MiB." % (MAX_DOC // (1024 * 1024)))
        mime = str(mime or "").lower()
        if mime not in DOC_MIME:
            mime = "application/octet-stream"
        with self.store.lock:
            chart = self._load_chart(pid)
            if eid:
                self._encounter(chart, eid)
            did = new_id("D")
            d = {"id": did, "name": safe_doc_name(name), "mime": mime, "size": len(data),
                 "sha256": sha256_bytes(data), "encounter_id": eid or None, "uploaded_by": user["id"],
                 "uploaded_by_name": user.get("display", ""), "at": now_iso()}
            chart["documents"].append(d)
            self._save_chart(chart, user["id"], "document.add", {"document": did, "sha256": d["sha256"]},
                             extra_files={"docs/%s.bin" % did: self.codec.seal(data)}, index=False)
        return d

    def get_document(self, user, pid, did):
        self.require(user, "doc.read")
        if not isinstance(did, str) or not ID_RE.match(did) or did[0] != "D":
            raise HttpError(400, "Bad document id.")
        chart = self._load_chart(pid)
        d = next((x for x in chart["documents"] if x["id"] == did), None)
        if d is None:
            raise HttpError(404, "No such document.")
        data = self.codec.open(self.store.read_raw("docs/%s.bin" % did))
        if sha256_bytes(data) != d["sha256"]:
            raise HttpError(500, "Document failed its integrity check.")
        self._commit({}, user["id"], "document.view", pid, {"document": did})
        return d, data

    # --- self_learner / AI (suggestions only; sign-off required) -----------------------------------
    def _learner(self, user):
        sl = optional("self_learner")
        if sl is None:
            return None
        lr = self._learners.get(user["id"])
        if lr is None:
            try:
                lr = sl.SelfLearner(os.path.join(self.data_root, self.store_name + "_learner"), user["id"],
                                    vault=self.vault, use_ollama=not self.test_hooks)
            except Exception:
                self.health("self_learner init failed\n" + traceback.format_exc())
                return None
            self._learners[user["id"]] = lr
        return lr

    def _learn(self, user, chart, note):
        try:
            lr = self._learner(user)
            if lr is not None:
                lr.learn_from_note(dict(note, author=user["id"], patient_name=chart["demographics"].get("name", "")))
        except Exception:
            self.health("self_learner learn failed\n" + traceback.format_exc())

    def ai_suggest(self, user, pid, prefix=""):
        self.require(user, "ai.use")
        lr = self._learner(user)
        if lr is None:
            return {"available": False, "suggestions": [], "label": AI_LABEL}
        out = []
        try:
            if prefix:
                out += [s.to_dict() for s in lr.autocomplete(prefix)]
            out += [s.to_dict() for s in lr.suggest_templates("note")]
            if pid:
                out += [s.to_dict() for s in lr.check_record(self.view(user, pid, audit=False))]
        except Exception:
            self.health("self_learner suggest failed\n" + traceback.format_exc())
        return {"available": True, "suggestions": out[:20], "label": AI_LABEL}

    def ai_accept(self, user, pid, nid, text, kind, suggestion_id, pin):
        """Explicit clinician sign-off (PIN) is required before suggested text enters a DRAFT note."""
        self.require(user, "ai.use")
        self.require(user, "note.write")
        sl = optional("self_learner")
        if sl is None:
            raise HttpError(501, "The self-learning helper is not installed.")
        text = _clean(text, 5000, multiline=True, field="suggestion")
        if not text:
            raise HttpError(400, "Empty suggestion.")
        with self.store.lock:
            chart = self._load_chart(pid)
            n = self._note(chart, nid)
            self._editable(user, n)
            self.verify_pin(user, pin)
            sug = sl.Suggestion(kind=_clean(kind, 40) or "suggestion", text=text)
            frag = sl.accept_suggestion(sug, sl.SignOff(clinician_id=user["id"], attested=True, at=now_iso()))
            chart = self._load_chart(pid)
            n = self._note(chart, nid)
            n["text"] = (n["text"].rstrip() + "\n" if n["text"].strip() else "") + frag["text"]
            prov = dict(frag["provenance"], client_suggestion_id=_clean(suggestion_id, 64))
            n.setdefault("ai_provenance", []).append(prov)
            n["updated"] = now_iso()
            self._save_chart(chart, user["id"], "note.ai_accept", {"note": nid,
                                                                    "suggestion": prov.get("suggestion_id")},
                             index=False)
        return n

    def interop_export(self, user, pid):
        """One patient as a FHIR R4 transaction Bundle and an HL7 v2.5.1 ADT^A08 message (interop_bridge)."""
        self.require(user, "clin.read")
        ib = optional("interop_bridge")
        if ib is None or not callable(getattr(ib, "record_to_fhir_bundle", None)) \
                or not callable(getattr(ib, "build_adt", None)):
            raise HttpError(501, "The interoperability module is not installed.")
        chart = self.view(user, pid, audit=False)
        record = chart_to_interop_record(chart)
        try:
            bundle = ib.record_to_fhir_bundle(record, timestamp=now_iso())
            hl7 = ib.build_adt(record, event="A08", facility="GORGON_COVENANT")
        except ValueError as e:
            raise HttpError(400, "This chart cannot be exported: %s." % e)
        issues = []
        if callable(getattr(ib, "validate_fhir", None)):
            issues = [i for i in ib.validate_fhir(bundle) if i.get("severity") == "error"]
        self._commit({}, user["id"], "chart.export", pid, {"formats": ["fhir-r4", "hl7v2-adt-a08"],
                                                           "fhir_errors": len(issues)})
        return {"fhir": bundle, "hl7v2": hl7, "fhir_errors": issues[:20], "synthetic": chart.get("synthetic", False),
                "notice": "Structural checks only; not validated by the official HL7 validator."}

    # --- audit + backups --------------------------------------------------------------------------
    def audit_entries(self, user, limit=200, offset=0):
        self.require(user, "audit.read")
        return self.store.audit.entries(limit, offset)

    def verify(self, user=None):
        if user is not None:
            self.require(user, "audit.read")
        with self.store.lock:
            return self.store.integrity()

    def backup(self, user=None, reason="manual"):
        if user is not None:
            self.require(user, "backup.run")
        os.makedirs(self.backup_dir, exist_ok=True)
        buf = io.BytesIO()
        with self.store.lock:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for dirpath, dirnames, filenames in os.walk(self.store.root):
                    for fn in sorted(filenames):
                        if fn.endswith(".tmp"):
                            continue
                        p = os.path.join(dirpath, fn)
                        z.write(p, os.path.relpath(p, self.store.root).replace(os.sep, "/"))
            self.dirty = False
        blob = buf.getvalue()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        name = ("covenant-%s.gcbak" % stamp) if not self.demo else ("covenant-DEMO-%s.zip" % stamp)
        out = os.path.join(self.backup_dir, name)
        atomic_write_bytes(out, self.codec.seal(blob) if not self.demo else blob)
        # never prune the backup just written (names made in the same second sort by their random suffix)
        kept = [b for b in self.list_backups() if b["name"] != name]
        for old in kept[BACKUP_KEEP - 1:]:
            try:
                os.remove(os.path.join(self.backup_dir, old["name"]))
            except OSError:
                pass
        self._commit({}, user["id"] if user else "system", "backup.create", "",
                     {"name": name, "sha256": sha256_bytes(read_bytes(out)), "reason": reason})
        return {"name": name, "size": os.path.getsize(out), "encrypted": not self.demo}

    def list_backups(self):
        try:
            names = [n for n in os.listdir(self.backup_dir) if n.startswith("covenant-") and not n.endswith(".tmp")]
        except FileNotFoundError:
            return []
        names.sort(reverse=True)
        return [{"name": n, "size": os.path.getsize(os.path.join(self.backup_dir, n))} for n in names]

    # --- demo data ----------------------------------------------------------------------------------
    def _seed_demo(self):
        """Synthetic, obviously fictional patients for DEMO mode only."""
        ppl = [("Ada Example", "1958-03-14", "female", [("Penicillin (synthetic)", "rash", "moderate")],
                [("Amlodipine", "5 mg", "oral", "once daily")], ["Hypertension (synthetic history)"]),
               ("Ben Testcase", "1990-11-02", "male", [], [], ["Seasonal allergies (synthetic history)"]),
               ("Chidi Sample", "2015-06-21", "male", [("Peanut (synthetic)", "hives", "severe")], [], []),
               ("Dana Placeholder", "1972-01-30", "female", [], [("Metformin", "500 mg", "oral", "twice daily")],
                ["Type 2 diabetes (synthetic history)"]),
               ("Eun-ji Fixture", "1984-09-09", "female", [], [], [])]
        seed = {"id": "system:demo-seed", "display": "Demo seed (synthetic)", "roles": ["owner"]}
        for name, dob, sex, allergies, meds, problems in ppl:
            pid = self.create_patient(seed, {"name": name, "dob": dob, "sex": sex,
                                             "mrn": "DEMO-" + secrets.token_hex(3).upper(),
                                             "notes": "Synthetic demo patient. Not a real person."},
                                      _actor="system:demo-seed")
            for s, r, sev in allergies:
                self.add_item(seed, pid, "allergies", {"substance": s, "reaction": r, "severity": sev})
            for nm, dose, route, freq in meds:
                self.add_item(seed, pid, "medications", {"name": nm, "dose": dose, "route": route,
                                                         "frequency": freq})
            for p in problems:
                self.add_item(seed, pid, "problems", {"description": p})
            e = self.add_encounter(seed, pid, "visit", "Synthetic demo visit")
            self.add_vitals(seed, pid, e["id"], {"bp_sys": 128, "bp_dia": 82, "pulse": 72, "temp_c": 36.8})
            self.add_note(seed, pid, e["id"], "Synthetic demo note. Subjective: example only.\nPlan: example only.",
                          "Demo visit note")


# ======================================================================================================
# HTTP server
# ======================================================================================================
def _is_loopback(addr):
    return addr in ("127.0.0.1", "::1") or addr.startswith("127.") or addr == "::ffff:127.0.0.1"


class CovenantHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, addr, handler, app):
        self.app = app
        http.server.ThreadingHTTPServer.__init__(self, addr, handler)


class CovenantHandler(http.server.BaseHTTPRequestHandler):
    server_version = "StarkCovenant/" + VERSION
    sys_version = ""
    timeout = SOCKET_TIMEOUT

    def log_message(self, fmt, *args):
        pass

    @property
    def app(self):
        return self.server.app

    def do_GET(self):
        self._guarded("GET")

    def do_POST(self):
        self._guarded("POST")

    def _deny(self, method):
        self._json(405, {"ok": False, "error": "Method not allowed."})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = lambda self: self._deny(None)

    def _guarded(self, method):
        """Fault boundary: nothing raised in a handler may take the server down."""
        try:
            self._dispatch(method)
        except SandboxError as e:
            self._json(403, {"ok": False, "error": "That path is not allowed (%s)." % e})
        except HttpError as e:
            self._json(e.status, {"ok": False, "error": e.msg})
        except Exception:
            self.app.clinic.health("handler %s %s\n%s" % (method, self.path.split("?")[0], traceback.format_exc()))
            try:
                self._json(500, {"ok": False, "error": "Something went wrong. Nothing was half-saved (every write "
                                 "is journaled). Details are in .covenant_health.log."})
            except Exception:
                pass

    def _security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(self, status, obj, cookies=(), compress=False):
        body = json.dumps(obj).encode("utf-8")
        # only public, secret-free bodies (the UI catalogs) are compressed
        gz = compress and len(body) > 4096 and self._accepts_gzip()
        if gz:
            body = gzip.compress(body, compresslevel=6, mtime=0)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _accepts_gzip(self):
        ae = (self.headers.get("Accept-Encoding") or "").lower()
        return any(p.split(";")[0].strip() == "gzip" and "q=0" not in p.replace(" ", "") for p in ae.split(","))

    def _html(self, text):
        body = text.encode("utf-8")
        gz = self._accepts_gzip() and len(body) > 1024
        if gz:
            body = gzip.compress(body, compresslevel=9, mtime=0)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Vary", "Accept-Encoding, Cookie, Accept-Language")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        # style-src 'self': the UI stylesheet (/assets/covenant-ui.css) is a same-origin file; script-src 'self': the
        # practice screens (/assets/covenant-practice.js, a fixed allow-listed file). Records are never served as
        # script: documents go out as application/octet-stream attachments and every reply carries nosniff.
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                         "frame-ancestors 'none'; form-action 'self'")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, data):
        ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120] or "document"
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", "attachment; filename=\"%s\"; filename*=UTF-8''%s"
                         % (ascii_name, urllib.parse.quote(name)))
        self.send_header("Content-Security-Policy", "sandbox")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

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
        raise HttpError(403, "Host not allowed (DNS-rebinding guard).")

    def _check_origin(self):
        origin = self.headers.get("Origin")
        if origin and origin.lower() != "http://" + (self.headers.get("Host") or "").lower():
            raise HttpError(403, "Cross-origin request refused.")
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            raise HttpError(403, "Cross-site request refused.")

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
            raise HttpError(413, "Request too large.")
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise HttpError(400, "Malformed JSON.")
        if not isinstance(obj, dict):
            raise HttpError(400, "JSON object expected.")
        return obj

    def _cookie(self, name="gc_sid"):
        c = http.cookies.SimpleCookie()
        try:
            c.load(self.headers.get("Cookie") or "")
        except http.cookies.CookieError:
            return ""
        m = c.get(name)
        return m.value if m else ""

    def _signed_in(self, clinic, token, csrf, user):
        cookie = "gc_sid=%s; HttpOnly; SameSite=Strict; Path=/" % token
        return self._json(200, {"ok": True, "csrf": csrf, "user": user, "idle_seconds": clinic.idle_seconds,
                                "region": clinic.effective_region(user.get("prefs"))}, cookies=[cookie])

    def _page_locale(self):
        clinic = self.app.clinic
        default = clinic.locale_settings().get("locale", "") if clinic.state == "ready" else ""
        return page_locale(urllib.parse.unquote(self._cookie("gc_lang"))[:20], self.headers.get("Accept-Language", ""),
                           default)

    def _dispatch(self, method):
        self._check_host()
        if not _is_loopback(self.client_address[0]) and not self.app.lan_enabled:
            raise HttpError(403, "LAN access is off.")
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path
        if method == "GET" and path in ("/", "/index.html"):
            clinic = self.app.clinic
            return self._html(render_page(self._page_locale(),
                                          clinic.locale_settings() if clinic.state == "ready" else None))
        if method == "GET" and path.startswith("/i18n/") and path.endswith(".json"):
            # public catalogs (UI text only, no records); the region part is the clinic default
            i18n = optional("i18n")
            code = i18n.canonical(urllib.parse.unquote(path[6:-5])) if i18n else None
            if not code or code not in i18n.available():
                raise HttpError(404, "Unknown language.")
            clinic = self.app.clinic
            prefs = clinic.locale_settings() if clinic.state == "ready" else {}
            scope = q.get("scope", "app")
            if scope not in i18n.SCOPES:
                raise HttpError(400, "Unknown scope.")
            return self._json(200, locale_bundle(code, {k: v for k, v in prefs.items() if k != "locale"}, scope),
                              compress=True)
        if method == "GET" and (path == "/favicon.ico" or path.startswith("/assets/")):
            # product marks only (no records): a fixed allow-list of files, cacheable
            name = "stark-covenant.ico" if path == "/favicon.ico" else path[len("/assets/"):]
            ctype = ASSET_FILES.get(name)
            data = None
            if ctype:
                try:
                    data = read_bytes(os.path.join(ASSET_DIR, name))
                except OSError:
                    data = None
            if data is None:
                if path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                raise HttpError(404, "Not found.")
            gz = name.endswith(".js") and self._accepts_gzip()      # the practice screens script (2G clinics)
            if gz:
                data = gzip.compress(data, compresslevel=9, mtime=0)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if gz:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/comms" or path.startswith("/comms/"):
            # emergency_comms checks its own bodies, same-origin header and login (via _comms_authorize); the
            # WebSocket upgrade must reach it untouched. Host + LAN checks above still apply.
            comms = getattr(self.app, "comms", None)
            if comms is None:
                raise HttpError(404, "Emergency & communications module not installed.")
            if comms.handle(self):
                return
        if path == "/research" or path.startswith("/research/"):
            research = getattr(self.app, "research", None)
            if research is None:
                raise HttpError(404, "Research Desk not installed.")
            if research.handle(self):
                return
        if not path.startswith("/api/"):
            raise HttpError(404, "Not found.")
        clinic = self.app.clinic
        body = {}
        if method == "POST":
            self._check_origin()
            body = self._body()
        if (method, path) == ("GET", "/api/status"):
            return self._json(200, r_status(self.app))
        if path.startswith("/api/vault/"):
            if not _is_loopback(self.client_address[0]):
                raise HttpError(403, "The vault can only be opened on this computer.")
            if (method, path) == ("POST", "/api/vault/example"):
                return self._signed_in(clinic, *clinic.example_signin())
            fn = VAULT_ROUTES.get((method, path))
            if fn is None:
                raise HttpError(404, "Unknown API.")
            out = {"ok": True}
            out.update(fn(clinic, body))
            return self._json(200, out)
        if clinic.state != "ready":
            raise HttpError(503, "Open the secure vault first.")
        if (method, path) == ("POST", "/api/setup"):
            if clinic.fatal:
                raise HttpError(503, clinic.fatal)
            user = clinic.setup(body.get("username"), body.get("password"), body.get("pin"), body.get("display", ""))
            if isinstance(body.get("updates"), bool) and optional("auto_updater") is not None:
                try:
                    clinic.update_settings_set(user, body["updates"])      # the first-run choice
                except HttpError:
                    pass
            return self._json(200, {"ok": True, "user": user})
        if (method, path) == ("POST", "/api/login"):
            return self._signed_in(clinic, *clinic.login(body.get("username"), body.get("password")))
        token = self._cookie()
        if (method, path) == ("POST", "/api/logout"):
            clinic.logout(token)
            return self._json(200, {"ok": True}, cookies=["gc_sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"])
        if path == "/api/_selftest/raise" and self.app.test_hooks:
            raise RuntimeError("deliberate test failure")
        sess, user = clinic.session(token)
        if method == "POST":
            tok = self.headers.get("X-CSRF-Token", "")
            if not tok or not secrets.compare_digest(tok, sess["csrf"]):
                raise HttpError(403, "This page is out of date. Reload the page and try again.")
        if path == "/api/_selftest/crash" and self.app.test_hooks and method == "POST":
            return self._json(200, r_selftest_crash(self.app, user, body))
        fn = ROUTES.get((method, path))
        if fn is None:
            raise HttpError(404, "Unknown API.")
        res = fn(clinic, user, q, body)
        if isinstance(res, tuple) and res[0] == "file":
            return self._file(res[1], res[2])
        out = {"ok": True}
        out.update(res)
        return self._json(200, out)


VAULT_ROUTES = {
    ("GET", "/api/vault/status"): lambda c, b: c.vault_status(),
    ("POST", "/api/vault/unlock"): lambda c, b: c.vault_unlock(b.get("recovery_code", ""), b.get("passphrase", ""),
                                                             b.get("usb", "")),
    ("POST", "/api/vault/create"): lambda c, b: c.vault_create(b.get("usb", ""), b.get("passphrase", "")),
    ("POST", "/api/vault/confirm"): lambda c, b: c.vault_confirm(b.get("code", "")),
    ("POST", "/api/vault/demo"): lambda c, b: c.choose_demo(),
}


def r_status(app):
    c = app.clinic
    plan = c.refresh_plan()
    ready = c.state == "ready"
    return {"ok": True, "app": APP_NAME, "version": VERSION, "channel": release_channel(c.app_dir), "demo": c.demo, "demo_banner": DEMO_BANNER if c.demo else "",
            "vault": c.vault_reason, "vault_state": c.state, "fatal": c.fatal, "plan": plan, "plans": PLANS,
            "needs_setup": ready and (not c.fatal) and c.needs_setup(), "disclaimer": DISCLAIMER, "about": ABOUT_LINE,
            "ai_label": AI_LABEL, "lan": app.lan_enabled, "port": app.port, "restarts": app.restarts,
            "idle_seconds": c.idle_seconds, "roles": list(ROLES), "item_kinds": {k: list(v["fields"]) for k, v in ITEM_KINDS.items()},
            "item_choices": {k: {f: list(ch) for f, ch in v.get("choices", {}).items()} for k, v in ITEM_KINDS.items()},
            "encounter_types": list(ENCOUNTER_TYPES), "vital_ranges": VITAL_RANGES,
            "modules": {m: optional(m) is not None for m in OPTIONAL_MODULES},
            "comms": getattr(app, "comms", None) is not None,
            "features": app_features(app),
            "practice": getattr(c, "practice_suite", None) is not None, "note_templates": list(NOTE_TEMPLATES),
            "care": getattr(c, "care_suite", None) is not None,
            "recovered": bool(c.recovery.get("replayed") or c.recovery.get("audit_added")),
            "data_moved": c.moved,
            # "Try with an example clinic": possible until encrypted records are open; ready = its account exists
            "example": {"possible": not ready or (c.demo and not c.fatal and (c.example_user() is not None
                                                                              or c.needs_setup())),
                        "ready": c.example_user() is not None,
                        "pin": EXAMPLE_PIN if c.demo else ""},
            "encrypted_records_waiting": c.demo and os.path.isdir(os.path.join(c.data_root, "records", "audit"))}


def app_features(app):
    """Which optional services are mounted right now (drives the navigation entries; never fatal)."""
    c = app.clinic
    return {"consent": getattr(c, "consent_center", None) is not None,
            "persona": getattr(c, "persona_ai", None) is not None,
            "nutrition": getattr(app, "nutrition_planner", None) is not None,
            "coverage": getattr(app, "coverage_navigator", None) is not None,
            "research": getattr(app, "research", None) is not None}


def _pid(q, b):
    return b.get("patient") or q.get("patient") or b.get("id") or q.get("id")


ROUTES = {
    ("GET", "/api/me"): lambda c, u, q, b: {"user": c.public_user(u, c.perms(u))},
    ("POST", "/api/me/secret"): lambda c, u, q, b: c.change_own_secret(u, b.get("current_password"),
                                                                      b.get("new_password"), b.get("new_pin")),
    ("GET", "/api/me/prefs"): lambda c, u, q, b: c.get_prefs(u),
    ("POST", "/api/me/prefs"): lambda c, u, q, b: c.set_prefs(u, b.get("prefs")),
    ("GET", "/api/updates"): lambda c, u, q, b: c.update_info(u),
    ("POST", "/api/updates/settings"): lambda c, u, q, b: c.update_settings_set(u, b.get("checks")),
    ("POST", "/api/updates/check"): lambda c, u, q, b: c.update_check_now(u),
    ("GET", "/api/licence"): lambda c, u, q, b: c.licence_info(u),
    ("POST", "/api/licence/activate"): lambda c, u, q, b: c.licence_activate(u, b.get("token")),
    ("POST", "/api/licence/deactivate"): lambda c, u, q, b: c.licence_deactivate(u),
    ("GET", "/api/settings/locale"): lambda c, u, q, b: (c.require(u, "users.manage") or
                                                         {"prefs": c.locale_settings(),
                                                          "region": c.effective_region({})}),
    ("POST", "/api/settings/locale"): lambda c, u, q, b: c.set_locale_settings(u, b.get("prefs")),
    ("GET", "/api/patients"): lambda c, u, q, b: {"patients": c.search(u, q.get("q", ""))},
    ("POST", "/api/patients"): lambda c, u, q, b: {"id": c.create_patient(u, b.get("demographics"))},
    ("GET", "/api/patient"): lambda c, u, q, b: {"chart": c.view(u, _pid(q, b))},
    ("POST", "/api/patient/update"): lambda c, u, q, b: {"chart": c.update_patient(u, _pid(q, b), b.get("demographics"))},
    ("POST", "/api/encounters"): lambda c, u, q, b: {"encounter": c.add_encounter(u, _pid(q, b), b.get("type", "visit"),
                                                                                 b.get("reason", ""), b.get("date"))},
    ("POST", "/api/vitals"): lambda c, u, q, b: {"vitals": c.add_vitals(u, _pid(q, b), b.get("encounter"), b.get("values"))},
    ("POST", "/api/vitals/error"): lambda c, u, q, b: {"vitals": c.vitals_error(u, _pid(q, b), b.get("vitals"), b.get("reason"))},
    ("POST", "/api/notes"): lambda c, u, q, b: {"note": c.add_note(u, _pid(q, b), b.get("encounter"), b.get("text", ""),
                                                                   b.get("title") or "Progress note", b.get("template"))},
    ("POST", "/api/notes/cosign"): lambda c, u, q, b: {"note": c.cosign_note(u, _pid(q, b), b.get("note"), b.get("pin"))},
    ("POST", "/api/notes/update"): lambda c, u, q, b: {"note": c.update_note(u, _pid(q, b), b.get("note"), b.get("text"),
                                                                             b.get("title"))},
    ("POST", "/api/notes/sign"): lambda c, u, q, b: {"note": c.sign_note(u, _pid(q, b), b.get("note"), b.get("pin"))},
    ("POST", "/api/notes/addendum"): lambda c, u, q, b: {"addendum": c.add_addendum(u, _pid(q, b), b.get("note"),
                                                                                    b.get("text"), b.get("pin"))},
    ("POST", "/api/items"): lambda c, u, q, b: {"item": c.add_item(u, _pid(q, b), b.get("kind"), b.get("item"))},
    ("POST", "/api/items/update"): lambda c, u, q, b: {"item": c.update_item(u, _pid(q, b), b.get("kind"),
                                                                             b.get("item_id"), b.get("item"))},
    ("POST", "/api/documents"): lambda c, u, q, b: {"document": c.add_document(u, _pid(q, b), b.get("name"), b.get("mime"),
                                                                               b.get("b64"), b.get("encounter"))},
    ("GET", "/api/audit"): lambda c, u, q, b: dict(zip(("entries", "total"), c.audit_entries(
        u, min(int(q.get("limit", "200") or 200), 1000), int(q.get("offset", "0") or 0)))),
    ("GET", "/api/audit/verify"): lambda c, u, q, b: {"result": c.verify(u)},
    ("GET", "/api/users"): lambda c, u, q, b: {"users": c.list_users(u)},
    ("POST", "/api/users"): lambda c, u, q, b: {"user": c.create_user(u, b.get("username"), b.get("password"), b.get("pin"),
                                                                      b.get("roles"), b.get("display", ""))},
    ("POST", "/api/users/update"): lambda c, u, q, b: {"user": c.update_user(u, b.get("id"), b.get("roles"), b.get("active"),
                                                                             b.get("password"), bool(b.get("unlock")),
                                                                             b.get("pin"))},
    ("POST", "/api/backup"): lambda c, u, q, b: {"backup": c.backup(u)},
    ("GET", "/api/backups"): lambda c, u, q, b: (c.require(u, "backup.run") or {"backups": c.list_backups()}),
    ("GET", "/api/ai/suggest"): lambda c, u, q, b: c.ai_suggest(u, q.get("patient"), q.get("prefix", "")),
    ("POST", "/api/ai/accept"): lambda c, u, q, b: {"note": c.ai_accept(u, _pid(q, b), b.get("note"), b.get("text"),
                                                                        b.get("kind"), b.get("suggestion_id"), b.get("pin"))},
    ("GET", "/api/interop/export"): lambda c, u, q, b: {"export": c.interop_export(u, _pid(q, b))},
}


def _r_document(c, u, q, b):
    d, data = c.get_document(u, _pid(q, b), q.get("doc"))
    return ("file", d["name"], data)


ROUTES[("GET", "/api/document")] = _r_document


def r_selftest_crash(app, user, b):
    """TEST HOOK (only with --test-hooks): stage a note save as a power cut, optionally kill the process."""
    c = app.clinic
    pid, nid = b.get("patient"), b.get("note")
    with c.store.lock:
        chart = c._load_chart(pid)
        n = c._note(chart, nid)
        c._editable(user, n)
        n["text"] = _clean(b.get("text"), 100000, multiline=True)
        chart["rev"] += 1
        files = {c._chart_rel(pid): c.codec.dump(chart)}
        res = c.store.simulate_crash(files, {"actor": user["id"], "action": "note.update", "target": pid,
                                             "detail": {"note": nid}}, b.get("stage", "partial_tmp"))
    if b.get("exit"):
        os._exit(3)
    return {"crash": res}


# --- emergency & communications (optional module emergency_comms; never fatal) ---------------------------
def _comms_authorize(handler):
    """Private /comms routes need a logged-in clinic session (same gc_sid cookie as the main app)."""
    try:
        handler.app.clinic.session(handler._cookie())
        return True
    except Exception:
        return False


def make_comms(app):
    """Mount emergency_comms. With a vault its data is encrypted in <data>/comms; without one (DEMO, or the vault
    still locked) it uses <data>/comms_no_vault, so plain files never mix with the encrypted ones. The public
    emergency page and number list work in every state, without a login."""
    ec = optional("emergency_comms")
    if ec is None:
        return None
    vault = app.clinic.vault
    try:
        return ec.CommsModule(data_dir=os.path.join(app.clinic.data_root, "comms" if vault is not None else
                                                    "comms_no_vault"),
                              vault=vault, authorize=_comms_authorize)
    except Exception:
        app.clinic.health("emergency_comms could not start\n" + traceback.format_exc())
        return None


class CovenantApp:
    def __init__(self, data_root=None, port=None, open_browser=True, test_hooks=False, lan=False, vault="auto",
                 plan=None, demo=False, idle_seconds=None, clinic=None, vault_root=None):
        self.clinic = clinic or Clinic(data_root, vault=vault, plan=plan, demo=demo, test_hooks=test_hooks,
                                       idle_seconds=idle_seconds, vault_root=vault_root)
        self.fixed_port = port
        self.open_browser = open_browser
        self.test_hooks = test_hooks
        self.lan_requested = bool(lan)
        self.lan_enabled = bool(lan) and self.clinic.plan["lan"]
        self.bind_host = "0.0.0.0" if self.lan_enabled else "127.0.0.1"
        self.port = None
        self.httpd = None
        self.serve_thread = None
        self.restarts = 0
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._browser_opened = False
        self._last_backup = 0.0
        self.comms = make_comms(self)
        self.clinic.on_ready.append(self._remount_comms)
        for _name in ("consent_center", "persona_ai"):  # optional modules; each one degrades safely when missing
            _m = optional(_name)
            setattr(self, _name, _m.mount(self) if _m is not None and callable(getattr(_m, "mount", None)) else None)
        for _n in ("nutrition_planner", "coverage_navigator"):  # after emergency_comms and consent_center
            try:
                _m = optional(_n); setattr(self, _n, _m.mount(self) if _m and hasattr(_m, "mount") else None)
            except Exception:
                setattr(self, _n, None)
                self.clinic.health("%s could not start\n%s" % (_n, traceback.format_exc()))
        ps = optional("practice_suite")      # scheduling, measures, plans, billing, exports (never fatal)
        self.practice_suite = ps.mount(self) if ps is not None and callable(getattr(ps, "mount", None)) else None
        cs = optional("care_suite") if self.practice_suite is not None else None   # builds on practice_suite
        self.care_suite = cs.mount(self) if cs is not None and callable(getattr(cs, "mount", None)) else None
        rd = optional("research_desk")
        self.research = None
        if rd is not None:
            try:
                self.research = rd.mount(self)      # also re-mounts itself when the vault opens later
            except Exception:
                self.clinic.health("research_desk could not start\n" + traceback.format_exc())

    def _remount_comms(self):
        """The vault opened after start-up: mount emergency_comms again so it stores its data encrypted."""
        if self.clinic.vault is not None:
            self.comms = make_comms(self) or self.comms

    def _bind(self):
        ports = [self.port or self.fixed_port] if (self.port or self.fixed_port) else []
        ports += [p for p in range(PORT_FIRST, PORT_LAST + 1) if p not in ports]
        last = None
        for p in ports:
            if not PORT_FIRST <= p <= PORT_LAST:
                continue
            try:
                httpd = CovenantHTTPServer((self.bind_host, p), CovenantHandler, self)
                httpd.timeout = SOCKET_TIMEOUT
                return httpd
            except OSError as e:
                last = e
        raise OSError("no free port in %d-%d (%s)" % (PORT_FIRST, PORT_LAST, last))

    def _listen(self):
        httpd = self._bind()
        self.httpd = httpd
        self.port = httpd.server_address[1]
        t = threading.Thread(target=self._serve, args=(httpd,), name="covenant-listener", daemon=True)
        self.serve_thread = t
        t.start()

    def _serve(self, httpd):
        try:
            httpd.serve_forever(poll_interval=0.25)
        except Exception:
            self.clinic.health("listener crashed\n" + traceback.format_exc())

    def start(self):
        if self.lan_requested and not self.lan_enabled:
            self.clinic.health("LAN requested but the %s plan does not include it; staying on 127.0.0.1"
                               % self.clinic.plan["label"])
        with self._lock:
            self._listen()
        self._watchdog_thread = threading.Thread(target=self._watchdog, name="covenant-watchdog", daemon=True)
        self._watchdog_thread.start()
        if not self.clinic.fatal and not self.test_hooks:
            threading.Thread(target=self._startup_backup, daemon=True).start()
        up = optional("auto_updater")
        if up is not None:
            try:
                fn = getattr(up, "confirm_healthy", None)
                if callable(fn):
                    fn(_HERE)
            except Exception:
                self.clinic.health("auto_updater confirm_healthy failed\n" + traceback.format_exc())
            if not self.test_hooks:
                threading.Thread(target=self._update_check, daemon=True).start()
        if self.open_browser and not self._browser_opened:
            self._browser_opened = True
            try:
                webbrowser.open("http://127.0.0.1:%d/" % self.port)
            except Exception:
                pass
        return self

    def _startup_backup(self):
        try:
            if self.clinic.state == "ready" and self.clinic.store.audit._last[0]:
                self.clinic.backup(None, "startup")
            self._last_backup = time.time()
        except Exception:
            self.clinic.health("startup backup failed\n" + traceback.format_exc())

    def _update_check(self):
        try:
            up = optional("auto_updater")
            fn = getattr(up, "check_for_updates", None) or getattr(up, "check", None)
            if callable(fn):
                fn()
        except Exception:
            self.clinic.health("auto_updater check failed\n" + traceback.format_exc())

    def _watchdog(self):
        while not self._stop.wait(0.5):
            if self.serve_thread is None or not self.serve_thread.is_alive():
                try:
                    with self._lock:
                        if self._stop.is_set():
                            return
                        old = self.httpd
                        if old is not None:
                            old.server_close()
                        self._listen()
                        self.restarts += 1
                    self.clinic.health("listener restarted on %s:%d" % (self.bind_host, self.port))
                except Exception:
                    self.clinic.health("watchdog restart failed\n" + traceback.format_exc())
            if (self.clinic.dirty and self.clinic.state == "ready" and not self.test_hooks and not self.clinic.fatal
                    and time.time() - self._last_backup > BACKUP_INTERVAL):
                self._last_backup = time.time()
                try:
                    self.clinic.backup(None, "periodic")
                except Exception:
                    self.clinic.health("periodic backup failed\n" + traceback.format_exc())

    def kill_listener_for_test(self):
        self.httpd.shutdown()

    def wait_until_listening(self, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.serve_thread is not None and self.serve_thread.is_alive():
                try:
                    socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                    return True
                except OSError:
                    pass
            time.sleep(0.05)
        return False

    def stop(self):
        self._stop.set()
        for svc in (getattr(self.clinic, "consent_center", None), getattr(self, "research", None)):
            try:
                if svc is not None and callable(getattr(svc, "stop", None)):
                    svc.stop()  # background schedulers (retention purge, watch-list refresh)
            except Exception:
                self.clinic.health("optional module stop failed\n" + traceback.format_exc())
        with self._lock:
            if self.httpd is not None:
                try:
                    self.httpd.shutdown()
                except Exception:
                    pass
                self.httpd.server_close()
        wd = getattr(self, "_watchdog_thread", None)
        if wd is not None and wd is not threading.current_thread():
            wd.join(5)  # a restart in progress logs after the lock is released; nothing may write after stop()
        v = self.clinic.vault_obj
        if v is not None and callable(getattr(v, "lock", None)):
            try:
                v.lock()  # wipe vault keys from memory
            except Exception:
                pass


def restore_backup(backup_path, out_dir, vault="auto"):
    """Extract a backup into a NEW empty folder (never overwrites). Returns the number of files."""
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise IOError("restore target must be a new or empty folder")
    data = read_bytes(backup_path)
    if not backup_path.endswith(".zip"):
        adapter = find_vault()[0] if vault == "auto" else (vault if isinstance(vault, VaultAdapter) else VaultAdapter(vault))
        if adapter is None:
            raise IOError("this backup is encrypted and the security vault is not available")
        data = Codec(adapter).open(data)
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            dest = safe_join(out_dir, info.filename, allow_hidden=True)  # zip-slip guard
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            atomic_write_bytes(dest, z.read(info))
            n += 1
    return n


ASSET_DIR = os.path.join(_HERE, "assets")
ASSET_FILES = {"stark-covenant.ico": "image/x-icon", "stark-covenant-favicon.svg": "image/svg+xml",
               "stark-covenant-lockup-light.svg": "image/svg+xml", "stark-covenant-lockup-dark.svg": "image/svg+xml",
               # the same marks under their pre-1.1.0 file names (older desktop shortcuts point at the .ico)
               "gorgon-covenant.ico": "image/x-icon", "gorgon-covenant-favicon.svg": "image/svg+xml",
               "gorgon-covenant-lockup-light.svg": "image/svg+xml", "gorgon-covenant-lockup-dark.svg": "image/svg+xml",
               "covenant-ui.css": "text/css; charset=utf-8",      # UI design tokens + components (no records)
               "covenant-icons.svg": "image/svg+xml",             # UI icon sprite
               "covenant-practice.js": "text/javascript; charset=utf-8"}   # practice screens, loaded after sign-in
_FAVICON_URI = None


def favicon_uri():
    """The product favicon as a data: URI (no extra request; works offline and on every page)."""
    global _FAVICON_URI
    if _FAVICON_URI is None:
        try:
            with open(os.path.join(ASSET_DIR, "stark-covenant-favicon.svg"), encoding="utf-8") as f:
                svg = re.sub(r"\s*\n\s*", " ", f.read().strip())
            _FAVICON_URI = "data:image/svg+xml," + urllib.parse.quote(svg, safe=" :/=;,()-.")
        except OSError:
            _FAVICON_URI = "data:,"
    return _FAVICON_URI


def _i18n():
    return optional("i18n")


def _regional():
    return optional("regional")


# --- interoperability: chart -> interop_bridge record (stored values are canonical: metric, mmol/L, ISO) ----
_LOINC_VITALS = {"bp_sys": ("8480-6", "mm[Hg]"), "bp_dia": ("8462-4", "mm[Hg]"), "pulse": ("8867-4", "/min"),
                 "resp": ("9279-1", "/min"), "spo2": ("59408-5", "%"), "temp_c": ("8310-5", "Cel"),
                 "weight_kg": ("29463-7", "kg"), "height_cm": ("8302-2", "cm"),
                 "glucose_mmol": ("15074-8", "mmol/L")}
_SEX_FHIR = {"f": "female", "female": "female", "m": "male", "male": "male", "o": "other", "other": "other",
             "u": "unknown", "unknown": "unknown"}


def chart_to_interop_record(chart):
    d = chart.get("demographics") or {}
    given = (d.get("given_name") or "").split()
    family = d.get("family_name") or ""
    if not (given or family):
        parts = (d.get("name") or "").replace("(synthetic)", "").split()
        given, family = parts[:-1] or parts[:1], (parts[-1] if len(parts) > 1 else "")
    patient = {"id": d.get("mrn") or chart["id"], "given": given, "family": family,
               "birth_date": d.get("dob") or None, "sex": _SEX_FHIR.get(str(d.get("sex") or "").strip().lower()),
               "phone": d.get("phone_e164") or d.get("phone") or None}
    if not d.get("mrn"):
        patient["id_system"] = "urn:gorgon-covenant:chart"
    addr = {"line": [x for x in (d.get("addr_line1"), d.get("addr_line2")) if x], "city": d.get("addr_city"),
            "state": d.get("addr_region"), "postal_code": d.get("addr_postal"), "country": d.get("addr_country")}
    if any(addr.values()):
        patient["address"] = {k: v for k, v in addr.items() if v}
    rec = {"patient": {k: v for k, v in patient.items() if v not in (None, "", [])}}
    encs = sorted(chart.get("encounters") or [], key=lambda e: (e.get("date") or "", e.get("created") or ""))
    if encs:
        e = encs[-1]
        rec["encounter"] = {"id": e["id"], "start": e.get("date"), "status": "finished",
                            "class": "outpatient" if e.get("type") != "inpatient" else "inpatient"}
    obs = []
    for v in chart.get("vitals") or []:
        if v.get("status") == "entered_in_error":
            continue
        for k, val in (v.get("values") or {}).items():
            if k in _LOINC_VITALS:
                code, unit = _LOINC_VITALS[k]
                o = {"code": code, "value": val, "unit": unit, "time": v.get("at"), "status": "final"}
                if k == "glucose_mmol":
                    o["display"] = "Glucose [Moles/volume] in Blood"
                obs.append(o)
    rec["observations"] = obs
    sev = {"mild": "mild", "moderate": "moderate", "severe": "severe"}
    rec["allergies"] = [{"id": a.get("id"), "display": a.get("substance"), "reaction": a.get("reaction") or None,
                         "severity": sev.get(a.get("severity")),
                         "clinical_status": "inactive" if a.get("status") == "inactive" else "active"}
                        for a in chart.get("allergies") or [] if a.get("status") != "entered_in_error"]
    rec["conditions"] = [{"id": c.get("id"), "display": c.get("description"), "code": c.get("code") or None,
                          "onset": c.get("onset") or None,
                          "clinical_status": {"resolved": "resolved", "inactive": "inactive"}.get(c.get("status"), "active")}
                         for c in chart.get("problems") or [] if c.get("status") != "entered_in_error"]
    rec["medications"] = [{"id": m.get("id"), "display": m.get("name"), "start": m.get("start") or None,
                           "status": "stopped" if m.get("status") == "stopped" else "active",
                           "dosage": " ".join(x for x in (m.get("dose"), m.get("route"), m.get("frequency")) if x) or None}
                          for m in chart.get("medications") or [] if m.get("status") != "entered_in_error"]
    for kind in ("allergies", "conditions", "medications"):
        rec[kind] = [{k: v for k, v in it.items() if v is not None} for it in rec[kind]]
    return rec


def page_locale(cookie_lang="", accept_language="", default=""):
    """Locale for a page: the person's explicit choice (gc_lang cookie) > clinic default > browser languages."""
    i18n = _i18n()
    if i18n is None:
        return "en"
    avail = i18n.available()
    for cand in (cookie_lang, default):
        c = i18n.canonical(cand or "")
        if c and c in avail:
            return c
    return i18n.negotiate(accept_language, avail)


def locale_bundle(locale="en", prefs=None, scope="app"):
    """Catalog bundle for the browser, with the effective region settings for these preferences."""
    i18n, rg = _i18n(), _regional()
    loc = i18n.canonical(locale) if i18n else "en"
    region = None
    if rg is not None:
        info = i18n.LOCALES.get(loc or "en", {}) if i18n else {}
        region = rg.client_region(prefs or {}, info.get("region"))
    if i18n is None:
        return {"locale": "en", "dir": "ltr", "intl": "en", "name": "English", "status": "source", "m": {},
                "fallback": [], "unreviewed": [], "clinical": [], "paired": [], "en": {}, "errs": {},
                "plural": ["one", "other"], "locales": [], "region": region}
    return i18n.bundle(loc, region=region, scope=scope)


_SHIPPED = []


def _page_shipped():
    """PAGE as sent to the browser: the same code without its whole-line // comments (first load on 2G; the
    comments stay in the source). The runtime pieces inserted later are stripped the same way, once."""
    if not _SHIPPED:
        i18n, rg = _i18n(), _regional()
        page = PAGE.replace("__I18N_JS__", i18n.js_runtime() if i18n else "").replace(
            "__REGION_JS__", rg.REGION_JS if rg else "const RG=null;")
        _SHIPPED.append(re.sub(r"(?m)^[ \t]*//[^\n]*\n", "", page))
    return _SHIPPED[0]


def render_page(locale="en", prefs=None):
    """The app shell with ONE locale's catalog inlined (a language change later fetches /i18n/<code>.json)."""
    i18n = _i18n()
    b = locale_bundle(locale, prefs)
    data = i18n.script_json(b) if i18n else json.dumps(b).replace("</", "<\\/")
    attrs = i18n.html_attrs(b["locale"]) if i18n else 'lang="en" dir="ltr"'
    return (_page_shipped().replace("__HTML_ATTRS__", attrs).replace("__I18N_DATA__", data)
            .replace("__FAVICON__", favicon_uri()).replace("__VERSION__", VERSION))


PAGE = r"""<!doctype html>
<html __HTML_ATTRS__><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stark Covenant</title>
<link rel="icon" href="__FAVICON__">
<link rel="stylesheet" href="/assets/covenant-ui.css?v=__VERSION__">
<style>/* critical first paint + safety fallback if the stylesheet cannot load; the design lives in assets/covenant-ui.css */
html{background:#0b1220;color:#e8eef8}html[data-theme=light]{background:#eef2f7;color:#0f1a2a}[hidden]{display:none!important}
.demo{background:#a3121f;color:#fff;font-weight:800;padding:8px 16px}a.sos{background:#c8102e;color:#fff;font-weight:800;padding:8px 16px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}.skip{position:absolute;top:-200px}.skip:focus{top:8px}
</style>
<script>try{if(localStorage.getItem('gc_theme')==='light')document.documentElement.setAttribute('data-theme','light')}catch(e){}</script>
</head><body>
<a class="skip" id="skipLink" href="#content"></a>
<header><h1><img src="__FAVICON__" alt="" width="28" height="28">Stark Covenant</h1><span class="badge beta" id="beta" hidden></span><span class="badge" id="mode"></span>
<div id="topSearch"></div><span class="spacer"></span>
<a class="sos" id="sosTop" href="/comms/" hidden></a>
<label class="sr" for="langSel" id="langLbl"></label><select id="langSel"></select>
<button class="ghost" id="themeBtn" type="button" aria-pressed="false"></button>
<button class="ghost" id="aboutBtn" type="button"></button>
<span id="who"></span>
<button class="ghost" id="lockBtn" type="button" hidden></button></header>
<div id="draftLang" class="draftlang" role="note" hidden></div>
<div id="demo" class="demo" role="alert" hidden></div><div id="fatal" class="fatal" role="alert" hidden></div>
<div id="status" class="status" role="status" aria-live="polite"></div>
<div id="app"></div>
<div id="actbar" hidden></div>
<footer id="foot"></footer>
<script type="application/json" id="i18n-data">__I18N_DATA__</script>
<script>
"use strict";
__I18N_JS__
__REGION_JS__
const S={csrf:null,user:null,st:null,pid:null,chart:null,tab:'summary',last:Date.now(),view:'patients',redo:null,inner:null};
const $=s=>document.querySelector(s);
function el(t,a,...kids){const e=document.createElement(t);if(a)for(const[k,v]of Object.entries(a)){if(v==null||v===false)continue;
 if(k==='class')e.className=Array.isArray(v)?v.filter(Boolean).join(' '):v;else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else if(k==='text')e.textContent=v;
 else if(k==='value')e.value=v;else e.setAttribute(k,v===true?'':v)}
 for(const c of kids.flat()){if(c==null||c===false)continue;e.append(c instanceof Node?c:document.createTextNode(String(c)))}return e}
function say(m,k){const s=$('#status');s.textContent=m||'';s.className='status '+(k||'');clearTimeout(S.sayT);
 if(m&&k==='ok')S.sayT=setTimeout(()=>{if(s.textContent===m)s.textContent=''},6000)}
// inline SVG icons from one cached sprite (assets/covenant-icons.svg); decorative: the text label always carries the meaning
function ic(n){const N='http://www.w3.org/2000/svg',s=document.createElementNS(N,'svg'),u=document.createElementNS(N,'use');
 s.setAttribute('class','i');s.setAttribute('aria-hidden','true');s.setAttribute('focusable','false');u.setAttribute('href','/assets/covenant-icons.svg?v=__VERSION__#'+n);s.append(u);return s}
function initials(n){return String(n||'').replace(/\(.*?\)/g,'').trim().split(/\s+/).slice(0,2).map(w=>[...w][0]||'').join('').toUpperCase()}
function chip(t,k,a){return el('span',Object.assign({class:['chip',k]},a||{}),t)}
function light(){return document.documentElement.getAttribute('data-theme')==='light'}
function lockup(){return '/assets/stark-covenant-lockup-'+(light()?'light':'dark')+'.svg'}
function themeUi(){const b=$('#themeBtn');b.replaceChildren(ic('sun'),T('app.theme.light'));b.setAttribute('aria-pressed',String(light()));
 document.querySelectorAll('img[data-lockup]').forEach(i=>{i.src=lockup()})}
$('#themeBtn').onclick=()=>{const l=!light(),r=document.documentElement;r.classList.add('theme-switching');if(l)r.setAttribute('data-theme','light');else r.removeAttribute('data-theme');
 void document.body.offsetHeight;requestAnimationFrame(()=>r.classList.remove('theme-switching'));
 try{localStorage.setItem('gc_theme',l?'light':'dark')}catch(e){}themeUi()};
function topH(){const h=document.querySelector('header'),d=$('#demo'),w=innerWidth>800,hh=w?h.offsetHeight:0,r=document.documentElement.style;
 r.setProperty('--hh',hh+'px');r.setProperty('--top',(hh+(d.hidden?0:d.offsetHeight))+'px')}
addEventListener('resize',topH);try{const ro=new ResizeObserver(topH);ro.observe(document.querySelector('header'));ro.observe($('#demo'))}catch(e){}
function can(p){return !!(S.user&&S.user.perms.includes(p))}
function fail(r,j){return new Error(j.error?trErr(j.error):T('app.err.http',{status:r.status}))}
async function api(m,p,b){const h={};if(m==='POST'){h['Content-Type']='application/json';if(S.csrf)h['X-CSRF-Token']=S.csrf}
 let r;try{r=await fetch(p,{method:m,headers:h,body:m==='POST'?JSON.stringify(b||{}):undefined,credentials:'same-origin'})}
 catch(e){throw new Error(T('app.err.offline'))}
 let j={};try{j=await r.json()}catch(e){}
 if(r.status===401&&S.user){S.user=null;S.csrf=null;showLogin(trErr(j.error||'')||T('app.login.session_locked'));throw new Error('locked')}
 if(!r.ok||j.ok===false)throw fail(r,j);return j}
function field(lbl,inp){const id='f'+Math.random().toString(36).slice(2);inp.id=id;return el('div',null,el('label',{for:id},lbl),inp)}
function inp(name,type,val,extra){return el('input',Object.assign({name,type:type||'text',value:val==null?'':val},extra||{}))}
function formData(f){const o={};for(const x of f.querySelectorAll('[name]'))o[x.name]=x.value;return o}
// every action: instant feedback (the button shows busy and cannot be pressed twice), plain errors in the status line
function act(fn){return async ev=>{if(ev&&ev.preventDefault)ev.preventDefault();
 const t=ev&&(ev.submitter||ev.currentTarget),b=t&&t.tagName==='BUTTON'?t:null,inC=!!(t&&t.closest&&t.closest('#content'));
 if(b){b.setAttribute('aria-busy','true');b.disabled=true}
 try{await fn(ev);if(inC)S.dirty=false}catch(e){if(e.message!=='locked')say(trErr(e.message),'err')}
 finally{if(b){b.removeAttribute('aria-busy');b.disabled=false}}}}
function mount(...n){const a=$('#app');a.replaceChildren(...n)}
// one primary action per screen: the screen's main action is the button marked "primary" (else the first solid button
// in reading order that is not folded away); every other solid button is shown as secondary ("sec")
function onePrimary(){const solid=[...$('#app').querySelectorAll('button')].filter(b=>!b.matches('.ghost,.danger,.warnbtn,.pcard,[role=tab],.plist button,.nav-new'));
 const pick=solid.find(b=>b.classList.contains('primary'))||solid.find(b=>!b.closest('details:not([open]),nav'))||null;
 for(const b of solid){b.classList.toggle('primary',b===pick);b.classList.toggle('sec',b!==pick)}actBar(pick)}
// the main action is never more than one tap away: while it is below the screen a copy waits at the bottom (for a
// form it takes the person to the form's first field; any other button does its job at once)
let _io=null,_pick=null;
function actBar(pick){if(pick===_pick)return;_pick=pick;const bar=$('#actbar');if(_io){_io.disconnect();_io=null}bar.hidden=true;bar.replaceChildren();
 if(!pick||!S.user||!('IntersectionObserver' in window))return;
 const go=()=>{const f=pick.type==='submit'&&pick.form;if(!f)return pick.click();const x=f.querySelector('input:not([type=hidden]),select,textarea');(x||pick).focus()};
 bar.append(el('button',{type:'button',id:'actBtn',onclick:go},pick.textContent));
 _io=new IntersectionObserver(es=>{const e=es[es.length-1];bar.hidden=e.intersectionRatio>0.99||e.boundingClientRect.top<0},{threshold:[0,0.5,1]});_io.observe(pick)}
new MutationObserver(onePrimary).observe($('#app'),{childList:true,subtree:true});
// no data loss on refresh, close, back or a menu click: a form being filled in asks before it is left
S.dirty=false;
document.addEventListener('input',e=>{if(S.user&&e.target.closest&&e.target.closest('#content'))S.dirty=true},true);
addEventListener('beforeunload',e=>{if(S.dirty&&S.user){e.preventDefault();e.returnValue=''}});
document.addEventListener('click',e=>{const b=S.dirty&&e.target.closest&&e.target.closest('button,a');
 if(!b||!(b.closest('nav')||b.matches('[role=tab],#aboutBtn,.pcard')))return;if(confirm(T('app.leave.confirm')))S.dirty=false;else{e.preventDefault();e.stopImmediatePropagation()}},true);
function fmt(ts){return RG?RG.ts(ts):(ts?ts.replace('T',' ').replace('+00:00',' UTC'):'')}
function dday(d){return RG?RG.date(d):String(d||'')}
function num(x,o){return nfmt(x,o)}
function money(x,cur){return nfmt(x,{style:'currency',currency:cur||'USD'})}
function kib(n){return T('app.common.kib',{n:Math.ceil(n/1024)})}
function auto(t,v,a){return el(t,Object.assign({dir:'auto'},a||{}),v)}
function wrap(t){return el('div',{class:'tablewrap'},t)}
function cookieLang(c){try{document.cookie='gc_lang='+encodeURIComponent(c)+';path=/;max-age=31536000;samesite=strict'}catch(e){}}

// --- chrome: header, banners, language picker ------------------------------------------------------
function chrome(){
 $('#skipLink').textContent=T('app.chrome.skip');$('#sosTop').textContent=T('app.chrome.emergency');
 $('#aboutBtn').replaceChildren(ic('info'),T('app.chrome.about'));$('#lockBtn').replaceChildren(ic('lock'),T('app.chrome.lock'));themeUi();
 $('#langLbl').textContent=T('app.lang.label');
 const sel=$('#langSel');sel.replaceChildren(...(I18N.locales||[]).map(l=>el('option',{value:l.code,lang:l.code,selected:l.code===I18N.locale?true:null},
  l.native+(l.status==='source'||l.status==='reviewed'?'':' - '+T('app.lang.draft_tag')))));
 sel.setAttribute('aria-label',T('app.lang.label'));
 const dl=$('#draftLang');dl.hidden=I18N.status==='source'||I18N.status==='reviewed';
 dl.replaceChildren(...(dl.hidden?[]:[legal('span','app.lang.draft_banner')]));
 const st=S.st;if(!st)return;
 const beta=st.channel==='beta';$('#beta').hidden=!beta;$('#beta').textContent=T('app.chrome.beta');
 $('#mode').textContent=T('app.chrome.plan_badge',{plan:st.plan.label})+(st.demo?' - '+T('app.chrome.demo_tag'):'');
 if(st.demo){$('#demo').hidden=false;$('#demo').replaceChildren(legal('span','legal.demo_banner'))}
 if(st.fatal){$('#fatal').hidden=false;$('#fatal').textContent=trErr(st.fatal)}
 $('#foot').replaceChildren(legal('span','legal.disclaimer'));
 if(st.comms)$('#sosTop').hidden=false;
 if(S.user)$('#who').textContent=S.user.display+' ('+S.user.roles.map(r=>TK('app.role',r)).join(', ')+')';
 topH();
}
async function setLocale(code,save){
 const r=await fetch('/i18n/'+encodeURIComponent(code)+'.json',{credentials:'same-origin'});
 if(!r.ok)throw new Error(T('app.lang.load_failed'));
 const b=await r.json();const region=I18N.region;cookieLang(b.locale);
 if(save&&S.user){const p=await api('POST','/api/me/prefs',{prefs:{locale:b.locale}});b.region=p.region;S.user.prefs=p.prefs}
 else if(S.user&&region)b.region=region;
 i18nUse(b);if(S.user)await later();rerender()}
// strings only the signed-in screens use (licence, consent, coverage: scope "later") arrive right after sign-in, so
// the first load stays small; merged like the practice screens' own strings
async function later(){try{const r=await fetch('/i18n/'+encodeURIComponent(I18N.locale)+'.json?scope=later',{credentials:'same-origin'});
 if(!r.ok)return;const b=await r.json();Object.assign(I18N.m,b.m);Object.assign(I18N.en,b.en||{});
 for(const k of b.clinical||[])_clin.add(k);for(const k of b.paired||[])_pair.add(k);for(const k of b.unreviewed||[])_unrev.add(k)}catch(e){}}
function rerender(){chrome();if(S.user){showMain(true);if(S.inner)S.inner()}else if(S.redo)S.redo();else boot()}
$('#langSel').addEventListener('change',act(async ev=>{await setLocale(ev.target.value,true);say(T('app.lang.changed'),'ok')}));

async function boot(){
 S.st=await api('GET','/api/status');const st=S.st;chrome();
 if(st.recovered)say(T('app.boot.recovered'),'ok');
 if(st.data_moved)say(T('app.boot.data_moved',{old:st.data_moved.from,new:st.data_moved.to}),'info');
 if(st.vault_state==='locked')return showUnlock();
 if(st.vault_state==='no_vault'&&!S.setup)return showWelcome();
 if(st.vault_state==='no_vault'||st.vault_state==='confirm_code')return showCreateVault();
 if(st.needs_setup)showSetup();else showLogin('');
}
// first minute: one obvious first action. The example clinic is the DEMO practice area (made-up patients, its own
// folder, never mixed with the encrypted records) with its own account, signed in with one click.
function exOk(){return !!(S.st&&S.st.example&&S.st.example.possible)}
function exampleBtn(primary){return el('button',{type:'button',id:'exampleBtn',class:primary?'primary':'ghost',onclick:act(async()=>{
  say(T('app.start.opening'),'');const r=await api('POST','/api/vault/example',{});S.st=await api('GET','/api/status');signedIn(r)})},T('app.start.example'))}
function showWelcome(){
 S.redo=showWelcome;
 const card=el('div',{class:['card','glass','start'],id:'startCard'},el('h2',null,T('app.start.title')),el('p',null,T('app.start.text')),
  exampleBtn(true),el('p',{class:'muted'},T('app.start.example_note')),
  el('button',{type:'button',class:'ghost',id:'setupBtn',onclick:()=>{S.setup=true;showCreateVault()}},ic('lock'),T('app.start.setup')),
  el('p',{class:'muted'},T('app.start.setup_note')));
 mount(el('div',{class:'center',id:'content'},brand(),sosBig(),card,disclaimerCard()));
}
function demoChoice(){return exOk()?el('div',{class:'card'},el('h3',null,T('app.demo.title')),el('p',{class:'muted'},T('app.start.example_note')),exampleBtn(false)):null}
async function showUnlock(){
 S.redo=showUnlock;const vs=await api('GET','/api/vault/status');
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);say(T('app.vault.opening'),'');await api('POST','/api/vault/unlock',d);say(T('app.vault.opened'),'ok');location.reload()})},
  el('h2',null,T('app.vault.unlock_title')),
  el('p',null,T('app.vault.unlock_text')),
  el('ol',{class:'steps'},el('li',null,T('app.vault.unlock_step1')),el('li',null,T('app.vault.unlock_step2'))),
  el('p',{class:vs.usb_key_found?'signed':'muted'},vs.usb_key_found?T('app.vault.usb_found'):T('app.vault.usb_not_found')),
  field(T('app.vault.recovery_label'),inp('recovery_code','text','',{autocomplete:'off',spellcheck:'false',placeholder:'XXXX-XXXX-...',dir:'ltr'})),
  field(T('app.vault.extra_pw_label'),inp('passphrase','password','',{autocomplete:'off'})),
  el('button',{type:'submit'},T('app.vault.unlock_btn')),el('button',{class:'ghost',type:'button',onclick:act(showUnlock)},T('app.vault.check_usb_again')),
  el('p',{class:'muted'},vs.reason?trErr(vs.reason):''));
 f.classList.add('glass');mount(el('div',{class:'center',id:'content'},brand(),sosBig(),f,demoChoice(),disclaimerCard()));
}
async function showCreateVault(){
 S.redo=showCreateVault;const vs=await api('GET','/api/vault/status');
 if(vs.state==='confirm_code'&&!S.codes){mount(el('div',{class:'center',id:'content'},el('div',{class:'card'},el('h2',null,T('app.vault.created_title')),
  el('p',null,T('app.vault.created_text')))));return}
 if(S.codes)return showRecoveryCode();
 const drives=vs.drives||[];
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);const pick=f.querySelector('input[name=usb]:checked');
   if(!pick)throw new Error(T('app.vault.choose_usb_first'));
   if(d.passphrase&&d.passphrase!==d.passphrase2)throw new Error(T('app.vault.pw_mismatch'));
   say(T('app.vault.creating'),'');const r=await api('POST','/api/vault/create',{usb:pick.value,passphrase:d.passphrase});S.codes=r.recovery_codes;say('','');showRecoveryCode()})},
  el('h2',null,T('app.vault.create_title')),
  el('p',null,T('app.vault.create_text')),
  el('ol',{class:'keys','aria-label':T('app.vault.two_of_three')},el('li',null,ic('pc'),el('b',null,num(1)),el('span',null,T('app.vault.key_computer'))),
   el('li',null,ic('usb'),el('b',null,num(2)),el('span',null,T('app.vault.key_usb'))),el('li',null,ic('print'),el('b',null,num(3)),el('span',null,T('app.vault.key_code')))),
  el('p',{class:'signed'},T('app.vault.two_of_three')),el('p',null,T('app.vault.simple')),
  el('p',{class:'muted'},T('app.vault.keep_apart')),
  el('fieldset',null,el('legend',null,T('app.vault.step1')),
   drives.length?drives.map(d=>el('label',{class:'inl'},el('input',{type:'radio',name:'usb',value:d,checked:drives.length===1?true:null}),' '+T('app.vault.usb_drive',{drive:d})))
   :el('p',{class:'bad'},T('app.vault.no_usb')),
   el('button',{class:'ghost',type:'button',onclick:act(showCreateVault)},T('app.vault.look_again'))),
  el('details',null,el('summary',null,T('app.vault.extra_pw_summary')),
   el('p',{class:'muted'},T('app.vault.extra_pw_hint')),
   field(T('app.vault.extra_pw'),inp('passphrase','password','',{autocomplete:'new-password'})),field(T('app.vault.extra_pw_again'),inp('passphrase2','password','',{autocomplete:'new-password'}))),
  el('button',{type:'submit',disabled:drives.length?null:true},T('app.vault.create_btn')));
 f.classList.add('glass');mount(el('div',{class:'center',id:'content'},brand(),sosBig(),f,demoChoice(),disclaimerCard()));
}
function showRecoveryCode(){
 S.redo=showRecoveryCode;const code=S.codes[0];
 const save=()=>{const t=T('app.vault.rc_file',{date:new Date().toISOString().slice(0,10),code});
  const a=el('a',{href:URL.createObjectURL(new Blob([t],{type:'text/plain'})),download:'Stark_Covenant_recovery_code.txt'});document.body.append(a);a.click();a.remove()};
 const ok=el('input',{type:'checkbox',id:'rcok'});
 const f=el('form',{class:'card',onsubmit:act(async()=>{if(!ok.checked)throw new Error(T('app.vault.tick_first'));
   await api('POST','/api/vault/confirm',{code:formData(f).code});S.codes=null;say(T('app.vault.ready'),'ok');location.reload()})},
  el('h2',null,T('app.vault.step2')),
  el('div',{id:'rcPrint'},el('p',null,T('app.vault.rc_title')),el('p',{class:'code',id:'rc'},code)),
  el('p',null,T('app.vault.rc_once')),
  el('button',{type:'button',onclick:()=>{document.body.classList.add('printing');window.print();document.body.classList.remove('printing')}},T('app.common.print')),
  el('button',{class:'ghost',type:'button',onclick:save},T('app.vault.save_txt')),
  el('p',null,el('label',{for:'rcok',style:'display:inline;color:inherit'},ok,' '+T('app.vault.rc_tick'))),
  el('h3',null,T('app.vault.step3')),
  field(T('app.vault.rc_label'),inp('code','text','',{autocomplete:'off',spellcheck:'false',required:true,dir:'ltr'})),
  el('button',{type:'submit'},T('app.vault.confirm_btn')));
 mount(el('div',{class:'center',id:'content'},f));
}
function showSetup(){
 S.redo=showSetup;
 const upd=el('input',{type:'checkbox',name:'updates_opt'});   // off by default (clinical setting)
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);delete d.updates_opt;d.updates=upd.checked;await api('POST','/api/setup',d);say(T('app.setup.done'),'ok');showLogin('')})},
  el('h2',null,T('app.setup.title')),el('p',{class:'muted'},S.st.plan.key==='baby'?T('app.setup.baby'):T('app.setup.admin')),
  field(T('app.field.username'),inp('username','text','',{required:true,autocomplete:'username',dir:'ltr'})),field(T('app.field.display_name'),inp('display')),
  field(T('app.setup.password'),inp('password','password','',{required:true,minlength:10,autocomplete:'new-password'})),
  field(T('app.setup.pin'),inp('pin','password','',{required:true,inputmode:'numeric',pattern:'[0-9]{4,12}'})),
  el('p',null,el('label',{class:'inl',style:'display:flex;gap:8px;align-items:flex-start;color:inherit'},upd,el('span',null,T('app.setup.updates_opt')))),
  el('button',{type:'submit'},T('app.setup.create_btn')));
 f.classList.add('glass');mount(el('div',{class:'center',id:'content'},brand(),sosBig(),f,S.st.demo?demoChoice():null,disclaimerCard()));
}
function showLogin(msg){
 S.redo=()=>showLogin(msg);S.inner=null;
 S.user=null;S.csrf=null;S.dirty=false;$('#lockBtn').hidden=true;$('#who').textContent='';$('#topSearch').replaceChildren();
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);await signedIn(await api('POST','/api/login',d))})},
  el('h2',null,T('app.login.title')),msg?el('p',{class:'bad',role:'alert'},msg):null,
  field(T('app.field.username'),inp('username','text','',{required:true,autocomplete:'username',dir:'ltr'})),
  field(T('app.field.password'),inp('password','password','',{required:true,autocomplete:'current-password'})),el('button',{type:'submit'},T('app.login.button')));
 f.classList.add('glass');
 const ex=S.st&&S.st.example&&S.st.example.ready?el('div',{class:['card','start'],id:'startCard'},el('h2',null,T('app.login.example_title')),
  el('p',null,T('app.login.example_text')),exampleBtn(true)):null;
 mount(el('div',{class:'center',id:'content'},brand(),sosBig(),ex,f,disclaimerCard()));
 const u=f.querySelector('input');if(u&&!ex)u.focus();
}
async function signedIn(r){
 S.csrf=r.csrf;S.user=r.user;S.idle=r.idle_seconds;S.last=Date.now();say('','');
 const pl=(r.user.prefs||{}).locale;
 if(pl&&pl!==I18N.locale){try{await setLocale(pl,false)}catch(e){}}
 await later();if(r.region)I18N.region=r.region;showMain()}
function brand(){return el('div',{class:'brand'},el('img',{src:lockup(),'data-lockup':'1',alt:'Stark Covenant',width:'260',height:'64'}))}
function disclaimerCard(){return el('div',{class:'card'},S.st&&S.st.channel==='beta'?legal('p','legal.beta_notice',null,{class:'bad'}):null,
 legal('p','legal.disclaimer',null,{class:'muted'}))}
function sosBig(){return S.st&&S.st.comms?el('a',{class:['sos','big'],href:'/comms/','aria-label':T('app.sos.aria')},T('app.sos.big')):null}
async function lock(msg){try{await api('POST','/api/logout',{})}catch(e){}showLogin(msg||T('app.login.locked'))}
$('#lockBtn').onclick=()=>lock(T('app.login.locked_signin'));
$('#aboutBtn').onclick=()=>showAbout();
['mousemove','keydown','click','scroll','touchstart'].forEach(e=>addEventListener(e,()=>{S.last=Date.now()},{passive:true}));
setInterval(()=>{if(S.user&&Date.now()-S.last>(S.idle||600)*1000)lock(T('app.login.idle'))},5000);

function here(b){document.querySelectorAll('nav.side [aria-current=page]').forEach(x=>x.removeAttribute('aria-current'));if(b)b.setAttribute('aria-current','page')}
function tool(icn,label,fn){return el('button',{class:'ghost',type:'button',onclick:act(async ev=>{here(ev.currentTarget);await fn()})},ic(icn),label)}
function showMain(keep){
 if(!keep)S.inner=null;S.redo=null;
 $('#lockBtn').hidden=false;chrome();
 const nav=el('nav',{class:'side','aria-label':T('app.main.nav_aria')});
 const q=inp('q','search','',{placeholder:T('app.main.search_ph'),'aria-label':T('app.main.search_aria')});
 const list=el('div',{class:'plist',role:'list','aria-labelledby':'navPat'});
 q.addEventListener('input',act(()=>loadPatients(q.value,list)));
 $('#topSearch').replaceChildren(el('div',{class:'srchwrap',role:'search'},ic('search'),q));
 if(can('demo.write'))nav.append(el('button',{type:'button',class:'nav-new',onclick:()=>newPatient()},ic('plus'),T('app.main.new_patient')));
 // phones: patients and tools fold into one menu so the screen's own content comes first (navFold)
 const more=el('details',{class:'navmore',open:true},el('summary',null,T('app.nav.menu')),el('div',{class:'navh',id:'navPat'},T('app.nav.patients')),list);nav.append(more);
 const tools=el('div',{class:'navtools',role:'group','aria-labelledby':'navWork'});
 if(can('users.manage'))tools.append(tool('users',T('app.main.users'),showUsers));
 if(can('audit.read'))tools.append(tool('audit',T('app.main.audit'),showAudit));
 if(can('backup.run'))tools.append(tool('backup',T('app.main.backups'),showBackups));
 if(can('users.manage'))tools.append(tool('key',T('app.main.licence'),showLicence));
 tools.append(tool('user',T('app.main.account'),async()=>showAccount()));
 more.append(...pmNav(),el('div',{class:'navh',id:'navWork'},T('app.nav.workspace')),tools,...serviceNav());
 const content=el('section',{class:'content',id:'content',tabindex:'-1','aria-label':T('app.main.content_aria')},home(null));
 mount(el('main',null,nav,content));navFold(true);
 loadPatients('',list).then(ps=>{const h=$('#home');if(h)h.replaceWith(home(ps))}).catch(e=>say(trErr(e.message),'err'));
}
function pline(p){return [el('span',{class:'av','aria-hidden':'true'},initials(p.name)),el('span',null,auto('span',p.name,{class:'pn'}),el('span',{class:'muted'},T('app.chart.dob_line',{dob:dday(p.dob)})+(p.mrn?' - '+p.mrn:'')))]}
function home(ps){
 const n=ps?ps.length:null;
 if(n===0&&can('demo.write'))return el('div',{class:'card',id:'home'},el('div',{class:'hero'},ic('plus'),el('div',null,el('h2',null,T('app.home.first_title')),
  el('p',null,T('app.home.first_text')),el('button',{type:'button',class:'primary',onclick:()=>newPatient()},ic('plus'),T('app.main.new_patient')))));
 const tour=startHere(ps);
 return el('div',{id:'home'},tour,el('div',{class:'card'},el('div',{class:'hero'},ic('search'),el('div',null,el('h2',null,T('app.home.title')),
  el('p',null,T('app.home.hint')),n?el('p',{class:'muted'},T('app.home.count',{n})):null,
  can('demo.write')?el('button',{type:'button',class:tour?'ghost':'primary',onclick:()=>newPatient()},ic('plus'),T('app.main.new_patient')):null)),
  n?el('div',{class:'pgrid'},ps.slice(0,24).map(p=>el('button',{class:['ghost','pcard'],type:'button',onclick:act(()=>openPatient(p.id))},...pline(p)))):null))}
// the example clinic's first screen: the everyday steps, each with the button that does it
function startHere(ps){
 if(!(S.user&&S.user.example)||!ps||!ps.length)return null;const p=ps[0],pin=(S.st.example||{}).pin||'';
 return el('div',{class:['card','start'],id:'startHere'},el('h2',null,T('app.start.here_title')),el('p',null,T('app.start.here_text')),
  el('ol',{class:'steps'},
   el('li',null,el('p',null,T('app.start.step_open')),el('button',{type:'button',class:'primary',id:'tourOpen',onclick:act(()=>openPatient(p.id))},T('app.start.open_chart',{name:p.name}))),
   el('li',null,el('p',null,T('app.start.step_note',{pin}))),
   can('demo.write')?el('li',null,el('p',null,T('app.start.step_patient'))):null,
   el('li',null,el('p',{class:'muted'},T('app.start.step_leave')))))}
// phones: fold the menu away whenever a screen opens, so that screen is what the person sees first
function navFold(first){const m=$('.navmore');if(!m)return;const narrow=matchMedia('(max-width:800px)').matches;
 if(first||narrow)m.open=!narrow}
addEventListener('resize',()=>{const m=$('.navmore');if(m&&!matchMedia('(max-width:800px)').matches)m.open=true});
function content(...n){const c=$('#content');S.dirty=false;c.replaceChildren(...n);c.focus();navFold()}
function inner(fn){S.inner=fn;return fn()}
async function loadPatients(q,list){
 if(!can('demo.read')){list.replaceChildren(el('p',{class:'muted'},T('app.main.no_chart_access')));return null}
 const r=await api('GET','/api/patients?q='+encodeURIComponent(q||''));
 list.replaceChildren(...r.patients.map(p=>el('div',{role:'listitem'},el('button',{type:'button','data-pid':p.id,'aria-current':String(p.id===S.pid),onclick:act(()=>openPatient(p.id))},...pline(p)))));
 if(!r.patients.length)list.append(el('p',{class:'muted'},T('app.main.no_patients')));
 return r.patients}
function addrBox(d){
 const parts={line1:d.addr_line1,line2:d.addr_line2,city:d.addr_city,region:d.addr_region,postal:d.addr_postal};
 const fmt=I18N.region&&I18N.region.address_format;const fields=RG?RG.addrFields(fmt):['line1','line2','city','region','postal'];
 const lbl=k=>k==='region'?TK('app.addr.region',fmt?fmt.region:'region'):k==='postal'?TK('app.addr.postal',fmt?fmt.postal:'postcode'):T('app.addr.'+k);
 const box=el('details',{open:Object.values(parts).some(Boolean)?true:null},el('summary',null,T('app.addr.structured')),
  el('p',{class:'muted'},T('app.addr.hint')),
  el('div',{class:'row'},fields.map(k=>field(lbl(k),inp('addr_'+k,'text',parts[k],{autocomplete:{line1:'address-line1',line2:'address-line2',city:'address-level2',region:'address-level1',postal:'postal-code'}[k]})))));
 return box}
function demoForm(d,onsave,title){
 d=d||{};const fam=(I18N.region||{}).name_order==='family';
 const given=field(T('app.field.given_name'),inp('given_name','text',d.given_name,{autocomplete:'given-name'}));
 const family=field(T('app.field.family_name'),inp('family_name','text',d.family_name,{autocomplete:'family-name'}));
 const f=el('form',{class:'card',onsubmit:act(async()=>{const x=formData(f);
   if(!x.name&&x.given_name&&x.family_name&&RG)x.name=RG.name(x);
   const a={line1:x.addr_line1,line2:x.addr_line2,city:x.addr_city,region:x.addr_region,postal:x.addr_postal};
   if(!x.address&&RG&&Object.values(a).some(Boolean))x.address=RG.addr(a).join(', ');
   x.addr_country=(I18N.region||{}).region||'';
   return onsave(x)})},el('h2',null,title),
  el('div',{class:'row'},field(T('app.field.full_name'),inp('name','text',d.name,{required:true,dir:'auto',autocomplete:'name'})),field(T('app.field.dob'),inp('dob','date',d.dob,{required:true}))),
  el('div',{class:'row'},...(fam?[family,given]:[given,family])),
  el('div',{class:'row'},field(T('app.field.sex'),inp('sex','text',d.sex)),field(T('app.field.mrn'),inp('mrn','text',d.mrn,{dir:'ltr'})),
   field(T('app.field.phone'),inp('phone','tel',d.phone,{dir:'ltr',autocomplete:'tel'}))),
  d.phone_e164?el('p',{class:'muted'},T('app.field.phone_stored',{e164:RG?RG.phone(d.phone_e164):d.phone_e164})):null,
  el('div',{class:'row'},field(T('app.field.email'),inp('email','email',d.email,{dir:'ltr',autocomplete:'email'})),field(T('app.field.emergency_contact'),inp('emergency_contact','text',d.emergency_contact))),
  field(T('app.field.address'),inp('address','text',d.address,{dir:'auto'})),addrBox(d),
  field(T('app.field.admin_notes'),el('textarea',{name:'notes',value:d.notes||'',style:'min-height:60px',dir:'auto'})),
  el('button',{type:'submit'},T('app.common.save')));
 return f}
function newPatient(){inner(()=>content(demoForm({},async d=>{const r=await api('POST','/api/patients',{demographics:d});say(T('app.chart.created'),'ok');
 await loadPatients('',document.querySelector('.plist'));await openPatient(r.id)},S.st.demo?T('app.chart.new_title_demo'):T('app.chart.new_title'))))}
async function openPatient(pid,keepTab){S.pid=pid;const r=await api('GET','/api/patient?id='+pid);S.chart=r.chart;if(!keepTab)S.tab='summary';inner(renderChart);here(null);
 document.querySelectorAll('.plist button').forEach(b=>b.setAttribute('aria-current',String(b.dataset.pid===pid)))}
async function reload(){const r=await api('GET','/api/patient?id='+S.pid);S.chart=r.chart;inner(renderChart)}
const PERM={allergies:'allergy.write',medications:'meds.write',problems:'problems.write'};
const MAIN={allergies:'substance',medications:'name',problems:'description'},KIC={allergies:'alert',medications:'pill',problems:'clip'};
const TONE={severe:'bad',moderate:'warn',mild:'info',active:'ok'};
function renderChart(){
 const c=S.chart,d=c.demographics;
 const tabs=[['summary',T('app.chart.tab_summary')]];if(c.encounters)tabs.push(['enc',T('app.chart.tab_enc')]);if(c.documents||can('doc.write'))tabs.push(['docs',T('app.chart.tab_docs')]);
 const pm=S.st.practice;if(pm){if(can('sched.read'))tabs.push(['pm_appt',T('app.pm.tab_appt')]);if(c.measures)tabs.push(['pm_meas',T('app.pm.tab_meas')],['pm_plan',T('app.pm.tab_plan')]);if(can('bill.read'))tabs.push(['pm_bill',T('app.pm.tab_bill')]);if(can('form.write'))tabs.push(['pm_form',T('app.pm.forms')]);if(can('clin.read'))tabs.push(['pm_letter',T('app.pm.tab_letters')]);
  if(S.st.care){if(can('clin.read'))tabs.push(['pm_safety',T('app.pm.tab_safety')]);if(can('psynote.write'))tabs.push(['pm_psy',T('app.pm.tab_psy')]);if(can('release.write'))tabs.push(['pm_release',T('app.pm.tab_release')])}}
 const tl=el('div',{class:'tabs',role:'tablist','aria-label':T('app.chart.tabs_aria')},tabs.map(([k,l])=>el('button',{type:'button',role:'tab','aria-selected':String(S.tab===k),onclick:()=>{S.tab=k;inner(renderChart)}},l)));
 const shown=RG?RG.name(d):d.name;
 const meta=el('div',{class:'pmeta'},chip(T('app.chart.dob_line',{dob:dday(d.dob)})),chip(d.sex||T('app.chart.sex_not_recorded')),d.mrn?chip(d.mrn,'',{dir:'ltr'}):null,
  c.synthetic?chip(T('app.chart.synthetic'),'warn'):null);
 const ex=S.st.modules.interop_bridge&&can('clin.read')?el('button',{class:'ghost',type:'button',id:'exportBtn',onclick:act(exportChart)},ic('down'),T('app.chart.export')):null;
 const head=el('div',{class:'card'},el('div',{class:'phead'},el('span',{class:'av','aria-hidden':'true'},initials(shown)),el('div',null,auto('h2',shown),meta),el('div',{class:'pacts'},ex)),
  allergyStrip(c),el('div',{id:'exportOut'}));
 if(pm&&can('clin.read'))head.querySelector('.pacts').append(...['pdf','json'].map(f=>el('button',{class:'ghost',type:'button',onclick:act(()=>download('GET','/api/pm/export.'+f+'?patient='+S.pid,null,'chart-'+S.pid+'.'+f))},ic('down'),T('app.pm.export_'+f))));
 let body;if(S.tab==='enc')body=renderEncounters();else if(S.tab==='docs')body=renderDocs();else if(S.tab.startsWith('pm_')){body=el('div');pmHook('tab',S.tab,body)}else body=renderSummary();
 content(head,tl,body);const sel=tl.querySelector('[aria-selected=true]');if(sel&&tl.scrollWidth>tl.clientWidth)tl.scrollLeft=sel.offsetLeft-tl.offsetLeft-16}
function allergyStrip(c){
 if(!c.allergies)return null;const a=c.allergies.filter(x=>x.status!=='entered_in_error'&&x.status!=='inactive');
 return el('div',{class:['alg',a.length?'':'none'],role:'note'},ic('alert'),el('b',null,T('app.items.allergies')),
  a.length?a.map(x=>chip([x.substance,x.severity&&x.severity!=='unknown'?TK('app.choice',x.severity):''].filter(Boolean).join(' - '),TONE[x.severity]||'',{dir:'auto'}))
  :el('span',null,T('app.chart.allergies_none')))}
function noteChip(n){return n.status!=='signed'?chip(T('app.chip.draft'),'warn'):n.signature_valid?chip(T('app.chip.signed'),'ok'):chip(T('app.chip.sig_invalid'),'bad')}
function renderSummary(){
 const c=S.chart,w=el('div'),g=el('div',{class:'grid'});
 if(c.vitals)g.append(vitalsCard(c));
 if(c.allergies)for(const k of ['allergies','problems','medications'])g.append(itemCard(k));
 if(c.notes)g.append(notesCard(c));
 if(g.childElementCount)w.append(g);
 if(can('demo.write')){const f=demoForm(c.demographics,async d=>{await api('POST','/api/patient/update',{id:S.pid,demographics:d});say(T('app.common.saved'),'ok');await reload()},T('app.chart.demographics'));
  w.append(c.allergies?el('details',{class:'card'},el('summary',null,T('app.chart.demographics')),f):f)}
 else{const d=c.demographics;const lines=RG&&d.addr_city?RG.addr({line1:d.addr_line1,line2:d.addr_line2,city:d.addr_city,region:d.addr_region,postal:d.addr_postal}):[d.address||''];
  w.append(el('div',{class:'card'},el('h3',null,T('app.chart.demographics')),el('p',{class:'ltr'},d.phone_e164&&RG?RG.phone(d.phone_e164):(d.phone||'')),...lines.map(x=>auto('p',x))))}
 return w}
function vitalsCard(c){
 const a=c.vitals.filter(v=>v.status==='active'),v=a[a.length-1];
 const card=el('div',{class:['card','wide']},el('div',{class:'ch'},el('h3',null,ic('pulse'),T('app.chart.latest_vitals')),v?el('span',{class:'muted'},fmt(v.at)):null));
 card.append(v?el('div',{class:'vit'},Object.entries(v.values).map(([k,x])=>{const[n,u]=RG?RG.vital(k,x):[x,''];
   return el('div',null,el('span',null,RG?T('app.vital.short_'+k):k),el('b',{dir:'ltr'},num(n),u?el('small',null,' '+u):null))}))
  :el('div',{class:'empty'},ic('pulse'),el('p',null,T('app.chart.no_vitals')),c.encounters&&can('vitals.write')?el('button',{type:'button',class:'ghost',onclick:()=>{S.tab='enc';inner(renderChart)}},T('app.vital.record')):null));
 return card}
function itemCard(k){
 const c=S.chart,fields=S.st.item_kinds[k],choices=S.st.item_choices[k]||{},m=MAIN[k];
 const val=(f,v)=>choices[f]?TK('app.choice',v):(['noted','start','end','onset'].includes(f)?dday(v):v);
 const card=el('div',{class:'card'},el('div',{class:'ch'},el('h3',null,ic(KIC[k]),T('app.items.'+k)),
  can(PERM[k])?el('button',{class:'ghost',type:'button',onclick:()=>card.append(itemForm(k,fields,choices,null))},ic('plus'),T('app.common.add')):null));
 const rows=c[k].map(it=>{const sub=fields.filter(f=>it[f]&&f!==m&&f!=='status'&&f!=='severity').map(f=>TK('app.field',f)+': '+val(f,it[f])).join(' · ');
  return el('li',{class:it.status==='entered_in_error'?'x':null},el('div',{class:'it'},auto('div',it[m]||'',{class:'t'}),sub?auto('div',sub,{class:'sub'}):null,
    el('div',{class:'meta'},(it.recorded_by_name||'')+' '+fmt(it.recorded_at)+(it.history.length?' '+T('app.items.edited',{n:it.history.length}):'')),
    el('div',{class:'pmeta'},it.severity&&it.severity!=='unknown'?chip(TK('app.field','severity')+': '+val('severity',it.severity),TONE[it.severity]):null,
     it.status?chip(val('status',it.status),TONE[it.status]):null)),
   can(PERM[k])?el('button',{class:'ghost',type:'button',onclick:()=>card.append(itemForm(k,fields,choices,it))},T('app.common.edit')):null)});
 card.append(rows.length?el('ul',{class:'items'},rows):el('div',{class:'empty'},el('p',null,T('app.items.none'))));
 if(k!=='problems')card.append(el('p',{class:'muted'},T('app.items.no_interactions')));
 return card}
function notesCard(c){
 const ns=[...c.notes].reverse().slice(0,4);
 return el('div',{class:['card','wide']},el('div',{class:'ch'},el('h3',null,ic('note'),T('app.chart.recent_notes')),
   c.encounters?el('button',{class:'ghost',type:'button',onclick:()=>{S.tab='enc';inner(renderChart)}},T('app.chart.tab_enc')):null),
  ns.length?el('ul',{class:'items'},ns.map(n=>el('li',null,el('div',{class:'it'},auto('div',n.title,{class:'t'}),el('div',{class:'meta'},T('app.note.by',{name:n.author_name||n.author,when:fmt(n.created)}))),noteChip(n))))
  :el('div',{class:'empty'},ic('note'),el('p',null,T('app.chart.no_notes')),c.encounters&&can('note.write')?el('button',{type:'button',class:'ghost',onclick:()=>{S.tab='enc';inner(renderChart)}},T('app.note.new')):null))}
function utc(iso){const d=new Date(iso);return isNaN(d)?String(iso||''):d.toISOString().slice(0,19).replace('T',' ')}
async function exportChart(){// FHIR R4 Bundle + HL7 v2 message exactly as the server built them; only written to two files here
 const r=(await api('GET','/api/interop/export?id='+encodeURIComponent(S.pid))).export;
 const id=String(S.pid).replace(/[^A-Za-z0-9_-]/g,'_'),fj='patient-'+id+'.fhir.json',fh='patient-'+id+'.hl7';
 const b1=new Blob([JSON.stringify(r.fhir,null,2)],{type:'application/fhir+json'}),b2=new Blob([r.hl7v2],{type:'text/plain'});
 saveBlob(b1,fj);saveBlob(b2,fh);
 const when=T('app.chart.exported',{time:utc(r.fhir&&r.fhir.timestamp)});
 $('#exportOut').replaceChildren(el('div',{class:'card'},el('p',{class:'signed',id:'exportTime'},when),
  el('div',{class:'dl'},[[b1,fj],[b2,fh]].map(([b,n])=>el('a',{href:URL.createObjectURL(b),download:n,dir:'ltr'},ic('down'),n))),
  r.fhir_errors&&r.fhir_errors.length?el('p',{class:'bad'},T('app.chart.export_issues',{n:r.fhir_errors.length})):null,
  r.notice?el('p',{class:'muted',dir:'auto'},r.notice):null));say(when,'ok')}
function itemForm(kind,fields,choices,it){
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);
  if(it)await api('POST','/api/items/update',{patient:S.pid,kind,item_id:it.id,item:d});else await api('POST','/api/items',{patient:S.pid,kind,item:d});
  say(T('app.common.saved'),'ok');await reload()})},el('h3',null,T(it?'app.items.edit_title':'app.items.add_title',{list:T('app.items.'+kind)})));
 for(const fl of fields){const v=it?it[fl]:'';let x;
  if(choices[fl])x=el('select',{name:fl},choices[fl].map(o=>el('option',{value:o,selected:o===v?true:null},TK('app.choice',o))));
  else if(['noted','start','end','onset'].includes(fl))x=inp(fl,'date',v);else x=inp(fl,'text',v,{dir:'auto'});
  f.append(field(TK('app.field',fl),x))}
 f.append(el('button',{type:'submit'},T('app.common.save')),el('button',{class:'ghost',type:'button',onclick:()=>f.remove()},T('app.common.cancel')));return f}
const VITALS=['bp_sys','bp_dia','pulse','temp_c','resp','spo2','weight_kg','height_cm','glucose_mmol'];
function vitalLabel(k){return T('app.vital.'+k,{unit:RG?RG.unit(k):''})}
function vitalShow(k,x){if(!RG)return k+' '+x;const[v,u]=RG.vital(k,x);return T('app.vital.short_'+k)+' '+num(v)+(u?' '+u:'')}
function renderEncounters(){
 const c=S.chart,w=el('div');
 if(can('encounter.write')){const f=el('form',{class:'card',onsubmit:act(async()=>{await api('POST','/api/encounters',Object.assign({patient:S.pid},formData(f)));say(T('app.enc.created'),'ok');await reload()})},
  el('h3',null,T('app.enc.new')),el('div',{class:'row'},field(T('app.field.date'),inp('date','date',new Date().toISOString().slice(0,10))),
  field(T('app.field.type'),el('select',{name:'type'},S.st.encounter_types.map(t=>el('option',{value:t},TK('app.enctype',t))))),field(T('app.field.reason'),inp('reason','text','',{dir:'auto'}))),el('button',{type:'submit'},T('app.common.create')));w.append(f)}
 for(const e of [...c.encounters].reverse()){
  const card=el('div',{class:'card'},auto('h3',dday(e.date)+' - '+TK('app.enctype',e.type)+(e.reason?' - '+e.reason:'')),el('div',{class:'muted'},T('app.enc.opened_by',{name:e.created_by_name||e.created_by})));
  if(c.vitals){const vs=c.vitals.filter(v=>v.encounter_id===e.id);
   if(vs.length)card.append(wrap(el('table',null,el('thead',null,el('tr',null,el('th',null,T('app.vital.col_time')),el('th',null,T('app.vital.col_values')),el('th',null,T('app.vital.col_by')),el('th',null,el('span',{class:'sr'},T('app.vital.col_actions'))))),
    el('tbody',null,vs.map(v=>el('tr',null,el('td',null,fmt(v.at)),el('td',{class:v.status!=='active'?'muted':null},
     (v.status!=='active'?'['+T('app.vital.in_error',{reason:v.error_reason})+'] ':'')+Object.entries(v.values).map(([k,x])=>vitalShow(k,x)).join(', ')+(v.comment?' - '+v.comment:'')),
     el('td',null,v.by_name||v.by),el('td',null,can('vitals.write')&&v.status==='active'?el('button',{class:'warnbtn',type:'button',onclick:act(async()=>{
      const reason=prompt(T('app.vital.error_prompt'));if(!reason)return;
      await api('POST','/api/vitals/error',{patient:S.pid,vitals:v.id,reason});await reload()})},T('app.vital.mark_error')):'')))))));
   if(can('vitals.write')){const vf=el('form',{onsubmit:act(async()=>{const d=formData(vf);const comment=d.comment;delete d.comment;
     for(const k of VITALS)if(d[k]!==''&&d[k]!=null&&RG)d[k]=RG.canon(k,d[k]);
     await api('POST','/api/vitals',{patient:S.pid,encounter:e.id,values:Object.assign(d,{comment})});say(T('app.vital.saved'),'ok');await reload()})},
     el('div',{class:'row'},VITALS.map(k=>field(vitalLabel(k),inp(k,'number','',{step:'any',dir:'ltr'})))),field(T('app.field.comment'),inp('comment','text','',{dir:'auto'})),
     el('p',{class:'muted'},T('app.vital.units_note')),el('button',{type:'submit'},T('app.vital.add')));
    card.append(el('details',null,el('summary',null,T('app.vital.record')),vf))}}
  if(c.notes){for(const n of c.notes.filter(n=>n.encounter_id===e.id))card.append(renderNote(n));
   if(can('note.write')){const nf=el('form',{onsubmit:act(async()=>{await api('POST','/api/notes',Object.assign({patient:S.pid,encounter:e.id},formData(nf)));say(T('app.note.draft_created'),'ok');await reload()})},
     field(T('app.field.title'),inp('title','text',T('app.note.default_title'),{dir:'auto'})),field(T('app.field.note'),el('textarea',{name:'text',dir:'auto'})),el('button',{type:'submit'},T('app.note.save_draft')));
    pmHook('noteform',nf);card.append(el('details',null,el('summary',null,T('app.note.new')),nf))}}
  w.append(card)}
 if(!c.encounters.length)w.append(el('div',{class:'empty'},el('p',null,T('app.enc.none')),can('encounter.write')?el('p',{class:'muted'},T('app.enc.none_hint')):null));
 return w}
function pinInput(){return inp('pin','password','',{inputmode:'numeric',autocomplete:'off',pattern:'[0-9]{4,12}',required:true})}
function renderNote(n){
 const box=el('div',{class:'card'},el('div',{class:'ch'},auto('h3',n.title),noteChip(n)),el('div',{class:'muted'},T('app.note.by',{name:n.author_name||n.author,when:fmt(n.created)})));
 if(n.status==='signed'){
  box.append(auto('pre',n.text,{class:'note'}),el('div',{class:n.signature_valid?'signed':'bad'},T(n.signature_valid?'app.note.signed_by':'app.note.sig_invalid_by',{name:n.signed_by_name||n.signed_by,when:fmt(n.signed_at)})));
  if(n.ai_provenance&&n.ai_provenance.length)box.append(el('div',{class:'muted'},T('app.note.ai_accepted',{n:n.ai_provenance.length})));
  for(const a of n.addenda||[])box.append(el('div',{class:'card'},el('strong',null,T('app.note.addendum')),auto('pre',a.text,{class:'note'}),
   el('div',{class:a.signature_valid?'signed':'bad'},T(a.signature_valid?'app.note.add_signed_by':'app.note.add_invalid_by',{name:a.author_name||a.author,when:fmt(a.at)}))));
  if(can('note.sign')){const af=el('form',{onsubmit:act(async()=>{const d=formData(af);await api('POST','/api/notes/addendum',{patient:S.pid,note:n.id,text:d.text,pin:d.pin});say(T('app.note.addendum_signed'),'ok');await reload()})},
    field(T('app.note.addendum_text'),el('textarea',{name:'text',style:'min-height:80px',dir:'auto'})),field(T('app.field.your_pin'),pinInput()),el('button',{type:'submit'},T('app.note.sign_addendum')));
   box.append(el('details',null,el('summary',null,T('app.note.add_addendum')),af))}
  if(n.cosign)pmHook('note',n,box);return box}
 const mine=n.author===S.user.id;
 if(!mine){box.append(auto('pre',n.text,{class:'note'}),el('div',{class:'muted'},T('app.note.draft_others')));return box}
 const ta=el('textarea',{name:'text','aria-label':T('app.note.text_aria'),value:n.text,dir:'auto'});
 const ti=inp('title','text',n.title,{'aria-label':T('app.note.title_aria'),dir:'auto'});
 const pin=pinInput();pin.required=false;
 const aiBox=el('div');
 box.append(el('p',{class:'bad'},T('app.note.draft_banner')),field(T('app.field.title'),ti),field(T('app.field.note'),ta),
  el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/notes/update',{patient:S.pid,note:n.id,text:ta.value,title:ti.value});say(T('app.note.draft_saved'),'ok')})},ic('note'),T('app.note.save_draft')),
  el('div',{class:'sign'},field(T('app.note.pin_label'),pin),
  el('button',{type:'button',onclick:act(async()=>{await api('POST','/api/notes/update',{patient:S.pid,note:n.id,text:ta.value,title:ti.value});
   await api('POST','/api/notes/sign',{patient:S.pid,note:n.id,pin:pin.value});say(T('app.note.signed_locked'),'ok');await reload()})},ic('lock'),T('app.note.sign_lock'))));
 if(can('ai.use')&&S.st.modules.self_learner)box.append(el('button',{class:'ghost',type:'button',onclick:act(async()=>{
   const last=(ta.value.split('\n').pop()||'').slice(-60);const r=await api('GET','/api/ai/suggest?patient='+S.pid+'&prefix='+encodeURIComponent(last));
   aiBox.replaceChildren(el('p',{class:'muted'},r.available?(r.suggestions.length?T('legal.ai_label'):T('app.ai.none_yet')):T('app.ai.unavailable')),
    ...r.suggestions.map(s=>el('div',{class:'ai'},auto('div',s.text),el('div',{class:'muted'},TK('app.ai.kind',s.kind)+' - '+s.detail+' - '+T('legal.ai_label')),
     (s.kind==='autocomplete'||s.kind==='template')?el('button',{class:'ghost',type:'button',onclick:act(async()=>{
      if(!pin.value){say(T('app.ai.pin_first'),'err');pin.focus();return}
      await api('POST','/api/notes/update',{patient:S.pid,note:n.id,text:ta.value,title:ti.value});
      await api('POST','/api/ai/accept',{patient:S.pid,note:n.id,text:s.text,kind:s.kind,suggestion_id:s.id,pin:pin.value});say(T('app.ai.inserted'),'ok');await reload()})},T('app.ai.accept')):'')))})},T('app.ai.suggest_btn')),aiBox);
 return box}
function renderDocs(){
 const c=S.chart,w=el('div',{class:'card'},el('h3',null,T('app.docs.title')));
 if(c.documents){w.append(c.documents.length?wrap(el('table',null,el('tbody',null,c.documents.map(d=>el('tr',null,
  el('td',null,el('a',{href:'/api/document?patient='+S.pid+'&doc='+d.id,download:d.name,dir:'auto'},d.name)),el('td',{class:'muted'},kib(d.size)),el('td',{class:'muted'},(d.uploaded_by_name||'')+' '+fmt(d.at))))))):el('div',{class:'empty'},el('p',null,T('app.docs.none')),can('doc.write')?el('p',{class:'muted'},T('app.docs.none_hint')):null))}
 else w.append(el('p',{class:'muted'},T('app.docs.upload_only')));
 if(can('doc.write')){const fi=el('input',{type:'file','aria-label':T('app.docs.choose_aria')});
  w.append(field(T('app.docs.choose_aria'),fi),el('button',{type:'button',onclick:act(async()=>{const file=fi.files[0];if(!file)throw new Error(T('app.docs.choose_first'));
   const b64=await new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(String(r.result).split(',')[1]||'');r.onerror=rej;r.readAsDataURL(file)});
   await api('POST','/api/documents',{patient:S.pid,name:file.name,mime:file.type,b64});say(T('app.docs.uploaded'),'ok');await reload()})},T('app.docs.upload')))}
 return w}
async function showUsers(){
 S.inner=showUsers;const r=await api('GET','/api/users');const roles=S.st.roles;
 const tbl=el('table',null,el('thead',null,el('tr',null,el('th',null,T('app.users.col_user')),el('th',null,T('app.users.col_roles')),el('th',null,T('app.users.col_state')),el('th',null,el('span',{class:'sr'},T('app.vital.col_actions'))))),
  el('tbody',null,r.users.map(u=>{const boxes=roles.map(ro=>el('label',{class:'inl'},el('input',{type:'checkbox',value:ro,checked:u.roles.includes(ro)?true:null}),' '+TK('app.role',ro)));
   return el('tr',null,el('td',{dir:'auto'},u.display+' ('+u.username+')'),el('td',null,u.roles.includes('owner')?T('app.users.owner_all'):boxes),
    el('td',null,chip(u.active?T('app.users.active'):T('app.users.disabled'),u.active?'ok':'bad'),u.locked?[' ',chip(T('app.users.locked'),'warn')]:null),
    el('td',null,S.st.plan.roles&&!u.roles.includes('owner')?el('button',{class:'ghost',type:'button',onclick:act(async()=>{const sel=boxes.map(b=>b.querySelector('input')).filter(i=>i.checked).map(i=>i.value);
      await api('POST','/api/users/update',{id:u.id,roles:sel});say(T('app.users.roles_saved'),'ok');await showUsers()})},T('app.users.save_roles')):'',
     el('button',{class:u.active?'warnbtn':'ghost',type:'button',onclick:act(async()=>{if(u.active&&!confirm(T('app.users.disable_confirm',{name:u.display})))return;
      await api('POST','/api/users/update',{id:u.id,active:!u.active});await showUsers()})},u.active?T('app.users.disable'):T('app.users.enable')),
     u.locked?el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/users/update',{id:u.id,unlock:true});await showUsers()})},T('app.users.unlock')):''))})));
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);d.roles=[...f.querySelectorAll('input[type=checkbox]:checked')].map(i=>i.value);
   await api('POST','/api/users',d);say(T('app.users.created'),'ok');await showUsers()})},el('h3',null,T('app.users.add_title',{plan:S.st.plan.label,max:S.st.plan.max_users})),
  field(T('app.field.username'),inp('username','text','',{dir:'ltr'})),field(T('app.field.display_name'),inp('display')),field(T('app.users.temp_pw'),inp('password','password')),field(T('app.field.signing_pin'),inp('pin','password')),
  el('fieldset',null,el('legend',null,T('app.users.col_roles')),roles.map(ro=>el('label',{class:'inl'},el('input',{type:'checkbox',value:ro}),' '+TK('app.role',ro)))),
  el('button',{type:'submit'},T('app.users.create_btn')));
 const sup=el('div');pmHook('users',r.users,sup);
 content(el('div',{class:'card'},el('h2',null,T('app.main.users')),wrap(tbl)),S.st.plan.roles?f:el('p',{class:'muted'},T('app.users.baby_single')),sup,...(can('users.manage')?[clinicLocaleCard()]:[]))}
async function showAudit(){
 S.inner=showAudit;const r=await api('GET','/api/audit?limit=200');const out=el('div'),acc=el('div');pmHook('audit',acc);
 content(el('div',{class:'card'},el('h2',null,T('app.main.audit')),el('p',{class:'muted'},T('app.audit.summary',{n:r.total})),
  el('button',{type:'button',onclick:act(async()=>{const v=(await api('GET','/api/audit/verify')).result;
   out.replaceChildren(el('p',{class:v.ok?'signed':'bad'},v.ok?T('app.audit.verified',{entries:v.entries,files:v.files_checked}):T('app.audit.tampered')),...v.problems.map(p=>el('div',{class:'bad',dir:'ltr'},p)))})},T('app.audit.verify')),out,
  wrap(el('table',null,el('thead',null,el('tr',null,['app.audit.col_seq','app.audit.col_time','app.audit.col_actor','app.audit.col_action','app.audit.col_target'].map(h=>el('th',null,T(h))))),el('tbody',null,r.entries.map(e=>el('tr',null,el('td',null,e.seq),el('td',null,fmt(e.ts)),el('td',{class:'ltr'},e.actor),el('td',{class:'ltr'},e.action),el('td',{class:'ltr'},e.target))))))),acc)}
async function showBackups(){
 S.inner=showBackups;const r=await api('GET','/api/backups');
 content(el('div',{class:'card'},el('h2',null,T('app.main.backups')),el('p',{class:'muted'},S.st.demo?T('app.backup.text_demo'):T('app.backup.text')),
  el('button',{type:'button',onclick:act(async()=>{await api('POST','/api/backup',{});say(T('app.backup.written'),'ok');await showBackups()})},T('app.backup.now')),
  el('ul',{class:'items'},r.backups.map(b=>el('li',{class:'ltr'},ic('backup'),el('div',{class:'it'},b.name),el('span',{class:'muted'},kib(b.size)))))))}
async function showLicence(){
 S.inner=showLicence;const r=await api('GET','/api/licence');
 if(!r.available){content(el('div',{class:'card'},el('h2',null,T('app.lic.title')),el('p',{class:'muted'},T('app.lic.unavailable'))));return}
 const st=r.status||{};
 const key=el('textarea',{name:'token',style:'min-height:80px',dir:'ltr',spellcheck:'false',autocomplete:'off'});
 const f=el('form',{class:'card',onsubmit:act(async()=>{const x=await api('POST','/api/licence/activate',{token:key.value});
   S.st.plan=x.plan;chrome();say(x.status.ok?T('app.lic.ok',{plan:x.status.plan_name}):T('app.lic.not_ok'),x.status.ok?'ok':'err');await showLicence()})},
  el('h3',null,T('app.lic.activate_title')),el('p',{class:'muted'},T('app.lic.how')),field(T('app.lic.paste'),key),el('button',{type:'submit'},T('app.lic.activate')));
 const paid=st.ok&&st.plan&&st.plan!=='BABY';
 const de=paid?el('button',{class:'warnbtn',type:'button',onclick:act(async()=>{if(!confirm(T('app.lic.deactivate_confirm')))return;
   const x=await api('POST','/api/licence/deactivate',{});S.st.plan=x.plan;chrome();say(T('app.lic.deactivated',{n:x.devices_left}),'ok');await showLicence()})},T('app.lic.deactivate')):null;
 const price=p=>p.price_usd?money(p.price_usd,'USD')+' '+TK('app.lic.per',p.period):T('app.lic.free');
 const plans=wrap(el('table',null,el('thead',null,el('tr',null,['app.lic.col_plan','app.lic.col_price','app.lic.col_seats','app.lic.col_scale'].map(h=>el('th',null,T(h))))),
  el('tbody',null,(r.plans||[]).map(p=>el('tr',{'aria-current':p.plan===st.plan?'true':null},el('td',null,p.name),el('td',null,price(p)),el('td',null,num(p.seats)),el('td',null,TK('app.scale',String(p.max_scale).toLowerCase())))))));
 content(el('div',{class:'card'},el('h2',null,T('app.lic.title')),el('p',{class:'muted'},T('app.lic.intro')),
   el('p',null,el('b',null,T('app.lic.current',{plan:st.plan_name||S.st.plan.label}))),el('p',{class:st.ok?'signed':'bad',dir:'auto'},st.message||''),enText(),
   st.expires?el('p',{class:'muted'},T('app.lic.expires',{date:dday(st.expires)})):null,
   el('p',{class:'muted'},T('app.lic.device_code'),' ',el('code',{class:'ltr'},r.device_code||'')),de),
  f,await updatesCard(),el('div',{class:'card'},el('h3',null,T('app.lic.plans_title')),plans,el('h3',null,T('app.lic.fair_title')),el('ul',null,[1,2,3,4,5].map(n=>el('li',null,T('app.lic.fair'+n))))))}
async function updatesCard(){
 let u;try{u=await api('GET','/api/updates')}catch(e){return el('p',{class:'bad'},trErr(e.message))}
 const box=el('div',{class:'card'},el('h3',null,T('app.upd.title')),el('p',{class:'muted'},T('app.upd.intro')),
  el('p',null,T('app.upd.version',{version:u.version,channel:TK('app.upd.ch',u.channel)})));
 if(!u.available){box.append(el('p',{class:'muted'},T('app.upd.missing')));return box}
 const out=el('p',{dir:'auto'});
 box.append(el('p',{class:u.checks?'signed':'muted'},T(u.checks?'app.upd.checks_on':'app.upd.checks_off')),
  u.channel==='beta'?el('p',{class:'muted'},T('app.upd.promote')):null,
  el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/updates/settings',{checks:!u.checks});say(T('app.upd.saved'),'ok');await showLicence()})},T(u.checks?'app.upd.turn_off':'app.upd.turn_on')),
  u.checks?el('button',{type:'button',onclick:act(async()=>{out.textContent=T('app.upd.checking');const r=await api('POST','/api/updates/check',{});out.textContent=r.detail||''})},T('app.upd.check_now')):null,out);
 return box}
function showAccount(){
 S.inner=showAccount;
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);await api('POST','/api/me/secret',d);say(T('app.account.updated'),'ok');f.reset()})},
  el('h2',null,T('app.main.account')),field(T('app.account.current_pw'),inp('current_password','password','',{required:true,autocomplete:'current-password'})),
  field(T('app.account.new_pw'),inp('new_password','password','',{autocomplete:'new-password'})),field(T('app.account.new_pin'),inp('new_pin','password','',{inputmode:'numeric'})),
  el('button',{type:'submit'},T('app.common.save')));content(f,regionCard())}
// --- language and region settings (per user; stored values never change) ----------------------------
function regionForm(cur,onsave,title,intro){
 if(!RG)return el('p',{class:'muted'},T('app.region.unavailable'));
 const R=I18N.region||{};const p=cur||{};
 let names=null;try{names=new Intl.DisplayNames([I18N.intl],{type:'region'})}catch(e){}
 let dnames=null;try{dnames=new Intl.DateTimeFormat(I18N.intl,{weekday:'long',timeZone:'UTC'})}catch(e){}
 const dayName=n=>dnames?dnames.format(new Date(Date.UTC(2024,0,n))):String(n);
 const opt=(v,l,sel)=>el('option',{value:v,selected:sel?true:null},l);
 const def=T('app.region.default_opt');
 const lang=el('select',{name:'locale'},(I18N.locales||[]).map(l=>opt(l.code,l.native+(l.status==='source'||l.status==='reviewed'?'':' - '+T('app.lang.draft_tag')),l.code===(p.locale||I18N.locale))));
 const reg=el('select',{name:'region'},opt('',def,!p.region),(R.regions||[]).map(cc=>[cc,names?names.of(cc):cc]).sort((a,b)=>a[1].localeCompare(b[1],I18N.intl)).map(([cc,n])=>opt(cc,n+' ('+cc+')',cc===p.region)));
 const sel=(name,vals,lab)=>el('select',{name},opt('',def,p[name]==null||p[name]===''),vals.map(v=>opt(String(v),lab(v),String(p[name])===String(v))));
 const date=sel('date',['DD/MM/YYYY','MM/DD/YYYY','DD.MM.YYYY','DD-MM-YYYY','YYYY-MM-DD','YYYY/MM/DD','YYYY.MM.DD'],v=>RG.date('2026-03-14',v)+'  ('+T('app.region.date_'+v.replace(/[^A-Z]/g,'').toLowerCase())+')');
 const fd=sel('first_day',[1,2,3,4,5,6,7],dayName);
 const units=sel('units',['metric','us'],v=>T('app.region.units_'+v));
 const glu=sel('glucose',['mmol/L','mg/dL'],v=>v);
 const nord=sel('name_order',['given','family'],v=>T('app.region.order_'+v));
 const dig=sel('digits',['latn','native'],v=>T('app.region.digits_'+v));
 const h12=sel('hour12',['0','1'],v=>T(v==='1'?'app.region.clock12':'app.region.clock24'));
 let browserTz='';try{browserTz=Intl.DateTimeFormat().resolvedOptions().timeZone||''}catch(e){}
 const tz=inp('tz','text',p.tz||'',{placeholder:R.tz||browserTz,dir:'ltr',list:'tzlist',autocomplete:'off'});
 let zones=[];try{zones=Intl.supportedValuesOf('timeZone')}catch(e){zones=[R.tz,browserTz].filter(Boolean)}
 const dl=el('datalist',{id:'tzlist'},zones.map(z=>el('option',{value:z})));
 const now=new Date().toISOString();
 const prev=el('div',{class:'card',role:'region','aria-label':T('app.region.preview')},el('h3',null,T('app.region.preview')),
  el('p',null,T('app.region.pv_date',{date:RG.date(now.slice(0,10)),time:RG.ts(now)})),
  el('p',null,T('app.region.pv_week',{days:RG.weekDays(R.first_day).map(dayName).join(', ')})),
  el('p',null,T('app.region.pv_number',{n:num(1234567.89),money:money(1234.5,R.currency||'USD')})),
  el('p',null,T('app.region.pv_vitals',{temp:vitalShow('temp_c',37),weight:vitalShow('weight_kg',70),glucose:vitalShow('glucose_mmol',5.5)})),
  el('p',null,T('app.region.pv_name',{name:RG.name({given_name:T('app.region.sample_given'),family_name:T('app.region.sample_family')})})),
  el('p',{class:'ltr'},RG.phone('+254712345678')));
 const f=el('form',{class:'card',onsubmit:act(async()=>{const d=formData(f);const out={};for(const[k,v]of Object.entries(d))if(v!=='')out[k]=v;
   if(out.first_day)out.first_day=Number(out.first_day);if(out.hour12!=null)out.hour12=out.hour12==='1';await onsave(out)})},
  el('h2',null,title),el('p',{class:'muted'},intro),
  el('div',{class:'row'},field(T('app.lang.label'),lang),field(T('app.region.region'),reg)),
  el('div',{class:'row'},field(T('app.region.date'),date),field(T('app.region.first_day'),fd),field(T('app.region.clock'),h12)),
  el('div',{class:'row'},field(T('app.region.units'),units),field(T('app.region.glucose'),glu),field(T('app.region.name_order'),nord)),
  el('div',{class:'row'},field(T('app.region.tz'),tz),field(T('app.region.digits'),dig)),dl,
  el('p',{class:'muted'},T('app.region.stored_note')),
  el('button',{type:'submit'},T('app.common.save')));
 return el('div',null,f,prev)}
function regionCard(){
 return regionForm((S.user&&S.user.prefs)||{},async out=>{const r=await api('POST','/api/me/prefs',{prefs:out});S.user.prefs=r.prefs;
   if(r.prefs.locale&&r.prefs.locale!==I18N.locale){await setLocale(r.prefs.locale,false)}I18N.region=r.region;say(T('app.region.saved'),'ok');rerender()},
  T('app.region.title'),T('app.region.intro'))}
function clinicLocaleCard(){
 const box=el('div');
 api('GET','/api/settings/locale').then(r=>{box.replaceChildren(regionForm(r.prefs||{},async out=>{await api('POST','/api/settings/locale',{prefs:out});say(T('app.region.clinic_saved'),'ok')},
  T('app.region.clinic_title'),T('app.region.clinic_intro')))}).catch(e=>box.replaceChildren(el('p',{class:'bad'},trErr(e.message))));
 return box}
function showAbout(){
 const lock=el('img',{src:lockup(),'data-lockup':'1',alt:'Stark Covenant',width:'280',height:'70',style:'max-width:100%;height:auto'});
 const credits=el('div',null,el('h3',null,T('app.about.credits_title')),el('ul',null,
  el('li',null,T('app.about.credit_mark')),el('li',null,T('app.about.credit_food')),el('li',null,T('app.about.credit_numbers')),
  legal('li','legal.credit_loinc'),legal('li','legal.credit_ucum'),el('li',null,T('app.about.credit_x12')),el('li',null,T('app.about.credit_fonts'))));
 const back=el('button',{type:'button',class:'ghost',id:'aboutBack',onclick:()=>{if(S.user){S.inner=null;showMain()}else boot()}},T('app.common.back'));
 const st=S.st,beta=st.channel==='beta',c=el('div',{class:'card'},back,el('h2',{class:'sr'},T('app.about.title')),lock,
  el('p',null,T('app.about.version',{version:'__VERSION__'}),beta?' ':null,beta?el('span',{class:['badge','beta'],id:'aboutBeta'},T('app.chrome.beta')):null),
  el('p',{class:'muted'},T('app.about.product')),
  beta?legal('p','legal.beta_notice',null,{class:'bad'}):null,
  legal('p','legal.disclaimer',null,{class:'bad'}),
  el('p',null,T('app.about.plans',{plans:Object.values(st.plans).map(p=>p.label+' '+p.price).join(' | '),plan:st.plan.label,message:st.plan.message})),
  el('p',{class:'muted'},T(st.demo?'app.about.enc_demo':'app.about.enc_vault')+' '+T('app.about.modules',{list:Object.entries(st.modules).map(([k,v])=>k+' '+T(v?'app.common.on':'app.common.off')).join(', ')})),
  el('p',{class:'muted'},T('app.about.language',{name:I18N.name,status:TK('app.lang.status',I18N.status)})),
  el('p',{class:'about-line'},T('app.about.line')),credits);
 if(S.user&&$('#content')){S.inner=showAbout;content(c)}else{S.redo=showAbout;mount(el('div',{class:'center',id:'content'},c))}}
// --- practice screens (practice_suite): script + catalog scope "pm" load on first use after sign-in ---------
let _pmP=null;
function pmReady(){if(!S.st||!S.st.practice)return Promise.reject(new Error(T('app.pm.unavailable')));
 if(!_pmP)_pmP=new Promise((ok,no)=>{const s=document.createElement('script');s.src='/assets/covenant-practice.js?v=__VERSION__';s.onload=ok;
  s.onerror=()=>{_pmP=null;no(new Error(T('app.pm.unavailable')))};document.head.append(s)});
 return _pmP.then(()=>PM.strings())}
function pmHook(k,...a){pmReady().then(()=>PM.hook[k](...a)).catch(e=>{if(e.message!=='locked')say(trErr(e.message),'err')})}
function pmNav(){if(!S.st.practice||!['sched.read','note.write','bill.read','form.design'].some(can))return [];
 const b=el('div',{role:'group','aria-labelledby':'navPm'}),go=k=>async()=>{await pmReady();await PM.view[k]()};
 if(!can('form.design')||can('sched.read'))b.append(navBtn(T('app.pm.today'),go('today'),'clock'));if(can('sched.read'))b.append(navBtn(T('app.pm.calendar'),go('cal'),'calendar'));
 if(can('bill.read'))b.append(navBtn(T('app.pm.billing'),go('billing'),'bill'));if(['sched.read','bill.read','clin.read'].some(can))b.append(navBtn(T('app.pm.dashboard'),go('dash'),'chart'));if(can('form.design'))b.append(navBtn(T('app.pm.forms'),go('forms'),'clip'));if(['sched.read','clin.read'].some(can))b.append(navBtn(T('app.pm.groups'),go('groups'),'users'));
 if(S.st.care){if(['sched.read','clin.read','bill.read'].some(can))b.append(navBtn(T('app.pm.todo'),go('todo'),'audit'));if(can('clin.read'))b.append(navBtn(T('app.pm.outcomes'),go('outcomes'),'pulse'))}
 return [el('div',{class:'navh',id:'navPm'},T('app.pm.nav')),b]}
// --- optional services (consent_center, persona_ai, nutrition_planner, coverage_navigator, research_desk) ---
function feat(k){return !!(S.st&&S.st.features&&S.st.features[k])}
function navBtn(label,fn,icn){return el('button',{class:'ghost',type:'button',onclick:act(ev=>{here(ev.currentTarget);return inner(fn)})},ic(icn),label)}
function serviceNav(){
 const box=el('div',{role:'group','aria-label':T('app.nav.services')});
 if(feat('consent'))box.append(navBtn(T('app.nav.consent'),showConsent,'consent'));
 if(feat('persona')&&(can('demo.read')||can('clin.read')))box.append(navBtn(T('app.nav.assistant'),showAssistant,'chat'));
 if(feat('nutrition')&&(can('clin.read')||can('demo.write')))box.append(navBtn(T('app.nav.nutrition'),showNutrition,'leaf'));
 if(feat('coverage')&&can('demo.read'))box.append(navBtn(T('app.nav.coverage'),()=>showCoverage(),'cover'));
 if(feat('research')&&can('clin.read'))box.append(el('a',{class:'navlink',href:'/research/'},ic('book'),T('app.nav.research')));
 return box.childElementCount?[el('div',{class:'navh','aria-hidden':'true'},T('app.nav.services')),box]:[]}
function curName(){return S.chart&&S.chart.demographics?(RG?RG.name(S.chart.demographics):S.chart.demographics.name):''}
function subjectSelect(patients){
 const s=el('select',{name:'subject'});
 if(patients&&S.pid)s.append(el('option',{value:S.pid},T('app.subject.patient',{name:curName()||S.pid})));
 s.append(el('option',{value:S.user.id},T('app.subject.me',{name:S.user.display})));
 return s}
function csv(v){return String(v||'').split(/[,،、，]/).map(x=>x.trim()).filter(Boolean)}
function pre(t){return el('pre',{class:'note',dir:'auto'},t||'')}
function check(name,label,on){return el('label',{class:'inl'},el('input',{type:'checkbox',name,value:name,checked:on?true:null}),' '+label)}
function saveBlob(blob,name){const a=el('a',{href:URL.createObjectURL(blob),download:name});document.body.append(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},2000)}
async function download(m,p,b,name){const h={};if(m==='POST'){h['Content-Type']='application/json';if(S.csrf)h['X-CSRF-Token']=S.csrf}
 const r=await fetch(p,{method:m,headers:h,body:m==='POST'?JSON.stringify(b||{}):undefined,credentials:'same-origin'});
 if(!r.ok){let j={};try{j=await r.json()}catch(e){}throw fail(r,j)}
 saveBlob(await r.blob(),name)}
async function apiRaw(p,text){// POST a body that is already JSON text; -> [parsed reply, reply text]
 const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':S.csrf||''},body:text,credentials:'same-origin'});
 const raw=await r.text();let j={};try{j=JSON.parse(raw)}catch(e){}
 if(r.status===401&&S.user){S.user=null;S.csrf=null;showLogin(trErr(j.error||'')||T('app.login.session_locked'));throw new Error('locked')}
 if(!r.ok||j.ok===false)throw fail(r,j);return [j,raw]}
function rawPlan(raw,plan){// the plan exactly as the server wrote it: its hash covers number formats (2.0) that JSON.parse loses
 const pre='{"ok": true, "plan": ';if(raw.startsWith(pre)&&raw.endsWith('}')){const t=raw.slice(pre.length,-1);try{JSON.parse(t);return t}catch(e){}}
 return JSON.stringify(plan)}
function noPatientHint(){return S.pid?null:el('p',{class:'muted'},T('app.subject.open_first'))}
function enText(){return I18N.locale!=='en'?el('p',{class:'muted'},T('app.common.module_english')):null}

// Consent & privacy --------------------------------------------------------------------------------
async function showConsent(){
 const subj=subjectSelect(can('demo.read')),box=el('div');
 const head=el('div',{class:'card'},el('h2',null,T('app.nav.consent')),
  el('p',{class:'muted'},T('app.consent.intro')),enText(),
  noPatientHint(),field(T('app.consent.whose'),subj));
 subj.addEventListener('change',act(()=>consentPanel(subj.value,box)));
 const parts=[head,box];if(can('users.manage'))parts.push(await consentSettingsCard());
 content(...parts);await consentPanel(subj.value,box)}
async function consentPanel(sid,box){
 const ex=await api('GET','/api/consent/explain?subject='+encodeURIComponent(sid));
 const st=(await api('GET','/api/consent/status?subject='+encodeURIComponent(sid))).status;
 const isPatient=sid[0]==='P',mayEdit=isPatient?can('demo.write'):sid===S.user.id;
 const j=ex.jurisdiction||{};
 const rows=ex.purposes.map(p=>{const s=st[p.key]||{active:false,state:'not_given'};
  return el('tr',null,el('td',null,mayEdit&&!s.active?el('input',{type:'checkbox',name:'purpose',value:p.key,'aria-label':T('app.consent.give_aria',{title:p.title})}):''),
   el('td',null,el('b',null,p.title),el('div',{class:'muted'},p.what),el('details',null,el('summary',null,T('app.consent.details')),
    el('p',null,T('app.consent.data',{v:p.data})),el('p',null,T('app.consent.who',{v:p.who})),el('p',null,T('app.consent.how_long',{v:p.how_long})),p.sensitive_note?el('p',{class:'bad'},p.sensitive_note):null)),
   el('td',{class:s.active?'signed':'muted'},TK('app.cstate',s.state)+(s.by_guardian?' '+T('app.consent.by_guardian'):'')),
   el('td',null,s.active&&mayEdit?el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/consent/withdraw',{subject:sid,purposes:[p.key],reason:'withdrawn in app'});say(T('app.consent.withdrawn',{title:p.title}),'ok');await consentPanel(sid,box)})},T('app.consent.withdraw')):''))});
 const tbl=el('table',null,el('thead',null,el('tr',null,el('th',null,el('span',{class:'sr'},T('app.consent.col_tick'))),el('th',null,T('app.consent.col_purpose')),el('th',null,T('app.consent.col_state')),el('th',null,el('span',{class:'sr'},T('app.vital.col_actions'))))),el('tbody',null,rows));
 const parts=[el('div',{class:'card'},el('h3',null,T(isPatient?'app.consent.patient_consents':'app.consent.staff_consents')),
  el('p',{class:'muted'},T('app.consent.rules',{name:j.name||TK('app.juris','generic')})+(j.consent_age?' - '+T('app.consent.age',{age:j.consent_age}):'')+'. '+(j.age_note||'')),wrap(tbl))];
 if(mayEdit){
  const g=el('details',null,el('summary',null,T('app.consent.guardian_summary')),
   el('div',{class:'row'},field(T('app.consent.g_name'),inp('g_name','text','',{dir:'auto'})),field(T('app.consent.g_rel'),inp('g_rel'))),
   check('g_auth',T('app.consent.g_auth')),check('g_assent',T('app.consent.g_assent')));
  const method=el('select',{name:'method'},['in_person','paper_form','phone','video','portal','self_service'].map(m=>el('option',{value:m},TK('app.cmethod',m))));
  const f=el('form',{class:'card',onsubmit:act(async()=>{const keys=[...tbl.querySelectorAll('input[name=purpose]:checked')].map(i=>i.value);
    if(!keys.length)throw new Error(T('app.consent.tick_one'));
    const b={subject:sid,purposes:keys,pin:f.querySelector('[name=pin]').value,method:method.value,explanation_version:ex.version};
    const gn=f.querySelector('[name=g_name]').value.trim();
    if(gn)b.guardian={name:gn,relationship:f.querySelector('[name=g_rel]').value.trim(),authority_checked:f.querySelector('[name=g_auth]').checked,child_assent:f.querySelector('[name=g_assent]').checked};
    const r=await api('POST','/api/consent/grant',b);say(T('app.consent.recorded',{id:r.receipt_id}),'ok');await consentPanel(sid,box)})},
   el('h3',null,T('app.consent.record_title')),field(T('app.consent.method'),method),g,field(T('app.consent.pin_label'),pinInput()),
   el('button',{type:'submit',class:'primary'},T('app.consent.record_btn')),
   el('button',{class:'ghost',type:'button',onclick:act(async()=>{const r=await api('POST','/api/consent/withdraw',{subject:sid,purposes:'all',reason:'withdraw all in app'});say(r.withdrawn.length?T('app.consent.withdrawn_list',{list:r.withdrawn.join(', ')}):T('app.consent.nothing_active'),'ok');await consentPanel(sid,box)})},T('app.consent.withdraw_all')));
  parts.push(f)}
 const rc=el('div');
 parts.push(el('div',{class:'card'},el('h3',null,T('app.consent.receipts_title')),
  el('button',{class:'ghost',type:'button',onclick:act(async()=>{const r=(await api('GET','/api/consent/receipts?subject='+encodeURIComponent(sid))).receipts;
   rc.replaceChildren(r.length?el('ul',null,r.map(x=>el('li',null,x.id+' - '+x.event+' - '+x.purposes.join(', ')+' - '+fmt(new Date(x.at*1000).toISOString())+(x.decoded?'':' ('+T('app.consent.sig_invalid')+')')))):el('p',{class:'muted'},T('app.consent.no_receipts')))})},T('app.consent.show_receipts')),
  (isPatient?can('clin.read'):true)?el('button',{class:'ghost',type:'button',onclick:act(async()=>{const r=await api('GET','/api/consent/export?subject='+encodeURIComponent(sid));
   saveBlob(new Blob([JSON.stringify(r.export,null,2)],{type:'application/json'}),'subject-access-'+sid+'.json')})},T('app.consent.export_json')):null,
  (isPatient?can('clin.read'):true)?el('button',{class:'ghost',type:'button',onclick:act(()=>download('GET','/api/consent/export.pdf?subject='+encodeURIComponent(sid),null,'subject-access-'+sid+'.pdf'))},T('app.consent.export_pdf')):null,
  rc,mayEdit?eraseForm(sid,box):null));
 box.replaceChildren(...parts)}
function eraseForm(sid,box){
 const f=el('form',{class:'dz',onsubmit:act(async()=>{if(!confirm(T('app.consent.erase_confirm')))return;
  const r=await api('POST','/api/consent/erase',{subject:sid,pin:f.querySelector('[name=pin]').value,reason:'subject_request'});say(T('app.consent.erased'),'ok');await consentPanel(sid,box)})},
  el('details',null,el('summary',null,T('app.consent.erase_summary')),el('p',{class:'muted'},T('app.consent.erase_text')),
   field(T('app.field.your_pin'),pinInput()),el('button',{class:'danger',type:'submit'},T('app.consent.erase_btn'))));
 return f}
const JURIS=['generic','us_hipaa','eu_gdpr','uk_gdpr','ca_pipeda','au_privacy','in_dpdp','br_lgpd'];
async function consentSettingsCard(){
 const r=await api('GET','/api/consent/settings'),s=r.settings;
 const simple=['country','clinic_name','clinic_phone','clinic_email','clinic_address','privacy_officer','policy_url'];
 const jur=el('select',{name:'jurisdiction'},JURIS.map(k=>el('option',{value:k,selected:k===s.jurisdiction?true:null},TK('app.juris',k))));
 const pol=el('select',{name:'crisis_alert_policy'},['notify_care_team','record_only'].map(k=>el('option',{value:k,selected:k===s.crisis_alert_policy?true:null},TK('app.cpolicy',k))));
 const adv={partners:s.partners,crisis_resources:s.crisis_resources,knowledge_sources:s.knowledge_sources,retention_days:s.retention_days};
 const ta=el('textarea',{name:'advanced',value:JSON.stringify(adv,null,1),spellcheck:'false',dir:'ltr'});
 const out=el('div');
 const f=el('form',{class:'card',onsubmit:act(async()=>{const ch={};
   if(jur.value!==s.jurisdiction)ch.jurisdiction=jur.value;if(pol.value!==s.crisis_alert_policy)ch.crisis_alert_policy=pol.value;
   for(const k of simple){const v=f.querySelector('[name='+k+']').value;if(v!==(s[k]||''))ch[k]=v}
   const age=f.querySelector('[name=consent_age]').value;if(age!==(s.consent_age==null?'':String(s.consent_age)))ch.consent_age=age===''?null:Number(age);
   let a;try{a=JSON.parse(ta.value)}catch(e){throw new Error(T('app.consent.adv_bad_json'))}
   for(const k of Object.keys(adv))if(JSON.stringify(a[k])!==JSON.stringify(adv[k]))ch[k]=a[k];
   if(!Object.keys(ch).length){say(T('app.common.nothing_changed'),'ok');return}
   await api('POST','/api/consent/settings',{settings:ch});say(T('app.consent.settings_saved'),'ok');await showConsent()})},
  el('h3',null,T('app.consent.settings_title')),legal('p','legal.not_legal_advice',null,{class:'muted'}),
  el('div',{class:'row'},field(T('app.consent.juris_profile'),jur),field(T('app.consent.age_override'),inp('consent_age','number',s.consent_age==null?'':s.consent_age,{min:0,max:25})),field(T('app.consent.crisis_alerts'),pol)),
  el('div',{class:'row'},simple.slice(0,4).map(k=>field(TK('app.cset',k),inp(k,'text',s[k])))),
  el('div',{class:'row'},simple.slice(4).map(k=>field(TK('app.cset',k),inp(k,'text',s[k])))),
  field(T('app.consent.adv_label'),ta),
  el('button',{type:'submit'},T('app.consent.save_settings')),
  el('button',{class:'ghost',type:'button',onclick:act(async()=>{const x=(await api('POST','/api/consent/purge',{})).report;out.replaceChildren(pre(JSON.stringify(x,null,1)))})},T('app.consent.purge_now')),
  el('details',null,el('summary',null,T('app.consent.summary')),pre(JSON.stringify(r.summary,null,1))),out);
 return f}

// Assistant ----------------------------------------------------------------------------------------
async function showAssistant(){
 const subj=subjectSelect(can('demo.read')),out=el('div'),extra=el('div');
 const msg=el('textarea',{name:'text',style:'min-height:80px',placeholder:T('app.asst.msg_ph'),dir:'auto'});
 const kind=el('select',{name:'kind'},['welcome','reminder','coverage_followup','nutrition_info'].map(k=>el('option',{value:k},T('app.asst.kind_'+k))));
 const cf=el('div',{class:'row'},field(T('app.field.date'),inp('date','text','',{placeholder:T('app.asst.date_ph')})),field(T('app.asst.time'),inp('time','text')),field(T('app.asst.place'),inp('place','text')),field(T('app.asst.topic'),inp('topic','text')));
 const reply=r=>el('div',{class:['card','ai']},el('p',{class:'muted'},r.label+(r.route?' - '+T('app.asst.route',{route:String(r.route).replaceAll('_',' ')}):'')+(r.engine?' - '+r.engine:'')),pre(r.text||''),
  r.emergency?el('p',{class:'bad'},T('app.asst.emergency_number',{number:r.emergency.number||T('app.asst.local_emergency')})+' ',el('a',{href:r.emergency.page||'/comms/'},T('app.asst.emergency_page'))):null,
  r.alert?el('p',{class:'bad'},T('app.asst.alert_raised',{kind:String(r.alert.kind).replaceAll('_',' ')})):null,
  r.draft?el('p',{class:'bad'},T('app.asst.draft_created')):null);
 const ask=el('form',{class:'card',onsubmit:act(async()=>{const r=(await api('POST','/api/persona/respond',{subject:subj.value,text:msg.value})).reply;out.replaceChildren(reply(r));await assistantExtra(subj.value,extra)})},
  el('h3',null,T('app.asst.answer_title')),field(T('app.asst.message'),msg),el('button',{type:'submit'},T('app.asst.prepare')));
 const comp=el('form',{class:'card',onsubmit:act(async()=>{const fl={};for(const k of ['date','time','place','topic']){const v=cf.querySelector('[name='+k+']').value.trim();if(v)fl[k]=v}
   const m=(await api('POST','/api/persona/compose',{subject:subj.value,kind:kind.value,fields:fl})).message;
   out.replaceChildren(m.text?reply(m):el('div',{class:['card','ai']},el('p',null,m.label),el('p',{class:'bad'},T('app.asst.draft_sign_below'))));await assistantExtra(subj.value,extra)})},
  el('h3',null,T('app.asst.write_title')),field(T('app.asst.kind'),kind),cf,el('button',{type:'submit'},T('app.asst.write_btn')));
 subj.addEventListener('change',act(()=>assistantExtra(subj.value,extra)));
 content(el('div',{class:'card'},el('h2',null,T('app.nav.assistant')),el('p',{class:'muted'},T('app.asst.intro')),enText(),
  noPatientHint(),field(T('app.asst.for'),subj)),ask,comp,out,extra);
 await assistantExtra(subj.value,extra)}
async function assistantExtra(sid,box){
 const parts=[];
 try{const p=(await api('GET','/api/persona/profile?subject='+encodeURIComponent(sid))).profile;
  parts.push(el('div',{class:'card'},el('h3',null,T('app.asst.profile_title')),el('p',{class:'muted'},T(p.personalised?'app.asst.personalised':'app.asst.generic')+' '+
   T('app.asst.profile',{tone:p.tone,level:p.reading_level,language:p.language})+(p.preferred_name?', '+T('app.asst.pref_name',{name:p.preferred_name}):'')+(p.accessibility.length?', '+T('app.asst.needs',{list:p.accessibility.join(', ')}):''))))}catch(e){parts.push(el('p',{class:'muted'},trErr(e.message)))}
 if(can('clin.read')){
  const d=(await api('GET','/api/persona/drafts?subject='+encodeURIComponent(sid))).drafts;
  parts.push(el('div',{class:'card'},el('h3',null,T('app.asst.drafts_title')),d.length?d.slice().reverse().map(x=>draftCard(sid,x,box)):el('p',{class:'muted'},T('app.asst.no_drafts'))));
  const a=(await api('GET','/api/persona/alerts?status=open')).alerts;
  parts.push(el('div',{class:'card'},el('h3',null,T('app.asst.alerts_title')),a.length?el('ul',null,a.map(x=>el('li',null,String(x.kind).replaceAll('_',' ')+' - '+x.subject+' - '+fmt(new Date(x.created*1000).toISOString())+' ',
   el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/persona/alerts/ack',{alert:x.id});say(T('app.asst.alert_closed'),'ok');await assistantExtra(sid,box)})},T('app.asst.mark_handled'))))):el('p',{class:'muted'},T('app.asst.no_alerts'))))}
 const sg=el('div');
 parts.push(el('div',{class:'card'},el('button',{class:'ghost',type:'button',onclick:act(async()=>{sg.replaceChildren(pre((await api('GET','/api/persona/style')).style_guide))})},T('app.asst.style_guide')),sg));
 box.replaceChildren(...parts)}
function draftCard(sid,x,box){
 const signed=x.status==='signed';
 const t=el('textarea',{name:'text',value:x.text,readonly:signed||!can('note.write')?true:null,dir:'auto','aria-label':T('app.asst.draft_text_aria')});
 const f=el('form',{class:'card',onsubmit:act(async()=>{await api('POST','/api/persona/draft/approve',{subject:sid,draft:x.id,pin:f.querySelector('[name=pin]').value});say(T('app.asst.reply_signed'),'ok');await assistantExtra(sid,box)})},
  el('p',{class:signed?'signed':'bad'},x.label+' - '+String(x.route).replaceAll('_',' ')+(signed?' - '+T('app.asst.signed_by',{name:x.signed_by_name||x.signed_by}):'')),t,
  !signed&&can('note.write')?el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/persona/draft/update',{subject:sid,draft:x.id,text:t.value});say(T('app.note.draft_saved'),'ok')})},T('app.asst.save_edit')):null,
  !signed&&can('note.sign')?[field(T('app.field.signing_pin'),pinInput()),el('button',{type:'submit'},T('app.asst.sign_reply'))]:null);
 return f}

// Nutrition ----------------------------------------------------------------------------------------
async function showNutrition(){
 const info=await api('GET','/api/nutrition/info'),out=el('div');
 const diets=['vegetarian','vegan','pescatarian','halal','kosher','no_pork','no_beef','dairy_free','egg_free','low_cost'];
 const usePt=S.pid&&can('clin.read')?check('use_patient',T('app.nut.use_patient',{name:curName()}),false):null;
 const f=el('form',{class:'card',onsubmit:act(async()=>{const q=k=>f.querySelector('[name='+k+']');
   const req={days:Number(q('days').value||1),diet:[...f.querySelectorAll('.diet input:checked')].map(i=>i.value),allergies:csv(q('allergies').value),intolerances:csv(q('intolerances').value),
    avoid:csv(q('avoid').value),liked:csv(q('liked').value),disliked:csv(q('disliked').value),
    profile:{clinical_flags:[...f.querySelectorAll('.flags input:checked')].map(i=>i.value)}};
   if(q('cuisine').value)req.cuisine=q('cuisine').value;if(q('budget').value)req.budget_per_day_usd=Number(q('budget').value);
   const b={request:req};if(usePt&&usePt.querySelector('input').checked)b.patient=S.pid;
   say(T('app.nut.planning'),'');const [r,raw]=await apiRaw('/api/nutrition/plan',JSON.stringify(b));say('','');out.replaceChildren(planView(r.plan,out,rawPlan(raw,r.plan)))})},
  el('h3',null,T('app.nut.make_title')),
  el('div',{class:'row'},field(T('app.nut.days'),inp('days','number','1',{min:1,max:14})),field(T('app.nut.cuisine'),el('select',{name:'cuisine'},el('option',{value:''},T('app.nut.any')),info.cuisines.map(c=>el('option',{value:c},TK('app.cuisine',c))))),field(T('app.nut.budget'),inp('budget','number','',{min:0,step:'0.5'}))),
  el('fieldset',{class:'diet'},el('legend',null,T('app.nut.diet')),diets.map(d=>check(d,TK('app.diet',d)))),
  el('div',{class:'row'},field(T('app.nut.allergies'),inp('allergies','text','',{dir:'auto'})),field(T('app.nut.intolerances'),inp('intolerances','text','',{dir:'auto'})),field(T('app.nut.avoid'),inp('avoid','text','',{dir:'auto'}))),
  el('div',{class:'row'},field(T('app.nut.liked'),inp('liked','text','',{dir:'auto'})),field(T('app.nut.disliked'),inp('disliked','text','',{dir:'auto'}))),
  el('fieldset',{class:'flags'},el('legend',null,T('app.nut.flags')),Object.entries(info.clinical_flags).map(([k,l])=>check(k,I18N.m['app.nflag.'+k]!=null?T('app.nflag.'+k):l))),
  usePt,el('button',{type:'submit'},T('app.nut.make_btn')));
 const parts=[el('div',{class:'card'},el('h2',null,T('app.nav.nutrition')),el('p',{class:'bad'},info.notice),enText(),el('p',{class:'muted'},T('app.nut.food_data',{name:info.food_data&&(info.food_data.name||info.food_data.title)||T('app.nut.fdc_default')})+' '+T(feat('consent')?'app.nut.consent_where':'app.nut.consent_only'))),f,out];
 if(!feat('consent')&&S.pid&&can('demo.write'))parts.splice(1,0,el('div',{class:'card'},el('h3',null,T('app.nut.consent_title',{name:curName()})),
  el('button',{type:'button',onclick:act(async()=>{await api('POST','/api/nutrition/consent',{subject:S.pid,grant:true,statement:'Recorded in person by '+S.user.display});say(T('app.nut.consent_recorded'),'ok')})},T('app.nut.record_yes')),
  el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/nutrition/consent',{subject:S.pid,grant:false});say(T('app.nut.consent_withdrawn'),'ok')})},T('app.consent.withdraw'))));
 content(...parts)}
function planView(p,out,raw){
 const w=el('div',{class:['card','ai']},el('p',{class:p.status==='clinician_approved'?'signed':'bad'},T('app.nut.status',{status:TK('app.nstatus',p.status)})),el('p',{class:'muted'},p.notice||''),p.review_note?el('p',{class:'bad'},p.review_note):null);
 for(const d of p.days||[]){const t=d.totals;
  w.append(el('h3',null,T('app.nut.day',{n:d.day})),wrap(el('table',null,el('tbody',null,Object.entries(d.meals).filter(([m,r])=>r.length).map(([m,r])=>el('tr',null,el('th',null,TK('app.meal',m)),el('td',null,r.map(x=>el('div',null,x.text)))))))),
   el('p',{class:'muted'},T('app.nut.totals',{kcal:num(t.energy_kcal),protein:num(t.protein_g),fibre:num(t.fibre_g),sodium:num(t.sodium_mg),potassium:num(t.potassium_mg)})))}
 if(p.shopping_list&&p.shopping_list.length)w.append(el('details',null,el('summary',null,T('app.nut.shopping')),el('ul',null,p.shopping_list.map(x=>el('li',null,x.text)))));
 if(p.explanations&&p.explanations.length)w.append(el('details',null,el('summary',null,T('app.nut.why')),el('ul',null,p.explanations.map(x=>el('li',null,x)))));
 if((p.status==='draft_requires_clinician_signoff'||p.status==='general_information')&&can('note.sign')){
  const f=el('form',{onsubmit:act(async()=>{const [r,rr]=await apiRaw('/api/nutrition/signoff','{"plan": '+raw+', "pin": '+JSON.stringify(f.querySelector('[name=pin]').value)+'}');
    say(T('app.nut.signed_off'),'ok');out.replaceChildren(planView(r.plan,out,rawPlan(rr,r.plan)))})},
   field(T('app.nut.signoff_pin'),pinInput()),el('button',{type:'submit'},T('app.nut.signoff_btn')));w.append(f)}
 return w}

// Coverage help ------------------------------------------------------------------------------------
function covExample(){return {plans:[{name:T('app.cov.ex_plan_a'),premium_monthly:250,deductible_individual:3000,oop_max_individual:7000,coinsurance:0.3,copays:{primary_care:40}},
 {name:T('app.cov.ex_plan_b'),premium_monthly:420,deductible_individual:500,oop_max_individual:4000,coinsurance:0.2,copays:{primary_care:20}}],
 scenarios:[{name:T('app.cov.ex_light'),services:[{type:'primary_care',count:3,allowed:150}]},{name:T('app.cov.ex_surgery'),services:[{type:'primary_care',count:4,allowed:150},{type:'outpatient_surgery',count:1,allowed:12000}]}]}}
async function showCoverage(tab){
 S.covTab=tab||S.covTab||'letter';S.inner=()=>showCoverage(S.covTab);const info=await api('GET','/api/coverage/info');
 const tabs=[['letter','app.cov.tab_letter'],['eob','app.cov.tab_eob'],['compare','app.cov.tab_compare'],['claims','app.cov.tab_claims']];
 const tl=el('div',{class:'tabs',role:'tablist','aria-label':T('app.nav.coverage')},tabs.map(([k,l])=>el('button',{type:'button',role:'tab','aria-selected':String(S.covTab===k),onclick:act(()=>showCoverage(k))},T(l))));
 const out=el('div'),body=await ({letter:covLetter,eob:covEob,compare:covCompare,claims:covClaims})[S.covTab](info,out);
 content(el('div',{class:'card'},el('h2',null,T('app.nav.coverage')),el('p',{class:'bad'},info.notice),el('p',{class:'muted'},info.no_action),legal('p','legal.coverage_navigation',null,{class:'muted'}),enText()),tl,body,out)}
function covText(title,text,out,slug){out.replaceChildren(el('div',{class:['card','ai']},pre(text),el('button',{class:'ghost',type:'button',onclick:act(()=>download('POST','/api/coverage/pdf',{title,text},slug+'.pdf'))},T('app.cov.download_pdf'))))}
function jurSelect(info){return el('select',{name:'jurisdiction'},Object.entries(info.jurisdictions).map(([k,l])=>el('option',{value:k},l)))}
function covLetter(info,out){
 const kinds=Object.keys(info.letter_kinds),ks=el('select',{name:'kind'},kinds.map(k=>el('option',{value:k},TK('app.letter',k)))),js=jurSelect(info),fl=el('div',{class:'row'});
 const draw=()=>fl.replaceChildren(...info.letter_kinds[ks.value].map(k=>field(TK('app.cfield',k),inp(k,'text','',{dir:'auto'}))));ks.addEventListener('change',draw);draw();
 const f=el('form',{class:'card',onsubmit:act(async()=>{const fields={};for(const x of fl.querySelectorAll('[name]'))if(x.value.trim())fields[x.name]=x.value.trim();
   if(f.querySelector('[name=urgent]').checked)fields.urgent='yes';
   const l=(await api('POST','/api/coverage/letter',{kind:ks.value,fields,jurisdiction:js.value})).letter;covText(T('app.cov.pdf_letter'),l.text,out,'coverage_letter_draft');
   if(l.missing&&l.missing.length)out.prepend(el('p',{class:'bad'},T('app.cov.missing',{list:l.missing.map(m=>TK('app.cfield',m)).join(', ')})))})},
  el('h3',null,T('app.cov.letter_title')),el('p',{class:'muted'},T('app.cov.nothing_sent')),
  el('div',{class:'row'},field(T('app.cov.letter'),ks),field(T('app.cov.plan_rules'),js)),fl,check('urgent',T('app.cov.urgent')),el('br'),el('button',{type:'submit'},T('app.cov.draft_letter')));
 return f}
function covEob(info,out){
 const cols=['description','service_date','billed','allowed','plan_paid','deductible','copay','coinsurance','not_covered','remark_codes'];
 const lines=el('div');const add=()=>lines.append(el('div',{class:'row eobline'},cols.map(k=>field(TK('app.cfield',k),inp(k,['description','service_date','remark_codes'].includes(k)?'text':'number','',{step:'0.01'})))));add();
 const f=el('form',{class:'card',onsubmit:act(async()=>{const ls=[...lines.querySelectorAll('.eobline')].map(r=>{const o={};for(const x of r.querySelectorAll('[name]')){const v=x.value.trim();if(!v)continue;
    o[x.name]=x.name==='remark_codes'?csv(v):(x.type==='number'?Number(v):v)}return o}).filter(o=>Object.keys(o).length);
   const e=(await api('POST','/api/coverage/eob',{eob:{lines:ls}})).eob;covText(T('app.cov.pdf_eob'),e.text,out,'explanation_of_benefits')})},
  el('h3',null,T('app.cov.eob_title')),el('p',{class:'muted'},T('app.cov.eob_hint')),lines,
  el('button',{class:'ghost',type:'button',onclick:add},T('app.cov.add_line')),el('button',{type:'submit'},T('app.cov.explain')));
 return f}
function covCompare(info,out){
 const ta=el('textarea',{name:'json',value:JSON.stringify(covExample(),null,1),spellcheck:'false',style:'min-height:260px',dir:'ltr'});
 const f=el('form',{class:'card',onsubmit:act(async()=>{let b;try{b=JSON.parse(ta.value)}catch(e){throw new Error(T('app.cov.bad_json'))}
   const ws=(await api('POST','/api/coverage/compare',{plans:b.plans,scenarios:b.scenarios})).worksheet;covText(T('app.cov.pdf_compare'),ws.text,out,'plan_comparison_worksheet')})},
  el('h3',null,T('app.cov.compare_title')),el('p',{class:'muted'},T('app.cov.compare_hint',{types:info.service_types.map(s=>TK('app.service',s)).join(', ')})),
  field(T('app.cov.compare_label'),ta),el('button',{type:'submit'},T('app.cov.compare_btn')));
 return f}
async function covClaims(info,out){
 const subj=el('select',{name:'subject'},el('option',{value:'household'},T('app.cov.household')),S.pid?el('option',{value:S.pid},T('app.cov.patient_claims',{name:curName()})):null);
 const list=el('div');
 const statuses=['submitted','processing','paid','partially_paid','denied','appeal_filed','appeal_denied','external_review','resolved','closed'];
 const load=async()=>{const t=(await api('POST','/api/coverage/claims',{subject:subj.value,action:'list'})).tracker;
  list.replaceChildren(el('p',{class:'muted'},T('app.cov.claims_summary',{n:t.claims.length,open:t.open,owe:money(t.totals.you_owe,'USD')})),
   t.deadlines.length?el('ul',null,t.deadlines.slice(0,10).map(d=>el('li',{class:d.days_left!=null&&d.days_left<0?'bad':''},(d.what||T('app.cov.deadline'))+': '+dday(d.date||'')+(d.days_left!=null?' ('+T('app.cov.days_left',{n:d.days_left})+')':'')+(d.claim_number?' - '+T('app.cov.claim_no',{n:d.claim_number}):'')))):null,
   t.claims.length?wrap(el('table',null,el('tbody',null,t.claims.map(c=>{const ss=el('select',{'aria-label':T('app.cov.status_aria')},statuses.map(x=>el('option',{value:x,selected:x===c.status?true:null},TK('app.claim',x))));
    return el('tr',null,el('td',{dir:'auto'},(c.claim_number||c.id)+' '+(c.provider||'')+' '+dday(c.service_date||'')),el('td',null,money(c.billed||0,'USD')),el('td',null,TK('app.claim',c.status)),
     el('td',null,can('demo.write')?[ss,el('button',{class:'ghost',type:'button',onclick:act(async()=>{await api('POST','/api/coverage/claims',{subject:subj.value,action:'update',id:c.id,status:ss.value});say(T('app.cov.claim_updated'),'ok');await load()})},T('app.cov.update'))]:''))})))):el('p',{class:'muted'},T('app.cov.no_claims')))};
 subj.addEventListener('change',act(load));
 const fields=['claim_number','provider','service_date','description','billed','you_owe','plan_name'];
 const f=can('demo.write')?el('form',{class:'card',onsubmit:act(async()=>{const c={};for(const k of fields){const v=f.querySelector('[name='+k+']').value.trim();if(v)c[k]=['billed','you_owe'].includes(k)?Number(v):v}
   c.jurisdiction=f.querySelector('[name=jurisdiction]').value;await api('POST','/api/coverage/claims',{subject:subj.value,action:'add',claim:c});say(T('app.cov.claim_added'),'ok');f.reset();await load()})},
  el('h3',null,T('app.cov.add_claim')),el('div',{class:'row'},fields.map(k=>field(TK('app.cfield',k),inp(k,k==='service_date'?'date':(['billed','you_owe'].includes(k)?'number':'text'),'',{step:'0.01'})))),field(T('app.cov.plan_rules'),jurSelect(info)),el('button',{type:'submit'},T('app.cov.add_claim_btn'))):null;
 const w=el('div',null,el('div',{class:'card'},el('h3',null,T('app.cov.claims_title')),field(T('app.cov.records_for'),subj),list),f);
 try{await load()}catch(e){list.replaceChildren(el('p',{class:'bad'},trErr(e.message)))}
 return w}
boot().catch(e=>say(T('app.boot.unreachable',{error:e.message}),'err'));
</script></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stark Covenant - local-first clinic records (not a certified EHR)")
    ap.add_argument("--data", help="data folder (default %%USERPROFILE%%\\Documents\\Stark_Covenant_Data)")
    ap.add_argument("--port", type=int, help="fixed port in 8600-8699 (default: first free)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--demo", action="store_true", help="force DEMO mode (synthetic data, separate folder)")
    ap.add_argument("--lan", action="store_true", help="opt-in: listen on the local network (plain HTTP)")
    ap.add_argument("--vault-dir", help="security vault folder (default: security_vault's own default)")
    ap.add_argument("--test-hooks", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--restore", nargs=2, metavar=("BACKUP", "NEW_FOLDER"), help="extract a backup into a new folder")
    a = ap.parse_args(argv)
    if a.restore:
        print("restored %d files" % restore_backup(a.restore[0], a.restore[1]))
        return
    if not a.data:
        moved = move_data_root()
        if moved:
            print("data folder %s -> %s: %s" % (LEGACY_DATA_DIR_NAME, DATA_DIR_NAME, moved), flush=True)
    app = CovenantApp(data_root=a.data, port=a.port, open_browser=not a.no_browser, demo=a.demo, lan=a.lan,
                      test_hooks=a.test_hooks, vault_root=a.vault_dir).start()
    c = app.clinic
    where = c.store.root if c.state == "ready" else ("waiting in the browser: " + {
        "locked": "unlock the secure vault", "no_vault": "create the secure vault"}.get(c.state, c.state))
    print("%s %s on http://%s:%d/  data: %s  mode: %s%s  (Ctrl+C quits)" % (
        APP_NAME, VERSION, "127.0.0.1" if not app.lan_enabled else "0.0.0.0", app.port, where,
        c.plan["label"], " DEMO (synthetic only)" if c.demo else ""), flush=True)
    if release_channel() == "beta":
        print("BETA: evaluate with DEMO data. Not a certified EHR or medical device; your compliance review is "
              "required before real patient data.", flush=True)
    if c.fatal:
        print("WARNING: " + c.fatal, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    main()
