from urllib.parse import urlparse

_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}


def get_safe_redirect_target(candidate):
    """Return `candidate` if it is safe to redirect to, else None.

    Only a same-origin, single-leading-slash relative path is accepted
    (e.g. "/admin/students"). This app never needs to redirect anywhere
    else via a user-supplied value (such as Flask-Login's `next` query
    parameter), so anything that isn't unambiguously that shape -- an
    absolute URL, a scheme-relative "//host/..." URL, a backslash that
    browsers can reinterpret as part of the authority, or embedded control
    characters -- is rejected outright rather than pattern-matched against
    a blocklist of known bypass tricks.
    """
    if not candidate:
        return None
    if any(ch in _CONTROL_CHARS for ch in candidate):
        return None
    if "\\" in candidate:
        return None
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    return candidate
