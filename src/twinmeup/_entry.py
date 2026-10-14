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
