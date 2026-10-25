"""PDF and Word documents.

PDFs are read page by page with PDFium (`pypdfium2`, Apache/BSD licensed; PyMuPDF was avoided for its
AGPL licence). A page with no text layer is a scan, so it is rendered and sent to OCR instead of
being silently skipped. Word documents are converted to Markdown, keeping their headings, and then
split like any other text.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from app.core.config import get_settings
from app.processors.base import ProcessingError, ProcessorContext, ProcessorResult, SegmentDraft
from app.processors.text import pack_paragraphs, process_text

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING_STYLE = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)


def process_pdf(path: str | Path, context: ProcessorContext) -> ProcessorResult:
    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        raise ProcessingError("Install pypdfium2 to read PDF documents") from error

    settings = get_settings()
    try:
        document = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as error:
        message = str(error).lower()
        if "password" in message:
            raise ProcessingError("This PDF is password-protected") from error
        raise ProcessingError("This PDF could not be opened; it may be damaged") from error

    segments: list[SegmentDraft] = []
    warnings: list[str] = []
    models = ["pdfium"]
    ocr_pages: list[int] = []
    try:
        page_count = len(document)
        if page_count > settings.max_document_pages:
            warnings.append(f"Only the first {settings.max_document_pages} of {page_count} pages were read")
        for index in range(min(page_count, settings.max_document_pages)):
            number = index + 1
            page = document[index]
            try:
                text = _page_text(page)
                modality = "text"
                if len(text.strip()) < settings.pdf_ocr_min_characters:
                    text, failure = _ocr_page(page, context, settings.pdf_ocr_dpi)
                    modality = "ocr"
                    if failure:
                        warnings.append(f"Page {number} has no text layer and OCR failed: {failure}")
                        continue
                    if text:
                        ocr_pages.append(number)
            finally:
                page.close()
            for part, _ in pack_paragraphs(_page_paragraphs(text), settings.segment_max_characters):
                segments.append(SegmentDraft(text=part, modality=modality, locator={"page": number}))
    finally:
        document.close()

    if not segments:
        raise ProcessingError("No text could be read from this PDF, even with OCR")
    if ocr_pages:
        models.append(type(context.ocr).__name__)
    return ProcessorResult(
        segments=segments,
        models=models,
        warnings=warnings,
        details={"page_count": page_count, "ocr_pages": ocr_pages},
    )


def _page_text(page) -> str:
    textpage = page.get_textpage()
    try:
        return textpage.get_text_bounded()
    finally:
        textpage.close()


def _page_paragraphs(text: str) -> list[str]:
    """PDF text arrives hard-wrapped; rejoin wrapped lines and split only at blank lines.

    PDFium marks a hyphen it inserted at a line end with \\x02 and drops the line break, so that marker
    is removed outright. Other control characters are dropped too; PostgreSQL rejects some in text.
    """
    text = text.replace("\x02", "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", _HYPHEN_BREAK.sub(r"\1\2", text))
    blocks = re.split(r"\n\s*\n", text)
    return [" ".join(block.split()) for block in blocks if block.strip()]


def _ocr_page(page, context: ProcessorContext, dpi: int) -> tuple[str, str | None]:
    """Render a page and OCR it. Returns the text and, on failure, a reason instead."""
    try:
        image = page.render(scale=dpi / 72).to_pil()
    except Exception as error:
        return "", f"the page could not be rendered ({type(error).__name__})"
    with tempfile.TemporaryDirectory() as directory:
        image_path = Path(directory) / "page.png"
        image.save(image_path)
        try:
            observations = context.ocr.extract([image_path])
        except Exception as error:
            return "", str(error) or type(error).__name__
    return "\n".join(observation.text for observation in observations if observation.text.strip()), None


def process_docx(path: str | Path) -> ProcessorResult:
    try:
        import docx
    except ImportError as error:
        raise ProcessingError("Install python-docx to read Word documents") from error
    try:
        document = docx.Document(str(path))
    except Exception as error:
        raise ProcessingError(f"This Word document could not be opened ({type(error).__name__})") from error

    result = process_text(docx_to_markdown(document))
    result.models = ["python-docx", *result.models]
    return result


def docx_to_markdown(document) -> str:
    """Body content in document order, with heading styles turned into Markdown headings."""
    from docx.table import Table

    blocks: list[str] = []
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in item.rows]
            rows = [row for row in rows if row.strip(" |")]
            if rows:
                blocks.append("\n".join(rows))
            continue
        text = item.text.strip()
        if not text:
            continue
        style = (item.style.name if item.style is not None else "") or ""
        level = 1 if style.lower() == "title" else None
        match = _HEADING_STYLE.match(style.strip())
        if match:
            level = min(int(match.group(1)), 6)
        blocks.append(f"{'#' * level} {text}" if level else text)
    return "\n\n".join(blocks)
