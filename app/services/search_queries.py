"""Read-only query layer for the Student learning-content search (M13).

Flask-independent: plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring
``app/services/student_lessons.py``. **Every function here is read-only**
-- no locks, no writes.

Design rules (Part M13):

- ALL authorization is expressed in the SQL ``WHERE`` clause, keyed off
  the caller-supplied ``student_id`` -- never "load content broadly and
  filter it in Python". The visibility chain is exactly the M11/M12
  effective-visibility formula: own **active** ``Enrollment`` for the
  Group; active AcademicTerm / Level / Course / Group; active Unit (for
  Unit / Lesson / Material results); **published** Lesson (for Lesson /
  Material results); active Material (for Material results).
- Student search never depends on a ``Schedule`` or a current Teacher
  assignment.
- A Course result is **Group-contextual**: the same Course appears once
  per authorized enrolled Group, because its destination and breadcrumb
  differ.
- Every function returns plain dicts of display strings + public ids --
  no ORM rows, no internal numeric ids, no storage keys / hashes / paths
  / uploader identity / raw rich-text HTML.
- The query count is bounded: one query per requested content type (so
  one when a type filter is set, at most four otherwise), plus the one
  ``authorized_search_groups`` query -- never one query per row.
- Matching is a bounded ``LIKE``/``ILIKE`` (``func.lower(col).like(...)``
  both sides lower-cased for SQLite/MySQL portability); ``%``, ``_`` and
  the escape character are neutralised by
  ``app.services.search_terms.escape_like`` so they match literally.
"""

from sqlalchemy import and_, case, func, or_

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    Lesson,
    LessonStatus,
    Level,
    Material,
    MaterialKind,
    Unit,
    UploadedFile,
)
from app.services.search_terms import escape_like

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_PUBLISHED = LessonStatus.PUBLISHED.value

_ESCAPE = "\\"
_SNIPPET_LIMIT = 160
_SNIPPET_LEAD = 40

_MATERIAL_KIND_BADGE = {
    MaterialKind.RICH_TEXT.value: "Rich text",
    MaterialKind.EXTERNAL_LINK.value: "External link",
    MaterialKind.FILE.value: "File",
}

CONTENT_TYPE_ORDER = ("course", "unit", "lesson", "material")


# ---------------------------------------------------------------------------
# Match / ranking expression helpers
# ---------------------------------------------------------------------------


def _contains(column, token):
    """``lower(column) LIKE '%<token>%'`` with literal ``%`` / ``_`` /
    escape-char handling. ``token`` is already lower-cased by
    :func:`app.services.search_terms.normalize_query`."""
    return func.lower(column).like(f"%{escape_like(token, _ESCAPE)}%", escape=_ESCAPE)


def _match_clause(columns, tokens):
    """AND across query tokens, OR across the authorized fields of one
    result type -- the core "this row matches the search" predicate."""
    return and_(*[or_(*[_contains(col, token) for col in columns]) for token in tokens])


def _rank_expr(query_text, tokens, title_col, keyword_cols=(), code_col=None):
    """Deterministic relevance bucket (lower is better):

    1. exact title/code match;
    2. title/code prefix match;
    3. every token found within title / code / keyword fields;
    4. otherwise (tokens only matched a description or Material metadata
       field -- the ``WHERE`` clause guarantees at least this).
    """
    q_lower = query_text.lower()
    prefix_pattern = f"{escape_like(q_lower, _ESCAPE)}%"

    exact = [func.lower(title_col) == q_lower]
    prefix = [func.lower(title_col).like(prefix_pattern, escape=_ESCAPE)]
    if code_col is not None:
        exact.append(func.lower(code_col) == q_lower)
        prefix.append(func.lower(code_col).like(prefix_pattern, escape=_ESCAPE))

    titleish = [title_col, *([code_col] if code_col is not None else []), *keyword_cols]
    every_token_in_titleish = and_(
        *[or_(*[_contains(col, token) for col in titleish]) for token in tokens]
    )

    return case(
        (or_(*exact), 1),
        (or_(*prefix), 2),
        (every_token_in_titleish, 3),
        else_=4,
    )


def _snippet(candidates, tokens):
    """A short, safe plain-text context string built around the first
    token hit among ``candidates`` (checked in priority order), or a
    truncated fall-back. Returns ``""`` when nothing usable is present.
    The caller's template autoescapes it -- it is never marked safe."""
    for text in candidates:
        if not text:
            continue
        low = text.lower()
        hit = next((low.find(tok) for tok in tokens if tok in low), -1)
        if hit == -1:
            continue
        start = max(0, hit - _SNIPPET_LEAD)
        end = min(len(text), start + _SNIPPET_LIMIT)
        fragment = text[start:end].strip()
        return ("… " if start > 0 else "") + fragment + (" …" if end < len(text) else "")
    for text in candidates:
        if text:
            return text if len(text) <= _SNIPPET_LIMIT else text[:_SNIPPET_LIMIT].rstrip() + " …"
    return ""


