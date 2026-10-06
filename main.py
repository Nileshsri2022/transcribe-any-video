"""ASGI entrypoint: Streamlit UI + chunked streaming upload API.

Run with: streamlit run main.py
"""
import json
from pathlib import Path

import streamlit as st
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

UPLOADS = Path(__file__).resolve().parent / "uploads"


def _fid(query) -> str | None:
    fid = query.get("id", "")
    return fid if fid.isalnum() and len(fid) <= 64 else None


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
    offset = int(q.get("offset", 0))
    UPLOADS.mkdir(exist_ok=True)
    part = UPLOADS / f"{fid}.part"
    if offset == 0:
        # fresh upload: reset partial file and record expected name/size for resume
        part.unlink(missing_ok=True)
        (UPLOADS / f"{fid}.json").write_text(
            json.dumps({
                "name": Path(q.get("name", "file")).name,
                "size": int(q.get("size", 0)),
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


app = st.App(
    str(Path(__file__).resolve().parent / "app.py"),
    routes=[
        Route("/api/upload", upload, methods=["POST"]),
        Route("/api/upload", upload_status, methods=["GET"]),
    ],
)
