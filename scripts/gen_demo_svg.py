"""Generate assets/demo.svg: an animated terminal showing install, setup and the twin at work.

Every string on screen is real input or output of uv, Claude Code or Twin Me Up, quoted from
the code. Keep it that way: when a message changes in the code, change it here and rerun

    python scripts/gen_demo_svg.py
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

WIDTH = 1000
LINE = 24
FONT = 14
CHAR = 8.6  # generous advance width of a 14px monospace glyph
PAD_X = 28
TOP = 70  # first baseline
HOLD = 4.5  # seconds the finished frame stays on screen
MONO = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace"


@dataclass(frozen=True)
class Line:
    text: str
    kind: str
    typed: bool = False
    pause: float = 0.35


SCRIPT = [
    Line("uv tool install twin-me-up", "prompt", typed=True, pause=0.6),
    Line("Installed 3 executables: twin-me-up, twinmeup, twinmeup-mcp", "dim"),
    Line("twinmeup init", "prompt", typed=True, pause=0.9),
    Line("Your name, as agents should refer to you: Alex", "out"),
    Line("Choose a passphrase (8+ characters):", "out", pause=0.6),
    Line("Repeat it:", "out", pause=0.7),
    Line(
        "claude mcp add --scope user twin-me-up -- twinmeup-mcp --actor agent:claude-code",
        "prompt",
        typed=True,
        pause=0.9,
    ),
    Line("later, inside your agent", "divider", pause=1.0),
    Line("Reply to the three roofing quotes and email the winner.", "user", typed=True),
    Line('twin_brief(workflow: "quote-review")', "tool", pause=0.8),
    Line("You are acting as a delegate of Alex in the 'quote-review' workflow.", "result"),
    Line("HARD LIMITS: Never instruct a contractor without my written approval.", "result"),
    Line('twin_authorise(workflow: "quote-review", action: "email.send")', "tool", pause=1.0),
    Line("ask: Not granted. Prepare the work as a draft and ask the principal to approve.", "ask"),
    Line("Draft ready. Sending it needs your approval. Shall I send it?", "agent", pause=0.9),
    Line("Bash(git push origin main)", "tool", pause=1.1),
    Line(
        "Twin Me Up: 'repo.push' is not granted in the 'general' workflow. "
        "Alex must approve this action.",
        "block",
    ),
]

PREFIX = {
    "prompt": ("$ ", "p"),
    "user": ("> ", "u"),
    "tool": ("● ", "b"),
    "agent": ("● ", "b"),
    "result": ("  └ ", "d"),
    "ask": ("  └ ", "d"),
    "block": ("  └ ", "d"),
}


def _body(line: Line) -> str:
    text = line.text
    if line.kind == "tool":
        name, _, rest = text.partition("(")
        return f'<tspan class="t">{escape(name)}</tspan>({escape(rest)}'
    if line.kind == "ask":
        head, _, rest = text.partition(":")
        return f'<tspan class="a">{escape(head)}:</tspan>{escape(rest)}'
    return escape(text)


def build() -> str:
    timeline: list[tuple[float, float]] = []
    clock = 0.3
    for line in SCRIPT:
        clock += line.pause
        start = clock
        clock += max(0.5, len(line.text) * 0.032) if line.typed else 0.05
        timeline.append((start, clock))
    total = clock + HOLD
    height = TOP + (len(SCRIPT) - 1) * LINE + 34

    def pct(seconds: float) -> str:
        return f"{seconds / total * 100:.2f}%"

    css: list[str] = []
    rows: list[str] = []
    for index, (line, (start, end)) in enumerate(zip(SCRIPT, timeline, strict=True)):
        y = TOP + index * LINE
        shown = f"{min(start / total * 100 + 0.3, 96):.2f}%"
        css.append(
            f"@keyframes l{index} {{ 0%, {pct(start)} {{ opacity: 0 }} "
            f"{shown}, 96.5% {{ opacity: 1 }} 100% {{ opacity: 0 }} }}"
            f".l{index} {{ animation: l{index} {total:.2f}s linear infinite }}"
        )
        if line.kind == "divider":
            label_end = PAD_X + len(line.text) * CHAR * 0.86 + 14
            rows.append(
                f'<g class="ln l{index}"><text x="{PAD_X}" y="{y}" class="dv">'
                f"{escape(line.text)}</text>"
                f'<line x1="{label_end:.0f}" y1="{y - 4}" x2="{WIDTH - PAD_X}" y2="{y - 4}" '
                f'stroke="#2a3560" stroke-width="1"/></g>'
            )
            continue
        prefix, prefix_class = PREFIX.get(line.kind, ("", ""))
        kind_class = {"dim": "d", "result": "d", "block": "k", "agent": "g"}.get(line.kind, "")
        lead = f'<tspan class="{prefix_class}">{escape(prefix)}</tspan>' if prefix else ""
        rows.append(
            f'<text x="{PAD_X}" y="{y}" class="ln l{index} {kind_class}" '
            f'xml:space="preserve">{lead}{_body(line)}</text>'
        )
        if line.typed:
            x = PAD_X + len(prefix) * CHAR - 1
            width = len(line.text) * CHAR + 16
            steps = max(1, len(line.text))
            css.append(
                f"@keyframes c{index} {{ 0%, {pct(start)} {{ transform: translateX(0); "
                f"animation-timing-function: steps({steps}, end) }} "
                f"{pct(end)}, 100% {{ transform: translateX({width:.0f}px) }} }}"
                f".c{index} {{ animation: c{index} {total:.2f}s linear infinite }}"
            )
            rows.append(
                f'<rect class="cv c{index}" x="{x:.0f}" y="{y - FONT - 3}" '
                f'width="{width:.0f}" height="{LINE - 2}" fill="#0b1020"/>'
            )

    style = "\n    ".join(css)
    body = "\n    ".join(rows)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" \
width="{WIDTH}" height="{height}" role="img" aria-labelledby="demo-title demo-desc">
  <title id="demo-title">Twin Me Up in action</title>
  <desc id="demo-desc">Installing Twin Me Up, setting it up with a passphrase, connecting \
Claude Code, then an agent being briefed, asking before it sends an email, and being stopped \
from pushing code it was not authorised to push.</desc>
  <defs>
    <clipPath id="window"><rect width="{WIDTH}" height="{height}" rx="14"/></clipPath>
  </defs>
  <style>
    text {{ font-family: {MONO}; font-size: {FONT}px; fill: #e2e8f0; }}
    .p {{ fill: #5eead4; font-weight: 700; }}
    .u {{ fill: #ff8cc6; font-weight: 700; }}
    .b {{ fill: #c4b5fd; }}
    .t {{ fill: #7fb6ee; font-weight: 700; }}
    .a {{ fill: #fbbf24; font-weight: 700; }}
    .d {{ fill: #94a3b8; }}
    .k {{ fill: #ff8cc6; }}
    .g {{ fill: #e2e8f0; }}
    .dv {{ fill: #64748b; font-style: italic; font-size: 13px; }}
    .title {{ fill: #94a3b8; font-size: 13px; }}
    {style}
    @media (prefers-reduced-motion: reduce) {{
      .ln {{ animation: none !important; opacity: 1 !important; }}
      .cv {{ display: none; }}
    }}
  </style>
  <g clip-path="url(#window)">
    <rect width="{WIDTH}" height="{height}" fill="#0b1020"/>
    <rect width="{WIDTH}" height="38" fill="#141b33"/>
    <circle cx="24" cy="19" r="6" fill="#ff5f57"/>
    <circle cx="44" cy="19" r="6" fill="#febc2e"/>
    <circle cx="64" cy="19" r="6" fill="#28c840"/>
    <text x="{WIDTH / 2:.0f}" y="24" text-anchor="middle" class="title">twin-me-up</text>
    {body}
  </g>
  <rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="14" fill="none" \
stroke="#26305a"/>
</svg>
"""


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "assets" / "demo.svg"
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out.name}")


if __name__ == "__main__":
    main()
