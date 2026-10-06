#!/usr/bin/env python3
"""st.App entrypoint: Streamlit UI (ui.py) + chunked upload API on /api/upload.

Run with: streamlit run app.py
(main.py is an equivalent launcher — either works as the deploy entrypoint.)
"""
from pathlib import Path

import streamlit as st

from upload_api import routes

app = st.App(
    str(Path(__file__).resolve().parent / "ui.py"),
    routes=routes(),
)