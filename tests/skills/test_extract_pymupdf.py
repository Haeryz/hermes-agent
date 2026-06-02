from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "productivity"
    / "ocr-and-documents"
    / "scripts"
    / "extract_pymupdf.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("extract_pymupdf", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_pages_accepts_ranges_and_commas():
    module = load_module()

    assert module._parse_pages("0,2,5-7") == [0, 2, 5, 6, 7]


def test_grid_table_outputs_fixed_width_raw_text():
    module = load_module()

    rendered = module._format_grid_table(
        [
            ["Name", "Score"],
            ["Alice", 10],
            ["Bob", None],
        ]
    )

    assert rendered == "\n".join(
        [
            "+-------+-------+",
            "| Name  | Score |",
            "+-------+-------+",
            "| Alice | 10    |",
            "+-------+-------+",
            "| Bob   |       |",
            "+-------+-------+",
        ]
    )


def test_pipe_table_escapes_cell_pipes():
    module = load_module()

    rendered = module._format_pipe_table([["A", "B"], ["x|y", "z"]])

    assert rendered == "\n".join(
        [
            "| A | B |",
            "| --- | --- |",
            r"| x\|y | z |",
        ]
    )


def test_output_path_preserves_relative_directory_for_corpus(tmp_path):
    module = load_module()
    corpus = tmp_path / "pdfs"
    pdf = corpus / "nested" / "case.pdf"
    output = tmp_path / "txt"

    assert module._output_path(pdf, corpus, output, ".txt") == output / "nested" / "case.txt"
