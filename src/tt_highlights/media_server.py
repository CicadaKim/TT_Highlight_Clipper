"""Background HTTP server to serve video files to the custom Streamlit component."""

import os
import re
import socket
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse a Range header like 'bytes=START-END' and return (start, end)."""
    m = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not m:
        return 0, file_size - 1
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else file_size - 1
    end = min(end, file_size - 1)
    return start, end


class _CORSHandler(SimpleHTTPRequestHandler):
    """HTTP handler that adds CORS headers and supports Range requests."""

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_GET(self):
        range_header = self.headers.get("Range")
        if not range_header:
            return super().do_GET()

        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404, "File not found")
            return

        file_size = os.path.getsize(path)
        start, end = _parse_range(range_header, file_size)

        if start >= file_size:
            self.send_error(416, "Requested Range Not Satisfiable")
            return

        self.send_response(206)
        ctype = self.guess_type(path)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()

        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            buf_size = 64 * 1024  # 64KB chunks
            while remaining > 0:
                chunk = f.read(min(buf_size, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress logs


_server_instance: ThreadingHTTPServer | None = None
_server_port: int | None = None
_server_directory: str | None = None


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_media_server(directory: str | Path) -> int:
    """Start a background HTTP server serving *directory*.

    Returns the port number. Idempotent — if the server is already running
    for the same directory it just returns the existing port.
    """
    global _server_instance, _server_port, _server_directory

    directory = str(Path(directory).resolve())

    if _server_instance is not None and _server_directory == directory:
        return _server_port

    # Stop any previous server
    stop_media_server()

    port = _find_free_port()
    handler = partial(_CORSHandler, directory=directory)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    _server_instance = server
    _server_port = port
    _server_directory = directory
    return port


def stop_media_server():
    """Shutdown the running media server if any."""
    global _server_instance, _server_port, _server_directory
    if _server_instance is not None:
        _server_instance.shutdown()
        _server_instance = None
        _server_port = None
        _server_directory = None


def get_media_url(port: int, filename: str) -> str:
    """Return the full URL for a file served by the media server."""
    return f"http://127.0.0.1:{port}/{filename}"
