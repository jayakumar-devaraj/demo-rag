"""Unit tests for the polymorphic workers and structure-aware chunking."""

import pytest

from src.ingestion.chunking import MAX_TABLE_ROWS, chunk, signature
from src.ingestion.workers import UnsupportedFormatError, worker_for
from tests.conftest import make_docx, make_pdf, make_xlsx


def test_xlsx_each_tab_is_a_unit_with_coordinates():
    data = make_xlsx({"Q1": [["Region", "Sales"], ["North", 100], ["South", 80]], "Q2": [["Region", "Sales"], ["North", 120]]})
    units = worker_for("book.xlsx").extract(data)
    assert [u.page_key for u in units] == ["tab:Q1", "tab:Q2"]
    q1 = units[0].markdown
    assert "| row | A:Region | B:Sales |" in q1
    assert "| 3 | South | 80 |" in q1  # Excel row 3 keeps its coordinate


def test_xlsx_formula_without_cached_value_is_kept():
    units = worker_for("f.xlsx").extract(make_xlsx({"S": [["a", "b", "sum"], [1, 2, "=A2+B2"]]}))
    assert "=A2+B2" in units[0].markdown


def test_pdf_table_becomes_markdown_grid_per_page():
    data = make_pdf([("Invoice page one", [["ID", "Amount"], ["INV-001", "250"]]),
                     ("Invoice page two", [["ID", "Amount"], ["INV-002", "900"]])])
    units = worker_for("x.pdf").extract(data)
    assert [u.page_key for u in units] == ["page:1", "page:2"]
    assert "Invoice page one" in units[0].markdown
    row = next(line for line in units[1].markdown.splitlines() if "INV-002" in line)
    assert row.startswith("|") and "900" in row


def test_docx_headings_paragraphs_and_table():
    md = worker_for("r.docx").extract(make_docx())[0].markdown
    assert "# Quarterly Review" in md and "## Budget" in md
    assert "| Servers | 4200 |" in md


def test_txt_single_unit():
    assert worker_for("a.TXT").extract("﻿hello".encode())[0].markdown == "hello"


def test_unsupported_extension():
    with pytest.raises(UnsupportedFormatError):
        worker_for("a.pptx")


def test_signature_ignores_whitespace_noise():
    assert signature("a  \r\nb\n\n\n\nc") == signature("a\nb\n\nc")
    assert len(signature("x")) == 64


def test_long_table_split_repeats_header_in_every_chunk():
    rows = "\n".join(f"| {i} | v{i} |" for i in range(MAX_TABLE_ROWS * 2 + 3))
    chunks = chunk(f"## Sheet: S\n\n| id | val |\n|---|---|\n{rows}")
    assert len(chunks) == 3
    for c in chunks:
        assert c.startswith("## Sheet: S") and "| id | val |\n|---|---|" in c


def test_paragraphs_carry_their_heading():
    chunks = chunk("# A\n\npara a\n\n# B\n\npara b")
    assert chunks == ["# A\n\npara a", "# B\n\npara b"]
