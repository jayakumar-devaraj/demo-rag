"""Normalization, SHA-256 signatures, and structure-aware chunking of Markdown units."""

import hashlib
import re
import unicodedata

MAX_CHARS = 1200
MAX_TABLE_ROWS = 25


def normalize(markdown: str) -> str:
    """Canonical form hashed by the gate: NFC, trailing spaces stripped, blank runs collapsed."""
    text = unicodedata.normalize("NFC", markdown).replace("\r\n", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def signature(markdown: str) -> str:
    """64-char SHA-256 of the normalized Markdown."""
    return hashlib.sha256(normalize(markdown).encode("utf-8")).hexdigest()


def _blocks(markdown: str) -> list[str]:
    """Split into blocks on blank lines, keeping each Markdown table as one block."""
    blocks, current, in_table = [], [], False
    for line in markdown.split("\n"):
        is_row = line.lstrip().startswith("|")
        if current and (not line.strip() or is_row != in_table):
            blocks.append("\n".join(current))
            current = []
        if line.strip():
            current.append(line)
            in_table = is_row
    if current:
        blocks.append("\n".join(current))
    return blocks


def _split_table(table: str, context: str) -> list[str]:
    """Split a long table into row groups, repeating the header rows in every piece."""
    lines = table.split("\n")
    header, rows = lines[:2], lines[2:]
    prefix = f"{context}\n\n" if context else ""
    return [prefix + "\n".join(header + rows[i : i + MAX_TABLE_ROWS]) for i in range(0, max(len(rows), 1), MAX_TABLE_ROWS)]


def chunk(markdown: str) -> list[str]:
    """Chunk on structure: headings open sections, tables stay intact with their header row,
    and paragraphs are packed under their nearest heading up to MAX_CHARS."""
    chunks, buffer, heading = [], [], ""

    def flush() -> None:
        if buffer:
            chunks.append("\n\n".join(([heading] if heading and buffer[0] != heading else []) + buffer))
            buffer.clear()

    for block in _blocks(normalize(markdown)):
        if block.startswith("#"):
            flush()
            heading = block.split("\n")[0]
            buffer.append(block)
        elif block.lstrip().startswith("|"):
            if buffer == [heading]:
                buffer.clear()  # the heading is repeated inside every table piece instead
            flush()
            chunks.extend(_split_table(block, heading))
        else:
            if buffer and sum(len(b) for b in buffer) + len(block) > MAX_CHARS:
                flush()
            buffer.append(block)
    flush()
    return chunks
