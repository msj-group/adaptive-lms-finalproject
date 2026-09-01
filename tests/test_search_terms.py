"""M13 pure normalisation helpers -- keyword policy, query policy, and
the LIKE-escape function. No app context / DB needed."""

import pytest

from app.services.search_terms import (
    MAX_CANONICAL_KEYWORDS_LENGTH,
    MAX_KEYWORDS,
    MAX_KEYWORD_LENGTH,
    SearchKeywordsError,
    escape_like,
    normalize_content_type,
    normalize_material_kind,
    normalize_query,
    normalize_search_keywords,
)


# ===========================================================================
# normalize_search_keywords
# ===========================================================================


def test_none_and_blank_return_none():
    assert normalize_search_keywords(None) is None
    assert normalize_search_keywords("") is None
    assert normalize_search_keywords("   \n  , ,  ") is None


def test_comma_and_newline_separators_both_work():
    assert normalize_search_keywords("alpha, beta\ngamma") == "alpha, beta, gamma"
    assert normalize_search_keywords("alpha\r\nbeta") == "alpha, beta"


def test_trim_and_collapse_internal_whitespace():
    assert normalize_search_keywords("  present    perfect  ,  past   simple ") == (
        "present perfect, past simple"
    )


def test_case_insensitive_dedupe_keeps_first_spelling():
    assert normalize_search_keywords("Present Perfect, present perfect, PRESENT PERFECT") == (
        "Present Perfect"
    )


def test_control_characters_rejected():
    with pytest.raises(SearchKeywordsError):
        normalize_search_keywords("good\tkeyword")
    with pytest.raises(SearchKeywordsError):
        normalize_search_keywords("bad\x07bell")


def test_too_many_keywords_rejected():
    ok = ", ".join(f"k{i}" for i in range(MAX_KEYWORDS))
    assert normalize_search_keywords(ok).count(",") == MAX_KEYWORDS - 1
    with pytest.raises(SearchKeywordsError):
        normalize_search_keywords(", ".join(f"k{i}" for i in range(MAX_KEYWORDS + 1)))


def test_keyword_too_long_rejected():
    assert normalize_search_keywords("a" * MAX_KEYWORD_LENGTH) == "a" * MAX_KEYWORD_LENGTH
    with pytest.raises(SearchKeywordsError):
        normalize_search_keywords("a" * (MAX_KEYWORD_LENGTH + 1))


def test_canonical_length_cap_enforced():
    # 20 keywords * 30 chars + separators > 500
    big = ", ".join(("x" * 30 + str(i)) for i in range(MAX_KEYWORDS))
    assert len(big) > MAX_CANONICAL_KEYWORDS_LENGTH
    with pytest.raises(SearchKeywordsError):
        normalize_search_keywords(big)


# ===========================================================================
# normalize_query
# ===========================================================================


def test_query_strip_and_collapse():
    qn = normalize_query("  the   quick  brown  ")
    assert qn.text == "the quick brown"
    assert qn.tokens == ("the", "quick", "brown")
    assert not qn.too_short and qn.is_searchable


def test_query_min_length():
    assert normalize_query("a").too_short is True
    assert normalize_query(" a ").too_short is True
    assert normalize_query("A1").too_short is False
    assert normalize_query("").too_short is True


def test_query_max_length_truncated():
    qn = normalize_query("x" * 250)
    assert len(qn.text) == 100


def test_query_max_eight_tokens():
    qn = normalize_query(" ".join(str(i) for i in range(20)))
    assert len(qn.tokens) == 8


def test_query_tokens_lowercased():
    assert normalize_query("Present PERFECT").tokens == ("present", "perfect")


# ===========================================================================
# escape_like
# ===========================================================================


def test_escape_like_neutralises_wildcards_and_escape_char():
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("x\\y") == "x\\\\y"
    # escape char handled before % / _ so no double-escaping surprise
    assert escape_like("\\%") == "\\\\\\%"


# ===========================================================================
# filter normalisers
# ===========================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("course", "course"),
        ("MATERIAL", "material"),
        ("", "all"),
        (None, "all"),
        ("nonsense", "all"),
        ("' OR 1=1", "all"),
    ],
)
def test_normalize_content_type(raw, expected):
    assert normalize_content_type(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("file", "file"),
        ("Rich_Text", "rich_text"),
        ("", "all"),
        ("weird", "all"),
    ],
)
def test_normalize_material_kind(raw, expected):
    assert normalize_material_kind(raw) == expected
