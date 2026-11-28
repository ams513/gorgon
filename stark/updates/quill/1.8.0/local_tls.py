#!/usr/bin/env python3
"""local_tls.py - local HTTPS for LAN phone pages (Python standard library + the OS's own certificate tool).

WHY: browsers grant Screen Wake Lock, getUserMedia (camera / mic) and Web Speech dictation only in a *secure
context*. http://127.0.0.1 counts as secure, http://192.168.x.y does not, so a phone on the LAN gets a
crippled page. This module gives a local app a real HTTPS listener whose certificate a phone can trust.

WHAT IT DOES
  * One per-install local Certificate Authority (RSA 2048, SHA-256, basicConstraints CA:TRUE pathlen 0,
    keyUsage keyCertSign + cRLSign, 5 years) and one server certificate signed by it (RSA 2048, SHA-256,
    397 days, EKU serverAuth, SANs: localhost, 127.0.0.1, <hostname>, <hostname>.local and every current
    LAN IPv4 address of this machine).
  * Windows (tested path, no admin, no PKCS#12 parsing): Windows PowerShell's New-SelfSignedCertificate in
    Cert:\\CurrentUser\\My.
      - CA: -KeyExportPolicy NonExportable. Its private key never leaves the Windows key store (DPAPI,
        user profile); it is only used there as -Signer to issue / renew the server certificate.
        New-SelfSignedCertificate also drops a public copy into CurrentUser\\CA, which Python / OpenSSL
        programs on Windows load as trusted (ssl.load_default_certs reads the CA + ROOT stores): that copy is
        deleted at once, so nothing on this PC trusts the CA; only the phones you set up do.
      - Server: -Provider "Microsoft Enhanced RSA and AES Cryptographic Provider" -KeyExportPolicy
        Exportable, then GetRSAPrivateKey(cert).ExportParameters($true) (falls back to cert.PrivateKey);
        the RSA integers travel over the PowerShell stdout pipe (never a file, never a PFX), Python encodes
        them as a PKCS#1 "RSA PRIVATE KEY" PEM (tiny DER encoder below) into the user-only folder, and the
        server cert + key are deleted from the store right away (Remove-Item -DeleteKey). The store then
        only holds the CA. This avoids Export-PfxCertificate / certutil and the PKCS#12 decryption
        (AES-256 or 3DES) that a pure-stdlib PFX -> PEM path would need.
  * macOS / Linux (or Windows with backend="openssl"): the openssl CLI (OpenSSL 1.1.1+ / 3.x, LibreSSL 3+):
    `openssl req -x509` for the CA and `openssl req -new` + `openssl x509 -req -CA ...` for the server;
    there the CA key is a file (ca.key) inside the same user-only folder (needed to renew). No openssl ->
    stays plain HTTP and says so (ensure() returns https False with a clear message).
  * Files: <LOCALAPPDATA | ~/Library/Application Support | $XDG_DATA_HOME>/<name>/ : ca.crt (DER, for
    phones), ca.pem, server.pem, server.key, state.json. The folder's ACL is reset to "current user only"
    (icacls /inheritance:r /grant:r *<SID>:(OI)(CI)F; chmod 700/600 on POSIX) BEFORE any key is written;
    if that fails nothing is written and the app stays HTTP.
  * Renewal: ensure() re-issues the server cert (same CA, no phone action) 30 days before expiry, when the
    machine's main LAN IP or hostname is not in its SANs, or when it was signed by another CA. The CA is
    replaced 60 days before its own expiry or if its key is gone from the store; then every phone must
    trust the new CA once (ensure() reports ca_created True).
  * Serving: server_context() = ssl.SSLContext(PROTOCOL_TLS_SERVER), minimum TLS 1.2. DualProtocolMixin
    lets one socketserver port speak both: a connection whose first byte is a TLS handshake (0x16) is
    wrapped, anything else stays plain HTTP (the app decides what plain HTTP may do: e.g. loopback only,
    plus the /trust onboarding page). Sniffing + handshake run in the per-connection thread, under a timeout.
  * Phone onboarding: onboarding_html() (steps for iOS + Android, fingerprint, live secure-context check),
    ca_der() for .crt (Android / iOS) and mobileconfig() for an iOS configuration profile.

HONEST LIMITS
  * Each phone needs a one-time manual trust step (iOS: install profile + enable full trust in Certificate
    Trust Settings; Android: Settings > Security > install CA certificate). Nothing can skip it.
  * The CA download page is plain HTTP (the phone does not trust us yet); compare the SHA-256 fingerprint
    shown on the PC with the one in the phone's certificate details if the Wi-Fi is not your own.
  * A phone that trusts this CA would accept any certificate it signs. The CA key is non-exportable in the
    Windows store (or a user-only file with openssl), but malware running as this Windows user could still
    use it. Remove the profile / CA from the phone when you stop using the app.
  * Chrome's Web Speech API still sends the audio to Google's servers; HTTPS does not change that.
  * Firefox for Android ignores user-installed CAs unless enabled in its settings; use Chrome (Android) or
    Safari (iOS).
"""
from __future__ import annotations

import base64
import binascii
import datetime as _dt
import hashlib
import html
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid

__all__ = ["LocalTLS", "TLSError", "DualProtocolMixin", "sniff_and_wrap", "parse_cert", "spki_sha256_b64",
           "onboarding_html", "mobileconfig", "lan_ipv4s", "primary_ipv4", "default_state_dir"]

CA_DAYS = 1826          # 5 years: phones trust once
SERVER_DAYS = 397       # browser / Apple ceiling for TLS server certificates
SERVER_RENEW_DAYS = 30
CA_RENEW_DAYS = 60
RSA_BITS = 2048
DEFAULT_NAME = "StarkLocalTLS"
LEGACY_NAME = "GorgonLocalTLS"      # the folder up to Quill 1.5.0: used until the app has copied it to DEFAULT_NAME
TAG_RE = re.compile(r"[^A-Za-z0-9 ._()\-]")
OID_CN = b"\x55\x04\x03"
OID_SAN = b"\x55\x1d\x11"
OID_BC = b"\x55\x1d\x13"
OID_EKU = b"\x55\x1d\x25"
OID_SERVER_AUTH = b"\x2b\x06\x01\x05\x05\x07\x03\x01"
UTC = _dt.timezone.utc


