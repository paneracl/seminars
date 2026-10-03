"""
Statement rendering.

Markdown in, safe HTML out, with maths left alone for KaTeX to handle in the
browser.

Two things need care:

  * Maths must survive Markdown. `$a_i \\le 10^5$` contains underscores and
    backslashes that Markdown would happily turn into emphasis and escapes,
    producing silent nonsense. So maths spans are pulled out before rendering
    and put back afterwards.

  * Statements are written by teachers, not strangers, but they are still the
    one place where free-form HTML reaches every student's browser. Sanitising
    is done with `bleach` -- a maintained, widely-audited library -- rather
    than a hand-rolled parser, because a home-grown HTML sanitiser is exactly
    the kind of thing that looks right and has a hole in it.
"""

from __future__ import annotations

import re

import bleach
import markdown

# Inline and display maths, in the order they must be matched.
_MATH_PATTERNS = [
    (re.compile(r"\$\$(.+?)\$\$", re.DOTALL), "$$", "$$"),
    (re.compile(r"\\\((.+?)\\\)", re.DOTALL), r"\(", r"\)"),
    (re.compile(r"\\\[(.+?)\\\]", re.DOTALL), r"\[", r"\]"),
    (re.compile(r"(?<![\\$])\$(?!\s)(.+?)(?<!\s)(?<!\\)\$(?!\$)", re.DOTALL), "$", "$"),
]

_PLACEHOLDER = "xMATHPLACEHOLDERx{}x"

ALLOWED_TAGS = [
    "p", "br", "hr", "div", "span",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "sub", "sup", "small", "mark",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "pre", "code", "kbd", "samp", "var",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "colgroup", "col",
    "a", "img", "figure", "figcaption",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height"],
    "th": ["colspan", "rowspan", "align"],
    "td": ["colspan", "rowspan", "align"],
    "col": ["span", "width"],
    "code": ["class"],
    "div": ["class"],
    "span": ["class"],
    "p": ["class"],
    "table": ["class"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _protect_math(text: str) -> tuple[str, list[str]]:
    stash: list[str] = []

    def stash_one(match, opener, closer):
        stash.append(f"{opener}{match.group(1)}{closer}")
        return _PLACEHOLDER.format(len(stash) - 1)

    for pattern, opener, closer in _MATH_PATTERNS:
        text = pattern.sub(lambda m, o=opener, c=closer: stash_one(m, o, c), text)
    return text, stash


def _restore_math(html: str, stash: list[str]) -> str:
    for index, snippet in enumerate(stash):
        html = html.replace(_PLACEHOLDER.format(index), snippet)
    return html


def sanitise(html: str) -> str:
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,          # drop disallowed tags rather than escaping them
        strip_comments=True,
    )


def render_statement(text: str, body_format: str = "md") -> str:
    """Render a statement body to safe HTML with maths left for KaTeX."""
    if not text or not text.strip():
        return ""

    protected, stash = _protect_math(text)

    if body_format == "html":
        html = protected
    else:
        html = markdown.markdown(
            protected,
            extensions=["extra", "sane_lists", "nl2br"],
            output_format="html",
        )

    html = sanitise(html)
    return _restore_math(html, stash)
