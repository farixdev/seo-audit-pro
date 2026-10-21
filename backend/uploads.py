# -*- coding: utf-8 -*-
"""
Safe file uploads.

* Extension allow-list per purpose
* Content sniffing (magic bytes) — the client-declared MIME type is ignored
* Size limits
* Random stored names; the original filename is only kept as display text
* Files are always served as attachments by id, never by path
"""

import hashlib
import os
import re
import zipfile

from backend import config, db, security
from backend.errors import ApiError, not_found
from backend.util import now_iso

_ALLOWED = {
    "chat": {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".csv", ".xlsx"},
    "email": {".pdf", ".docx", ".png", ".jpg", ".jpeg"},
    "avatar": {".png", ".jpg", ".jpeg"},
}

_MIME = {".pdf": "application/pdf", ".doc": "application/msword",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".txt": "text/plain", ".csv": "text/csv"}


def _sniff_ok(ext, head, path):
    if ext == ".pdf":
        return head.startswith(b"%PDF-")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".doc":
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext in (".docx", ".xlsx"):
        if not head.startswith(b"PK\x03\x04"):
            return False
        marker = "word/" if ext == ".docx" else "xl/"
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if len(names) > 2000:
                    return False
                return any(n.startswith(marker) for n in names)
        except zipfile.BadZipFile:
            return False
    if ext in (".txt", ".csv"):
        if b"\x00" in head:
            return False
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            try:
                head.decode("latin-1")
            except UnicodeDecodeError:
                return False
        return True
    return False


def safe_display_name(name):
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^\w.\- ()]", "_", base).strip(" .")
    return (base or "file")[:150]


def save_upload(file_storage, owner_id, purpose):
    """Validate and persist a werkzeug FileStorage. Returns the upload row."""
    if file_storage is None or not file_storage.filename:
        raise ApiError(422, "Choose a file to upload.")
    display = safe_display_name(file_storage.filename)
    ext = os.path.splitext(display)[1].lower()
    if ext not in _ALLOWED[purpose]:
        allowed = ", ".join(sorted(_ALLOWED[purpose]))
        raise ApiError(422, f"This file type is not allowed. Allowed types: {allowed}.")
    limit = config.MAX_AVATAR_BYTES if purpose == "avatar" else config.MAX_UPLOAD_BYTES
    upload_id = security.new_id("upl_")
    stored = f"{upload_id}{ext}"
    path = os.path.join(config.UPLOAD_DIR, stored)
    sha = hashlib.sha256()
    size = 0
    with open(path, "wb") as out:
        while True:
            chunk = file_storage.stream.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                out.close()
                os.remove(path)
                raise ApiError(413, f"The file is larger than {limit // (1024 * 1024)} MB.")
            sha.update(chunk)
            out.write(chunk)
    if size == 0:
        os.remove(path)
        raise ApiError(422, "The file is empty.")
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if not _sniff_ok(ext, head, path):
        os.remove(path)
        raise ApiError(422, "The file content does not match its type.")
    row = {"id": upload_id, "owner_id": owner_id, "purpose": purpose, "original_name": display,
           "stored_name": stored, "mime": _MIME[ext], "size_bytes": size, "sha256": sha.hexdigest(),
           "created_at": now_iso()}
    db.execute("INSERT INTO uploads (id, owner_id, purpose, original_name, stored_name, mime, size_bytes, sha256, created_at) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
               tuple(row[k] for k in ("id", "owner_id", "purpose", "original_name", "stored_name", "mime",
                                      "size_bytes", "sha256", "created_at")))
    return row


def upload_path(row):
    path = os.path.realpath(os.path.join(config.UPLOAD_DIR, row["stored_name"]))
    if not path.startswith(os.path.realpath(config.UPLOAD_DIR) + os.sep) or not os.path.isfile(path):
        raise not_found("File")
    return path


def public_upload(row):
    return {"id": row["id"], "name": row["original_name"], "mime": row["mime"], "size_bytes": row["size_bytes"]}
