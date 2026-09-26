"""Phase 6 / M01S -- the structural helpers behind the negative assertions.

Collision-shaped opaque values are injected on purpose: a signed token whose
random base64 happens to spell a forbidden marker must not fail a correct
page, and a real disclosure must still be found.
"""

import uuid

from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer

from tests.structural_checks import (
    SIGNED_TOKEN_SHAPE,
    is_public_id,
    leaves,
    redact_signed_values,
)

_SIGNATURE = "Q" * 27


def _hidden(name, value):
    return f'<input type="hidden" name="{name}" value="{value}">'


def test_real_signed_tokens_have_the_signed_shape():
    body = {"purpose": "p", "public_id": str(uuid.uuid4()), "version": 3}
    for serializer in (URLSafeSerializer("k" * 32, salt="s"),
                       URLSafeTimedSerializer("k" * 32, salt="s")):
        assert SIGNED_TOKEN_SHAPE.fullmatch(serializer.dumps(body))
        assert SIGNED_TOKEN_SHAPE.fullmatch(serializer.dumps(dict(body, pad="x" * 400)))


def test_a_marker_inside_a_signed_hidden_value_is_blanked():
    for marker in ("LYD", "Bob", "sql", "cvc", "pin", "Q20"):
        token = f"eyJ{marker}x.a{marker}Q.{marker}{_SIGNATURE}"
        page = f"<main>Saved.</main>{_hidden('csrf_token', token)}"
        assert marker in page
        redacted = redact_signed_values(page)
        assert marker not in redacted, marker
        assert redacted == f"<main>Saved.</main>{_hidden('csrf_token', '')}"


def test_what_the_page_says_is_never_blanked():
    token = f"abc.def.{_SIGNATURE}"
    leaks = (
        "<p>Total 12.500 LYD</p>",
        _hidden("note", "LYD"),
        _hidden("note", "Bob"),
        _hidden("error", "[SQL: INSERT INTO courses]"),
        f'<input type="text" name="csrf_token" value="{token}">',
        f'<a href="/x?state={token}">Open</a>',
        f"<!-- {token} -->",
    )
    for leak in leaks:
        assert redact_signed_values(leak) == leak, leak


def test_a_public_id_is_recognised_exactly():
    public_id = str(uuid.UUID("12504321-abcd-4ef0-8abc-000000001250"))
    assert "1250" in public_id and "4321" in public_id
    assert is_public_id(public_id)
    for value in ("1250", "4321.5", public_id.upper(), public_id + "x", 7, None,
                  public_id.replace("-", "")):
        assert not is_public_id(value), value


def test_leaves_walks_every_nested_value():
    payload = {"a": 1, "b": ["x", ["y", 2]], "c": {"d": None, "e": ("z",)}}
    assert list(leaves(payload)) == [1, "x", "y", 2, None, "z"]
