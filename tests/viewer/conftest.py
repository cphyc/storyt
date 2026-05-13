import http.server
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FRONTEND_DIR = Path(__file__).parent.parent.parent / "storyt" / "viewer" / "frontend"


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    """Build frontend (if needed), combine with fixtures, serve on random port."""
    dist_dir = FRONTEND_DIR / "dist"
    if not dist_dir.exists():
        subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)

    serve_dir = tmp_path_factory.mktemp("serve")
    for item in dist_dir.iterdir():
        dst = serve_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
    shutil.copytree(FIXTURES_DIR / "data", serve_dir / "data")

    with socket.socket() as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(serve_dir), **kw)

        def log_message(self, *_):
            pass

    httpd = http.server.HTTPServer(("localhost", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://localhost:{port}"
    httpd.shutdown()
