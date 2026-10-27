"""Simple static frontend server."""
from http.server import SimpleHTTPRequestHandler, HTTPServer
import os

PORT = int(os.environ.get("PORT", 8080))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, format, *args):
        # structured-ish stdout so Loki can ingest
        print(f'[frontend] {self.address_string()} - {format % args}')

if __name__ == "__main__":
    os.chdir(STATIC_DIR)
    httpd = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[frontend] serving static files on :{PORT}")
    httpd.serve_forever()
