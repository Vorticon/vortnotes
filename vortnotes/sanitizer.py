"""HTML sanitization for note content.

Notes are stored as HTML emitted by the editor (Quill). We sanitize on save to
prevent XSS while still allowing common formatting and trusted embeds.

This module has no Flask imports so it can be unit-tested easily.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import bleach
from bleach.css_sanitizer import CSSSanitizer

_css_sanitizer = CSSSanitizer(
    allowed_css_properties=[
        "color",
        "background-color",
        "text-align",
        "font-weight",
        "font-style",
        "text-decoration",
        "font-size",
        "font-family",
        "margin",
        "margin-left",
        "margin-right",
        "padding",
        "padding-left",
        "padding-right",
        "border",
        "border-width",
        "border-style",
        "border-color",
        "border-radius",
        "width",
        "height",
        "max-width",
        "min-width",
        "max-height",
        "min-height",
        "display",
        "float",
    ]
)


ALLOWED_TAGS = [
    "p",
    "br",
    "div",
    "span",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "blockquote",
    "pre",
    "code",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "table",
    "colgroup",
    "col",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "a",
    "img",
    "hr",
    # Quill video embeds use iframes (e.g., YouTube).
    "iframe",
]

ALLOWED_ATTRS = {
    "*": ["class", "style"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    # Quill list bullets/numbering are rendered via CSS using these attributes.
    # Without them, lists may appear as plain indented text.
    "li": ["data-list", "data-checked"],
    # Tables
    "table": ["class", "style", "data-vn-border-width", "data-vn-border-style", "data-vn-border-color"],
    "colgroup": ["class", "style"],
    "col": ["class", "style", "width"],
    "tr": ["class", "style"],
    "td": ["class", "style", "colspan", "rowspan"],
    "th": ["class", "style", "colspan", "rowspan"],
    "iframe": [
        "src",
        "width",
        "height",
        "frameborder",
        "allow",
        "allowfullscreen",
        "referrerpolicy",
    ],
}

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _is_safe_img_src(src: str) -> bool:
    if not src:
        return False
    src = src.strip()
    # Allow our own uploads and http(s) images.
    return (
        src.startswith("/uploads/")
        or src.startswith("uploads/")
        or src.startswith("http://")
        or src.startswith("https://")
    )


def _is_safe_iframe_src(src: str) -> bool:
    """Allow only known-safe iframe providers.

    Quill's video embed feature inserts an <iframe class="ql-video" ...>.
    We allow YouTube (and YouTube-nocookie) embeds only.
    """

    if not src:
        return False
    src = src.strip()
    if not (src.startswith("https://") or src.startswith("http://")):
        return False

    try:
        u = urlparse(src)
        host = (u.netloc or "").lower()
        return host.endswith("youtube.com") or host.endswith("youtube-nocookie.com")
    except Exception:
        return False


def _ensure_rel_noopener(a_tag: str) -> str:
    # Add rel="noopener noreferrer" if missing (minimal robust handling).
    if re.search(r"\srel=", a_tag, flags=re.IGNORECASE):
        return a_tag
    return a_tag[:-1] + ' rel="noopener noreferrer">' if a_tag.endswith(">") else a_tag


def sanitize_note_html(html: str) -> str:
    """Sanitize note HTML from the editor."""

    html = html or ""
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=_css_sanitizer,
        strip=True,
    )

    # Post-process: enforce safe targets.
    try:
        cleaned = re.sub(
            r'<a([^>]*?)\starget="\s*_blank\s*"([^>]*)>',
            lambda m: _ensure_rel_noopener(m.group(0)),
            cleaned,
            flags=re.IGNORECASE,
        )
    except Exception:
        pass

    # Remove any img tags with unsafe src (e.g., data:, javascript:).
    def _img_filter(match):
        tag = match.group(0)
        m = re.search(r'src="([^"]+)"', tag, flags=re.IGNORECASE)
        src = m.group(1) if m else ""
        return tag if _is_safe_img_src(src) else ""

    cleaned = re.sub(r"<img\b[^>]*>", _img_filter, cleaned, flags=re.IGNORECASE)

    # Remove any iframe tags with unsafe src.
    def _iframe_filter(match):
        tag = match.group(0)
        m = re.search(r'src="([^"]+)"', tag, flags=re.IGNORECASE)
        src = m.group(1) if m else ""
        return tag if _is_safe_iframe_src(src) else ""

    cleaned = re.sub(r"<iframe\b[^>]*>(?:\s*</iframe>)?", _iframe_filter, cleaned, flags=re.IGNORECASE)

    return cleaned
