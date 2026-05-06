"""Apply theme palette + fonts to a python-docx Document's styles.

Strategy:
  - Configure the built-in Word styles ('Normal', 'Heading 1'..'Heading 4',
    'Title', 'Subtitle') so any text written under those style names picks
    up the theme automatically. This keeps Word's outline/TOC features
    working and means the output is semantically-correct .docx.
  - Add a small set of custom styles where Word's built-ins don't fit:
      * 'MdpCodeBlock'   — monospace, theme code-bg shading, accent left rule
      * 'MdpInlineCode'  — character style for inline `code`
      * 'MdpBlockquote'  — italic, indented, accent left rule
      * 'MdpEyebrow'     — small caps sans, used on cover for tags
      * 'MdpCoverTitle'  / 'MdpCoverSubtitle' / 'MdpCoverMeta'
  - Set the document default font in styles.xml so Word renders Normal
    paragraphs with the theme body font without per-run overrides.
  - Set the page background via <w:background> + <w:displayBackgroundShape/>
    in settings.xml; python-docx doesn't expose this directly so we reach
    into the underlying lxml element.
"""

from __future__ import annotations

from docx.document import Document as _Doc
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import nsmap
from docx.shared import Pt, RGBColor, Inches
from lxml import etree

from .docx_helpers import _w, hex_to_ooxml, hex_to_rgb
from .theme_loader import ThemeSelection


W_NS = nsmap["w"]


def _body_font(selection: ThemeSelection) -> str:
    """The font used for body text, picked per family.

    atlas + arcade: serif body. phosphor: mono body (it's a mono-everywhere
    theme). Centralized so apply_theme + _set_default_font can't drift.
    """
    return selection.fonts.mono if selection.name == "phosphor" else selection.fonts.serif


def set_page_background(doc: _Doc, color_hex: str) -> None:
    """Inject <w:background> into the document part + a settings flag.

    Word draws the page background only when settings.xml contains
    <w:displayBackgroundShape/>; without that, the <w:background> color
    sits in the file but never paints. Both halves are required.
    """
    color = hex_to_ooxml(color_hex)

    # 1. document.xml: add <w:background w:color="..."/> as first child of
    #    <w:document> (must come BEFORE <w:body>).
    doc_el = doc.element  # <w:document>
    # Remove any existing background
    for existing in doc_el.findall(_w("background")):
        doc_el.remove(existing)
    bg = etree.SubElement(doc_el, _w("background"))
    bg.set(_w("color"), color)
    # Move it to be the first child (before <w:body>)
    doc_el.remove(bg)
    doc_el.insert(0, bg)

    # 2. settings.xml: add <w:displayBackgroundShape/>
    settings_part = doc.settings.element
    if settings_part.find(_w("displayBackgroundShape")) is None:
        # The flag conventionally goes near the top of <w:settings>.
        flag = etree.Element(_w("displayBackgroundShape"))
        settings_part.insert(0, flag)


def _set_paragraph_shading(pPr: etree._Element, color_hex: str) -> None:
    """Add a <w:shd> child to a paragraph properties element."""
    color = hex_to_ooxml(color_hex)
    # remove existing shading first
    for old in pPr.findall(_w("shd")):
        pPr.remove(old)
    shd = etree.SubElement(pPr, _w("shd"))
    shd.set(_w("val"), "clear")
    shd.set(_w("color"), "auto")
    shd.set(_w("fill"), color)


def _set_paragraph_left_border(pPr: etree._Element, color_hex: str, sz: int = 24) -> None:
    """Add a left rule to a paragraph (used for code blocks + blockquotes).

    sz is in 1/8 of a point (OOXML convention). 24 = 3pt. We use a generous
    value so the accent rule reads at the typical Word zoom levels.
    """
    color = hex_to_ooxml(color_hex)
    pBdr = pPr.find(_w("pBdr"))
    if pBdr is None:
        pBdr = etree.SubElement(pPr, _w("pBdr"))
    for old in pBdr.findall(_w("left")):
        pBdr.remove(old)
    left = etree.SubElement(pBdr, _w("left"))
    left.set(_w("val"), "single")
    left.set(_w("sz"), str(sz))
    left.set(_w("space"), "8")
    left.set(_w("color"), color)


def _ensure_pPr(style_element: etree._Element) -> etree._Element:
    pPr = style_element.find(_w("pPr"))
    if pPr is None:
        pPr = etree.SubElement(style_element, _w("pPr"))
    return pPr


