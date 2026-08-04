"""Build a <nav id="toc"> block from the rendered document body.

We don't trust python-markdown's built-in toc extension to give us the
heading-id slugs we want; this module re-parses the rendered HTML and
emits a TOC structure that pairs with theme/print.css. Page numbers are
filled in by WeasyPrint at paged-render time via target-counter() — pure
CSS, no second build pass.
"""

from __future__ import annotations

import html as html_lib
import re
import unicodedata
from dataclasses import dataclass


@dataclass
class Heading:
    level: int   # 1 or 2
    text: str    # plain-text heading text
    slug: str    # the id="..." attribute on the heading


HEADING_RE = re.compile(
    r'<h(?P<level>[12])(?P<attrs>[^>]*)>(?P<inner>.*?)</h(?P=level)>',
    re.IGNORECASE | re.DOTALL,
)
ID_RE = re.compile(r'\bid="([^"]+)"')
TAG_RE = re.compile(r'<[^>]+>')


def slugify(text: str) -> str:
    """Generate a heading id matching GitHub's slugger.

    Markdown is overwhelmingly authored against GitHub's anchor rules —
    that is what a hand-written `[Section](#section)` link in a `.md`
    file resolves against when viewed on GitHub, in VS Code, or in most
    other renderers. Matching those rules means one set of TOC links
    works in both the source markdown and the rendered PDF.

    The rules, per github-slugger:

    1. lowercase
    2. drop everything that is not a word character, space, or hyphen
       (so ``.``, ``,``, ``:``, ``&``, ``'``, ``(``, ``)``, ``/`` all go)
    3. replace each remaining space with a single hyphen

    Step 3 is per-space and deliberately does **not** collapse runs. A
    heading like ``1. Purpose & Scope`` loses the ``&`` but keeps the
    spaces that surrounded it, yielding ``1-purpose--scope`` with a
    double hyphen. Collapsing here would silently diverge from every
    markdown renderer and break hand-authored anchors in the `.md`.

    Authors needing a specific id can still override it with attr_list
    (``## Heading {#custom-id}``); `assign_heading_ids` honours an
    existing id and never overwrites one.
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s", "-", text)
    return text or "section"


def _strip_tags(html: str) -> str:
    return html_lib.unescape(TAG_RE.sub("", html)).strip()


def assign_heading_ids(body_html: str) -> tuple[str, list[Heading]]:
    """Walk the body HTML, ensure every h1/h2 has an id, collect headings.

    We assign ids ourselves rather than rely on the markdown toc extension
    because the toc extension also generates a TOC block that doesn't have
    the leader-dot + page-counter structure we need. Doing it here keeps
    the slug logic in one place.
    """
    headings: list[Heading] = []
    seen_slugs: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        level = int(match.group("level"))
        attrs = match.group("attrs") or ""
        inner = match.group("inner")
        text = _strip_tags(inner)

        existing_id = ID_RE.search(attrs)
        if existing_id:
            slug = existing_id.group(1)
        else:
            base = slugify(text)
            count = seen_slugs.get(base, 0)
            slug = base if count == 0 else f"{base}-{count}"
            seen_slugs[base] = count + 1
            attrs = f' id="{slug}"' + attrs

        # Tag the doc title (very first h1) so the running header can pick
        # it up via string-set.
        extra_class = ""
        if level == 1 and not headings:
            extra_class = ' class="doc-title"'
            if 'class="' in attrs:
                attrs = attrs.replace('class="', 'class="doc-title ', 1)
                extra_class = ""

        headings.append(Heading(level=level, text=text, slug=slug))
        return f"<h{level}{attrs}{extra_class}>{inner}</h{level}>"

    new_html = HEADING_RE.sub(replace, body_html)
    return new_html, headings


ANCHOR_HREF_RE = re.compile(r'href="#([^"]+)"')
ANY_ID_RE = re.compile(r'\bid="([^"]+)"')


def find_broken_anchors(body_html: str) -> list[str]:
    """Return internal ``#fragment`` links that match no id in the document.

    A hand-authored table of contents is just markdown links, so a stale
    or mis-slugified anchor fails silently: the PDF renders, the link
    simply goes nowhere. This surfaces those at build time.

    Fragments are compared literally, exactly as a browser resolves them
    — an href of ``#a%3Ab`` does *not* match ``id="a:b"``, because the
    fragment is not URL-decoded before comparison. Percent-encoding an
    anchor to match a literal id is therefore always a bug, and this
    check reports it as one.
    """
    ids = set(ANY_ID_RE.findall(body_html))
    seen: list[str] = []
    for fragment in ANCHOR_HREF_RE.findall(body_html):
        if fragment not in ids and fragment not in seen:
            seen.append(fragment)
    return seen


def render_toc(headings: list[Heading]) -> str:
    """Render the headings list as a nested <ol> nav block.

    Structure: outer <ol> contains h1 entries; each h1 with following h2s
    becomes an <li> containing a nested <ol> of those h2s. CSS handles
    leader dots and page numbers.
    """
    lines: list[str] = ['<nav id="toc">']
    lines.append('<h2 class="toc-title">Contents</h2>')
    lines.append("<ol>")

    i = 0
    n = len(headings)
    while i < n:
        h = headings[i]
        if h.level != 1:
            # An h2 before any h1 is structurally odd; promote to top level.
            lines.append(
                f'<li class="toc-h2"><a href="#{h.slug}">'
                f'{html_lib.escape(h.text)}</a></li>'
            )
            i += 1
            continue

        # Skip the doc title in the TOC — it's the document name, not a section.
        if i == 0:
            i += 1
            continue

        # Look ahead for h2s in this section
        children: list[Heading] = []
        j = i + 1
        while j < n and headings[j].level == 2:
            children.append(headings[j])
            j += 1

        lines.append(f'<li class="toc-h1"><a href="#{h.slug}">'
                     f'{html_lib.escape(h.text)}</a>')
        if children:
            lines.append("<ol>")
            for c in children:
                lines.append(
                    f'<li class="toc-h2"><a href="#{c.slug}">'
                    f'{html_lib.escape(c.text)}</a></li>'
                )
            lines.append("</ol>")
        lines.append("</li>")
        i = j

    lines.append("</ol>")
    lines.append("</nav>")
    return "\n".join(lines)
