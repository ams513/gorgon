"""Stark Quill paste cleaner (Q79): turn the HTML that Word or Google Docs put on the clipboard into Quill text.

Keeps italics (*like this*) and bold (**like this**) and the paragraph breaks (a blank line between paragraphs,
as Quill writes them). Everything else is dropped: fonts, sizes, colours, underline, links (their words stay),
images, comments, styles and Word's hidden markup. Standard library only; nothing leaves the computer.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

MAX_HTML = 6 * 1024 * 1024          # larger clipboards paste as plain text (the page says so)

_SKIP = {"head", "style", "script", "title", "xml", "template", "noscript", "meta", "link", "object", "svg"}
_BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote", "pre", "section", "article",
          "table", "ul", "ol", "dl", "dt", "dd", "header", "footer", "aside", "figure", "figcaption", "hr", "body"}
_CELL = {"td", "th"}
_VOID = {"br", "img", "hr", "input", "meta", "link", "col", "area", "base", "wbr", "source", "embed", "param"}
_BOLD_TAGS = {"b", "strong"}
_ITALIC_TAGS = {"i", "em", "cite", "dfn", "var"}
_WS = re.compile(r"[ \t\r\n\f\v ]+")


def _style(attrs):
    out = {}
    for k, v in attrs:
        if k == "style" and v:
            for decl in v.split(";"):
                if ":" in decl:
                    name, val = decl.split(":", 1)
                    out[name.strip().lower()] = val.strip().lower().replace("!important", "").strip()
    return out


def _weight(val):
    """True (bold), False (not bold) or None (not said) for a CSS font-weight value."""
    if val in ("bold", "bolder"):
        return True
    if val in ("normal", "lighter"):
        return False
    if val.isdigit():
        return int(val) >= 600
    return None


def _italic(val):
    if val in ("italic", "oblique") or val.startswith("oblique"):
        return True
    if val == "normal":
        return False
    return None


class _Cleaner(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack = []          # (tag, bold, italic)
        self.skip = 0            # depth inside a dropped element (head, style, Word's list bullets ...)
        self.skip_tag = None
        self.paras = [[]]        # paragraphs -> runs [text, bold, italic]

    # -- state
    def _state(self):
        return (self.stack[-1][1], self.stack[-1][2]) if self.stack else (False, False)

    def _break(self):
        if self.paras[-1]:
            self.paras.append([])

    def _run(self, text, bold, italic):
        para = self.paras[-1]
        if para and para[-1][1] == bold and para[-1][2] == italic:
            para[-1][0] += text
        else:
            para.append([text, bold, italic])

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip_tag and tag not in _VOID:
                self.skip += 1
            return
        st = _style(attrs)
        if tag in _SKIP or st.get("mso-list") == "ignore":  # Word's typed bullets and numbers are markup
            if tag not in _VOID:
                self.skip, self.skip_tag = 1, tag
            return
        if tag == "br":
            self._run("\n", *self._state())
            return
        if tag in _VOID:
            if tag == "hr":
                self._break()
            return
        if tag in _BLOCK:
            if tag == "p":  # an unclosed <p> ends at the next one
                for i in range(len(self.stack) - 1, -1, -1):
                    if self.stack[i][0] == "p":
                        del self.stack[i:]
                        break
            self._break()
        elif tag in _CELL and self.paras[-1]:
            self._run(" ", *self._state())
        bold, italic = self._state()
        if tag in _BOLD_TAGS:
            bold = True
        if tag in _ITALIC_TAGS:
            italic = True
        w = _weight(st.get("font-weight", ""))
        if w is not None:
            bold = w
        it = _italic(st.get("font-style", ""))
        if it is not None:
            italic = it
        self.stack.append((tag, bold, italic))

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag == "br" and not self.skip:
            self._run("\n", *self._state())
        elif tag == "hr" and not self.skip:
            self._break()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
                if not self.skip:
                    self.skip_tag = None
            return
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in _BLOCK:
            self._break()

    def handle_data(self, data):
        if self.skip or not data:
            return
        text = _WS.sub(" ", data)
        if text:
            self._run(text, *self._state())

    def unknown_decl(self, data):  # Word's <![if !supportLists]> ... <![endif]>: markup, not text
        pass


def _wrap(text, bold, italic):
    if not (bold or italic) or not text.strip():
        return text
    lead = text[: len(text) - len(text.lstrip())]
    tail = text[len(text.rstrip()):]
    mark = "***" if (bold and italic) else "**" if bold else "*"
    return lead + mark + text.strip() + mark + tail


def _paragraph(runs):
    # merge neighbours that ended up with the same style after whitespace runs between them
    merged = []
    for text, bold, italic in runs:
        if merged and (merged[-1][1:] == [bold, italic] or (not text.strip() and "\n" not in text)):
            merged[-1][0] += text
        else:
            merged.append([text, bold, italic])
    out = []
    for text, bold, italic in merged:
        parts = text.split("\n")  # a <br> inside a paragraph: a line break, styles closed on each line
        out.append("\n".join(_wrap(p, bold, italic) for p in parts))
    lines = [_WS.sub(" ", ln).strip() for ln in "".join(out).split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def html_to_text(html):
    """Clipboard HTML -> Quill text with *italics*, **bold** and blank lines between paragraphs."""
    if not isinstance(html, str):
        raise TypeError("html must be str")
    html = html[:MAX_HTML]
    m = re.search(r"<!--\s*StartFragment\s*-->(.*)<!--\s*EndFragment\s*-->", html, re.S | re.I)
    if m:  # Windows' clipboard format wraps the copied part in these markers; the rest is page furniture
        html = m.group(1)
    p = _Cleaner()
    p.feed(html)
    p.close()
    paras = [_paragraph(r) for r in p.paras]
    return "\n\n".join(x for x in paras if x)
