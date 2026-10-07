#!/usr/bin/env python3
"""Windows desktop entry point for Sanford Library."""

from __future__ import annotations

import os
import threading

import webview

os.environ["SANFORD_LIBRARY_DESKTOP"] = "1"

import server


def main() -> None:
    server.initialize_data_files()
    http_server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.SanfordLibraryHandler)
    http_server.daemon_threads = True
    server_thread = threading.Thread(
        target=http_server.serve_forever,
        name="sanford-library-server",
        daemon=True,
    )
    server_thread.start()

    host, port = http_server.server_address
    start_page = "/" if server.TMDB_READ_TOKEN else "/setup.html"
    webview.create_window(
        "Sanford Library",
        f"http://{host}:{port}{start_page}",
        width=1440,
        height=900,
        min_size=(800, 600),
        background_color="#10180d",
    )

    try:
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(server.DATA_ROOT / "webview"),
        )
    finally:
        http_server.shutdown()
        http_server.server_close()
        server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
