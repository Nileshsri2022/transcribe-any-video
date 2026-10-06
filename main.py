#!/usr/bin/env python3
"""ASGI entrypoint: Streamlit UI (ui.py) + chunked upload API on /api/upload.

Run with: streamlit run main.py
(app.py is an equivalent launcher — either works as the deploy entrypoint.)
"""
from pathlib import Path

import streamlit as st

from upload_api import routes

app = st.App(
    str(Path(__file__).resolve().parent / "ui.py"),
    routes=routes(),
)
