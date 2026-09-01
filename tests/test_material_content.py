"""M12 rich-text sanitisation (nh3) and external-link validation policy.

Pure-function tests -- no app context, no database, no network.
``validate_external_url`` and ``sanitize_rich_text_html`` must never make
a network request (asserted structurally by there being no socket/DNS
import in the module and by these tests running with no network mock).
"""

import pytest

from app.services.material_content import (
    ExternalUrlError,
    sanitize_rich_text_html,
    validate_external_url,
)


# ===========================================================================
# Rich text -- allowed formatting survives
# ===========================================================================


@pytest.mark.parametrize(
    "html",
    [
        "<p>A paragraph.</p>",
        "<h2>Heading two</h2><h3>Heading three</h3>",
        "<p><strong>bold</strong> <em>italic</em> <u>underline</u></p>",
        "<ul><li>one</li><li>two</li></ul>",
        "<ol><li>first</li></ol>",
        "<blockquote>quoted</blockquote>",
        "<pre><code>code block</code></pre>",
        "<p>before</p><hr><p>after</p>",
        '<p><a href="https://example.com/page">link</a></p>',
    ],
)
def test_allowed_formatting_survives(html):
    out = sanitize_rich_text_html(html)
    assert out is not None and out.strip()


def test_https_link_kept_with_safe_rel():
    out = sanitize_rich_text_html('<a href="https://example.com">x</a>')
    assert 'href="https://example.com"' in out
    assert "noopener" in out and "noreferrer" in out and "nofollow" in out


# ===========================================================================
# Rich text -- dangerous content removed / rejected
# ===========================================================================


def test_script_tag_and_content_removed():
    out = sanitize_rich_text_html("<p>ok</p><script>steal()</script>")
    assert "script" not in out and "steal" not in out
    assert "<p>ok</p>" in out


def test_event_handlers_removed():
    out = sanitize_rich_text_html('<p onclick="x()" onmouseover="y()">hi</p>')
    assert "onclick" not in out and "onmouseover" not in out


def test_style_and_class_and_id_removed():
    out = sanitize_rich_text_html('<p style="color:red" class="c" id="i">t</p>')
    assert "style" not in out and 'class="c"' not in out and 'id="i"' not in out


def test_iframe_object_embed_form_removed():
    out = sanitize_rich_text_html(
        "<p>k</p><iframe src='x'></iframe><object></object><embed><form></form>"
    )
    for tag in ("iframe", "object", "embed", "<form"):
        assert tag not in out


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "  javascript:alert(1)",
    ],
)
def test_dangerous_link_schemes_stripped(href):
    out = sanitize_rich_text_html(f'<a href="{href}">x</a>')
    assert "javascript" not in out.lower()
    assert "data:text/html" not in out.lower()
    assert "file:" not in out.lower()
    assert "vbscript" not in out.lower()


def test_relative_urls_denied():
    out = sanitize_rich_text_html('<a href="/internal/path">x</a><a href="../up">y</a>')
    assert 'href="/internal/path"' not in out
    assert 'href="../up"' not in out


def test_http_link_downgraded_href_removed():
    out = sanitize_rich_text_html('<a href="http://insecure.example">x</a>')
    assert "http://insecure.example" not in out


def test_media_tags_not_allowed_in_rich_text():
    out = sanitize_rich_text_html(
        "<p>t</p><img src='https://x/y.png'><audio src='a'></audio><video src='v'></video>"
    )
    assert "<img" not in out and "<audio" not in out and "<video" not in out


@pytest.mark.parametrize(
    "html",
    [
        "",
        "   ",
        None,
        "<script>only()</script>",
        "<style>.x{}</style>",
        "<p>   </p>",
        "<div></div>",
        "<img src=x onerror=alert(1)>",
    ],
)
def test_effectively_empty_content_rejected(html):
    assert sanitize_rich_text_html(html) is None


def test_malformed_html_does_not_crash():
    out = sanitize_rich_text_html("<p><strong>unclosed <em>tags")
    assert out is None or isinstance(out, str)


def test_sanitize_is_idempotent():
    once = sanitize_rich_text_html("<p>Hello <strong>world</strong></p><script>x</script>")
    twice = sanitize_rich_text_html(once)
    assert once == twice


# ===========================================================================
# External link validation
# ===========================================================================


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "https://example.com/path?q=1#frag",
        "https://sub.example.co.uk/a/b",
        "https://8.8.8.8/public",
        "https://example.com:8443/x",
    ],
)
def test_valid_https_urls_accepted(url):
    assert validate_external_url(url) == url


def test_url_is_stripped():
    assert validate_external_url("  https://example.com/x  ") == "https://example.com/x"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "ftp://example.com",
        "//example.com/x",
        "example.com",
        "https://user:pass@example.com",
        "https://user@example.com",
        "https://localhost/x",
        "https://sub.localhost/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://10.1.2.3/x",
        "https://192.168.0.1/x",
        "https://172.16.0.1/x",
        "https://169.254.10.1/x",
        "https://224.0.0.1/x",
        "https://0.0.0.0/x",
        "https://240.0.0.1/x",
        "https:// example.com",
        "https://exa\tmple.com",
        "https://example.com/\x00path",
        "https://example.com/\x01",
        "https:///nohost",
        "",
        "   ",
        None,
    ],
)
def test_invalid_urls_rejected(url):
    with pytest.raises(ExternalUrlError):
        validate_external_url(url)


def test_overlong_url_rejected():
    with pytest.raises(ExternalUrlError):
        validate_external_url("https://example.com/" + "a" * 3000)


def test_no_server_side_fetch_import():
    """The module must not import anything that performs network I/O."""
    from pathlib import Path

    import app.services.material_content as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("import requests", "import socket", "urlopen", "http.client", "urllib.request"):
        assert forbidden not in source
