"""Vercel serverless entrypoint — exposes the FastAPI app as an ASGI handler."""

import os
import sys

# Make the project root importable so `main` resolves on Vercel.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app  # noqa: E402

# Vercel's Python runtime serves the ASGI `app` object directly.
