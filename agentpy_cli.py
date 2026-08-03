"""Console entry point for the installed `agentpy` command.

    agentpy            # interactive terminal agent (the default)
    agentpy serve      # run the web / API server (needs the [server] extra)
    agentpy --version

Installed via `pipx install agentpy` (or `pip install agentpy`), so users get
the command without cloning the source — the same shape as a global CLI.
"""

import sys


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("agentpy")
    except Exception:
        return "dev"


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in ("-v", "--version"):
        print(f"agentpy {_version()}")
        return
    if argv and argv[0] in ("-h", "--help") and len(argv) == 1:
        print("usage: agentpy [serve] [--version]\n"
              "  agentpy         start the interactive terminal agent\n"
              "  agentpy serve   run the web/API server (pip install 'agentpy[server]')")
        return
    if argv and argv[0] == "serve":
        sys.argv = [sys.argv[0], *argv[1:]]  # hand the rest to the server
        try:
            from server.app import main as serve_main
        except ImportError as exc:
            raise SystemExit(
                "The web server needs the extra dependencies:\n"
                "  pip install 'agentpy[server]'\n"
                f"({exc})"
            )
        serve_main()
        return
    from interfaces.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