class TLSError(RuntimeError):
    pass


def _utcnow():
    return _dt.datetime.now(UTC)


# ======================================================================================================
# DER / PEM (just enough to read certificates and write a PKCS#1 RSA key)
# ======================================================================================================
def _tlv(buf, i):
    """-> (tag, content_start, content_end) of the DER element at buf[i]."""
    tag = buf[i]
    ln = buf[i + 1]
    i += 2
    if ln & 0x80:
        n = ln & 0x7F
        if n == 0 or n > 4:
            raise ValueError("bad DER length")
        ln = int.from_bytes(buf[i:i + n], "big")
        i += n
    if i + ln > len(buf):
        raise ValueError("truncated DER")
    return tag, i, i + ln


def _children(buf, start, end):
    out = []
    i = start
    while i < end:
        tag, s, e = _tlv(buf, i)
        out.append((tag, s, e, i))
        i = e
    return out


def _der_len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _der(tag, content):
    return bytes([tag]) + _der_len(len(content)) + content


def _der_uint(b):
    if isinstance(b, int):
        b = b.to_bytes(max(1, (b.bit_length() + 7) // 8), "big")
    b = b.lstrip(b"\x00") or b"\x00"
    if b[0] & 0x80:
        b = b"\x00" + b
    return _der(0x02, b)


def rsa_private_key_der(n, e, d, p, q, dp, dq, qi):
    """PKCS#1 RSAPrivateKey from big-endian unsigned byte strings (as .NET RSAParameters gives them)."""
    return _der(0x30, b"".join([_der_uint(0)] + [_der_uint(x) for x in (n, e, d, p, q, dp, dq, qi)]))


def pem_encode(der, label):
    b = base64.b64encode(der).decode("ascii")
    return "-----BEGIN %s-----\n%s\n-----END %s-----\n" % (label, "\n".join(b[i:i + 64] for i in range(0, len(b), 64)), label)


def pem_decode(text, label="CERTIFICATE"):
    if isinstance(text, bytes):
        text = text.decode("ascii", "replace")
    m = re.search(r"-----BEGIN %s-----(.*?)-----END %s-----" % (label, label), text, re.S)
    if not m:
        raise ValueError("no PEM %s block" % label)
    return base64.b64decode("".join(m.group(1).split()))


def _parse_time(buf, tag, s, e):
    t = buf[s:e].decode("ascii")
    if tag == 0x17:  # UTCTime YYMMDDHHMMSSZ
        yy = int(t[:2])
        t = ("19" if yy >= 50 else "20") + t
    return _dt.datetime.strptime(t[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def parse_cert(der):
    """Minimal X.509 reader: subject CN, issuer/subject raw, validity, SANs, CA flag, serverAuth, SPKI."""
    _, cs, ce = _tlv(der, 0)
    top = _children(der, cs, ce)
    tbs_tag, ts, te, _ = top[0]
    f = _children(der, ts, te)
    k = 1 if f[0][0] == 0xA0 else 0
    serial = der[f[k][1]:f[k][2]]
    issuer = der[f[k + 2][3]:f[k + 2][2]]
    validity = _children(der, f[k + 3][1], f[k + 3][2])
    subject_el = f[k + 4]
    subject = der[subject_el[3]:subject_el[2]]
    spki = der[f[k + 5][3]:f[k + 5][2]]
    cn = ""
    for _t, rs, re_, _o in _children(der, subject_el[1], subject_el[2]):
        for _t2, as_, ae, _o2 in _children(der, rs, re_):
            parts = _children(der, as_, ae)
            if len(parts) == 2 and der[parts[0][1]:parts[0][2]] == OID_CN:
                cn = der[parts[1][1]:parts[1][2]].decode("utf-8", "replace")
    info = {"cn": cn, "serial": serial.hex(), "issuer_der": issuer, "subject_der": subject, "spki_der": spki,
            "not_before": _parse_time(der, validity[0][0], validity[0][1], validity[0][2]),
            "not_after": _parse_time(der, validity[1][0], validity[1][1], validity[1][2]),
            "dns": [], "ips": [], "is_ca": False, "server_auth": False,
            "sha256": hashlib.sha256(der).hexdigest(), "sha1": hashlib.sha1(der).hexdigest().upper()}
    for tag, s, e, _o in f[k + 6:]:
        if tag != 0xA3:
            continue
        _, xs, xe = _tlv(der, s)
        for _t, es, ee, _o2 in _children(der, xs, xe):
            parts = _children(der, es, ee)
            oid = der[parts[0][1]:parts[0][2]]
            val = der[parts[-1][1]:parts[-1][2]]
            if oid == OID_SAN:
                _, vs, ve = _tlv(val, 0)
                for gt, gs, ge, _o3 in _children(val, vs, ve):
                    if gt == 0x82:
                        info["dns"].append(val[gs:ge].decode("ascii", "replace").lower())
                    elif gt == 0x87:
                        info["ips"].append(str(ipaddress.ip_address(val[gs:ge])))
            elif oid == OID_BC:
                _, vs, ve = _tlv(val, 0)
                bc = _children(val, vs, ve)
                info["is_ca"] = bool(bc and bc[0][0] == 0x01 and val[bc[0][1]] != 0)
            elif oid == OID_EKU:
                _, vs, ve = _tlv(val, 0)
                info["server_auth"] = any(val[s2:e2] == OID_SERVER_AUTH for _t3, s2, e2, _o4 in _children(val, vs, ve))
    return info


def spki_sha256_b64(cert_der):
    """Base64 SHA-256 of the SubjectPublicKeyInfo (Chrome --ignore-certificate-errors-spki-list / HPKP form)."""
    return base64.b64encode(hashlib.sha256(parse_cert(cert_der)["spki_der"]).digest()).decode("ascii")


def fingerprint(hexdigest):
    return ":".join(hexdigest[i:i + 2] for i in range(0, len(hexdigest), 2)).upper()


# ======================================================================================================
# Machine names
# ======================================================================================================
def primary_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET: a UDP connect sends nothing, it only picks the route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _usable_ipv4(a):
    try:
        ip = ipaddress.IPv4Address(a)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)


def lan_ipv4s(hostname=None):
    found = set()
    p = primary_ipv4()
    if p and _usable_ipv4(p):
        found.add(p)
    try:
        for fam, _a, _b, _c, sa in socket.getaddrinfo(hostname or socket.gethostname(), None, socket.AF_INET):
            if _usable_ipv4(sa[0]):
                found.add(sa[0])
    except OSError:
        pass
    return sorted(found, key=lambda x: tuple(int(o) for o in x.split(".")))


# The CA a phone trusts may only vouch for this PC's own names and private addresses (RFC 5280 nameConstraints),
# so even a stolen ca.key cannot mint a certificate the phone would accept for a real web site.
NC_IPS = ("127.0.0.0/255.0.0.0", "10.0.0.0/255.0.0.0", "172.16.0.0/255.240.0.0", "192.168.0.0/255.255.0.0",
          "169.254.0.0/255.255.0.0")


def nc_permits_ip(a):
    """True when the CA's nameConstraints allow this IPv4 address (a SAN outside them voids the whole cert)."""
    try:
        ip = ipaddress.IPv4Address(a)
    except ValueError:
        return False
    return any(ip in ipaddress.IPv4Network(n) for n in NC_IPS)


NC_OID_DER = bytes([0x06, 0x03, 0x55, 0x1D, 0x1E])   # 2.5.29.30 nameConstraints


def has_name_constraints(cert_der):
    """True when this CA certificate carries nameConstraints (every CA made from 1.6.0/1.6.1 on does)."""
    return bool(cert_der) and NC_OID_DER in cert_der


def name_constraints(hostname):
    """OpenSSL extension value: localhost, any *.local name, this PC's bare name and private IPv4 ranges only."""
    host = _clean_host(hostname)
    dns = ["localhost", "local"] + ([host] if host != "localhost" else [])
    return "critical," + ",".join(["permitted;DNS:" + d for d in dns] + ["permitted;IP:" + i for i in NC_IPS])


def name_constraints_der(hostname):
    """DER of the same permitted-only nameConstraints value, for the Windows PowerShell CA (extension 2.5.29.30)."""
    host = _clean_host(hostname)
    dns = ["localhost", "local"] + ([host] if host != "localhost" else [])
    subs = [_der(0x30, _der(0x82, d.encode("ascii"))) for d in dns]
    for n in NC_IPS:
        a, m = n.split("/")
        subs.append(_der(0x30, _der(0x87, ipaddress.IPv4Address(a).packed + ipaddress.IPv4Address(m).packed)))
    return _der(0x30, _der(0xA0, b"".join(subs)))


def _clean_host(h):
    h = re.sub(r"[^A-Za-z0-9\-]", "", (h or "").split(".")[0]).lower()
    return h[:63] or "localhost"


def default_state_dir(name=DEFAULT_NAME):
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path, old = os.path.join(base, name), os.path.join(base, LEGACY_NAME)
    if name == DEFAULT_NAME and not os.path.isdir(path) and os.path.isdir(old):
        return old          # not copied yet: the same CA keeps working, so phones need nothing new
    return path


# ======================================================================================================
# Process runner (mockable)
# ======================================================================================================
def default_runner(args, env=None, timeout=120):
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run(args, env=e, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, **kw)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


# ======================================================================================================
# ACLs: user-only folder
# ======================================================================================================
_SID_CACHE = {}


def _current_sid(runner):
    if "sid" not in _SID_CACHE:
        rc, out, err = runner(["whoami", "/user", "/fo", "csv", "/nh"], timeout=30)
        m = re.search(r"(S-1-[0-9\-]+)", out or "")
        if rc != 0 or not m:
            raise TLSError("could not read the current user's SID (whoami): %s" % (err or out).strip()[:200])
        _SID_CACHE["sid"] = m.group(1)
    return _SID_CACHE["sid"]


def restrict_to_user(path, runner=default_runner, is_dir=None):
    """Only the current user may read / write path (and, for a folder, everything created inside it)."""
    if is_dir is None:
        is_dir = os.path.isdir(path)
    if os.name == "nt":
        sid = _current_sid(runner)
        grant = "*%s:(OI)(CI)F" % sid if is_dir else "*%s:F" % sid
        rc, out, err = runner(["icacls", path, "/inheritance:r", "/grant:r", grant, "/Q"], timeout=30)
        if rc != 0:
            raise TLSError("icacls could not restrict %s: %s" % (path, (err or out).strip()[:200]))
    else:
        os.chmod(path, 0o700 if is_dir else 0o600)


def _write_private(path, data, runner):
    """Atomic write of a secret file inside the (already user-only) folder."""
    if isinstance(data, str):
        data = data.encode("ascii")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    restrict_to_user(tmp, runner, is_dir=False)
    os.replace(tmp, path)


def _write_public(path, data):
    if isinstance(data, str):
        data = data.encode("ascii")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class _DirLock:
    """Cross-process lock (O_EXCL lock file) so two apps sharing the folder never issue at the same time."""

    def __init__(self, d, timeout=90.0, stale=180.0):
        self.p = os.path.join(d, ".lock")
        self.timeout, self.stale = timeout, stale

    def __enter__(self):
        end = time.time() + self.timeout
        while True:
            try:
                fd = os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.p) > self.stale:
                        os.remove(self.p)
                        continue
                except OSError:
                    pass
                if time.time() > end:
                    raise TLSError("certificate folder is locked by another process")
                time.sleep(0.2)

    def __exit__(self, *a):
        try:
            os.remove(self.p)
        except OSError:
            pass


# ======================================================================================================
# Backends
# ======================================================================================================
_PS_PRE = "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';" \
          "$P = $env:LOCALTLS_ARGS | ConvertFrom-Json;"

PS_CREATE_CA = _PS_PRE + r"""
$ca = New-SelfSignedCertificate -Subject $P.subject -FriendlyName $P.friendly -CertStoreLocation 'Cert:\CurrentUser\My' `
  -KeyUsage CertSign,CRLSign,DigitalSignature -KeyUsageProperty Sign `
  -TextExtension @('2.5.29.19={critical}{text}ca=1&pathlength=0', ('2.5.29.30={critical}{hex}' + $P.nc)) -KeyAlgorithm RSA -KeyLength $P.bits `
  -HashAlgorithm SHA256 -NotBefore (Get-Date).AddHours(-1) -NotAfter (Get-Date).AddDays([int]$P.days) `
  -KeyExportPolicy NonExportable
# New-SelfSignedCertificate also copies a self-signed cert into CurrentUser\CA; OpenSSL-based programs (Python's
# ssl.load_default_certs reads the Windows CA + ROOT stores) would then treat it as trusted. Keep it only in My.
$dup = 'Cert:\CurrentUser\CA\' + $ca.Thumbprint
if (Test-Path -LiteralPath $dup) { Remove-Item -LiteralPath $dup }
@{thumbprint=$ca.Thumbprint; der=[Convert]::ToBase64String($ca.RawData)} | ConvertTo-Json -Compress
"""

PS_ISSUE = _PS_PRE + r"""
$cp = 'Cert:\CurrentUser\My\' + $P.ca
if (-not (Test-Path -LiteralPath $cp)) { throw 'CA certificate not found in CurrentUser\My' }
$ca = Get-Item -LiteralPath $cp
if (-not $ca.HasPrivateKey) { throw 'CA private key missing' }
$names = @(); foreach ($d in $P.dns) { $names += ('DNS=' + $d) }; foreach ($i in $P.ips) { $names += ('IPAddress=' + $i) }
$san = '2.5.29.17={text}' + ($names -join '&')
$srv = New-SelfSignedCertificate -Subject ('CN=' + $P.cn) -FriendlyName $P.friendly -Signer $ca `
  -CertStoreLocation 'Cert:\CurrentUser\My' -KeyUsage DigitalSignature,KeyEncipherment `
  -TextExtension @($san, '2.5.29.37={text}1.3.6.1.5.5.7.3.1', '2.5.29.19={critical}{text}ca=0') `
  -KeyAlgorithm RSA -KeyLength $P.bits -HashAlgorithm SHA256 -NotBefore (Get-Date).AddHours(-1) `
  -NotAfter (Get-Date).AddDays([int]$P.days) -KeyExportPolicy Exportable `
  -Provider 'Microsoft Enhanced RSA and AES Cryptographic Provider'
try {
  $k = $null
  try { $k = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($srv).ExportParameters($true) }
  catch { $k = $srv.PrivateKey.ExportParameters($true) }
  $b = { param($x) [Convert]::ToBase64String($x) }
  @{der=(& $b $srv.RawData); n=(& $b $k.Modulus); e=(& $b $k.Exponent); d=(& $b $k.D); p=(& $b $k.P); q=(& $b $k.Q);
    dp=(& $b $k.DP); dq=(& $b $k.DQ); qi=(& $b $k.InverseQ)} | ConvertTo-Json -Compress
} finally {
  Remove-Item -LiteralPath $srv.PSPath -DeleteKey -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $srv.PSPath) { Remove-Item -LiteralPath $srv.PSPath }
}
"""

PS_HAS_CA = _PS_PRE + r"""
$cp = 'Cert:\CurrentUser\My\' + $P.ca
$ok = $false; if (Test-Path -LiteralPath $cp) { $ok = [bool](Get-Item -LiteralPath $cp).HasPrivateKey }
@{present=$ok} | ConvertTo-Json -Compress
"""

PS_DELETE = _PS_PRE + r"""
$n = 0
foreach ($t in @($P.thumbprints)) { foreach ($s in 'My','CA') {
  $cp = 'Cert:\CurrentUser\' + $s + '\' + $t
  if (Test-Path -LiteralPath $cp) {
    if ($s -eq 'My') { Remove-Item -LiteralPath $cp -DeleteKey -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $cp) { Remove-Item -LiteralPath $cp }
    $n++ } } }
@{removed=$n} | ConvertTo-Json -Compress
"""

PS_FIND = _PS_PRE + r"""
$r = @(Get-ChildItem 'Cert:\CurrentUser\My','Cert:\CurrentUser\CA' | Where-Object { $_.FriendlyName -eq $P.friendly } |
  ForEach-Object { @{store=$_.PSParentPath.Split('\')[-1]; thumbprint=$_.Thumbprint; subject=$_.Subject} })
ConvertTo-Json -Compress -InputObject $r
"""


def _powershell_exe():
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    p = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return p if os.path.exists(p) else (shutil.which("powershell") or shutil.which("pwsh"))


class PowerShellBackend:
    name = "powershell"

    def __init__(self, runner=default_runner, exe=None):
        self.runner = runner
        self.exe = exe or _powershell_exe() or "powershell.exe"

    def _ps(self, script, args, timeout=120):
        enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        rc, out, err = self.runner([self.exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-EncodedCommand", enc], env={"LOCALTLS_ARGS": json.dumps(args)}, timeout=timeout)
        lines = [ln for ln in (out or "").splitlines() if ln.strip().startswith(("{", "["))]
        if rc != 0 or not lines:
            raise TLSError("PowerShell certificate step failed (rc %s): %s" % (rc, (err or out or "").strip()[:400]))
        return json.loads(lines[-1])

    def create_ca(self, state_dir, subject, friendly, days, hostname=None):
        r = self._ps(PS_CREATE_CA, {"subject": subject, "friendly": friendly, "days": days, "bits": RSA_BITS,
                                        "nc": name_constraints_der(hostname).hex()})
        return r["thumbprint"], base64.b64decode(r["der"])

    def has_ca(self, state_dir, ca_id):
        return bool(ca_id) and bool(self._ps(PS_HAS_CA, {"ca": ca_id}).get("present"))

    def issue_server(self, state_dir, ca_id, cn, dns, ips, friendly, days):
        r = self._ps(PS_ISSUE, {"ca": ca_id, "cn": cn, "dns": list(dns), "ips": list(ips), "friendly": friendly,
                                "days": days, "bits": RSA_BITS})
        g = lambda k: base64.b64decode(r[k])  # noqa: E731
        key_der = rsa_private_key_der(g("n"), g("e"), g("d"), g("p"), g("q"), g("dp"), g("dq"), g("qi"))
        return g("der"), pem_encode(key_der, "RSA PRIVATE KEY")

    def delete(self, state_dir, ids):
        ids = [i for i in ids if i]
        if not ids:
            return 0
        return int(self._ps(PS_DELETE, {"thumbprints": ids}).get("removed", 0))

    def find(self, friendly):
        r = self._ps(PS_FIND, {"friendly": friendly})
        return r if isinstance(r, list) else [r]


class OpenSSLBackend:
    """openssl CLI path (macOS / Linux; also usable on Windows when openssl is installed). CA key = ca.key file."""
    name = "openssl"

    def __init__(self, runner=default_runner, exe=None):
        self.runner = runner
        self.exe = exe or shutil.which("openssl") or "openssl"

    def _run(self, args):
        rc, out, err = self.runner([self.exe] + args, timeout=120)
        if rc != 0:
            raise TLSError("openssl failed (rc %s): %s" % (rc, (err or out or "").strip()[:400]))
        return out

    @staticmethod
    def _cnf_escape(v):
        return re.sub(r"[^A-Za-z0-9 ._()\-]", "", v)

    def create_ca(self, state_dir, subject, friendly, days, hostname=None):
        cn = self._cnf_escape(subject.split("CN=", 1)[1].split(",")[0])
        cnf = os.path.join(state_dir, "ca.cnf")
        _write_public(cnf, "[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=v3_ca\n[dn]\nCN=%s\n"
                           "O=Stark Local TLS\n[v3_ca]\nbasicConstraints=critical,CA:TRUE,pathlen:0\n"
                           "keyUsage=critical,keyCertSign,cRLSign,digitalSignature\nsubjectKeyIdentifier=hash\n" % cn
                           + ("nameConstraints=%s\n" % name_constraints(hostname) if hostname else ""))
        key, pem = os.path.join(state_dir, "ca.key"), os.path.join(state_dir, "ca.new.pem")
        try:
            self._run(["req", "-x509", "-new", "-newkey", "rsa:%d" % RSA_BITS, "-nodes", "-sha256", "-days", str(days),
                       "-config", cnf, "-keyout", key + ".tmp", "-out", pem])
            restrict_to_user(key + ".tmp", self.runner, is_dir=False)
            os.replace(key + ".tmp", key)
            with open(pem, "rb") as f:
                der = pem_decode(f.read())
        finally:
            for p in (cnf, pem, key + ".tmp"):
                if os.path.exists(p):
                    os.remove(p)
        return hashlib.sha1(der).hexdigest().upper(), der

    def has_ca(self, state_dir, ca_id):
        return os.path.exists(os.path.join(state_dir, "ca.key")) and os.path.exists(os.path.join(state_dir, "ca.pem"))

    def issue_server(self, state_dir, ca_id, cn, dns, ips, friendly, days):
        j = lambda n: os.path.join(state_dir, n)  # noqa: E731
        san = ",".join(["DNS:" + d for d in dns] + ["IP:" + i for i in ips])
        _write_public(j("srv.cnf"), "[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=%s\n" % self._cnf_escape(cn))
        _write_public(j("srv_ext.cnf"), "[v3_srv]\nbasicConstraints=critical,CA:FALSE\n"
                                        "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                                        "subjectAltName=%s\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n" % san)
        try:
            self._run(["req", "-new", "-newkey", "rsa:%d" % RSA_BITS, "-nodes", "-sha256", "-config", j("srv.cnf"),
                       "-keyout", j("srv.key.tmp"), "-out", j("srv.csr")])
            restrict_to_user(j("srv.key.tmp"), self.runner, is_dir=False)
            self._run(["x509", "-req", "-in", j("srv.csr"), "-CA", j("ca.pem"), "-CAkey", j("ca.key"),
                       "-set_serial", "0x" + secrets.token_hex(16), "-days", str(days), "-sha256",
                       "-extfile", j("srv_ext.cnf"), "-extensions", "v3_srv", "-out", j("srv.new.pem")])
            with open(j("srv.new.pem"), "rb") as f:
                der = pem_decode(f.read())
            with open(j("srv.key.tmp"), "r", encoding="ascii") as f:
                key_pem = f.read()
        finally:
            for n in ("srv.cnf", "srv_ext.cnf", "srv.csr", "srv.new.pem", "srv.key.tmp"):
                if os.path.exists(j(n)):
                    os.remove(j(n))
        return der, key_pem

    def delete(self, state_dir, ids):
        p = os.path.join(state_dir, "ca.key")
        if os.path.exists(p):
            os.remove(p)
            return 1
        return 0


def pick_backend(runner=default_runner, prefer=None):
    if prefer == "powershell" or (prefer is None and os.name == "nt" and _powershell_exe()):
        return PowerShellBackend(runner)
    if prefer == "openssl" or (prefer is None and shutil.which("openssl")):
        return OpenSSLBackend(runner)
    return None


# ======================================================================================================
# Manager
# ======================================================================================================
class LocalTLS:
    """ensure() -> status dict; server_context() -> ssl.SSLContext or None. Thread-safe; cheap when nothing changed."""

    def __init__(self, state_dir=None, name=DEFAULT_NAME, backend="auto", runner=default_runner, tag="",
                 hostname=None, ipv4s=None, now=None):
        self.name = name
        self.dir = os.path.abspath(state_dir or default_state_dir(name))
        self.runner = runner
        self.backend = pick_backend(runner) if backend == "auto" else (
            pick_backend(runner, backend) if isinstance(backend, str) else backend)
        self.tag = TAG_RE.sub("", tag)[:40]
        self._hostname = hostname
        self._ipv4s = ipv4s
        self._now = now or _utcnow
        self._lock = threading.RLock()
        self._ctx = None
        self._ctx_key = None
        self._open_ca = False   # True only while the CA in use is an older one without nameConstraints
        self.last = None

    # --- paths / state ---------------------------------------------------------------------------------
    def path(self, n):
        return os.path.join(self.dir, n)

    @property
    def friendly(self):
        return "Stark Local TLS" + (" " + self.tag if self.tag else "")

    def hostname(self):
        return _clean_host(self._hostname if self._hostname is not None else socket.gethostname())

    def wanted_names(self):
        h = self.hostname()
        dns = ["localhost"] + ([h, h + ".local"] if h != "localhost" else [])
        ips = self._ipv4s() if callable(self._ipv4s) else (self._ipv4s if self._ipv4s is not None else lan_ipv4s())
        ips = ["127.0.0.1"] + [i for i in ips if i != "127.0.0.1" and self._ip_allowed(i)]
        return dns, ips

    def _ip_allowed(self, ip):
        # Both CA backends write nameConstraints: a public or CGNAT (e.g. VPN) address in the SANs would make phones
        # refuse the whole certificate, so such addresses are left out. A CA made before the constraints existed has
        # none, and phones already trust it: its certificates keep every address they always carried.
        return self._open_ca or nc_permits_ip(ip)

    def primary_ip(self):
        if self._ipv4s is not None:
            ips = self._ipv4s() if callable(self._ipv4s) else self._ipv4s
            ip = ips[0] if ips else None
        else:
            ip = primary_ipv4()
        return ip if ip and self._ip_allowed(ip) else None

    def _load_state(self):
        try:
            with open(self.path("state.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self, st):
        _write_public(self.path("state.json"), json.dumps(st, indent=1, sort_keys=True))

    def _read_der(self, n):
        try:
            with open(self.path(n), "rb") as f:
                data = f.read()
            return pem_decode(data) if n.endswith(".pem") else data
        except (OSError, ValueError):
            return None

    def _prepare_dir(self):
        os.makedirs(self.dir, exist_ok=True)
        restrict_to_user(self.dir, self.runner, is_dir=True)

    # --- main entry ---------------------------------------------------------------------------------------
    def ensure(self):
        with self._lock:
            if self.backend is None:
                self.last = {"ok": False, "https": False, "backend": None,
                             "message": "No certificate tool found (needs Windows PowerShell, or the openssl command on "
                                        "macOS/Linux): staying on plain HTTP. Wake Lock, camera/mic and dictation will "
                                        "be unavailable on phones."}
                return self.last
            try:
                self.last = self._ensure()
            except (TLSError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
                self.last = {"ok": False, "https": False, "backend": self.backend.name,
                             "message": "HTTPS setup failed, staying on plain HTTP: %s" % e}
            return self.last

    def _ensure(self):
        now = self._now()
        self._prepare_dir()
        with _DirLock(self.dir):
            st = self._load_state()
            ca_der = self._read_der("ca.crt")
            ca = parse_cert(ca_der) if ca_der else None
            ca_created = issued = False
            ca_ok = bool(ca and st.get("backend") == self.backend.name and st.get("ca_id")
                         and ca["not_after"] - now > _dt.timedelta(days=CA_RENEW_DAYS)
                         and ca["sha256"] == st.get("ca_sha256")
                         and st.get("ca_nc_host", self.hostname()) == self.hostname())  # renamed PC: new CA
            srv_der = self._read_der("server.pem")
            srv = parse_cert(srv_der) if srv_der else None
            self._open_ca = bool(ca_ok and not has_name_constraints(ca_der))
            primary = self.primary_ip()
            need_srv = not (srv and ca_ok and os.path.exists(self.path("server.key"))
                            and srv["issuer_der"] == ca["subject_der"]
                            and srv["not_after"] - now > _dt.timedelta(days=SERVER_RENEW_DAYS)
                            and self.hostname() in srv["dns"] and (not primary or primary in srv["ips"]))
            if need_srv and ca_ok and not self.backend.has_ca(self.dir, st["ca_id"]):
                ca_ok = False
            if not ca_ok:
                old = st.get("ca_id")
                stamp = now.strftime("%Y-%m-%d")
                subject = "CN=Stark Local CA %s %s%s, O=Stark Local TLS" % (
                    self.hostname(), stamp, (" " + self.tag) if self.tag else "")
                ca_id, ca_der = self.backend.create_ca(self.dir, subject, self.friendly, CA_DAYS,
                                                       hostname=self.hostname())
                ca = parse_cert(ca_der)
                if not ca["is_ca"]:
                    raise TLSError("generated CA certificate lacks CA:TRUE")
                _write_public(self.path("ca.crt"), ca_der)
                _write_public(self.path("ca.pem"), pem_encode(ca_der, "CERTIFICATE"))
                if old and old != ca_id and st.get("backend") == "powershell" and self.backend.name == "powershell":
                    try:
                        self.backend.delete(self.dir, [old])
                    except TLSError:
                        pass
                st = {"backend": self.backend.name, "ca_id": ca_id, "ca_sha256": ca["sha256"],
                      "ca_created": now.isoformat()}
                st["ca_nc_host"] = self.hostname()  # both backends write nameConstraints
                ca_created = need_srv = True
                self._open_ca = not has_name_constraints(ca_der)
            if need_srv:
                dns, ips = self.wanted_names()
                srv_der, key_pem = self.backend.issue_server(self.dir, st["ca_id"], self.hostname(), dns, ips,
                                                             self.friendly, SERVER_DAYS)
                srv = parse_cert(srv_der)
                if srv["issuer_der"] != ca["subject_der"] or not srv["server_auth"]:
                    raise TLSError("issued server certificate is not signed by the CA / lacks serverAuth")
                _write_private(self.path("server.key"), key_pem, self.runner)
                _write_public(self.path("server.pem"), pem_encode(srv_der, "CERTIFICATE"))
                st.update({"server_issued": now.isoformat(), "server_sha256": srv["sha256"]})
                issued = True
                self._ctx = None
            self._save_state(st)
            ctx = self.server_context()  # proves cert + key load into stdlib ssl
        msg = "HTTPS ready (%s). " % self.backend.name
        if ca_created:
            msg += "A NEW local CA was created: each phone must install and trust it once (open /trust on the phone)."
        elif issued:
            msg += "Server certificate renewed with the existing CA: phones that already trust it need nothing."
        else:
            msg += "Certificates are current."
        return {"ok": ctx is not None, "https": ctx is not None, "backend": self.backend.name,
                "ca_created": ca_created, "issued": issued, "message": msg,
                "ca_sha256": fingerprint(ca["sha256"]), "ca_cn": ca["cn"],
                "ca_not_after": ca["not_after"].isoformat(), "server_not_after": srv["not_after"].isoformat(),
                "dns": srv["dns"], "ips": srv["ips"], "spki_sha256_b64": spki_sha256_b64(srv_der),
                "cert": self.path("server.pem"), "key": self.path("server.key"), "ca_pem": self.path("ca.pem"),
                "ca_crt": self.path("ca.crt"), "dir": self.dir}

    def server_context(self):
        with self._lock:
            cert, key = self.path("server.pem"), self.path("server.key")
            try:
                k = (os.path.getmtime(cert), os.path.getmtime(key))
            except OSError:
                return None
            if self._ctx is None or self._ctx_key != k:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                ctx.load_cert_chain(cert, key)
                self._ctx, self._ctx_key = ctx, k
            return self._ctx

    # --- phone downloads -----------------------------------------------------------------------------------
    def ca_der(self):
        return self._read_der("ca.crt")

    def mobileconfig(self):
        der = self.ca_der()
        return mobileconfig(der, parse_cert(der)["cn"]) if der else None

    def remove(self):
        """Delete this install's CA (store + files). Phones keep trusting the old CA until removed there."""
        with self._lock:
            st = self._load_state()
            n = 0
            if self.backend is not None and st.get("ca_id"):
                n = self.backend.delete(self.dir, [st["ca_id"]])
            for f in ("ca.crt", "ca.pem", "server.pem", "server.key", "state.json", "ca.key"):
                if os.path.exists(self.path(f)):
                    os.remove(self.path(f))
            self._ctx = None
            return n


# ======================================================================================================
# Serving: one port, TLS or plain HTTP by the first byte
# ======================================================================================================
def sniff_and_wrap(sock, ctx, timeout=20.0):
    """-> socket to hand to the HTTP handler: TLS-wrapped if the client opened with a TLS handshake, else the
    original plain socket. None = drop the connection (closed / failed handshake)."""
    if ctx is None:
        return sock
    try:
        sock.settimeout(timeout)
        first = sock.recv(1, socket.MSG_PEEK)
    except OSError:
        return None
    if not first:
        return None
    if first != b"\x16":
        return sock
    try:
        return ctx.wrap_socket(sock, server_side=True)
    except (ssl.SSLError, OSError):
        return None


def is_tls(sock):
    return isinstance(sock, ssl.SSLSocket)


class DualProtocolMixin:
    """Mix into a socketserver.TCPServer subclass BEFORE it; define tls_context() -> SSLContext|None."""

    tls_sniff_timeout = 20.0

    def tls_context(self):
        return None

    def finish_request(self, request, client_address):
        conn = sniff_and_wrap(request, self.tls_context(), self.tls_sniff_timeout)
        if conn is None:
            return
        try:
            super().finish_request(conn, client_address)
        finally:
            if conn is not request:  # socketserver only shuts down the (now detached) raw socket
                for fn in (lambda: conn.shutdown(socket.SHUT_WR), conn.close):
                    try:
                        fn()
                    except OSError:
                        pass


# ======================================================================================================
# Phone onboarding
# ======================================================================================================
def mobileconfig(ca_der, display="Stark Local CA"):
    """Unsigned iOS configuration profile carrying the CA (com.apple.security.root). Same CA -> same UUIDs."""
    h = hashlib.sha256(ca_der).digest()
    u1, u2 = str(uuid.UUID(bytes=h[:16], version=4)).upper(), str(uuid.UUID(bytes=h[16:32], version=4)).upper()
    b64 = base64.encodebytes(ca_der).decode("ascii")
    e = html.escape(display)
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
 <key>PayloadContent</key>
 <array>
  <dict>
   <key>PayloadCertificateFileName</key><string>stark-local-ca.cer</string>
   <key>PayloadContent</key>
   <data>
%s</data>
   <key>PayloadDescription</key><string>Lets this device trust the HTTPS pages of your own Stark apps on your PC.</string>
   <key>PayloadDisplayName</key><string>%s</string>
   <key>PayloadIdentifier</key><string>local.stark.tls.ca.%s</string>
   <key>PayloadType</key><string>com.apple.security.root</string>
   <key>PayloadUUID</key><string>%s</string>
   <key>PayloadVersion</key><integer>1</integer>
  </dict>
 </array>
 <key>PayloadDescription</key><string>Local certificate authority for your own PC's apps (Stark Local TLS).</string>
 <key>PayloadDisplayName</key><string>%s</string>
 <key>PayloadIdentifier</key><string>local.stark.tls.%s</string>
 <key>PayloadRemovalDisallowed</key><false/>
 <key>PayloadType</key><string>Configuration</string>
 <key>PayloadUUID</key><string>%s</string>
 <key>PayloadVersion</key><integer>1</integer>
</dict>
</plist>
""" % (b64, e, u1, u1, e, u2, u2)).encode("utf-8")


def onboarding_html(app_name, ca_sha256, ca_cn, https_url, crt_path="/trust/ca.crt",
                    mobileconfig_path="/trust/ca.mobileconfig", ca_not_after=""):
    e = html.escape
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trust this PC</title>
<style>:root{--bg:#f7f4ee;--ink:#2a2622;--muted:#7a7168;--line:#ddd6ca;--ok:#3f6e4b;--warn:#a4552f;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#1b1d1c;--ink:#e6e1d8;--muted:#9a9e99;--line:#343836;--ok:#86b393;--warn:#e09a72;--card:#232625}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif}
main{max-width:640px;margin:0 auto;padding:16px}h1{font-size:22px;margin:8px 0}h2{font-size:17px;margin:18px 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}
a.btn{display:block;text-align:center;padding:12px;border-radius:8px;background:var(--ok);color:#fff;text-decoration:none;font-weight:600;margin:8px 0}
ol{padding-left:20px}li{margin:4px 0}code{font-size:12px;word-break:break-all}.ok{color:var(--ok);font-weight:600}.no{color:var(--warn);font-weight:600}
small{color:var(--muted)}</style></head><body><main>
<h1>Trust %(app)s on this phone</h1>
<p>One time per phone: install your PC's own certificate so the phone shows a padlock and allows screen wake lock,
microphone/camera and dictation on %(app)s pages.</p>
<div class="card" id="chk"><b>This connection:</b> <span id="sc">checking&hellip;</span><br>
Screen Wake Lock: <span id="wl">&hellip;</span><br>Dictation (Web Speech): <span id="sr">&hellip;</span><br>
Microphone / camera API: <span id="gum">&hellip;</span></div>
<h2>iPhone / iPad (Safari)</h2>
<a class="btn" href="%(mc)s">Download profile for iPhone / iPad</a>
<ol><li>Open this page in <b>Safari</b> (other iOS browsers cannot install profiles). Tap the button, then <b>Allow</b>.</li>
<li>Open <b>Settings</b> &rarr; <b>Profile Downloaded</b> (near the top) &rarr; <b>Install</b>, enter your passcode, <b>Install</b> again.</li>
<li><b>Settings &rarr; General &rarr; About &rarr; Certificate Trust Settings</b>: turn on full trust for
<b>%(cn)s</b> &rarr; <b>Continue</b>. (Without this step iOS installs it but does not trust it.)</li></ol>
<h2>Android (Chrome)</h2>
<a class="btn" href="%(crt)s">Download certificate for Android (.crt)</a>
<ol><li>Tap the button; the file lands in <b>Downloads</b>.</li>
<li><b>Settings &rarr; Security &amp; privacy &rarr; More security settings &rarr; Encryption &amp; credentials &rarr;
Install a certificate &rarr; CA certificate</b> &rarr; <b>Install anyway</b> &rarr; pick the downloaded file
(menu names differ by maker: search Settings for &ldquo;CA certificate&rdquo;).</li>
<li>Android may then say &ldquo;Network may be monitored&rdquo;: that notice is about this certificate.</li>
<li>Use Chrome. Firefox for Android ignores user certificates unless enabled in its settings.</li></ol>
<h2>Then</h2>
<p>Go back to the PC and scan the pairing code again, or open <a href="%(url)s">%(urlt)s</a>. The padlock should
show and the checks above turn green on the secure page.</p>
<div class="card"><small>Certificate: %(cn)s<br>SHA-256 fingerprint (compare with the PC screen if this is not your own Wi-Fi):<br>
<code>%(fp)s</code><br>Valid until %(na)s. Remove it any time: iOS Settings &rarr; General &rarr; VPN &amp; Device Management;
Android Settings &rarr; Encryption &amp; credentials &rarr; User credentials.<br>
Limits: Chrome's dictation sends your audio to Google's servers; everything else stays on your PC.</small></div>
<script>(function(){function s(id,ok,t){var e=document.getElementById(id);e.textContent=t||(ok?"available":"not available");e.className=ok?"ok":"no"}
s("sc",window.isSecureContext,window.isSecureContext?"secure (HTTPS trusted)":"not secure (plain HTTP or untrusted)");
s("wl","wakeLock" in navigator);s("sr",!!(window.SpeechRecognition||window.webkitSpeechRecognition));
s("gum",!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia));
document.body.setAttribute("data-secure",String(window.isSecureContext));document.body.setAttribute("data-wakelock",String("wakeLock" in navigator))})()</script>
</main></body></html>""" % {"app": e(app_name), "mc": e(mobileconfig_path), "crt": e(crt_path), "cn": e(ca_cn),
                            "url": e(https_url), "urlt": e(https_url), "fp": e(ca_sha256), "na": e(ca_not_after[:10])}


# ======================================================================================================
# CLI
# ======================================================================================================
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Local HTTPS certificates for LAN phone pages")
    ap.add_argument("cmd", choices=["ensure", "status", "remove", "spki"])
    ap.add_argument("--dir", help="state folder (default %s)" % default_state_dir())
    ap.add_argument("--backend", choices=["auto", "powershell", "openssl"], default="auto")
    a = ap.parse_args(argv)
    t = LocalTLS(state_dir=a.dir, backend=a.backend)
    if a.cmd == "remove":
        print(json.dumps({"removed_from_store": t.remove()}))
        return 0
    if a.cmd == "status":
        der = t._read_der("server.pem")
        print(json.dumps({"dir": t.dir, "backend": t.backend.name if t.backend else None,
                          "server": ({k: str(v) for k, v in parse_cert(der).items() if not k.endswith("_der")}
                                     if der else None)}, indent=1))
        return 0
    r = t.ensure()
    if a.cmd == "spki":
        print(r.get("spki_sha256_b64", ""))
    else:
        print(json.dumps(r, indent=1))
    return 0 if r.get("https") else 1


if __name__ == "__main__":
    sys.exit(main())