def _ensure_rPr(style_element: etree._Element) -> etree._Element:
    rPr = style_element.find(_w("rPr"))
    if rPr is None:
        rPr = etree.SubElement(style_element, _w("rPr"))
    return rPr


def _set_rfonts(rPr: etree._Element, font_name: str) -> None:
    """Set ascii/hAnsi/cs/eastAsia font on a run-properties element.

    Word's ``<w:rFonts>`` selects per-script font axes:
      - ``ascii``    — codepoints ≤ U+007F
      - ``hAnsi``    — most other Latin/Greek/Cyrillic codepoints
      - ``cs``       — complex script (Arabic, Hebrew, Indic, ...)
      - ``eastAsia`` — CJK ranges
    Setting only ascii/hAnsi/cs leaves CJK falling back to whatever
    Word's east-asian theme font is (typically a default-installed CJK
    face), which breaks consistency for any non-Latin codepoint that
    finds its way into the document. Adding ``eastAsia`` ensures the
    full Unicode coverage uses the theme font.
    """
    for old in rPr.findall(_w("rFonts")):
        rPr.remove(old)
    rfonts = etree.SubElement(rPr, _w("rFonts"))
    rfonts.set(_w("ascii"), font_name)
    rfonts.set(_w("hAnsi"), font_name)
    rfonts.set(_w("cs"), font_name)
    rfonts.set(_w("eastAsia"), font_name)


def _set_default_font(doc: _Doc, body_font: str, body_color: str) -> None:
    """Set the document defaults so Normal-derived styles inherit theme font.

    This writes <w:rPrDefault><w:rPr><w:rFonts .../></w:rPr></w:rPrDefault>
    into styles.xml's <w:docDefaults>, which is the canonical location for
    "every run inherits this unless overridden".
    """
    styles_el = doc.styles.element
    docDefaults = styles_el.find(_w("docDefaults"))
    if docDefaults is None:
        docDefaults = etree.Element(_w("docDefaults"))
        styles_el.insert(0, docDefaults)
    rPrDefault = docDefaults.find(_w("rPrDefault"))
    if rPrDefault is None:
        rPrDefault = etree.SubElement(docDefaults, _w("rPrDefault"))
    rPr = rPrDefault.find(_w("rPr"))
    if rPr is None:
        rPr = etree.SubElement(rPrDefault, _w("rPr"))
    _set_rfonts(rPr, body_font)
    # default size
    for old in rPr.findall(_w("sz")):
        rPr.remove(old)
    sz = etree.SubElement(rPr, _w("sz"))
    sz.set(_w("val"), "22")  # 11pt (sz is half-points)
    # default color
    for old in rPr.findall(_w("color")):
        rPr.remove(old)
    color = etree.SubElement(rPr, _w("color"))
    color.set(_w("val"), hex_to_ooxml(body_color))


def _configure_builtin(doc: _Doc, name: str, *, font: str, size_pt: float,
                       color_hex: str, bold: bool = False, italic: bool = False,
                       all_caps: bool = False, letter_spacing_pt: float | None = None,
                       space_before_pt: float = 0, space_after_pt: float = 6,
                       keep_with_next: bool = False) -> None:
    """Configure an existing Word style (Heading 1, Title, etc.) per theme."""
    style = doc.styles[name]
    style.font.name = font
    style.font.size = Pt(size_pt)
    r, g, b = hex_to_rgb(color_hex)
    style.font.color.rgb = RGBColor(r, g, b)
    style.font.bold = bold
    style.font.italic = italic
    pf = style.paragraph_format
    pf.space_before = Pt(space_before_pt)
    pf.space_after = Pt(space_after_pt)
    pf.keep_with_next = keep_with_next

    # The high-level python-docx API doesn't expose all-caps or character
    # spacing — drop into XML for those.
    rPr = _ensure_rPr(style.element)
    # eastAsia + cs font (the high-level API only sets ascii/hAnsi)
    _set_rfonts(rPr, font)
    if all_caps:
        for old in rPr.findall(_w("caps")):
            rPr.remove(old)
        caps = etree.SubElement(rPr, _w("caps"))
        caps.set(_w("val"), "1")
    if letter_spacing_pt is not None:
        for old in rPr.findall(_w("spacing")):
            rPr.remove(old)
        sp = etree.SubElement(rPr, _w("spacing"))
        # OOXML letter spacing is in twentieths of a point.
        sp.set(_w("val"), str(int(letter_spacing_pt * 20)))


