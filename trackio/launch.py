import os
import socket
import threading
import time

import uvicorn


def get_first_available_port(initial: int, final: int, host: str = "127.0.0.1") -> int:
    for port in range(initial, final):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                s.close()
                return port
        except OSError:
            continue
    raise OSError(
        f"All ports from {initial} to {final - 1} are in use. Please specify a different port."
    )


def start_server(
    app,
    server_name: str | None = None,
    server_port: int | None = None,
) -> tuple[str, uvicorn.Server]:
    """Start a uvicorn server for `app` in a background daemon thread.

    Returns `(local_url, uvicorn_server)`.
    """
    server_name = server_name or os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1")
    if server_port is None:
        scan_host = "" if server_name == "0.0.0.0" else server_name
        server_port = get_first_available_port(
            int(os.environ.get("GRADIO_SERVER_PORT", 7860)),
            int(os.environ.get("GRADIO_SERVER_PORT", 7860)) + 100,
            host=scan_host or "127.0.0.1",
        )

    config = uvicorn.Config(
        app,
        host=server_name,
        port=server_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    def _run():
        try:
            server.run()
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)

    if not server.started:
        raise OSError(
            f"Failed to start server on {server_name}:{server_port} within 15 seconds"
        )

    url_host = "localhost" if server_name == "0.0.0.0" else server_name
    local_url = f"http://{url_host}:{server_port}"
    return local_url, server
