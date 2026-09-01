"""Server-side content policy for Rich Text and External Link Materials
(M12, sections 8-9).

Flask-independent: pure functions, no request/flash/template concerns.
Both functions are read-only/pure -- neither performs any network I/O.
"""

import ipaddress
import re
from html import unescape
from urllib.parse import urlsplit

import nh3

# ---------------------------------------------------------------------------
# Rich text -- server-side sanitisation via nh3 (never a hand-rolled cleaner)
# ---------------------------------------------------------------------------

#: Small, explicit allowlist: ordinary text structure, headings, emphasis,
#: lists, blockquotes, code/preformatted text, horizontal rules, and links.
#: Deliberately excludes img/audio/video/iframe/object/embed/table/form --
#: images, audio, and video are separate `file` Materials, never rich-text
#: embeds.
RICH_TEXT_ALLOWED_TAGS = {
    "p", "br", "h2", "h3", "strong", "b", "em", "i", "u",
    "ul", "ol", "li", "blockquote", "code", "pre", "hr", "a",
}

#: Only `href` on `<a>` -- no style, id, class, or event-handler attribute
#: on any tag.
RICH_TEXT_ALLOWED_ATTRIBUTES = {"a": {"href"}}

#: Tag *and* its content are removed entirely -- these never leave
#: harmless leftover text behind.
RICH_TEXT_CLEAN_CONTENT_TAGS = {
    "script", "style", "iframe", "object", "embed", "form", "noscript", "svg", "template",
}

_TAG_RE = re.compile(r"<[^>]+>")
_NBSP_RE = re.compile(r"&nbsp;| ", re.IGNORECASE)


def _is_effectively_empty(sanitized_html):
    """True if `sanitized_html` carries no visible text and no
    stand-alone structural element (a lone `<hr>`)."""
    if "<hr" in sanitized_html:
        return False
    text = _NBSP_RE.sub(" ", sanitized_html)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    return not text.strip()


def sanitize_rich_text_html(raw_html):
    """Sanitise `raw_html` with `nh3` against the explicit allowlist above.

    Returns the sanitised HTML, or ``None`` if `raw_html` is empty/None or
    becomes effectively empty after sanitisation (e.g. it was only
    scripts/styles/disallowed tags) -- the caller must treat ``None`` as a
    rejected submission, never as "store nothing".

    Only the sanitised result is ever persisted or rendered; this same
    function is called again at the rendering boundary as defense in
    depth (Part M12 section 8) before the result is explicitly marked
    safe for the template.
    """
    if not raw_html or not raw_html.strip():
        return None
    cleaned = nh3.clean(
        raw_html,
        tags=RICH_TEXT_ALLOWED_TAGS,
        clean_content_tags=RICH_TEXT_CLEAN_CONTENT_TAGS,
        attributes=RICH_TEXT_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer nofollow",
        url_schemes={"https"},
        url_relative="deny",
    )
    if _is_effectively_empty(cleaned):
        return None
    return cleaned


# ---------------------------------------------------------------------------
# External links -- structural validation only, never a server-side fetch
# ---------------------------------------------------------------------------

_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}
_LOCALHOST_NAMES = {"localhost", "localhost.localdomain"}
_MAX_URL_LENGTH = 2048


class ExternalUrlError(ValueError):
    """`raw_url` failed the External Link Material validation policy."""


def validate_external_url(raw_url):
    """Validate an External Link Material's URL and return the cleaned,
    stripped string on success.

    Raises :class:`ExternalUrlError` with a human-readable reason on the
    first failed check. Deliberately performs **no** network I/O: no DNS
    resolution, no HTTP fetch, no redirect-following, no preview
    generation -- only structural parsing of the URL string itself.
    """
    if raw_url is None:
        raise ExternalUrlError("A URL is required.")
    url = raw_url.strip()
    if not url:
        raise ExternalUrlError("A URL is required.")
    if len(url) > _MAX_URL_LENGTH:
        raise ExternalUrlError("URL is too long.")
    if any(ch in _CONTROL_CHARS for ch in url):
        raise ExternalUrlError("URL contains control characters.")
    if any(ch.isspace() for ch in url):
        raise ExternalUrlError("URL must not contain whitespace.")

    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port  # validates the port is a parseable integer
    except ValueError as exc:
        raise ExternalUrlError("Malformed URL.") from exc

    if parts.scheme.lower() != "https":
        raise ExternalUrlError("Only HTTPS links are allowed.")
    if parts.username or parts.password:
        raise ExternalUrlError("URL must not contain a username or password.")
    if not host:
        raise ExternalUrlError("URL must have a valid host.")
    if host.lower() in _LOCALHOST_NAMES or host.lower().endswith(".localhost"):
        raise ExternalUrlError("Links to localhost are not allowed.")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    ):
        raise ExternalUrlError(
            "Links to loopback, private, link-local, multicast, unspecified, or reserved "
            "addresses are not allowed."
        )
    del port  # parsed only to force validation; not otherwise needed here

    return url
