"""Plain text from an HTML mail body, for messages without a text/plain part."""

import re
from html.parser import HTMLParser

# Elements whose content is never shown.
_HIDDEN = frozenset({"head", "script", "style", "title", "template", "noscript"})
# Elements that start a new line.
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "fieldset", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
        "section", "table", "tbody", "tfoot", "thead", "tr", "ul",
    }
)  # fmt: skip
# Table cells keep their content apart on one line.
_CELL = frozenset({"td", "th"})
# Invisible characters that marketing mail pads its preview text with
# (soft hyphen, combining grapheme joiner, zero-width space and non-joiner,
# word joiner, braille blank, zero-width no-break space); a str.translate
# table that deletes them. The zero-width joiner stays: emoji need it.
INVISIBLE_FILLERS = dict.fromkeys(
    (0x00AD, 0x034F, 0x200B, 0x200C, 0x2060, 0x2800, 0xFEFF)
)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._hidden = 0
        self._pre = 0

    def handle_starttag(self, tag, attrs):
        if tag in _HIDDEN:
            self._hidden += 1
        elif tag in _BLOCK:
            self.chunks.append("\n")
            if tag == "pre":
                self._pre += 1
        elif tag in _CELL:
            self.chunks.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _HIDDEN:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK:
            self.chunks.append("\n")
            if tag == "pre":
                self._pre = max(0, self._pre - 1)

    def handle_data(self, data):
        if self._hidden:
            return
        if self._pre:
            # Preformatted text keeps its line breaks.
            self.chunks.append(data)
        else:
            # Outside <pre>, any run of whitespace renders as one space.
            self.chunks.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    """Visible text of an HTML document, one block element per line."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = []
    for line in "".join(parser.chunks).split("\n"):
        line = line.translate(INVISIBLE_FILLERS)
        # Non-breaking spaces (&nbsp;) become plain spaces.
        line = re.sub(r"[ \t]+", " ", line.replace("\u00a0", " ")).strip()
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()
