# Hugging Face Space (Docker SDK): the full app — Streamlit UI + /api/upload
# routes via st.App — runs in one container on port 7860. HF builds and runs
# this image on their servers; no local Docker needed.
FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces run containers as a non-root user (uid 1000)
RUN useradd -m -u 1000 user
USER user
ENV PATH=/home/user/.local/bin:$PATH

WORKDIR /app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .

EXPOSE 7860
CMD ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]
