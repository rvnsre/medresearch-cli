"""Bound response bytes while reading, never after an unbounded download."""
from __future__ import annotations

import time
from contextlib import nullcontext

import httpx

from .config import ResearchError


def bounded_request(client: httpx.Client | None, method: str, url: str, *, limit: int,
                    label: str, timeout: float = 25, headers: dict | None = None, **kwargs) -> httpx.Response:
    started = time.monotonic()
    manager = nullcontext(client) if client is not None else httpx.Client(trust_env=False)
    with manager as active:
        with active.stream(method, url, headers={**(headers or {}), "Accept-Encoding": "identity"},
                           timeout=timeout, follow_redirects=False, **kwargs) as response:
            response.raise_for_status()
            # Identity encoding makes the byte cap also a decoded-memory cap. Do not
            # silently decompress an unexpected gzip/brotli bomb supplied by a peer.
            if response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
                raise ResearchError(f"{label} returned unexpected compressed content.")
            declared = response.headers.get("content-length", "")
            if declared.isascii() and declared.isdigit() and (len(declared) > 12 or int(declared) > limit):
                raise ResearchError(f"{label} response exceeded size limit.")
            if response.is_stream_consumed:  # Pre-buffered test/custom transports.
                if len(response.content) > limit:
                    raise ResearchError(f"{label} response exceeded size limit.")
                return response
            body = bytearray()
            for chunk in response.iter_raw():
                if time.monotonic() - started > timeout:
                    raise httpx.ReadTimeout("Response deadline exceeded.", request=response.request)
                if len(body) + len(chunk) > limit:
                    raise ResearchError(f"{label} response exceeded size limit.")
                body.extend(chunk)
            return httpx.Response(response.status_code, content=bytes(body), headers=response.headers,
                                  request=response.request)
