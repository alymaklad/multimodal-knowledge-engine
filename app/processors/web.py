"""Web page snapshots.

The page's main content is extracted as Markdown so headings survive, then split like any other text.
`trafilatura` is used when installed: it is much better at separating an article from navigation,
cookie banners, and comment threads. Without it a built-in extractor drops the obvious page chrome
(scripts, navigation, headers, footers, asides, forms) and keeps headings, paragraphs, lists, and tables.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from app.processors.base import ProcessingError, ProcessorResult
from app.processors.text import process_text, read_text_file

# <title> is skipped because it is already the source's title. <head> itself is not: HTML5 lets a
# page omit </head>, and skipping up to a close tag that never comes would lose the whole page.
_SKIPPED = {
    "title", "script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside", "form", "iframe",
}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_BLOCKS = {"p", "div", "section", "article", "main", "li", "tr", "blockquote", "pre", "table", "ul", "ol", "dd", "dt", "figcaption"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def process_html(path: str | Path, charset: str | None = None) -> ProcessorResult:
    html = read_text_file(path, charset)
    markdown, extractor = html_to_markdown(html)
    if not markdown.strip():
        raise ProcessingError("No readable text was found on this page")
    result = process_text(markdown)
    result.models = [extractor, *result.models]
    return result


def html_to_markdown(html: str) -> tuple[str, str]:
    try:
        import trafilatura
    except ImportError:
        trafilatura = None
    if trafilatura is not None:
        extracted = trafilatura.extract(
            html, output_format="markdown", include_tables=True, include_links=False, include_comments=False
        )
        if extracted and extracted.strip():
            return extracted, "trafilatura"
    parser = _MarkdownExtractor()
    parser.feed(html)
    parser.close()
    return parser.markdown(), "html-extractor-v1"


class _MarkdownExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[str] = []
        self._current: list[str] = []
        # Only the tag that started a skipped region is counted. Real pages leave <li> and <p> unclosed,
        # so counting every tag inside the region would never get back to zero and lose the page.
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._heading: int | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in _VOID:
            if tag == "br":
                self._current.append("\n")
            return
        if tag in _SKIPPED:
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag in _HEADINGS or tag in _BLOCKS:
            self._flush()
            if tag in _HEADINGS:
                self._heading = _HEADINGS[tag]
            elif tag == "li":
                self._current.append("- ")
        elif tag in {"td", "th"} and self._current:
            self._current.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag in _HEADINGS or tag in _BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_tag is None:
            self._current.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        text = " ".join("".join(self._current).split())
        self._current = []
        if text and text != "-":
            self._blocks.append(f"{'#' * self._heading} {text}" if self._heading else text)
        self._heading = None

    def markdown(self) -> str:
        return "\n\n".join(self._blocks)