def _section(rows, cap, build):
    """Turn ``limit(cap + 1)`` rows into ``{"items": [...], "has_more":
    bool}`` without a separate COUNT query."""
    return {"items": [build(row) for row in rows[:cap]], "has_more": len(rows) > cap}


# ---------------------------------------------------------------------------
# Authorized Groups (filter <select> + group-filter resolution)
# ---------------------------------------------------------------------------


def authorized_search_groups(student_id):
    """Every Group the Student may search within -- their own **active**
    Enrollments joined to an active AcademicTerm / Level / Course / Group
    -- as plain dicts ordered by Group name. Serves both the filter
    drop-down and the resolution of the ``group`` filter value (an
    unknown / unauthorized public id simply is not in this list, so the
    route yields an empty, non-disclosing result)."""
    rows = (
        db.session.query(
            Group.public_id.label("public_id"),
            Group.name.label("name"),
            Course.title.label("course_title"),
            Level.name.label("level_name"),
            AcademicTerm.name.label("term_name"),
        )
        .select_from(Enrollment)
        .join(Group, Enrollment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(
            Enrollment.student_id == student_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
        )
        .order_by(Group.name, Group.public_id)
        .all()
    )
    return [
        {
            "public_id": r.public_id,
            "name": r.name,
            "course_title": r.course_title,
            "level_name": r.level_name,
            "term_name": r.term_name,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Per-type authorized result queries
# ---------------------------------------------------------------------------


def _authorized_base(query, *, group_public_id):
    """Shared enrollment + hierarchy scoping for every result query."""
    query = query.filter(
        Enrollment.status == _ENROLLMENT_ACTIVE,
        Group.status == _ACTIVE,
        Course.status == _ACTIVE,
        Level.status == _ACTIVE,
        AcademicTerm.status == _ACTIVE,
    )
    if group_public_id:
        query = query.filter(Group.public_id == group_public_id)
    return query


def _course_results(student_id, qn, group_public_id, cap):
    rank = _rank_expr(qn.text, qn.tokens, Course.title, code_col=Course.code)
    query = _authorized_base(
        db.session.query(
            Course.title.label("title"),
            Course.description.label("description"),
            Course.code.label("code"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Level.name.label("level_name"),
            AcademicTerm.name.label("term_name"),
        )
        .select_from(Enrollment)
        .join(Group, Enrollment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(
            Enrollment.student_id == student_id,
            _match_clause([Course.title, Course.code, Course.description], qn.tokens),
        ),
        group_public_id=group_public_id,
    )
    rows = query.order_by(
        rank, Course.display_order, Course.id, Group.name, Group.public_id
    ).limit(cap + 1).all()

    def build(row):
        return {
            "type": "course",
            "title": row.title,
            "badge": "Course",
            "breadcrumb": [row.level_name, row.term_name, row.group_name],
            "snippet": _snippet([row.description, row.code], qn.tokens),
            "group_public_id": row.group_public_id,
        }

    return _section(rows, cap, build)


def _unit_results(student_id, qn, group_public_id, cap):
    rank = _rank_expr(
        qn.text, qn.tokens, Unit.title, keyword_cols=(Unit.search_keywords,)
    )
    query = _authorized_base(
        db.session.query(
            Unit.title.label("title"),
            Unit.description.label("description"),
            Unit.search_keywords.label("search_keywords"),
            Unit.public_id.label("unit_public_id"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Course.title.label("course_title"),
            Level.name.label("level_name"),
        )
        .select_from(Enrollment)
        .join(Group, Enrollment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Unit, Unit.group_id == Group.id)
        .filter(
            Enrollment.student_id == student_id,
            Unit.status == _ACTIVE,
            _match_clause(
                [Unit.title, Unit.description, Unit.search_keywords], qn.tokens
            ),
        ),
        group_public_id=group_public_id,
    )
    rows = query.order_by(rank, Unit.display_order, Unit.id).limit(cap + 1).all()

    def build(row):
        return {
            "type": "unit",
            "title": row.title,
            "badge": "Unit",
            "breadcrumb": [row.level_name, row.course_title, row.group_name],
            "snippet": _snippet([row.description, row.search_keywords], qn.tokens),
            "group_public_id": row.group_public_id,
            "unit_public_id": row.unit_public_id,
        }

    return _section(rows, cap, build)


def _lesson_results(student_id, qn, group_public_id, cap):
    rank = _rank_expr(
        qn.text, qn.tokens, Lesson.title, keyword_cols=(Lesson.search_keywords,)
    )
    query = _authorized_base(
        db.session.query(
            Lesson.title.label("title"),
            Lesson.description.label("description"),
            Lesson.search_keywords.label("search_keywords"),
            Lesson.public_id.label("lesson_public_id"),
            Unit.public_id.label("unit_public_id"),
            Unit.title.label("unit_title"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
            Course.title.label("course_title"),
        )
        .select_from(Enrollment)
        .join(Group, Enrollment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Unit, Unit.group_id == Group.id)
        .join(Lesson, Lesson.unit_id == Unit.id)
        .filter(
            Enrollment.student_id == student_id,
            Unit.status == _ACTIVE,
            Lesson.status == _PUBLISHED,
            _match_clause(
                [Lesson.title, Lesson.description, Lesson.search_keywords], qn.tokens
            ),
        ),
        group_public_id=group_public_id,
    )
    rows = (
        query.order_by(rank, Unit.display_order, Lesson.display_order, Lesson.id)
        .limit(cap + 1)
        .all()
    )

    def build(row):
        return {
            "type": "lesson",
            "title": row.title,
            "badge": "Lesson",
            "breadcrumb": [row.course_title, row.group_name, row.unit_title],
            "snippet": _snippet([row.description, row.search_keywords], qn.tokens),
            "group_public_id": row.group_public_id,
            "unit_public_id": row.unit_public_id,
            "lesson_public_id": row.lesson_public_id,
        }

    return _section(rows, cap, build)


def _material_results(student_id, qn, group_public_id, material_kind, cap):
    rank = _rank_expr(
        qn.text, qn.tokens, Material.title, keyword_cols=(Material.search_keywords,)
    )
    search_columns = [
        Material.title,
        Material.search_keywords,
        Material.external_url,
        UploadedFile.original_filename,
    ]
    query = _authorized_base(
        db.session.query(
            Material.title.label("title"),
            Material.kind.label("kind"),
            Material.search_keywords.label("search_keywords"),
            Material.external_url.label("external_url"),
            Material.public_id.label("material_public_id"),
            UploadedFile.original_filename.label("original_filename"),
            Lesson.public_id.label("lesson_public_id"),
            Lesson.title.label("lesson_title"),
            Unit.public_id.label("unit_public_id"),
            Unit.title.label("unit_title"),
            Group.public_id.label("group_public_id"),
            Group.name.label("group_name"),
        )
        .select_from(Enrollment)
        .join(Group, Enrollment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Unit, Unit.group_id == Group.id)
        .join(Lesson, Lesson.unit_id == Unit.id)
        .join(Material, Material.lesson_id == Lesson.id)
        .outerjoin(UploadedFile, Material.uploaded_file_id == UploadedFile.id)
        .filter(
            Enrollment.student_id == student_id,
            Unit.status == _ACTIVE,
            Lesson.status == _PUBLISHED,
            Material.status == _ACTIVE,
            _match_clause(search_columns, qn.tokens),
        ),
        group_public_id=group_public_id,
    )
    if material_kind and material_kind != "all":
        query = query.filter(Material.kind == material_kind)
    rows = (
        query.order_by(rank, Lesson.display_order, Material.display_order, Material.id)
        .limit(cap + 1)
        .all()
    )

    def build(row):
        return {
            "type": "material",
            "title": row.title,
            "kind": row.kind,
            "badge": _MATERIAL_KIND_BADGE.get(row.kind, "Material"),
            "breadcrumb": [row.group_name, row.unit_title, row.lesson_title],
            "snippet": _snippet(
                [row.search_keywords, row.original_filename, row.external_url], qn.tokens
            ),
            "group_public_id": row.group_public_id,
            "unit_public_id": row.unit_public_id,
            "lesson_public_id": row.lesson_public_id,
            "material_public_id": row.material_public_id,
        }

    return _section(rows, cap, build)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def search_learning_content(
    student_id,
    query_norm,
    *,
    content_type="all",
    group_public_id=None,
    material_kind="all",
    cap=20,
):
    """Run the authorized search for the requested content type(s).

    Returns ``{type: {"items": [...], "has_more": bool}}`` containing only
    the requested type when ``content_type`` names one, or all four types
    when it is ``"all"``. The caller is responsible for only invoking this
    once the query is actually searchable (>= 2 chars, has tokens) and the
    ``group`` filter -- if any -- resolved to an authorized Group.
    """
    wanted = (
        CONTENT_TYPE_ORDER if content_type == "all" else (content_type,)
    )
    builders = {
        "course": lambda: _course_results(student_id, query_norm, group_public_id, cap),
        "unit": lambda: _unit_results(student_id, query_norm, group_public_id, cap),
        "lesson": lambda: _lesson_results(student_id, query_norm, group_public_id, cap),
        "material": lambda: _material_results(
            student_id, query_norm, group_public_id, material_kind, cap
        ),
    }
    return {name: builders[name]() for name in CONTENT_TYPE_ORDER if name in wanted}


def has_any_results(results):
    """True when at least one content-type section has an item."""
    return any(section["items"] for section in results.values())
