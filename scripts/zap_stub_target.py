"""Inert local HTTP target for verifying ZAP's OpenAPI import (never DEV/PROD).

Answers every method on every path with a small JSON 404 and logs nothing
sensitive. Used only by .github/workflows/zap-import-verify.yml on a CI runner.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys


class Stub(BaseHTTPRequestHandler):
    """Return 404 JSON for any request."""

    def _reply(self):
        body = b'{"detail":"zap import verification stub"}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _reply

    def log_message(self, fmt, *args):  # keep request lines, drop client detail
        sys.stderr.write("stub %s %s\n" % (self.command, self.path.split("?")[0]))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    ThreadingHTTPServer(("127.0.0.1", port), Stub).serve_forever()
