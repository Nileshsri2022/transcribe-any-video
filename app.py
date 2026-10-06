#!/usr/bin/env python3
"""Streamlit UI for transcribe.py — chunked Groq Whisper transcription."""
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections import deque
from pathlib import Path

import streamlit as st

import transcribe

st.set_page_config(page_title="Groq Whisper Transcriber", page_icon="🎙️", layout="wide")

ROOT = Path(__file__).resolve().parent
UPLOADS = ROOT / "uploads"
OUT = ROOT / "out"
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac"}

MODELS = {
    "whisper-large-v3-turbo": "Faster (default)",
    "whisper-large-v3": "A bit more accurate",
}
LANGUAGES = {
    "auto-detect": None,
    "en": "English",
    "hi": "Hindi",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "zh": "Chinese",
}

# ---- chunked uploader component (browser slices file, POSTs to /api/upload) ----

_UP_HTML = """
<input type="file" id="pick"
  accept="video/*,audio/*,.mkv,.mp4,.mov,.avi,.webm,.m4v,.ts,.flv,.wmv,.mp3,.m4a,.wav,.flac,.ogg,.opus,.aac" />
<button type="button" id="go">Upload</button>
<div id="wrap" style="display:none;margin-top:.5rem">
  <div style="height:8px;background:var(--st-secondary-background-color);border-radius:4px;overflow:hidden">
    <div id="fill" style="height:100%;width:0;background:var(--st-primary-color);transition:width .2s"></div>
  </div>
  <div id="msg" style="font-size:.85rem;opacity:.8;margin-top:.25rem"></div>
</div>
"""

_UP_JS = """
async function start(root, file) {
  const id = root.__data.uploadId
  const sv = root.__sv
  const msg = root.querySelector("#msg")
  const fill = root.querySelector("#fill")
  const wrap = root.querySelector("#wrap")
  const go = root.querySelector("#go")
  wrap.style.display = "block"
  go.disabled = true
  msg.textContent = ""
  const CHUNK = 16 * 1024 * 1024
  let off = 0
  try {
    const st = await (await fetch("/api/upload?id=" + id)).json()
    if (st.name === file.name && st.size === file.size) off = st.received
    let fails = 0
    while (off < file.size) {
      const blob = file.slice(off, Math.min(off + CHUNK, file.size))
      let j
      for (;;) {
        try {
          const r = await fetch(
            "/api/upload?id=" + id + "&offset=" + off +
            "&name=" + encodeURIComponent(file.name) + "&size=" + file.size,
            { method: "POST", body: blob })
          if (!r.ok) throw new Error("HTTP " + r.status)
          j = await r.json()
          break
        } catch (e) {
          if (++fails > 5) throw e
          msg.textContent = "retry " + fails + ": " + e
          await new Promise((res) => setTimeout(res, 1000))
        }
      }
      fails = 0
      off = j.received > off ? j.received : off + blob.size
      fill.style.width = Math.round((100 * off) / file.size) + "%"
      msg.textContent = Math.round(off / 1048576) + " / " +
        Math.round(file.size / 1048576) + " MB"
    }
    sv("status", "done")
    sv("name", file.name)
    sv("size", file.size)
    msg.textContent = "Upload complete"
  } catch (e) {
    sv("status", "error")
    sv("error", String(e))
    msg.textContent = "Upload failed: " + e
  } finally {
    go.disabled = false
  }
}

export default function (component) {
  const { data, parentElement, setStateValue } = component
  const go = parentElement.querySelector("#go")
  const pick = parentElement.querySelector("#pick")
  if (!go || !pick) return
  parentElement.__data = data
  parentElement.__sv = setStateValue
  go.onclick = () => {
    const file = pick.files[0]
    if (!file) {
      parentElement.querySelector("#msg").textContent = "Choose a file first"
      return
    }
    start(parentElement, file)
  }
}
"""

_upload_component = st.components.v2.component(
    "chunked_uploader", html=_UP_HTML, js=_UP_JS,
)

# ---- helpers ----


def kind_of(path: Path) -> str:
    return "audio" if path.suffix.lower() in AUDIO_EXTS else "video"


def go_stage(stage: str, *, path: Path | None = None, name: str | None = None):
    if path is not None:
        ss.source = {"path": str(path), "name": name, "kind": kind_of(path)}
        ss.audio = None
        ss.stage = "transcribe" if ss.source["kind"] == "audio" else "convert"
    else:
        ss.stage = stage
    st.rerun()


def start_over():
    for f in UPLOADS.glob(f"{ss.upload_id}*"):
        if f.is_dir():
            shutil.rmtree(f, ignore_errors=True)
        else:
            f.unlink(missing_ok=True)
    ss.pop("upl", None)
    ss.upload_id = uuid.uuid4().hex
    ss.source = None
    ss.audio = None
    ss.result = None
    ss.stage = "input"
    st.rerun()


def convert_to_mp3(src: Path, dst: Path, bar):
    """ffmpeg video→96kbps mono MP3 with live progress; raises RuntimeError on failure."""
    try:
        dur = transcribe.get_duration(src)
    except SystemExit as e:
        raise RuntimeError(str(e)) from None
    tmp = dst.with_suffix(".tmp.mp3")
    tmp.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-b:a", "96k",
        "-progress", "pipe:1", "-nostats", str(tmp),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    tail = deque(maxlen=40)
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        if line.startswith(("out_time_us=", "out_time_ms=")):
            # both counters are microseconds (ffmpeg naming quirk)
            try:
                sec = int(line.split("=", 1)[1]) / 1_000_000
                if dur > 0:
                    bar.progress(min(sec / dur, 1.0), text=f"{sec/60:.1f} / {dur/60:.1f} min")
            except ValueError:
                pass
        else:
            tail.append(line)
    proc.wait()
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("\n".join(tail))
    tmp.replace(dst)


