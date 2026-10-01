"""Формат статьи в боте и его переводы.

Статья хранится простой разметкой, которую легко писать с телефона:

    # Заголовок          секция
    - пункт              маркированный список
    1. пункт             нумерованный список
    > цитата             цитата
    ---                  разделитель
    | а | б |            таблица (строки подряд; первая — шапка)
    обычный текст        абзац

Из неё собираются: Rich Message (настоящая «Статья» Telegram) и запасной HTML.
Обратно — из входящей статьи в эту же разметку.
"""
from __future__ import annotations

import html
import re
from typing import Any

HELP = (
    "<b>Как писать</b>\n"
    "<code># Заголовок</code> — секция\n"
    "<code>- пункт</code> — список, <code>1. пункт</code> — нумерованный\n"
    "<code>&gt; цитата</code> — цитата\n"
    "<code>---</code> — разделитель\n"
    "<code>| Статья | Сумма |</code> — таблица, строки подряд\n"
    "Остальное — обычные абзацы. Пустая строка разделяет абзацы."
)

_ORDERED = re.compile(r"^\s*(\d{1,3})[.)]\s+(.*)$")
_BULLET = re.compile(r"^\s*[-•*]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def parse_blocks(body: str) -> list[dict]:
    """Разметка -> список простых блоков {'t': ..., ...}."""
    blocks: list[dict] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append({"t": "p", "text": "\n".join(paragraph).strip()})
            paragraph.clear()

    def last(kind: str) -> dict | None:
        return blocks[-1] if blocks and blocks[-1]["t"] == kind and not paragraph else None

    for raw in (body or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush()
            continue
        if re.fullmatch(r"-{3,}|_{3,}|\*{3,}", stripped):
            flush()
            blocks.append({"t": "hr"})
            continue
        heading = re.match(r"^#{1,3}\s+(.*)$", stripped)
        if heading:
            flush()
            blocks.append({"t": "h", "text": heading.group(1).strip()})
            continue
        if stripped.startswith(">"):
            flush()
            text = stripped.lstrip(">").strip()
            prev = last("quote")
            if prev:
                prev["text"] += "\n" + text
            else:
                blocks.append({"t": "quote", "text": text})
            continue
        if stripped.startswith("|"):
            flush()
            if _TABLE_SEP.match(stripped):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            prev = last("table")
            if prev:
                prev["rows"].append(cells)
            else:
                blocks.append({"t": "table", "rows": [cells]})
            continue
        ordered = _ORDERED.match(line)
        if ordered:
            flush()
            prev = last("ol")
            if prev:
                prev["items"].append(ordered.group(2).strip())
            else:
                blocks.append({"t": "ol", "items": [ordered.group(2).strip()]})
            continue
        bullet = _BULLET.match(line)
        if bullet:
            flush()
            prev = last("ul")
            if prev:
                prev["items"].append(bullet.group(1).strip())
            else:
                blocks.append({"t": "ul", "items": [bullet.group(1).strip()]})
            continue
        paragraph.append(stripped)
    flush()
    return blocks


# ---------------------------------------------------------------- Rich Message

def to_rich_message(title: str, body: str) -> dict[str, Any]:
    """Тело для sendRichMessage: заголовок статьи + блоки из разметки."""
    out: list[dict] = []
    if title:
        out.append({"type": "section_heading", "text": title})
    for block in parse_blocks(body):
        kind = block["t"]
        if kind == "h":
            out.append({"type": "section_heading", "text": block["text"]})
        elif kind == "p":
            out.append({"type": "paragraph", "text": block["text"]})
        elif kind == "quote":
            out.append({"type": "block_quotation", "text": block["text"]})
        elif kind == "hr":
            out.append({"type": "divider"})
        elif kind in ("ul", "ol"):
            out.append({
                "type": "list",
                "is_ordered": kind == "ol",
                "items": [{"blocks": [{"type": "paragraph", "text": item}]}
                          for item in block["items"]],
            })
        elif kind == "table":
            width = max(len(row) for row in block["rows"])
            rows = [{"cells": [{"text": cell} for cell in row + [""] * (width - len(row))]}
                    for row in block["rows"]]
            out.append({"type": "table", "columns": width, "rows": rows})
    return {"blocks": out}


# ---------------------------------------------------------------- запасной HTML

MAX_HTML = 3800  # лимит Telegram 4096, остаток — про запас


def _table_pre(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    sizes = [min(max(len(r[i]) for r in rows), 24) for i in range(width)]
    lines = []
    for n, row in enumerate(rows):
        cells = [cell[:24].ljust(sizes[i]) for i, cell in enumerate(row)]
        lines.append("  ".join(cells).rstrip())
        if n == 0 and len(rows) > 1:
            lines.append("  ".join("─" * s for s in sizes))
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def to_html_parts(title: str, body: str) -> list[str]:
    """Статья как одно или несколько HTML-сообщений (если длиннее лимита)."""
    pieces: list[str] = []
    if title:
        pieces.append(f"<b>{html.escape(title)}</b>")
    for block in parse_blocks(body):
        kind = block["t"]
        if kind == "h":
            pieces.append(f"<b>{html.escape(block['text'])}</b>")
        elif kind == "p":
            pieces.append(html.escape(block["text"]))
        elif kind == "quote":
            pieces.append(f"<blockquote>{html.escape(block['text'])}</blockquote>")
        elif kind == "hr":
            pieces.append("──────────")
        elif kind == "ul":
            pieces.append("\n".join("• " + html.escape(i) for i in block["items"]))
        elif kind == "ol":
            pieces.append("\n".join(f"{n}. {html.escape(i)}" for n, i in enumerate(block["items"], 1)))
        elif kind == "table":
            pieces.append(_table_pre(block["rows"]))

    parts, current = [], ""
    for piece in pieces:
        for chunk in _split_long(piece):
            if current and len(current) + len(chunk) + 2 > MAX_HTML:
                parts.append(current)
                current = chunk
            else:
                current = f"{current}\n\n{chunk}" if current else chunk
    if current:
        parts.append(current)
    return parts or [html.escape(title or "Пустая статья")]


def _split_long(piece: str) -> list[str]:
    if len(piece) <= MAX_HTML:
        return [piece]
    # режем по строкам; теги <pre>/<blockquote> длинного куска не делим — режем только текст
    plain = piece.startswith("<pre>") is False and piece.startswith("<blockquote>") is False
    if not plain:
        return [piece[:MAX_HTML - 8] + "…"]
    chunks, current = [], ""
    for line in piece.split("\n"):
        while len(line) > MAX_HTML:
            chunks.append(line[:MAX_HTML])
            line = line[MAX_HTML:]
        if len(current) + len(line) + 1 > MAX_HTML:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------- из входящей статьи

def inline_text(node: Any) -> str:
    """Текст из вложенных узлов rich-text (bold, italic, ссылка…)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(inline_text(n) for n in node)
    if isinstance(node, dict):
        return inline_text(node.get("text"))
    return str(node)


_MEDIA = {"photo", "video", "animation", "audio", "document", "voice_note",
          "collage", "slideshow", "map"}


def _walk(blocks: Any, out: list[str]) -> None:
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind == "divider":
            out.append("---")
        elif kind == "section_heading":
            text = inline_text(b.get("text")).strip()
            if text:
                out.append(f"# {text}")
        elif kind == "list":
            for item in b.get("items", []):
                label = str(item.get("label") or "•")
                inner: list[str] = []
                _walk(item.get("blocks", []), inner)
                inner = [x for x in inner if x.strip()]
                marker = f"{label.rstrip('.)')}." if label.rstrip(".)").isdigit() else "-"
                if inner:
                    out.append(f"{marker} {inner[0]}")
                    out.extend(inner[1:])
        elif kind == "table":
            for row in b.get("cells", b.get("rows", [])):
                cells = row.get("cells", row) if isinstance(row, dict) else row
                texts = [inline_text(c.get("text") if isinstance(c, dict) else c).strip()
                         for c in cells]
                if any(texts):
                    out.append("| " + " | ".join(texts) + " |")
        elif kind == "details":
            summary = inline_text(b.get("summary")).strip()
            if summary:
                out.append(summary)
            _walk(b.get("blocks", []), out)
        elif kind in ("block_quotation", "pull_quotation", "expandable_block_quotation"):
            text = inline_text(b.get("text")).strip()
            if text:
                out.extend(f"> {line}" for line in text.split("\n"))
            _walk(b.get("blocks"), out)
        elif kind in _MEDIA:
            caption = inline_text(b.get("caption")).strip()
            if caption:
                out.append(caption)
        else:  # paragraph, footer, preformatted…
            text = inline_text(b.get("text")).strip()
            if text:
                out.append(text)
        out.append("")  # пустая строка между блоками — абзацы не слипаются при разборе обратно


def from_rich_message(rich: Any) -> str:
    """Входящая статья Telegram (объект aiogram или dict) -> наша разметка."""
    data = rich.model_dump(exclude_none=True) if hasattr(rich, "model_dump") else rich
    if not isinstance(data, dict):
        return ""
    out: list[str] = []
    _walk(data.get("blocks", []), out)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_title(markup: str, fallback: str = "Без названия") -> tuple[str, str]:
    """Заголовок статьи — первая секция или первая строка; остальное — тело."""
    lines = markup.strip().split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        title = re.sub(r"^#{1,3}\s+", "", line.strip())
        title = re.sub(r"^[-•>]\s+", "", title)[:100].strip()
        rest = "\n".join(lines[i + 1:]).strip()
        return title or fallback, rest
    return fallback, ""
