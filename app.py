#!/usr/bin/env python3
"""Streamlit UI for transcribe.py — chunked Groq Whisper transcription."""
import os
import re
import subprocess
import sys
from pathlib import Path

import streamlit as st

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

st.set_page_config(page_title="Groq Whisper Transcriber", page_icon="🎙️", layout="wide")

with st.sidebar:
    st.title("🎙️ Transcriber")
    has_key = bool(os.environ.get("GROQ_API_KEY"))
    if has_key:
        st.success("GROQ_API_KEY set")
    else:
        st.error("GROQ_API_KEY not set")
        st.code(
            'PowerShell: $env:GROQ_API_KEY="your_key"\n'
            'cmd: set GROQ_API_KEY=your_key',
            language=None,
        )
    model = st.selectbox("Model", list(MODELS), format_func=lambda m: f"{m} — {MODELS[m]}")
    language = st.selectbox("Language", list(LANGUAGES))
    st.caption("Outputs go to `out/` next to the video.")

st.title("Video → Text + SRT")

video_path = st.text_input("Video file path", placeholder=r"D:\videos\lecture.mp4")
run = st.button("Transcribe", type="primary", disabled=not has_key)


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


if run:
    video = Path(video_path.strip().strip('"'))
    if not video_path.strip():
        st.stop()
    if not video.exists():
        st.error(f"File not found: {video}")
        st.stop()

    cmd = [sys.executable, "transcribe.py", str(video), "--model", model, "--out", "out"]
    if LANGUAGES[language]:
        cmd += ["--language", LANGUAGES[language]]

    st.session_state.pop("result", None)
    with st.status("Transcribing...", expanded=True) as status:
        box = st.empty()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        rc = tail_output(proc, box)
        if rc == 0:
            status.update(label="Done", state="complete", expanded=False)
            st.session_state["result"] = video.stem
        else:
            status.update(label="Failed", state="error")
            st.error(f"exit code {rc} — last lines above")

if st.session_state.get("result"):
    stem = st.session_state["result"]
    txt_path, srt_path = Path("out") / f"{stem}.txt", Path("out") / f"{stem}.srt"
    if txt_path.exists():
        st.subheader("Transcript preview")
        st.text_area("Text", txt_path.read_text(encoding="utf-8"), height=400)
        c1, c2 = st.columns(2)
        c1.download_button(
            "Download .txt", txt_path.read_bytes(), file_name=txt_path.name,
            mime="text/plain", use_container_width=True,
        )
        if srt_path.exists():
            c2.download_button(
                "Download .srt", srt_path.read_bytes(), file_name=srt_path.name,
                mime="text/plain", use_container_width=True,
            )
