"""Minimal stdlib client for Moss Inference with per-request JSONL logging.

Every request and response is appended to logs/<suite>.jsonl with timings and all
X-Moss-* / rate-limit headers. The Authorization header and any provider key are never
written; the project id and key are also scrubbed from every logged string.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import ssl
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://inference.moss.dev/v1"
NEVER_LOG = {"authorization", "x-moss-provider-key"}


def load_env(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines into os.environ (existing values win)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _secrets() -> list[str]:
    return [s for s in (os.environ.get("MOSS_PROJECT_ID"), os.environ.get("MOSS_PROJECT_KEY")) if s]


def scrub(value: Any) -> Any:
    """Replace the project id and key wherever they appear in a logged value."""
    if isinstance(value, str):
        for secret in _secrets():
            value = value.replace(secret, "<redacted>")
        return value
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def _interesting(headers: dict[str, str]) -> dict[str, str]:
    keep = ("x-moss-", "x-ratelimit-", "retry-after", "x-should-retry", "idempotent-replayed", "x-request-id")
    return {k: v for k, v in headers.items() if k.lower().startswith(keep)}


@dataclass
class Result:
    status: int
    headers: dict[str, str]
    body: Any
    ttfb_ms: float
    total_ms: float
    first_token_ms: float | None = None
    events: list[dict] = field(default_factory=list)
    text: str = ""

    def moss(self) -> dict[str, str]:
        return _interesting(self.headers)


class Moss:
    """One HTTPS connection, reused (warm) unless `fresh=True` is passed per call."""

    def __init__(self, suite: str, project_id: str | None = None, project_key: str | None = None):
        load_env()
        self.project_id = project_id if project_id is not None else os.environ.get("MOSS_PROJECT_ID", "")
        self.project_key = project_key if project_key is not None else os.environ.get("MOSS_PROJECT_KEY", "")
        parts = urlsplit(BASE_URL)
        self.host, self.prefix = parts.hostname or "", parts.path
        self.ctx = ssl.create_default_context()
        self.conn: http.client.HTTPSConnection | None = None
        self.log_path = ROOT / "logs" / f"{suite}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.suite = suite

    # -- plumbing -----------------------------------------------------------------------
    def _connection(self, fresh: bool) -> http.client.HTTPSConnection:
        if fresh or self.conn is None:
            if self.conn is not None:
                self.conn.close()
            self.conn = http.client.HTTPSConnection(self.host, 443, timeout=120, context=self.ctx)
        return self.conn

    def _headers(self, extra: dict[str, str] | None, auth: str | None) -> dict[str, str]:
        token = auth if auth is not None else f"{self.project_id}:{self.project_key}"
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": "moss-work-sample/1.0 (python-stdlib)"}
        if token != "":
            headers["Authorization"] = f"Bearer {token}"
        headers.update(extra or {})
        return headers

    def _log(self, record: dict) -> None:
        record = scrub(record)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- requests -----------------------------------------------------------------------
    def request(self, method: str, path: str, body: Any = None, *, test: str, headers: dict | None = None,
                fresh: bool = False, auth: str | None = None, raw_body: bytes | None = None) -> Result:
        """A blocking request; the whole body is read and parsed as JSON when possible."""
        hdrs = self._headers(headers, auth)
        payload = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        t0 = time.perf_counter()
        conn = self._connection(fresh)
        try:
            conn.request(method, self.prefix + path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            ttfb = (time.perf_counter() - t0) * 1000
            data = resp.read()
        except (http.client.HTTPException, OSError) as exc:  # retry once on a dropped keep-alive
            self.conn = None
            conn = self._connection(True)
            t0 = time.perf_counter()
            conn.request(method, self.prefix + path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            ttfb = (time.perf_counter() - t0) * 1000
            data = resp.read()
            self._log({"note": f"retried after {type(exc).__name__}", "test": test})
        total = (time.perf_counter() - t0) * 1000
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        try:
            parsed: Any = json.loads(data) if data else None
        except ValueError:
            parsed = data[:4000].decode("utf-8", "replace")
        result = Result(resp.status, resp_headers, parsed, ttfb, total)
        self._log({
            "ts": time.time(), "suite": self.suite, "test": test, "id": str(uuid.uuid4()),
            "request": {"method": method, "url": BASE_URL + path, "fresh_connection": fresh,
                        "headers": {k: v for k, v in hdrs.items() if k.lower() not in NEVER_LOG},
                        "body": body if raw_body is None else raw_body.decode("utf-8", "replace")},
            "response": {"status": resp.status, "moss_headers": result.moss(), "headers": resp_headers,
                         "body": parsed},
            "timing_ms": {"ttfb": round(ttfb, 1), "total": round(total, 1)},
        })
        return result

    def stream(self, path: str, body: dict, *, test: str, headers: dict | None = None, fresh: bool = False,
               kind: str = "responses", stop_after_first_token: bool = False) -> Result:
        """A streaming request (SSE). Records each event with its offset in ms."""
        hdrs = self._headers(headers, None)
        t0 = time.perf_counter()
        conn = self._connection(fresh)
        try:
            conn.request("POST", self.prefix + path, body=json.dumps(body).encode(), headers=hdrs)
            resp = conn.getresponse()
        except (http.client.HTTPException, OSError):
            self.conn = None
            conn = self._connection(True)
            t0 = time.perf_counter()
            conn.request("POST", self.prefix + path, body=json.dumps(body).encode(), headers=hdrs)
            resp = conn.getresponse()
        ttfb = (time.perf_counter() - t0) * 1000
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        events: list[dict] = []
        first_token: float | None = None
        text_parts: list[str] = []
        error_body: Any = None
        if resp.status != 200:
            data = resp.read()
            try:
                error_body = json.loads(data)
            except ValueError:
                error_body = data[:4000].decode("utf-8", "replace")
        else:
            for ev in self._sse(resp):
                at = (time.perf_counter() - t0) * 1000
                delta = _token(ev, kind)
                if delta and first_token is None:
                    first_token = at
                if delta:
                    text_parts.append(delta)
                events.append({"t_ms": round(at, 1), "data": ev})
                if delta and stop_after_first_token:
                    break
            if stop_after_first_token:
                self.conn.close()  # abandon the rest of the stream
                self.conn = None
        total = (time.perf_counter() - t0) * 1000
        result = Result(resp.status, resp_headers, error_body, ttfb, total, first_token, events, "".join(text_parts))
        self._log({
            "ts": time.time(), "suite": self.suite, "test": test, "id": str(uuid.uuid4()),
            "request": {"method": "POST", "url": BASE_URL + path, "fresh_connection": fresh, "stream": True,
                        "headers": {k: v for k, v in hdrs.items() if k.lower() not in NEVER_LOG}, "body": body},
            "response": {"status": resp.status, "moss_headers": result.moss(), "headers": resp_headers,
                         "error_body": error_body, "events": events, "text": result.text},
            "timing_ms": {"ttfb": round(ttfb, 1), "first_token": None if first_token is None else round(first_token, 1),
                          "total": round(total, 1)},
        })
        return result

    @staticmethod
    def _sse(resp: http.client.HTTPResponse) -> Iterator[Any]:
        data_lines: list[str] = []
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    if payload == "[DONE]":
                        yield {"done": True}
                        break
                    try:
                        yield json.loads(payload)
                    except ValueError:
                        yield {"unparsed": payload}
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            payload = "\n".join(data_lines)
            try:
                yield json.loads(payload)
            except ValueError:
                yield {"unparsed": payload}


def _token(ev: Any, kind: str) -> str:
    if not isinstance(ev, dict):
        return ""
    if kind == "responses":
        if ev.get("type") == "response.output_text.delta":
            return ev.get("delta") or ""
        return ""
    choices = ev.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return (choices[0].get("delta") or {}).get("content") or ""
    return ""


def pct(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, math.ceil(p / 100 * len(vals)) - 1))
    return vals[k]