def _add_or_get_style(doc: _Doc, name: str, style_type: WD_STYLE_TYPE):
    if name in [s.name for s in doc.styles]:
        return doc.styles[name]
    return doc.styles.add_style(name, style_type)


def _build_code_block_style(doc: _Doc, selection: ThemeSelection) -> None:
    style = _add_or_get_style(doc, "MdpCodeBlock", WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = doc.styles["Normal"]
    style.font.name = selection.fonts.mono
    style.font.size = Pt(9.5)
    r, g, b = hex_to_rgb(selection.palette.code_text)
    style.font.color.rgb = RGBColor(r, g, b)
    pf = style.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.left_indent = Inches(0.15)

    # Apply font + shading + left rule via raw XML.
    rPr = _ensure_rPr(style.element)
    _set_rfonts(rPr, selection.fonts.mono)
    pPr = _ensure_pPr(style.element)
    _set_paragraph_shading(pPr, selection.palette.code_bg)
    _set_paragraph_left_border(pPr, selection.palette.accent, sz=24)


def _build_inline_code_style(doc: _Doc, selection: ThemeSelection) -> None:
    style = _add_or_get_style(doc, "MdpInlineCode", WD_STYLE_TYPE.CHARACTER)
    style.font.name = selection.fonts.mono
    style.font.size = Pt(10)
    r, g, b = hex_to_rgb(selection.palette.code_text)
    style.font.color.rgb = RGBColor(r, g, b)
    rPr = _ensure_rPr(style.element)
    _set_rfonts(rPr, selection.fonts.mono)
    # subtle shaded background on inline code
    for old in rPr.findall(_w("shd")):
        rPr.remove(old)
    shd = etree.SubElement(rPr, _w("shd"))
    shd.set(_w("val"), "clear")
    shd.set(_w("color"), "auto")
    shd.set(_w("fill"), hex_to_ooxml(selection.palette.code_bg))


def _build_blockquote_style(doc: _Doc, selection: ThemeSelection) -> None:
    style = _add_or_get_style(doc, "MdpBlockquote", WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = doc.styles["Normal"]
    style.font.name = selection.fonts.serif
    style.font.size = Pt(11)
    style.font.italic = True
    r, g, b = hex_to_rgb(selection.palette.ink_soft)
    style.font.color.rgb = RGBColor(r, g, b)
    pf = style.paragraph_format
    pf.left_indent = Inches(0.3)
    pf.space_before = Pt(6)
    pf.space_after = Pt(6)
    rPr = _ensure_rPr(style.element)
    _set_rfonts(rPr, selection.fonts.serif)
    pPr = _ensure_pPr(style.element)
    _set_paragraph_left_border(pPr, selection.palette.accent, sz=24)


def _build_cover_styles(doc: _Doc, selection: ThemeSelection) -> None:
    """Cover page styles — display font, accent, generous spacing."""
    # Title — Word's built-in 'Title' would work but we want explicit control.
    title = _add_or_get_style(doc, "MdpCoverTitle", WD_STYLE_TYPE.PARAGRAPH)
    title.base_style = doc.styles["Normal"]
    title.font.name = selection.fonts.display
    # Per-family title sizing — atlas/arcade are showy, phosphor is small.
    title_size = {"atlas": 36, "phosphor": 26, "arcade": 38}[selection.name]
    title.font.size = Pt(title_size)
    title.font.bold = selection.name != "arcade"  # Audiowide has its own weight
    title.font.italic = selection.name == "atlas"
    r, g, b = hex_to_rgb(selection.palette.ink)
    title.font.color.rgb = RGBColor(r, g, b)
    title.paragraph_format.space_before = Pt(0)
    title.paragraph_format.space_after = Pt(8)
    rPr = _ensure_rPr(title.element)
    _set_rfonts(rPr, selection.fonts.display)

    sub = _add_or_get_style(doc, "MdpCoverSubtitle", WD_STYLE_TYPE.PARAGRAPH)
    sub.base_style = doc.styles["Normal"]
    sub.font.name = selection.fonts.serif if selection.name == "atlas" else selection.fonts.sans
    sub.font.size = Pt(13)
    sub.font.italic = selection.name in ("atlas", "phosphor")
    r, g, b = hex_to_rgb(selection.palette.ink_soft if selection.name != "arcade" else selection.palette.accent_alt)
    sub.font.color.rgb = RGBColor(r, g, b)
    sub.paragraph_format.space_before = Pt(0)
    sub.paragraph_format.space_after = Pt(24)
    rPr = _ensure_rPr(sub.element)
    _set_rfonts(rPr, sub.font.name)

    meta = _add_or_get_style(doc, "MdpCoverMeta", WD_STYLE_TYPE.PARAGRAPH)
    meta.base_style = doc.styles["Normal"]
    meta.font.name = selection.fonts.sans
    meta.font.size = Pt(10)
    r, g, b = hex_to_rgb(selection.palette.ink_soft)
    meta.font.color.rgb = RGBColor(r, g, b)
    meta.paragraph_format.space_before = Pt(0)
    meta.paragraph_format.space_after = Pt(4)
    rPr = _ensure_rPr(meta.element)
    _set_rfonts(rPr, selection.fonts.sans)
    # uppercase + letter-spacing for atlas/arcade meta (matches cover.css)
    if selection.name in ("atlas", "arcade"):
        caps = etree.SubElement(rPr, _w("caps"))
        caps.set(_w("val"), "1")
        sp = etree.SubElement(rPr, _w("spacing"))
        sp.set(_w("val"), "20")  # 1pt letter spacing


def apply_theme(doc: _Doc, selection: ThemeSelection) -> None:
    """Apply all theme styling to a freshly-opened Document.

    Order matters: page background must come before any content is added,
    style configuration must come after default-font setup so individual
    styles override the doc default rather than the other way around.
    """
    if selection.palette is None or selection.fonts is None:
        raise ValueError(
            "apply_theme requires a themed ThemeSelection (palette and/or "
            "fonts is None — likely the 'default' theme was passed in; "
            "DOCX needs a real theme)."
        )

    set_page_background(doc, selection.palette.bg)
    _set_default_font(doc, _body_font(selection), selection.palette.ink)

    # Built-ins. Heading 1 picks up display font + accent color underline
    # (the underline is added per-paragraph since we want it only on H1s,
    # not in the style definition).
    _configure_builtin(doc, "Title",
                       font=selection.fonts.display, size_pt=24,
                       color_hex=selection.palette.ink, bold=True,
                       italic=selection.name == "atlas",
                       space_after_pt=12)
    _configure_builtin(doc, "Heading 1",
                       font=selection.fonts.display, size_pt=22,
                       color_hex=selection.palette.accent if selection.name == "atlas" else selection.palette.ink,
                       bold=selection.name != "arcade",
                       italic=False,
                       space_before_pt=18, space_after_pt=6,
                       keep_with_next=True,
                       letter_spacing_pt=-0.2 if selection.name == "atlas" else None)
    _configure_builtin(doc, "Heading 2",
                       font=selection.fonts.display, size_pt=16,
                       color_hex=selection.palette.ink,
                       bold=selection.name != "arcade",
                       space_before_pt=14, space_after_pt=4,
                       keep_with_next=True)
    _configure_builtin(doc, "Heading 3",
                       font=selection.fonts.sans, size_pt=12.5,
                       color_hex=selection.palette.accent if selection.name == "phosphor" else selection.palette.ink,
                       bold=True,
                       all_caps=selection.name == "atlas",
                       letter_spacing_pt=0.6 if selection.name == "atlas" else None,
                       space_before_pt=10, space_after_pt=2,
                       keep_with_next=True)
    _configure_builtin(doc, "Heading 4",
                       font=selection.fonts.sans, size_pt=11,
                       color_hex=selection.palette.ink_soft, bold=True, italic=True,
                       space_before_pt=8, space_after_pt=2,
                       keep_with_next=True)

    # Normal — body font, body color, comfortable line spacing.
    body_font = _body_font(selection)
    normal = doc.styles["Normal"]
    normal.font.name = body_font
    normal.font.size = Pt(10.5)
    r, g, b = hex_to_rgb(selection.palette.ink)
    normal.font.color.rgb = RGBColor(r, g, b)
    normal.paragraph_format.line_spacing = 1.35
    normal.paragraph_format.space_after = Pt(8)
    rPr = _ensure_rPr(normal.element)
    _set_rfonts(rPr, body_font)

    _build_code_block_style(doc, selection)
    _build_inline_code_style(doc, selection)
    _build_blockquote_style(doc, selection)
    _build_cover_styles(doc, selection)
