"""The studio API: the backend the human loop talks to.

It drives pipeline runs, serves the artifacts they produce, and reports what is
happening. Each of those is a route module; this file only assembles them, so
finding the code for a thing means opening the file named after it.
"""

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR
from .routes import audio, health, jobs, status, voices, workspace

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    log.info("persistent httpx.AsyncClient created (timeout=300s)")
    yield
    await app.state.http_client.aclose()
    log.info("httpx.AsyncClient closed")


app = FastAPI(
    title="Studio API",
    description="Drives pipeline runs and serves what they produce",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (voices, audio, status, jobs, workspace, health):
    app.include_router(module.router)

# The frontend is mounted last so API routes take priority.
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
