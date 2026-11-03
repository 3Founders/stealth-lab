"""What a canvas is called. A title that is only an id (a UUID, a hash, a number, a key) is not a name: the document's own title or first heading,
else its first line, is. Used when a canvas is made (a new document is never saved under an id) and when the list is read (documents made before this
rule keep their stored title, but the room shows the derived name; nothing in the database is rewritten)."""
from __future__ import annotations

import re

_UUID = re.compile(r"^[0-9a-f]{8}-?([0-9a-f]{4}-?){3}[0-9a-f]{12}$", re.I)
_HEX = re.compile(r"^[0-9a-f]{12,}$", re.I)
_NUMBERISH = re.compile(r"^[\d\s._#:-]+$")
_FILEISH = re.compile(r"^[\w-]{1,40}\.(html?|txt|md|tex)$", re.I)
_UNTITLED = {"untitled", "canvas", "document", "page", "web page", "html artifact", "artifact", "slides", "code", "result", "output"}


def is_ugly(title: str) -> bool:
    """True for a title that reads as an id, not a name: a UUID, a hash, a bare number, a token with no word in it, 1.html."""
    t = (title or "").strip()
    if len(t) < 3:
        return True
    if _UUID.match(t) or _HEX.match(t) or _NUMBERISH.match(t):
        return True
    if _FILEISH.match(t) and not re.search(r"[a-z]{4}", re.sub(r"\.[a-z]+$", "", t, flags=re.I), re.I):
        return True
    if " " not in t and len(t) >= 10 and re.search(r"\d", t) and re.search(r"[a-z]", t, re.I) and not re.search(r"[aeiou]{1}[^aeiou\d]{1,4}[aeiou]", t[:8], re.I):
        return True                                           # one long run of letters and digits with no word shape: a key or a short id
    return False


def looks_like_html(text: str) -> bool:
    head = (text or "").lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or ("<section" in head and "<style" in (text or "")[:4000].lower())


def guess_kind(text: str) -> str:
    """html for a page or deck, code for source code, otherwise a document."""
    if looks_like_html(text):
        return "html"
    head = (text or "").lstrip()[:400]
    if re.match(r"^(#!|import |from \w+ import |def |class |function |const |let |package |#include|using |public )", head):
        return "code"
    return "document"


def guess_title(text: str, kind: str = "document") -> str:
    """A name from the text: the page's <title> or first <h1>, else the first markdown heading, else the first real line, else a plain word."""
    body = text or ""
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S) or re.search(r"<h[12][^>]*>(.*?)</h[12]>", body, re.I | re.S)
    cand = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    if not cand or is_ugly(cand) or cand.lower() in _UNTITLED:
        h = re.search(r"^\s{0,3}#{1,3}\s+(.+)$", body, re.M)
        cand = h.group(1).strip("# ").strip() if h else ""
    if not cand or is_ugly(cand):
        cand = ""
        for line in body.splitlines():
            line = line.strip(" #*-`>")
            if len(line) >= 3 and not line.startswith(("import ", "from ", "def ", "<", "{", "\\")):
                cand = line
                break
    cand = " ".join(cand.split())[:60].strip(" .:-")
    return cand if len(cand) >= 3 and not is_ugly(cand) else {"html": "Web page", "code": "Code", "document": "Document", "latex": "LaTeX document"}.get(kind, "Document")


def label_for(title: str, kind: str, body: str) -> str:
    """The name to show: the stored title when it is a name, else one derived from the text."""
    t = (title or "").strip()
    if not is_ugly(t) and t.lower() not in _UNTITLED:
        return t
    return guess_title(body, kind)
