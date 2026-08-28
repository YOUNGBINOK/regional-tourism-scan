"""Vercel ASGI entrypoint.

The Vite frontend calls this function through the same-origin `/api` prefix,
so provider keys remain in Vercel's server-side environment variables.
"""
from fastapi import FastAPI
from backend.app.main import app as backend_app

app = FastAPI(title="R-GAP Vercel API")
app.mount("/api", backend_app)
