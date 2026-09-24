"""Dev server for the architecture explorer.

Identical to `python -m http.server` except it tells the browser never to
cache anything. Plain http.server sends Last-Modified with no Cache-Control,
which lets browsers apply heuristic caching and silently keep serving a stale
styles.css after an edit — the edit lands on disk and the page looks unchanged.
"""

import sys
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4180


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt, *args):
        # One line per request, without the noisy timestamp prefix.
        sys.stderr.write("%s\n" % (fmt % args))


if __name__ == "__main__":
    handler = partial(NoCacheHandler, directory=".")
    server = HTTPServer(("127.0.0.1", PORT), handler)
    print(f"Serving {PORT} with caching disabled — http://127.0.0.1:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
