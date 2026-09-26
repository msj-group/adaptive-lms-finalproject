"""Structural helpers for negative assertions over random-bearing output.

A rendered page carries opaque random values -- the CSRF token and the signed
state tokens -- and a decoded token payload carries random public UUIDs. A
short marker searched as a raw substring of either ("cvc", "LYD", "sql",
"1250", "/attendance/1") occurs inside such a value by chance, and a correct
page or payload then fails for reasons unrelated to what the test protects.

These helpers let a test search only what the output actually *says*:

* :func:`redact_signed_values` blanks the value of each hidden input that is
  shaped like a signed itsdangerous token, and leaves every other tag,
  attribute, text node, comment and script exactly as rendered. A signed
  token is base64 of its payload, so a word inside it is never a literal
  disclosure; payload content is proven by decoding it, never by scanning
  the page.
* :func:`leaves` and :func:`is_public_id` let a payload be inspected by key
  and by exact value, so a random UUID is recognised as a UUID instead of
  being searched for sensitive-looking digits.
"""

import re
import uuid
from html.parser import HTMLParser

#: An itsdangerous token: an optional leading dot (a zlib-compressed body),
#: URL-safe base64 segments joined by dots, and a final signature segment of
#: at least 27 characters (HMAC-SHA1 is 20 bytes). It has no space, colon,
#: bracket or quote, so a sentence, an error message or a plain value such as
#: "LYD" or "Bob" is never mistaken for one and is still searched.
SIGNED_TOKEN_SHAPE = re.compile(r"\.?(?:[A-Za-z0-9_-]+\.)+[A-Za-z0-9_-]{27,}")

_INPUT_TAG = re.compile(r"<input\b[^>]*>", re.I)
_VALUE_ATTRIBUTE = re.compile(r'\svalue="[^"]*"')


class _Attributes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.attrs = {}

    def handle_starttag(self, tag, attrs):
        self.attrs = dict(attrs)

    handle_startendtag = handle_starttag


def _attributes(tag):
    parser = _Attributes()
    parser.feed(tag)
    parser.close()
    return parser.attrs


def _blank_signed_value(match):
    tag = match.group(0)
    attrs = _attributes(tag)
    value = attrs.get("value") or ""
    if (attrs.get("type") or "").lower() != "hidden":
        return tag
    if not SIGNED_TOKEN_SHAPE.fullmatch(value):
        return tag
    return _VALUE_ATTRIBUTE.sub(' value=""', tag, count=1)


def redact_signed_values(html):
    """`html` with the value of every hidden input holding a signed token
    blanked. Nothing else changes, so every visible word, attribute, link and
    plain hidden value is still searched."""
    return _INPUT_TAG.sub(_blank_signed_value, html)


def leaves(value):
    """Every scalar a decoded payload carries, however deeply nested."""
    if isinstance(value, dict):
        for item in value.values():
            yield from leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from leaves(item)
    else:
        yield value


def is_public_id(value):
    """Whether `value` is exactly a canonical UUID string -- a public id, whose
    random hexadecimal carries no name, amount or internal id."""
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False
