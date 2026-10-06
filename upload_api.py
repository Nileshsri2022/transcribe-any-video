"""Chunked upload API shared by the st.App entrypoints (app.py / main.py).

The browser uploader (see ui.py) slices a file into 16 MB chunks and
POSTs them here; partial files live in uploads/<id>.part so interrupted
uploads can resume from the last received byte.
"""
import json
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

UPLOADS = Path(__file__).resolve().parent / "uploads"


def _fid(query) -> str | None:
    fid = query.get("id", "")
    return fid if fid.isalnum() and len(fid) <= 64 else None


def _int(query, key: str, default: int) -> int | None:
    raw = query.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return None


async def upload_status(request: Request):
    fid = _fid(request.query_params)
    if not fid:
        return JSONResponse({"error": "bad id"}, status_code=400)
    part = UPLOADS / f"{fid}.part"
    received = part.stat().st_size if part.exists() else 0
    meta = {"name": "", "size": 0}
    meta_file = UPLOADS / f"{fid}.json"
    if meta_file.exists():
        try:
            meta.update(json.loads(meta_file.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return JSONResponse({"received": received, **meta})


async def upload(request: Request):
    q = request.query_params
    fid = _fid(q)
    if not fid:
        return JSONResponse({"error": "bad id"}, status_code=400)
    offset = _int(q, "offset", 0)
    size = _int(q, "size", 0)
    if offset is None or offset < 0 or size is None or size < 0:
        return JSONResponse({"error": "bad offset/size"}, status_code=400)
    UPLOADS.mkdir(exist_ok=True)
    part = UPLOADS / f"{fid}.part"
    if offset == 0:
        # fresh upload: reset partial file and record expected name/size for resume
        part.unlink(missing_ok=True)
        (UPLOADS / f"{fid}.json").write_text(
            json.dumps({
                "name": Path(q.get("name", "file")).name,
                "size": size,
            }),
            encoding="utf-8",
        )
    elif not part.exists() or part.stat().st_size != offset:
        # client offset out of sync — report truth, client resends from there
        return JSONResponse({"received": part.stat().st_size if part.exists() else 0})
    with open(part, "ab" if offset else "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return JSONResponse({"received": part.stat().st_size})


def routes() -> list[Route]:
    return [
        Route("/api/upload", upload, methods=["POST"]),
        Route("/api/upload", upload_status, methods=["GET"]),
    ]
