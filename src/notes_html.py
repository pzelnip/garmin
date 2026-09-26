"""Render a markdown notes summary as a self-contained, phone-friendly HTML page.

Usage: uv run python src/notes_html.py IN.md [OUT.html]
"""

import html
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #ffffff;
    --fg: #1a1a1a;
    --accent: #b23a48;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #121212;
      --fg: #eaeaea;
      --accent: #ff8a80;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    padding: 16px;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 17px;
    line-height: 1.5;
    max-width: 700px;
    margin: 0 auto;
    overflow-wrap: break-word;
  }}
  h1 {{ font-size: 1.3em; margin: 0 0 20px; }}
  h2 {{
    font-size: 1.05em;
    margin: 28px 0 8px;
    border-bottom: 2px solid var(--accent);
    padding-bottom: 4px;
  }}
  h3 {{ font-size: 1em; margin: 20px 0 6px; }}
  ul, ol {{ padding-left: 1.2em; margin: 8px 0; }}
  li {{ margin-bottom: 8px; }}
  strong {{ color: var(--accent); }}
</style>
</head>
<body>
{body}</body>
</html>
"""

# CommonMark so 2-space nested lists nest; html=False escapes any raw HTML in notes.
_md = MarkdownIt("commonmark", {"html": False})


def render(markdown_text: str) -> str:
    match = re.search(r"^# (.+)$", markdown_text, re.MULTILINE)
    title = match.group(1).strip() if match else "Notes"
    return PAGE.format(title=html.escape(title), body=_md.render(markdown_text))


def main(argv: list[str]) -> None:
    if len(argv) not in (2, 3):
        sys.exit("usage: notes_html.py IN.md [OUT.html]")
    src = Path(argv[1])
    out = Path(argv[2]) if len(argv) == 3 else src.with_suffix(".html")
    out.write_text(render(src.read_text()))
    print(out)


if __name__ == "__main__":
    main(sys.argv)
