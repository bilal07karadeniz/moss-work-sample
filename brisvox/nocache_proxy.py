"""Loopback proxy that adds `X-Moss-Cache: off` to every request brisvox sends to Moss.

brisvox's agent file has no per-request header setting, and the A/B fixture repeats the same two
questions, so without this most turns are answered from Moss's reply cache. The proxy keeps a pool of
warm HTTPS connections to inference.moss.dev and streams the response through as it arrives.
It logs, per request, the upstream time to headers and to the first body bytes, so its own overhead
can be checked (results/proxy-<label>.jsonl). Usage: python nocache_proxy.py <port> <label>
"""

from __future__ import annotations

import http.client
import json
import queue
import ssl
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "inference.moss.dev"
CTX = ssl.create_default_context()
POOL: "queue.LifoQueue[http.client.HTTPSConnection]" = queue.LifoQueue()
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host", "accept-encoding"}
LOG = Path(__file__).resolve().parent / "results" / f"proxy-{sys.argv[2] if len(sys.argv) > 2 else 'run'}.jsonl"


def conn() -> http.client.HTTPSConnection:
    try:
        return POOL.get_nowait()
    except queue.Empty:
        return http.client.HTTPSConnection(UPSTREAM, 443, timeout=60, context=CTX)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # quiet
        pass

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["X-Moss-Cache"] = "off"
        t0 = time.perf_counter()
        c = conn()
        reconnected = False
        try:
            c.request("POST", self.path, body=body, headers=headers)
            resp = c.getresponse()
        except (http.client.HTTPException, OSError):
            c.close()
            reconnected = True
            c = http.client.HTTPSConnection(UPSTREAM, 443, timeout=60, context=CTX)
            t0 = time.perf_counter()
            c.request("POST", self.path, body=body, headers=headers)
            resp = c.getresponse()
        t_head = time.perf_counter()
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        t_first = None
        while True:
            data = resp.read1(65536)
            if not data:
                break
            if t_first is None:
                t_first = time.perf_counter()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        POOL.put(c)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "status": resp.status, "box": resp.getheader("x-moss-box"), "reconnected": reconnected,
                                 "upstream_headers_ms": round((t_head - t0) * 1000, 1),
                                 "upstream_first_bytes_ms": round(((t_first or t_head) - t0) * 1000, 1)}) + "\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 28095
    for _ in range(4):  # pre-warm the pool so the first turns don't pay TCP + TLS
        c = http.client.HTTPSConnection(UPSTREAM, 443, timeout=60, context=CTX)
        c.request("GET", "/v1/models")
        c.getresponse().read()
        POOL.put(c)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
