#!/usr/bin/env python3
"""
Transcribe a long video (even 2 GB+) using ffmpeg + Groq Whisper (free tier).

How it avoids rate limits:
  * Extracts tiny mono Opus audio (2 hr ~ 20-30 MB) - the video is never uploaded.
  * Splits audio into chunks (default 10 min, with small overlap on both sides).
  * Paces requests with a sliding-window limiter (audio-seconds/hour, requests/min).
  * On HTTP 429 it waits for the Retry-After time and retries.
  * Saves every finished chunk to disk, so you can stop and re-run to resume.

Setup:
pip install groq
ffmpeg + ffprobe must be on PATH
set GROQ_API_KEY=your_key        (Windows cmd)   |  $env:GROQ_API_KEY="your_key" (PowerShell)
uv tree
Resolved 15 packages in 1ms
uv tree 
groq-plus-ffmpeg v0.1.0
└── groq v1.7.0
    ├── anyio v4.15.1
    │   ├── idna v3.20
    │   └── typing-extensions v4.16.0
    ├── distro v1.9.0
    ├── httpx v0.28.1
    │   ├── anyio v4.15.1 (*)
    │   ├── certifi v2026.7.22
    │   ├── httpcore v1.0.9
    │   │   ├── certifi v2026.7.22
    │   │   └── h11 v0.16.0
    │   └── idna v3.20
    ├── pydantic v2.13.5
    │   ├── annotated-types v0.8.0
    │   ├── pydantic-core v2.46.5
    │   │   └── typing-extensions v4.16.0
    │   ├── typing-extensions v4.16.0
    │   └── typing-inspection v0.4.4
    │       └── typing-extensions v4.16.0
    ├── sniffio v1.3.1
    └── typing-extensions v4.16.0
(*) Package tree already displayed
Usage:
python transcribe_groq.py video.mp4
python transcribe_groq.py video.mp4 --language en --model whisper-large-v3
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from groq import APIConnectionError, APIStatusError, Groq, RateLimitError

# ---- Tunables (check Groq's rate-limit page; free tier is roughly 7200 audio sec/hour,
#      ~20 requests/min, ~28800 audio sec/day). Values below leave a safety margin. ----
AUDIO_SEC_PER_HOUR = 6800
REQUESTS_PER_MIN = 15
CHUNK_SEC = 600      # nominal chunk length (10 min)
OVERLAP_SEC = 4      # extra audio on each side so words at boundaries aren't cut
BITRATE = "24k"      # 24 kbps mono opus is plenty for speech


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"Command failed: {' '.join(cmd)}\n{p.stderr[-1500:]}")
    return p.stdout


def get_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def extract_audio(video, out, parts=None):
    """Convert video to mono Opus audio using several ffmpeg processes at once."""
    base = ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", BITRATE]
    total = get_duration(video)
    parts = parts or min(os.cpu_count() or 4, 8)
    t0 = time.time()

    if total < 600 or parts < 2:
        print("  single ffmpeg process", flush=True)
        run(["ffmpeg", "-y", "-i", str(video), *base, str(out)])
        print(f"Extraction done in {time.time() - t0:.1f}s", flush=True)
        return

    step = total / parts
    files = [out.parent / f"part_{i}.ogg" for i in range(parts)]

    def job(i):
        print(f"  part {i+1}/{parts} STARTED  at {time.time() - t0:6.1f}s", flush=True)
        run(["ffmpeg", "-y", "-ss", f"{i * step:.3f}", "-t", f"{step:.3f}",
             "-i", str(video), *base, str(files[i])])
        print(f"  part {i+1}/{parts} FINISHED at {time.time() - t0:6.1f}s", flush=True)

    print(f"Extracting audio with {parts} parallel ffmpeg processes...", flush=True)
    with ThreadPoolExecutor(parts) as ex:
        list(ex.map(job, range(parts)))

    print(f"Joining parts ({time.time() - t0:.1f}s elapsed)...", flush=True)
    listfile = out.parent / "parts.txt"
    listfile.write_text("".join(f"file '{f.name}'\n" for f in files), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-c", "copy", str(out)])
    for f in files:
        f.unlink(missing_ok=True)
    listfile.unlink(missing_ok=True)
    print(f"Extraction done in {time.time() - t0:.1f}s", flush=True)

def cut_chunk(audio, start, dur, out):
    run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(audio),
        "-c:a", "libopus", "-b:a", BITRATE, str(out)])


class Limiter:
    """Sliding-window limiter: audio seconds per hour and requests per minute."""

    def __init__(self, sec_per_hour, req_per_min):
        self.sph, self.rpm = sec_per_hour, req_per_min
        self.audio = deque()  # (timestamp, seconds)
        self.reqs = deque()   # timestamps

    def wait(self, dur):
        while True:
            now = time.time()
            while self.audio and now - self.audio[0][0] >= 3600:
                self.audio.popleft()
            while self.reqs and now - self.reqs[0] >= 60:
                self.reqs.popleft()
            used = sum(s for _, s in self.audio)
            ok_audio = used + dur <= self.sph or not self.audio
            ok_req = len(self.reqs) < self.rpm
            if ok_audio and ok_req:
                return
            until = now + 1
            if not ok_audio:
                until = max(until, self.audio[0][0] + 3600)
            if not ok_req:
                until = max(until, self.reqs[0] + 60)
            pause = until - now
            print(f"   pacing: waiting {pause/60:.1f} min to stay under rate limit...")
            time.sleep(min(pause, 30))

    def record(self, dur):
        now = time.time()
        self.audio.append((now, dur))
        self.reqs.append(now)


def seg_get(seg, key):
    return seg[key] if isinstance(seg, dict) else getattr(seg, key)


def transcribe_chunk(client, path, model, language):
    kwargs = dict(model=model, response_format="verbose_json",
                timestamp_granularities=["segment"], temperature=0.0)
    if language:
        kwargs["language"] = language
    for attempt in range(10):
        try:
            with open(path, "rb") as f:
                resp = client.audio.transcriptions.create(file=(path.name, f.read()), **kwargs)
            segs = getattr(resp, "segments", None) or []
            return [{"start": float(seg_get(s, "start")),
                    "end": float(seg_get(s, "end")),
                    "text": seg_get(s, "text").strip()} for s in segs]
        except RateLimitError as e:
            ra = None
            try:
                ra = float(e.response.headers.get("retry-after"))
            except Exception:
                pass
            wait = (ra + 2) if ra else 60 * (attempt + 1)
            if wait > 3600:
                sys.exit(f"Rate limit says wait {wait/3600:.1f} h (daily quota?). "
                        "Progress is saved - re-run the same command later to resume.")
            print(f"   429 rate limited, waiting {wait:.0f}s (attempt {attempt+1})")
            time.sleep(wait)
        except APIStatusError as e:
            if e.status_code < 500:
                raise
            time.sleep(10 * (attempt + 1))
        except APIConnectionError:
            time.sleep(10 * (attempt + 1))
    sys.exit("Too many failed attempts. Re-run to resume.")


def fmt_ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", default="whisper-large-v3-turbo",
                    help="whisper-large-v3-turbo (faster) or whisper-large-v3 (a bit more accurate)")
    ap.add_argument("--language", default=None, help="e.g. en, hi (auto-detect if omitted)")
    ap.add_argument("--keep", action="store_true", help="keep temp files")
    ap.add_argument("--out", default="out", help="folder for the .txt and .srt files")
    args = ap.parse_args()

    if not os.environ.get("GROQ_API_KEY"):
        sys.exit("Set GROQ_API_KEY first.")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on PATH.")

    video = Path(args.video)
    work = video.with_name(video.stem + "_work")
    work.mkdir(exist_ok=True)

    audio = work / "audio.ogg"
    if not audio.exists():
        print("Extracting audio...")
        extract_audio(video, audio)
    total = get_duration(audio)
    print(f"Audio: {total/60:.1f} min, {audio.stat().st_size/1e6:.1f} MB")

    n_chunks = int(-(-total // CHUNK_SEC))
    client = Groq(max_retries=0)  # we handle retries ourselves
    limiter = Limiter(AUDIO_SEC_PER_HOUR, REQUESTS_PER_MIN)
    all_segments = []

    for i in range(n_chunks):
        nom_start = i * CHUNK_SEC
        nom_end = min(nom_start + CHUNK_SEC, total)
        c_start = max(0.0, nom_start - OVERLAP_SEC)
        c_end = min(total, nom_end + OVERLAP_SEC)
        c_dur = c_end - c_start

        cache = work / f"chunk_{i:03d}.json"
        if cache.exists():
            segs = json.loads(cache.read_text(encoding="utf-8"))
            print(f"[{i+1}/{n_chunks}] cached")
        else:
            print(f"[{i+1}/{n_chunks}] {c_start/60:.1f}-{c_end/60:.1f} min")
            piece = work / f"chunk_{i:03d}.ogg"
            cut_chunk(audio, c_start, c_dur, piece)
            limiter.wait(c_dur)
            segs = transcribe_chunk(client, piece, args.model, args.language)
            limiter.record(c_dur)
            cache.write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
            piece.unlink(missing_ok=True)

        # Keep a segment only if its midpoint falls in this chunk's nominal range,
        # so overlapping regions never produce duplicates.
        for s in segs:
            mid = c_start + (s["start"] + s["end"]) / 2
            if nom_start <= mid < nom_end or (i == n_chunks - 1 and mid >= nom_start):
                all_segments.append({"start": c_start + s["start"],
                                    "end": c_start + s["end"],
                                    "text": s["text"]})

    all_segments.sort(key=lambda s: s["start"])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    txt = out_dir / (video.stem + ".txt")
    srt = out_dir / (video.stem + ".srt")
    txt.write_text("\n".join(s["text"] for s in all_segments), encoding="utf-8")
    with open(srt, "w", encoding="utf-8") as f:
        for n, s in enumerate(all_segments, 1):
            f.write(f"{n}\n{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}\n{s['text']}\n\n")

    print(f"\nDone.\n  Text: {txt}\n  Subtitles: {srt}")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()