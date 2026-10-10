"""A small, safe Markdown renderer for agent output.

Agent text is model-written, and SCOUT's input comes from a public form, so
everything is HTML-escaped *first* and only a fixed set of constructs is turned
back into markup: headings, paragraphs, lists, quotes, rules, code, bold,
italics and links. Links are kept only for http(s), mailto and site-relative
targets. No raw HTML survives.
"""
from __future__ import annotations

import html
import re

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])[*_](?![\s*_])(.+?)(?<![\s*_])[*_](?![\w*])")
# Site-relative links must not start "//" or "/\" — browsers read both as a
# protocol-relative link to another host.
_SAFE_URL = re.compile(r"^(https?://|mailto:|/(?![/\\]))", re.I)


def _link(m: re.Match) -> str:
    text, url = m.group(1), m.group(2)
    if not _SAFE_URL.match(html.unescape(url)):
        return m.group(0)
    return f'<a href="{url}" rel="nofollow noopener">{text}</a>'


def inline(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        s = html.escape(part, quote=True)
        s = _LINK.sub(_link, s)
        s = _BOLD.sub(r"<strong>\1</strong>", s)
        s = _ITALIC.sub(r"<em>\1</em>", s)
        out.append(s)
    return "".join(out)


def render(md: str, heading_offset: int = 1) -> str:
    """Markdown → HTML. `heading_offset` demotes headings (# → h2 by default)
    because the page itself owns the h1."""
    out: list[str] = []
    para: list[str] = []
    list_tag = ""
    code: list[str] = []
    in_code = False

    def flush_para() -> None:
        if para:
            out.append("<p>" + "<br>".join(inline(p) for p in para) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = ""

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        if raw.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code, in_code = [], False
            else:
                flush_para()
                close_list()
                in_code = True
            continue
        if in_code:
            code.append(raw)
            continue
        line = raw.strip()
        if not line:
            flush_para()
            close_list()
            continue
        if m := re.match(r"^(#{1,6})\s+(.*)$", line):
            flush_para()
            close_list()
            level = min(6, len(m.group(1)) + heading_offset)
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", line):
            flush_para()
            close_list()
            out.append("<hr>")
        elif m := re.match(r"^([-*+]|\d+[.)])\s+(.*)$", line):
            flush_para()
            tag = "ol" if m.group(1)[0].isdigit() else "ul"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{inline(m.group(2))}</li>")
        elif line.startswith(">"):
            flush_para()
            close_list()
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
        else:
            close_list()
            para.append(line)
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_para()
    close_list()
    return "\n".join(out)


def plain_excerpt(md: str, length: int = 180) -> str:
    """First words of a Markdown body as plain text, for cards and meta tags."""
    text = re.sub(r"[#*_>`\[\]]|\(https?://[^)]*\)", "", md or "")
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length].rsplit(" ", 1)[0] + "…"
