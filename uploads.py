"""Bounded, single-file multipart reader; client names never become paths."""
import re
from email.message import Message
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException, Request
from config import MAX_BYTES

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}


def valid_filename(value):
    decoded = unquote(value)
    return (len(decoded) <= 240 and not decoded.startswith("-")
            and not any(char in decoded for char in ("..", "/", "\\", ":"))
            and not any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            and Path(decoded).suffix.lower() in VIDEO_SUFFIXES)


async def receive_upload(request: Request, destination: Path):
    """Stream one multipart `file` part, with bounded header/tail buffers.

    Deliberately accepts only the single-file form used by this UI and curl -F.
    This avoids adding python-multipart to the existing environment.
    """
    content_type = Message()
    content_type["content-type"] = request.headers.get("content-type", "")
    boundary = content_type.get_param("boundary")
    if content_type.get_content_type() != "multipart/form-data" or not boundary:
        raise HTTPException(400, "Upload a video as multipart/form-data with one field named file.")
    if not re.fullmatch(r"[\x21-\x7e]{1,70}", boundary):
        raise HTTPException(400, "Invalid upload boundary.")
    opening = b"--" + boundary.encode("ascii") + b"\r\n"
    marker = b"\r\n--" + boundary.encode("ascii")
    pending = b""
    state = "headers"
    size = 0
    with destination.open("xb") as output:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BYTES + 16384:
                raise HTTPException(413, "Videos must be smaller than 200 MiB.")
            pending += chunk
            if state == "headers":
                end = pending.find(b"\r\n\r\n")
                if end < 0:
                    if len(pending) > 16384:
                        raise HTTPException(400, "Upload headers are too large.")
                    continue
                if end > 16384 or not pending.startswith(opening):
                    raise HTTPException(400, "Invalid upload headers.")
                headers = Message()
                for line in pending[len(opening):end].decode("latin-1").split("\r\n"):
                    name, separator, value = line.partition(":")
                    if not separator:
                        raise HTTPException(400, "Invalid upload headers.")
                    headers[name] = value.strip()
                name = headers.get_param("name", header="content-disposition")
                filename = headers.get_filename()
                if headers.get_content_disposition() != "form-data" or name != "file" or not filename:
                    raise HTTPException(400, "Use a single video field named file.")
                if not valid_filename(filename):
                    raise HTTPException(400, "Upload an MP4, MOV or WebM.")
                pending = pending[end + 4:]
                state = "file"
            if state == "file":
                end = pending.find(marker)
                if end >= 0:
                    output.write(pending[:end])
                    pending = pending[end + len(marker):]
                    state = "end"
                elif len(pending) > len(marker):
                    output.write(pending[:-len(marker)])
                    pending = pending[-len(marker):]
            if state == "end" and len(pending) > 4:
                raise HTTPException(400, "Upload one file at a time, with no additional form fields.")
        if state != "end" or pending not in (b"--", b"--\r\n"):
            raise HTTPException(400, "The upload was incomplete. Try again.")
        if not 0 < output.tell() <= MAX_BYTES:
            raise HTTPException(413, "Choose a non-empty video smaller than 200 MiB.")

    return filename
