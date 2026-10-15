"""Console entry points that shield Twin Me Up from a foreign PYTHONPATH.

A PYTHONPATH exported for another project can shadow this package's dependencies with
incompatible builds, and every agent host that passes the user's environment through would
then fail to start the server. These entry points import nothing heavy: when PYTHONPATH is
set they re-run the same command in isolated mode (-I), which ignores it, before any
dependency loads.
"""

from __future__ import annotations

import os
import sys


def _isolate(module: str) -> None:
    if not os.environ.get("PYTHONPATH"):
        return
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    argv = [sys.executable, "-I", "-m", module, *sys.argv[1:]]
    if os.name == "nt":  # execve does not replace the process on Windows
        import subprocess

        raise SystemExit(subprocess.call(argv, env=env))
    os.execve(sys.executable, argv, env)


def cli() -> None:
    _isolate("twinmeup.cli")
    from .cli import app

    app(prog_name="twinmeup")


def mcp() -> None:
    _isolate("twinmeup.mcp_server")
    from .mcp_server import main

    raise SystemExit(main())


# The MCP server's options that take a value. Everything else it accepts is a flag.
_SERVER_VALUE_OPTIONS = frozenset({"--home", "--actor", "--principal"})


def wants_cli(args: list[str]) -> bool:
    """True when the arguments name a command, as in `twin-me-up doctor`.

    The MCP server takes options only, so any other word can only be a twinmeup command.
    """
    takes_value = False
    for arg in args:
        if takes_value:
            takes_value = False
        elif arg in _SERVER_VALUE_OPTIONS:
            takes_value = True
        elif not arg.startswith("-"):
            return True
    return False


def twin_me_up() -> None:
    """`twin-me-up`, the package's own name: the MCP server, and every twinmeup command.

    With a command, such as `twin-me-up doctor`, it behaves exactly like `twinmeup`. Without
    one it starts the MCP server, so `uvx twin-me-up` and agent hosts launch it as before.
    """
    if not wants_cli(sys.argv[1:]):
        mcp()
        return
    _isolate("twinmeup.cli")
    from .cli import app

    app(prog_name="twin-me-up")
