"""Capped reads from untrusted zips (.docx, .odt, .epub, hand-ins, contest entries).

A small zip can inflate to gigabytes. Every member read goes through read_member(), which checks the size the
zip declares and then reads at most cap+1 bytes, so a lying header cannot get past it either. check_zip() caps
the member count and the declared total before anything is parsed.
"""
from __future__ import annotations

import zipfile

MEMBER_CAP = 20 * 1024 * 1024     # one XML part or chapter
TOTAL_CAP = 200 * 1024 * 1024     # everything a zip may declare
MAX_MEMBERS = 5000


class ZipTooBig(ValueError):
    """The zip is larger inside than Quill will open."""


def check_zip(z: zipfile.ZipFile, total_cap: int = TOTAL_CAP, max_members: int = MAX_MEMBERS) -> None:
    infos = z.infolist()
    if len(infos) > max_members:
        raise ZipTooBig("the file holds %d parts; Quill opens at most %d" % (len(infos), max_members))
    total = sum(max(0, i.file_size) for i in infos)
    if total > total_cap:
        raise ZipTooBig("the file unpacks to %d MB; Quill opens at most %d MB" % (total >> 20, total_cap >> 20))


def read_member(z: zipfile.ZipFile, name: str, cap: int = MEMBER_CAP) -> bytes:
    """Bytes of one member, refusing anything over cap (declared or actual). KeyError if it is missing."""
    info = z.getinfo(name)
    if info.file_size > cap:
        raise ZipTooBig("%s unpacks to %d MB; Quill opens at most %d MB" % (name, info.file_size >> 20, cap >> 20))
    with z.open(info) as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise ZipTooBig("%s is larger than its header says" % name)
    return data


class CappedZip(zipfile.ZipFile):
    """ZipFile whose read() is capped, for validators that walk every part of a file the writer picked."""

    def read(self, name, pwd=None):
        return read_member(self, name.filename if isinstance(name, zipfile.ZipInfo) else name)


def open_capped(path) -> "CappedZip":
    """Open a zip for checking. BadZipFile (with a plain message) if it is larger inside than Quill will open."""
    z = CappedZip(path)
    try:
        check_zip(z)
        for i in z.infolist():
            if i.file_size > MEMBER_CAP:
                raise ZipTooBig("%s unpacks to %d MB; Quill opens at most %d MB" % (i.filename, i.file_size >> 20, MEMBER_CAP >> 20))
    except ZipTooBig as e:
        z.close()
        raise zipfile.BadZipFile("too large inside: %s" % e)
    return z
