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

## Deploy to Streamlit Community Cloud

- **Main file path**: `app.py` (or `main.py` — both are `st.App` launchers
  that mount the `/api/upload` routes; the UI itself lives in `ui.py`).
- `requirements.txt` — Python deps (streamlit, groq, starlette).
- `packages.txt` — apt deps (`ffmpeg`).
- Add `GROQ_API_KEY` to the app's **Secrets**.

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
