"""Find candidate facts on one public page, for a person to choose from. It never picks for you and never fills facts.csv.

  python scripts/outreach/evidence.py find https://example.com/blog/post --word agent --word cost

Fetches ONE page: public only, robots.txt respected, an honest User-Agent, a size cap, a timeout. Prints the sentences that contain your
words, numbered, with the URL. You copy the one you will quote into outreach/facts.csv (evidence_url, evidence_fact) and check it says
what you think it says: a wrong personal detail is worse than none. Nothing here logs in, follows links, or goes around a block.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

UA = "outreach-research/0.1 (single page fetch by a person; contact in the email footer)"
MAX_BYTES = 1_000_000


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "nav", "footer", "header"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "nav", "footer", "header") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def allowed_by_robots(url: str, fetch=None) -> bool:
    u = urllib.parse.urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    robots = f"{u.scheme}://{u.netloc}/robots.txt"
    try:
        if fetch:
            rp.parse(fetch(robots).splitlines())
        else:
            req = urllib.request.Request(robots, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
                rp.parse(r.read(200_000).decode("utf-8", "replace").splitlines())
    except Exception:  # noqa: BLE001 -- no readable robots.txt: the usual convention is that fetching a public page is allowed
        return True
    return rp.can_fetch(UA, url)


def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 -- one public page, chosen by a person
        return r.read(MAX_BYTES).decode("utf-8", "replace")


def sentences_with(html: str, words: list[str], limit: int = 12) -> list[str]:
    t = _Text()
    t.feed(html)
    text = re.sub(r"\s+", " ", " ".join(t.parts))
    found = []
    for s in re.split(r"(?<=[.!?])\s+", text):
        if 40 <= len(s) <= 300 and any(w.lower() in s.lower() for w in words):
            found.append(s.strip())
        if len(found) >= limit:
            break
    return found


def main(argv=None, out=print, fetch=fetch_page, robots=allowed_by_robots) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("url")
    f.add_argument("--word", action="append", required=True)
    a = p.parse_args(argv)
    if not a.url.startswith(("http://", "https://")):
        out("give an http(s) address")
        return 2
    if not robots(a.url):
        out("that site's robots.txt does not allow this page for automated fetching; open it in a browser and read it yourself")
        return 3
    try:
        found = sentences_with(fetch(a.url), a.word)
    except Exception as e:  # noqa: BLE001
        out(f"could not fetch the page ({type(e).__name__})")
        return 1
    out(f"{len(found)} candidate sentence(s) from {a.url}:")
    for i, s in enumerate(found, 1):
        out(f"  [{i}] {s}")
    out("Copy ONE into outreach/facts.csv (evidence_url, evidence_fact) after checking it says what you think.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
