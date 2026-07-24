"""Launch the Trackio dashboard for a project directory.

Usage:
    python -m trackio.show <dir> [--host HOST] [--port PORT] [--mcp-server]
                                 [--color-palette ...] [--frontend DIR]
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="python -m trackio.show",
        description="Launch the Trackio dashboard for a project directory.",
    )
    parser.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Project directory to show (default: current directory).",
    )
    parser.add_argument(
        "--host",
        help="Host to bind. Defaults to '127.0.0.1'. Use '0.0.0.0' for remote access.",
    )
    parser.add_argument("--port", type=int, help="Port to bind.")
    parser.add_argument(
        "--mcp-server",
        action="store_true",
        help="Enable the MCP server (requires the trackio[mcp] extra).",
    )
    parser.add_argument(
        "--color-palette",
        nargs="+",
        help="Custom hex color codes for plot lines.",
    )
    parser.add_argument(
        "--frontend",
        help="Path to a custom frontend directory containing index.html.",
    )
    args = parser.parse_args()

    import trackio

    if args.mcp_server:
        os.environ["GRADIO_MCP_SERVER"] = "True"
    else:
        os.environ["GRADIO_MCP_SERVER"] = "False"

    trackio.show(
        dir=args.dir,
        mcp_server=args.mcp_server,
        color_palette=args.color_palette,
        host=args.host,
        server_port=args.port,
        frontend_dir=args.frontend,
    )


if __name__ == "__main__":
    sys.exit(main())
