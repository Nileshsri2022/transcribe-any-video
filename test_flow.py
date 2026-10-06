"""Smoke tests: chunked upload API (live server) + convert_to_mp3 + stage routing."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import ui as ui

BASE = "http://localhost:8599"
PARTS = Path("uploads")


def req(base, method, url, data=None):
    r = urllib.request.Request(base + url, data=data, method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)


def wait_server(base):
    for _ in range(90):
        try:
            urllib.request.urlopen(base + "/", timeout=2)
            return
        except Exception:
            time.sleep(1)
    sys.exit("server did not start")


def ensure_server(port, entrypoint):
    base = f"http://localhost:{port}"
    try:
        urllib.request.urlopen(base + "/", timeout=2)
        return None  # already running
    except Exception:
        pass
    p = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", entrypoint,
         "--server.headless", "true", "--server.port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    wait_server(base)
    return p


def test_js_upload():
    import shutil as sh
    if not sh.which("node"):
        print("js upload SKIP (no node)")
        return
    subprocess.run(
        [sys.executable, "-c", "import ui; open('app_component.mjs','w',encoding='utf-8').write(ui._UP_JS)"],
        check=True, capture_output=True,
    )
    subprocess.run(["node", "test_upload_js.mjs"], check=True)
    sh.rmtree("uploads", ignore_errors=True)
    print("js upload OK")


def test_noapi_js():
    """The uploader JS must fall back cleanly when /api/upload is missing."""
    if not shutil.which("node"):
        print("noapi js SKIP (no node)")
        return
    env = {**os.environ, "UPLOAD_MODE": "noapi", "UPLOAD_BASE": "http://localhost:8597"}
    subprocess.run(["node", "test_upload_js.mjs"], check=True, env=env)
    print("noapi js OK")


def test_upload_route(base):
    st = req(base, "GET", "/api/upload?id=t1abc")
    assert st["received"] == 0 and st["size"] == 0, st

    b1 = b"x" * 1000
    r = req(base, "POST", "/api/upload?id=t1abc&offset=0&name=vid.mp4&size=1500", b1)
    assert r["received"] == 1000, r

    st = req(base, "GET", "/api/upload?id=t1abc")
    assert st == {"received": 1000, "name": "vid.mp4", "size": 1500}, st

    # offset out of sync -> server reports truth, no append
    r = req(base, "POST", "/api/upload?id=t1abc&offset=500&name=vid.mp4&size=1500", b"y" * 100)
    assert r["received"] == 1000, r

    # resume append
    r = req(base, "POST", "/api/upload?id=t1abc&offset=1000&name=vid.mp4&size=1500", b"y" * 500)
    assert r["received"] == 1500, r
    assert (PARTS / "t1abc.part").read_bytes() == b1 + b"y" * 500

    # restart from offset 0 wipes partial
    r = req(base, "POST", "/api/upload?id=t1abc&offset=0&name=vid.mp4&size=4", b"abcd")
    assert r["received"] == 4, r
    assert (PARTS / "t1abc.part").read_bytes() == b"abcd"

    # traversal name sanitized
    req(base, "POST", "/api/upload?id=t2def&offset=0&name=../../evil.mp4&size=3", b"abc")
    meta = json.loads((PARTS / "t2def.json").read_text(encoding="utf-8"))
    assert meta["name"] == "evil.mp4", meta

    # bad id rejected
    try:
        req(base, "GET", "/api/upload?id=../evil")
        raise AssertionError("bad id accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code

    for f in PARTS.glob("t1*"):
        f.unlink()
    for f in PARTS.glob("t2*"):
        f.unlink()
    print("upload route OK")


class Bar:
    def __init__(self):
        self.values = []

    def progress(self, v, text=None):
        self.values.append(v)


def test_convert():
    video = Path("test_in.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", str(video)],
        check=True, capture_output=True,
    )
    out = Path("test_out.mp3")
    out.unlink(missing_ok=True)
    bar = Bar()
    ui.convert_to_mp3(video, out, bar)
    assert out.exists() and out.stat().st_size > 1000, "mp3 missing/empty"
    assert bar.values and max(bar.values) <= 1.0 and bar.values[-1] >= 0.99, bar.values
    dur = ui.transcribe.get_duration(out)
    assert 2.5 < dur < 3.5, dur

    # failure path raises with ffmpeg tail, leaves no output
    bad = Path("test_bad.mp4")
    bad.write_bytes(b"not a video")
    try:
        ui.convert_to_mp3(bad, Path("test_bad.mp3"), Bar())
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "ffmpeg" in str(e).lower() or "Invalid" in str(e), e
    assert not Path("test_bad.mp3").exists()

    video.unlink()
    out.unlink()
    bad.unlink(missing_ok=True)
    print("convert_to_mp3 OK")


def test_kind():
    assert ui.kind_of(Path("a.MP3")) == "audio"
    assert ui.kind_of(Path("b.mkv")) == "video"
    assert ui.kind_of(Path("c.flac")) == "audio"
    print("kind_of OK")


def test_noapi_fallback():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("ui.py")
    at.session_state["upl"] = {"status": "noapi"}
    at.run()
    assert not at.exception, at.exception
    assert len(at.warning) == 1, [str(w.value) for w in at.warning]
    assert len(at.file_uploader) == 1
    print("noapi fallback OK")


def test_app_stages():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("ui.py")
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["stage"] == "input"

    # audio source -> straight to transcribe (no convert stage)
    at.session_state["source"] = {"path": "missing.mp3", "name": "missing.mp3", "kind": "audio"}
    at.session_state["audio"] = None
    at.session_state["stage"] = "transcribe"
    at.run(timeout=120)
    assert not at.exception, at.exception
    assert at.session_state["stage"] == "transcribe"  # failed run stays
    assert any("exit code" in str(e.value) for e in at.error), [str(e.value) for e in at.error]

    # video source -> convert stage reached (ffmpeg fails on missing file -> error shown)
    at.session_state["source"] = {"path": "missing.mp4", "name": "missing.mp4", "kind": "video"}
    at.session_state["stage"] = "convert"
    at.run(timeout=60)
    assert not at.exception, at.exception
    assert any("ffmpeg failed" in str(e.value) for e in at.error), [str(e.value) for e in at.error]

    # start over must delete upload files AND work directories
    uid = at.session_state["upload_id"]
    (PARTS / f"{uid}_work").mkdir(parents=True, exist_ok=True)
    (PARTS / f"{uid}_work" / "x.json").write_text("{}")
    (PARTS / f"{uid}.part").write_bytes(b"z")
    buttons = list(at.button) + list(at.sidebar.button)
    btn = next(b for b in buttons if b.label == "Start over")
    btn.click().run(timeout=60)
    assert not at.exception, at.exception
    assert at.session_state["stage"] == "input"
    assert not (PARTS / f"{uid}_work").exists()
    assert not (PARTS / f"{uid}.part").exists()
    shutil.rmtree("missing_work", ignore_errors=True)
    print("app stages OK")


if __name__ == "__main__":
    test_kind()
    test_convert()
    test_noapi_fallback()
    test_app_stages()

    server = ensure_server(8599, "main.py")
    try:
        test_upload_route("http://localhost:8599")
        test_js_upload()
    finally:
        if server:
            server.terminate()
    print("main.py entrypoint OK")

    # whichever entrypoint Streamlit Cloud runs must serve the API — the
    # deployed failure was /api/upload returning the SPA's HTML
    server = ensure_server(8598, "app.py")
    try:
        test_upload_route("http://localhost:8598")
    finally:
        if server:
            server.terminate()
    print("app.py entrypoint OK")

    # ui.py run directly has no routes — the JS must report noapi (fallback UI)
    server = ensure_server(8597, "ui.py")
    try:
        test_noapi_js()
    finally:
        if server:
            server.terminate()

    shutil.rmtree("uploads", ignore_errors=True)
    print("ALL OK")
