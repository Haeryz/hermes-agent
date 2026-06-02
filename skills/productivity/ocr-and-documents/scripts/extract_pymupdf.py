#!/usr/bin/env python3
"""Extract PDF text with inline raw-text tables using PyMuPDF.

Usage:
    python extract_pymupdf.py document.pdf
    python extract_pymupdf.py document.pdf --output-dir text_out
    python extract_pymupdf.py /path/to/pdfs --output-dir text_out --recursive
    python extract_pymupdf.py /path/to/pdfs --output-dir text_out --skip-existing
    python extract_pymupdf.py document.pdf --pages 0-4
    python extract_pymupdf.py document.pdf --table-format grid
    python extract_pymupdf.py document.pdf --tables-only
    python extract_pymupdf.py document.pdf --markdown
    python extract_pymupdf.py document.pdf --images output_dir/
    python extract_pymupdf.py document.pdf --metadata

Notes:
    PyMuPDF is deterministic for text-based PDFs and preserves detected tables
    as raw text tables. It does not OCR scanned pages; use extract_marker.py
    for scanned PDFs or documents that need model-based layout/OCR.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class _Element:
    y0: float
    x0: float
    text: str
    kind: str


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalize_rows(rows: Sequence[Sequence[object]]) -> list[list[str]]:
    normalized = [[_clean_cell(cell) for cell in row] for row in rows]
    width = max((len(row) for row in normalized), default=0)
    return [row + [""] * (width - len(row)) for row in normalized]


def _format_grid_table(rows: Sequence[Sequence[object]]) -> str:
    normalized = _normalize_rows(rows)
    if not normalized:
        return ""

    widths = [
        max(len(row[col]) for row in normalized)
        for col in range(len(normalized[0]))
    ]
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    lines = [border]
    for row in normalized:
        lines.append(
            "|"
            + "|".join(f" {cell.ljust(widths[col])} " for col, cell in enumerate(row))
            + "|"
        )
        lines.append(border)
    return "\n".join(lines)


def _format_pipe_table(rows: Sequence[Sequence[object]]) -> str:
    normalized = _normalize_rows(rows)
    if not normalized:
        return ""
    escaped = [[cell.replace("|", r"\|") for cell in row] for row in normalized]
    header = escaped[0]
    separator = ["---"] * len(header)
    body = escaped[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _format_tsv_table(rows: Sequence[Sequence[object]]) -> str:
    normalized = _normalize_rows(rows)
    return "\n".join("\t".join(cell for cell in row) for row in normalized)


def _format_table(rows: Sequence[Sequence[object]], table_format: str) -> str:
    if table_format == "grid":
        return _format_grid_table(rows)
    if table_format == "pipe":
        return _format_pipe_table(rows)
    if table_format == "tsv":
        return _format_tsv_table(rows)
    raise ValueError(f"Unsupported table format: {table_format}")


def _parse_pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None

    pages: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_s, end_s = token.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if end < start:
                raise ValueError(f"Invalid page range: {token}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(token))
    return sorted(pages)


def _rects_overlap(a: Sequence[float], b: Sequence[float], threshold: float = 0.2) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    x0 = max(ax0, bx0)
    y0 = max(ay0, by0)
    x1 = min(ax1, bx1)
    y1 = min(ay1, by1)
    if x1 <= x0 or y1 <= y0:
        return False
    overlap_area = (x1 - x0) * (y1 - y0)
    a_area = max((ax1 - ax0) * (ay1 - ay0), 1.0)
    return overlap_area / a_area >= threshold


def _table_rows(table: object) -> list[list[str]]:
    if hasattr(table, "extract"):
        return _normalize_rows(table.extract())
    if hasattr(table, "to_pandas"):
        dataframe = table.to_pandas()
        return _normalize_rows([list(dataframe.columns), *dataframe.values.tolist()])
    return []


def _page_tables(page: object, table_format: str) -> tuple[list[_Element], list[Sequence[float]]]:
    finder = page.find_tables()
    tables = []
    bboxes = []
    for index, table in enumerate(getattr(finder, "tables", []), start=1):
        bboxes.append(table.bbox)
        rows = _table_rows(table)
        rendered = _format_table(rows, table_format)
        if not rendered:
            continue
        x0, y0, _x1, _y1 = table.bbox
        tables.append(_Element(y0=y0, x0=x0, text=f"[Table {index}]\n{rendered}", kind="table"))
    return tables, bboxes


def _page_text_blocks(page: object, table_bboxes: Sequence[Sequence[float]]) -> list[_Element]:
    blocks = []
    for block in page.get_text("blocks", sort=True):
        if len(block) < 5:
            continue
        x0, y0, x1, y1, text = block[:5]
        if not isinstance(text, str) or not text.strip():
            continue
        bbox = (x0, y0, x1, y1)
        if any(_rects_overlap(bbox, table_bbox) for table_bbox in table_bboxes):
            continue
        blocks.append(_Element(y0=y0, x0=x0, text=text.strip(), kind="text"))
    return blocks


def extract_text(path: Path, pages: Iterable[int] | None = None, table_format: str = "grid") -> str:
    import pymupdf

    doc = pymupdf.open(path)
    page_indexes = range(len(doc)) if pages is None else pages
    parts: list[str] = []
    for page_index in page_indexes:
        if page_index < 0 or page_index >= len(doc):
            continue
        page = doc[page_index]
        tables, table_bboxes = _page_tables(page, table_format)
        blocks = _page_text_blocks(page, table_bboxes)
        elements = sorted([*blocks, *tables], key=lambda item: (item.y0, item.x0, item.kind))
        parts.append(f"--- Page {page_index + 1}/{len(doc)} ---")
        parts.extend(element.text for element in elements if element.text.strip())
    return "\n\n".join(parts).rstrip() + "\n"


def extract_tables(path: Path, pages: Iterable[int] | None = None, table_format: str = "grid") -> str:
    import pymupdf

    doc = pymupdf.open(path)
    page_indexes = range(len(doc)) if pages is None else pages
    parts: list[str] = []
    for page_index in page_indexes:
        if page_index < 0 or page_index >= len(doc):
            continue
        page = doc[page_index]
        for table_index, table in enumerate(getattr(page.find_tables(), "tables", []), start=1):
            rendered = _format_table(_table_rows(table), table_format)
            if rendered:
                parts.append(f"--- Page {page_index + 1}, Table {table_index} ---\n{rendered}")
    return "\n\n".join(parts).rstrip() + ("\n" if parts else "")


def extract_markdown(path: Path, pages: Iterable[int] | None = None) -> str:
    import pymupdf4llm

    return pymupdf4llm.to_markdown(str(path), pages=list(pages) if pages is not None else None)


def extract_images(path: Path, output_dir: Path) -> int:
    import pymupdf

    output_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(path)
    count = 0
    for page_index, page in enumerate(doc):
        for img_index, image in enumerate(page.get_images(full=True)):
            xref = image[0]
            pix = pymupdf.Pixmap(doc, xref)
            if pix.n >= 5:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            pix.save(output_dir / f"page{page_index + 1}_img{img_index + 1}.png")
            count += 1
    return count


def metadata(path: Path) -> str:
    import pymupdf

    doc = pymupdf.open(path)
    return json.dumps(
        {
            "pages": len(doc),
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "subject": doc.metadata.get("subject", ""),
            "creator": doc.metadata.get("creator", ""),
            "producer": doc.metadata.get("producer", ""),
            "format": doc.metadata.get("format", ""),
        },
        indent=2,
        ensure_ascii=False,
    )


def _iter_pdfs(input_path: Path, recursive: bool, glob_pattern: str) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    iterator = input_path.rglob(glob_pattern) if recursive else input_path.glob(glob_pattern)
    return sorted(path for path in iterator if path.is_file() and path.suffix.lower() == ".pdf")


def _output_path(pdf_path: Path, input_path: Path, output_dir: Path, suffix: str) -> Path:
    if input_path.is_file():
        relative = pdf_path.with_suffix(suffix).name
    else:
        relative = pdf_path.relative_to(input_path).with_suffix(suffix)
    return output_dir / relative


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="PDF file or directory containing PDFs.")
    parser.add_argument("--pages", help="Zero-based page spec, for example '0-4' or '0,2,5-7'.")
    parser.add_argument("--output-dir", type=Path, help="Write one text file per PDF to this directory.")
    parser.add_argument("--suffix", default=".txt", help="Output suffix for --output-dir mode. Default: .txt")
    parser.add_argument("--skip-existing", action="store_true", help="Do not reprocess PDFs whose output file exists.")
    parser.add_argument("--recursive", action="store_true", help="Recursively scan an input directory.")
    parser.add_argument("--glob", default="*.pdf", help="PDF glob for directory mode. Default: *.pdf")
    parser.add_argument("--table-format", choices=("grid", "pipe", "tsv"), default="grid")
    parser.add_argument("--tables-only", action="store_true", help="Output only detected tables.")
    parser.add_argument("--markdown", action="store_true", help="Use pymupdf4llm Markdown extraction.")
    parser.add_argument("--images", type=Path, help="Extract embedded images to the given directory.")
    parser.add_argument("--metadata", action="store_true", help="Print PDF metadata as JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    input_path = args.input.expanduser()

    if not input_path.exists():
        parser.error(f"Input does not exist: {input_path}")

    pages = _parse_pages(args.pages)

    if args.images:
        if not input_path.is_file():
            parser.error("--images requires a single PDF file input")
        count = extract_images(input_path, args.images)
        print(f"Extracted {count} images to {args.images}")
        return 0

    if args.metadata:
        if not input_path.is_file():
            parser.error("--metadata requires a single PDF file input")
        print(metadata(input_path))
        return 0

    pdfs = _iter_pdfs(input_path, args.recursive, args.glob)
    if not pdfs:
        parser.error(f"No PDF files found in {input_path}")

    output_dir = args.output_dir
    if input_path.is_dir() and output_dir is None:
        output_dir = input_path.with_name(f"{input_path.name}_text")

    for pdf_path in pdfs:
        destination = _output_path(pdf_path, input_path, output_dir, args.suffix) if output_dir is not None else None
        if args.skip_existing and destination is not None and destination.exists():
            print(f"Skipped existing {destination}")
            continue

        if args.markdown:
            content = extract_markdown(pdf_path, pages)
        elif args.tables_only:
            content = extract_tables(pdf_path, pages, args.table_format)
        else:
            content = extract_text(pdf_path, pages, args.table_format)

        if output_dir is None:
            print(content, end="")
            continue

        assert destination is not None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
        print(f"Wrote {destination}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
