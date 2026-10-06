---
title: Transcribe Any Video
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Groq Whisper Transcriber 🎙️

Turn long videos or audio files (even 2 GB+) into a text transcript and an
`.srt` subtitle file, using [Groq](https://groq.com)'s Whisper API on the free
tier. The video is never uploaded anywhere — ffmpeg extracts tiny mono audio
locally, which is what keeps the free-tier limits workable.

## How it works

1. **Upload** — the browser slices the file into 16 MB chunks and POSTs them
   to `/api/upload` (a Starlette route mounted via `st.App`), with a progress
   bar, automatic retries, and resume of interrupted uploads. Alternatively,
   enter a local file path (useful when running locally).
2. **Convert** — video inputs are converted to a small mono MP3 with live
   ffmpeg progress.
3. **Transcribe** — audio is split into 10-minute chunks (4 s overlap) and
   sent to Groq's Whisper, paced by a sliding-window rate limiter
   (≈6,800 audio-seconds/hour, 15 req/min) with 429/`Retry-After` handling.
   Finished chunks are cached to disk, so an interrupted run resumes.
4. **Download** — preview the transcript and download `.txt` / `.srt`.

## Run locally

```bash
pip install -r requirements.txt   # or: uv sync
# ffmpeg + ffprobe must be on PATH

set GROQ_API_KEY=your_key         # Windows cmd
# $env:GROQ_API_KEY="your_key"    # PowerShell
# export GROQ_API_KEY=your_key    # bash

streamlit run app.py              # or main.py — equivalent entrypoints
```

CLI only (no UI): `python transcribe.py video.mp4 [--language en] [--model whisper-large-v3]`

## Deploy

### Hugging Face Space (recommended — full chunked uploads)

The `Dockerfile` runs the whole app (UI + `/api/upload` routes) in one
container on port 7860. HF builds and hosts it for free — no local Docker.

1. Create a free [Hugging Face](https://huggingface.co) account, then a new
   **Space** → SDK **Docker** → blank template.
2. Space **Settings → Variables and secrets** → add secret `GROQ_API_KEY`.
3. Push this repo to the Space:
   ```bash
   git remote add space https://huggingface.co/spaces/<your-username>/transcribe-any-video
   git push space main
   ```
4. Live at `https://<your-username>-transcribe-any-video.hf.space` after the
   build (~3 min).

Notes: free Spaces sleep after ~48 h idle (wake ~1 min on next visit);
uploads go through HF's proxy as 16 MB chunks (well under its ~50 MB
per-request cap); a **private** Space limits use to your account, which
protects your Groq free-tier quota.

### Streamlit Community Cloud (limited)

Main file `app.py` or `main.py`, secrets `GROQ_API_KEY`, `packages.txt` for
ffmpeg. Cloud's edge blocks the custom upload API, so the app automatically
falls back to Streamlit's built-in uploader (files up to ~200 MB).

## Tests

```bash
python test_flow.py    # convert, stages, upload API on both entrypoints, noapi fallback
node test_upload_js.mjs  # runs the real uploader JS against the live server (started by test_flow.py)
```

## Files

| File | Role |
|---|---|
| `app.py` / `main.py` | `st.App` entrypoints: UI + `/api/upload` routes |
| `ui.py` | Streamlit UI (stage-based: input → convert → ready → transcribe → done) |
| `upload_api.py` | Chunked upload API handlers shared by both entrypoints |
| `transcribe.py` | Transcription engine (also a standalone CLI) |
| `test_flow.py` | Smoke tests for the whole pipeline |
| `packages.txt` | apt packages for Streamlit Cloud (`ffmpeg`) |
