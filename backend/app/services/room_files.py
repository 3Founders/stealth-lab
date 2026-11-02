"""Files sent in a room (db/172_room_files.sql, docs/adding_files.md).

The message carries a file id and metadata; the bytes are stored here and served only to people in the room. The rules, from the
upload-safety list in that doc (OWASP): the file's true type is sniffed from its bytes (the filename and the Content-Type header are
user input); the name is cleaned and the file stored under an id we choose; programs are refused; size is capped; nothing is served
as a page of ours (everything but pictures, audio, video and PDF is a download, with ``nosniff``).

Honest limits: bytes are in the database for now (the column that will point at object storage is there); no malware scan runs yet
(``scanned`` stays false and the response says so); resumable uploads and server-made thumbnails are not built; a download needs the
person's session token (no signed URLs yet).
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.rooms import RoomError, _Limiter, _iso, _member_room, _uuid
from app.utils.ids import uuid7

MAX_BYTES = int(os.environ.get("KELL_FILE_MAX_MB", "25")) * 1024 * 1024
RETENTION_DAYS = int(os.environ.get("KELL_FILE_RETENTION_DAYS", "30"))
MAX_FILES_PER_ROOM = 2000
# Uploads per person per minute, in memory (one worker, like the rooms limiter): a loop of uploads must not fill the table.
UPLOADS_PER_MINUTE = int(os.environ.get("KELL_UPLOADS_PER_MIN", "20"))
LIMITER = _Limiter(UPLOADS_PER_MINUTE, 60.0)

BLOCKED_EXT = {"exe", "dll", "msi", "scr", "com", "bat", "cmd", "vbs", "ps1", "jar", "apk", "app", "dmg", "pif", "cpl", "lnk", "reg"}
# Served inline (the browser draws them). Everything else is a download. SVG and HTML are never inline: they can run script.
INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "audio/mpeg", "audio/ogg", "audio/wav",
          "video/mp4", "video/webm"}
ZIP_OFFICE = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def safe_name(raw: str) -> str:
    """No path, no control characters, at most 120 characters, the extension kept."""
    base = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in unicodedata.normalize("NFKC", base) if c.isprintable() and c not in '<>:"|?*').strip(" .")
    if not base:
        return "file"
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 119 - len(ext)] + "." + ext) if dot and len(ext) <= 12 else base[:120]
    return base


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def sniff(data: bytes, name: str) -> str:
    """The true type, from the bytes. An unknown type is application/octet-stream (a download)."""
    h = data[:16]
    ext = extension(name)
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if h.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if h[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if h[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if h.startswith(b"OggS"):
        return "audio/ogg"
    if h.startswith(b"ID3") or h[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if h.startswith(b"PK\x03\x04"):
        return ZIP_OFFICE.get(ext, "application/zip")
    if h.startswith(b"\xd0\xcf\x11\xe0"):
        return "application/x-ole-storage"        # old .doc / .xls / .ppt
    if h.startswith((b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"#!")):
        return "application/x-executable"
    if b"\x00" not in data[:4096]:
        try:
            data[:65536].decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return "application/octet-stream"


def check(name: str, data: bytes) -> tuple[str, str]:
    """(clean name, true mime), or a RoomError saying why the file is refused."""
    if not data:
        raise RoomError("that file is empty")
    if len(data) > MAX_BYTES:
        raise RoomError(f"a file is at most {MAX_BYTES // (1024 * 1024)} MB here", 413)
    clean = safe_name(name)
    mime = sniff(data, clean)
    if extension(clean) in BLOCKED_EXT or mime == "application/x-executable":
        raise RoomError("programs and scripts can't be sent here")
    return clean, mime


def _json(row: Any) -> dict[str, Any]:
    return {"id": str(row["id"]), "name": row["name"], "mime": row["mime"], "size": row["size"], "sha256": row["sha256"],
            "state": row["state"], "scanned": row["scanned"], "expires_at": _iso(row["expires_at"]),
            "inline": row["mime"] in INLINE}


async def upload(pool: Any, sub: str, room_id: str, name: str, data: bytes) -> dict[str, Any]:
    room = await _member_room(pool, sub, room_id)
    if not LIMITER.allow(sub):
        raise RoomError("too many files at once; wait a minute", 429)
    clean, mime = check(name, data)
    digest = hashlib.sha256(data).hexdigest()
    existing = await pool.fetchrow("SELECT * FROM room_files WHERE room_id = $1 AND sha256 = $2", room["id"], digest)
    if existing is not None and existing["state"] != "expired":
        return {**_json(existing), "duplicate": True}
    count = await pool.fetchval("SELECT count(*) FROM room_files WHERE room_id = $1", room["id"])
    if int(count or 0) >= MAX_FILES_PER_ROOM:
        raise RoomError("this room has reached its file limit", 413)
    expires = datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)
    row = await pool.fetchrow(
        "INSERT INTO room_files (id, room_id, uploader, sha256, size, mime, name, state, scanned, storage, data, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, 'clean', false, 'db', $8, $9) "
        "ON CONFLICT (room_id, sha256) DO UPDATE SET expires_at = EXCLUDED.expires_at, state = 'clean', data = EXCLUDED.data "
        "RETURNING *", uuid7(), room["id"], sub, digest, len(data), mime, clean, data, expires)
    return {**_json(row), "duplicate": False}


async def fetch(pool: Any, sub: str, room_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
    room = await _member_room(pool, sub, room_id)
    fid = _uuid(file_id, "file id")
    row = await pool.fetchrow("SELECT * FROM room_files WHERE id = $1 AND room_id = $2", fid, room["id"])
    if row is None:
        raise RoomError("no such file", 404)
    if row["state"] == "quarantine":
        raise RoomError("that file was removed", 410)
    if row["state"] == "expired" or row["expires_at"] <= datetime.now(timezone.utc) or row["data"] is None:
        raise RoomError("that file has expired; ask the sender to send it again", 410)
    return _json(row), bytes(row["data"])


def download_headers(meta: dict[str, Any]) -> dict[str, str]:
    """Never let a stored file run as our page: a download unless it is a picture, audio, video or PDF; nosniff always."""
    name = re.sub(r"[^\x20-\x7e]", "_", meta["name"]).replace('"', "'")
    disp = "inline" if meta["inline"] else "attachment"
    return {"Content-Type": meta["mime"] if meta["inline"] else "application/octet-stream",
            "Content-Disposition": f'{disp}; filename="{name}"',
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox"}
