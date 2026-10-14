"""Regenerate docs/integrations.md from the same guides `twinmeup connect` prints."""

from __future__ import annotations

from pathlib import Path

from twinmeup import connect

HEADER = """# Integrations

Every guide below is generated from `src/twinmeup/connect.py`, the same code behind
`twinmeup connect <system>`. Run that command instead of copying from here: it prints these
steps with the absolute paths of your own installation.

Agent identities (`agent:<system>`) are labels for the audit log, so you can see which system
did what. Use any `agent:` name you like with `twinmeup --actor agent:<name> connect <system>`.

| System | Key | Kind |
|---|---|---|
"""


def main() -> None:
    rows = "".join(f"| {h.title} | `{h.key}` | {h.kind} |\n" for h in connect.HOSTS.values())
    body = []
    for key in connect.HOSTS:
        text = connect.guide(key, portable=True)
        title, _, steps = text.partition("\n\n")
        body.append(title.replace("# ", "## ", 1) + "\n\n```text\n" + steps.rstrip() + "\n```\n")
    out = Path(__file__).resolve().parents[1] / "docs" / "integrations.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HEADER + rows + "\n" + "\n".join(body), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
