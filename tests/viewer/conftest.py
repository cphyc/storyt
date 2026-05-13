import functools
import http.server
import shutil
import socket
import threading
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FRONTEND_HTML = (
    Path(__file__).parent.parent.parent
    / "storyt"
    / "viewer"
    / "frontend"
    / "index.html"
)


@pytest.fixture(scope="module")
def http_server():
    """Serve the fixtures directory on a random free port; yield base_url."""
    # Always copy the latest frontend build into the fixtures directory
    shutil.copy(FRONTEND_HTML, FIXTURES_DIR / "index.html")

    with socket.socket() as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(FIXTURES_DIR),
    )
    # Suppress server log noise during tests
    handler.log_message = lambda *a: None  # type: ignore[method-assign]

    httpd = http.server.HTTPServer(("localhost", port), handler)
    t = threading.Thread(target=httpd.serve_forever)
    t.daemon = True
    t.start()

    yield f"http://localhost:{port}"

    httpd.shutdown()
