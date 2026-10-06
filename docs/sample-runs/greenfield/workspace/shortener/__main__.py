"""Run with: python -m shortener"""

import logging
import os

import uvicorn

from .api import create_app

logging.basicConfig(level=logging.INFO, format="%(message)s")
uvicorn.run(
    create_app(),
    host=os.environ.get("SHORTENER_HOST", "127.0.0.1"),
    port=int(os.environ.get("SHORTENER_PORT", "8000")),
)