def tail_output(proc, box):
    """Stream subprocess stdout into the status box until it exits."""
    pattern = re.compile(r"^\[(\d+)/(\d+)\]")
    progress = None
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            if progress is None:
                progress = box.progress(0.0)
            progress.progress(int(m.group(1)) / int(m.group(2)), text=line)
        box.write(line)
    proc.wait()
    return proc.returncode


# ---- page ----

ss = st.session_state

with st.sidebar:
    st.title("🎙️ Transcriber")
    has_key = bool(os.environ.get("GROQ_API_KEY"))
    if has_key:
        st.success("GROQ_API_KEY set")
    else:
        st.error("GROQ_API_KEY not set")
        st.code(
            'PowerShell: $env:GROQ_API_KEY="your_key"\n'
            "cmd: set GROQ_API_KEY=your_key",
            language=None,
        )
    model = st.selectbox("Model", list(MODELS), format_func=lambda m: f"{m} — {MODELS[m]}")
    language = st.selectbox("Language", list(LANGUAGES))
    if st.button("Start over"):
        start_over()

ss.setdefault("upload_id", uuid.uuid4().hex)
ss.setdefault("stage", "input")
ss.setdefault("source", None)
ss.setdefault("audio", None)
ss.setdefault("result", None)

if ss.stage == "input":
    st.title("Video → Text + SRT")

    up_state = ss.get("upl", {})
    if up_state.get("status") == "done":
        name = up_state["name"]
        part = UPLOADS / f"{ss.upload_id}.part"
        final = UPLOADS / f"{ss.upload_id}-{name}"
        if part.exists():
            part.replace(final)
        if final.exists():
            go_stage(None, path=final, name=name)
        st.error("Uploaded file missing — please upload again.")
        ss.pop("upl", None)
    elif up_state.get("status") == "error":
        st.error(f"Upload failed: {up_state.get('error')}")

    _upload_component(key="upl", data={"uploadId": ss.upload_id})

    st.caption("— or —")
    path_in = st.text_input("Local file path (video or audio)", placeholder=r"D:\videos\lecture.mp4")
    if st.button("Load path"):
        p = Path(path_in.strip().strip('"'))
        if not p.exists():
            st.error(f"File not found: {p}")
        else:
            go_stage(None, path=p, name=p.name)
    st.stop()

if ss.stage == "convert":
    src = Path(ss.source["path"])
    dst = UPLOADS / f"{ss.upload_id}.mp3"
    st.title("Convert to audio")
    st.caption(f"Converting {ss.source['name']} → MP3 (96 kbps mono)")
    bar = st.progress(0.0, text="Starting ffmpeg…")
    try:
        convert_to_mp3(src, dst, bar)
    except RuntimeError as e:
        st.error(f"ffmpeg failed:\n\n```\n{e}\n```")
        st.stop()
    bar.progress(1.0, text="Done")
    ss.audio = str(dst)
    ss.stage = "ready"
    st.rerun()

if ss.stage == "ready":
    audio = Path(ss.audio)
    stem = Path(ss.source["name"]).stem
    st.title("Audio ready")
    c1, c2 = st.columns(2)
    c1.download_button(
        f"⬇ Download {stem}.mp3", audio.read_bytes(),
        file_name=f"{stem}.mp3", mime="audio/mpeg", type="primary",
    )
    if c2.button("Continue → Transcribe", type="primary"):
        go_stage("transcribe")
    st.stop()

if ss.stage == "transcribe":
    src = Path(ss.audio) if ss.audio else Path(ss.source["path"])
    orig_stem = Path(ss.source["name"]).stem
    st.title("Transcribing")
    cmd = [
        sys.executable, str(ROOT / "transcribe.py"), str(src),
        "--model", model, "--out", str(OUT),
    ]
    if LANGUAGES[language]:
        cmd += ["--language", LANGUAGES[language]]
    with st.status("Transcribing...", expanded=True) as status:
        box = st.empty()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        rc = tail_output(proc, box)
    if rc == 0:
        # transcribe.py names outputs after the source file — rename to the original name
        for ext in ("txt", "srt"):
            produced = OUT / f"{src.stem}.{ext}"
            wanted = OUT / f"{orig_stem}.{ext}"
            if produced.exists() and produced != wanted:
                produced.replace(wanted)
        status.update(label="Done", state="complete", expanded=False)
        ss.result = orig_stem
        ss.stage = "done"
        st.rerun()
    status.update(label="Failed", state="error")
    st.error(f"exit code {rc} — see log above")
    st.stop()

if ss.stage == "done":
    stem = ss.result
    txt_path, srt_path = OUT / f"{stem}.txt", OUT / f"{stem}.srt"
    st.title("Transcript")
    if txt_path.exists():
        st.subheader("Preview")
        st.text_area("Text", txt_path.read_text(encoding="utf-8"), height=400)
        c1, c2 = st.columns(2)
        c1.download_button(
            "Download .txt", txt_path.read_bytes(), file_name=txt_path.name,
            mime="text/plain", type="primary",
        )
        if srt_path.exists():
            c2.download_button(
                "Download .srt", srt_path.read_bytes(), file_name=srt_path.name,
                mime="text/plain",
            )
    else:
        st.error(f"Missing {txt_path}")
