## Goal

Deploy the transcriber to a **Hugging Face Space (Docker SDK)** where the chunked upload API works — the app runs unchanged in a container on HF's servers (port 7860, all paths forwarded). HF's proxy caps single request bodies at ~50 MB; our uploader already sends 16 MB chunks, so it fits.

## Code changes (I do these)

1. **`Dockerfile`** (new) — HF's official pattern:
   - `python:3.14-slim` base, `apt-get install ffmpeg`
   - non-root user (HF runs containers as uid 1000)
   - `pip install -r requirements.txt` (already in repo: streamlit, groq, starlette)
   - `EXPOSE 7860`, `CMD ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]`
   - No other app code changes needed — `st.App` mounts the UI + `/api/upload` on one port.

2. **`README.md`** — add HF Spaces YAML front matter (`sdk: docker`, `app_port: 7860`, title/emoji) so HF recognizes the Space, plus a "Deploy to HF Space" section; keep the Streamlit Cloud section (still works there via the built-in-uploader fallback for files ≤ ~200 MB).

3. **Finish the hardening work already in progress** (uncommitted from the previous fix):
   - Uploader JS probes `/api/upload` once on component mount → the built-in-uploader fallback appears immediately on hosts that block custom routes (no failed click needed)
   - Keep a completed upload's status taking priority over a probe result; clearer fallback warning copy
   - Update `test_upload_js.mjs` noapi test to expect mount-time detection; update `test_flow.py` if needed
   - Run the full test suite (`python test_flow.py`) and fix anything that fails

## Your steps (after I'm done — ~5 minutes, no Docker install needed)

1. Create a free account at huggingface.co (if you don't have one)
2. Create a new Space → SDK: **Docker** → blank template, name it e.g. `transcribe-any-video`
3. In Space **Settings → Variables and secrets**, add secret `GROQ_API_KEY`
4. Push the repo to the Space:
   ```
   git remote add space https://huggingface.co/spaces/<your-username>/transcribe-any-video
   git push space main
   ```
5. HF builds it (~3 min) → app live at `https://<your-username>-transcribe-any-video.hf.space`

## Notes

- Free Spaces sleep after ~48 h without traffic and wake on the next visit (~1 min cold start)
- A public Space means anyone with the URL uses your Groq free quota — you can switch it to **Private** in Space settings (only you can open it)
- Keep the Streamlit Cloud deployment as-is if you want; it still works for files ≤ ~200 MB via the fallback uploader
