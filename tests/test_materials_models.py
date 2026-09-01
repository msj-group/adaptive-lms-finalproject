"""M12 models + migration shape: UploadedFile / Material / FileAccessLog
constraints, indexes, payload exclusivity by kind, title uniqueness
(incl. archived), public-id uniqueness, non-cascading FKs, and the
lifecycle/ordering column rules.

SQLite enforces CHECK / UNIQUE / (with the project PRAGMA) FKs, so these
model-level tests are meaningful for application logic. They do not prove
MySQL/InnoDB behaviour -- the M12 report covers the real-MySQL migration
verification separately.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    FileAccessLog,
    Group,
    Lesson,
    LessonStatus,
    Level,
    Material,
    MaterialKind,
    Unit,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password


def _lesson(title="Lesson 1"):
    term = AcademicTerm(name=f"T {title}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    db.session.add(term)
    level = Level(name=f"L {title}", display_order=0)
    db.session.add(level)
    db.session.commit()
    course = Course(title=f"C {title}", level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=f"G {title}", capacity=20)
    db.session.add(group)
    db.session.commit()
    unit = Unit(group_id=group.id, title=f"U {title}", display_order=0)
    db.session.add(unit)
    db.session.commit()
    lesson = Lesson(unit_id=unit.id, title=title, display_order=0, status=LessonStatus.DRAFT.value)
    db.session.add(lesson)
    db.session.commit()
    return lesson


def _uploader():
    u = User(
        email=f"up{db.session.query(User).count()}@example.com",
        password_hash=hash_password("Sup3rSecret!123"),
        full_name="Uploader",
        role=UserRole.TEACHER.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(u)
    db.session.commit()
    return u


def _uploaded_file(uploader, key="k-1", commit=True):
    f = UploadedFile(
        storage_key=key,
        original_filename="doc.pdf",
        extension="pdf",
        category="document",
        content_type="application/pdf",
        byte_size=10,
        sha256="0" * 64,
        uploaded_by_id=uploader.id,
    )
    db.session.add(f)
    if commit:
        db.session.commit()
    return f


def _material(lesson, title="M1", kind=MaterialKind.RICH_TEXT.value, display_order=1,
              status=AcademicStatus.ACTIVE.value, nonce=None, commit=True, **payload):
    nonce = nonce or f"nonce-{title}-{display_order}"
    kwargs = dict(
        lesson_id=lesson.id, title=title, kind=kind, display_order=display_order,
        status=status, creation_nonce=nonce,
    )
    if kind == MaterialKind.RICH_TEXT.value:
        kwargs["content_html"] = payload.get("content_html", "<p>x</p>")
    elif kind == MaterialKind.EXTERNAL_LINK.value:
        kwargs["external_url"] = payload.get("external_url", "https://example.com/x")
    elif kind == MaterialKind.FILE.value:
        kwargs["uploaded_file_id"] = payload["uploaded_file_id"]
    m = Material(**kwargs)
    db.session.add(m)
    if commit:
        db.session.commit()
    return m


# ===========================================================================
# UploadedFile
# ===========================================================================


def test_uploaded_file_defaults_and_relationship(app):
    with app.app_context():
        up = _uploader()
        f = _uploaded_file(up)
        assert f.id is not None and f.public_id is not None
        assert f.created_at is not None
        assert f.uploaded_by.id == up.id


def test_uploaded_file_positive_size_check(app):
    with app.app_context():
        up = _uploader()
        f = _uploaded_file(up, commit=False)
        f.byte_size = 0
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_uploaded_file_invalid_category_rejected_by_model(app):
    with app.app_context():
        up = _uploader()
        with pytest.raises(ValueError):
            UploadedFile(
                storage_key="x", original_filename="x", extension="pdf", category="exe",
                content_type="application/pdf", byte_size=1, sha256="0" * 64, uploaded_by_id=up.id,
            )


def test_uploaded_file_storage_key_unique(app):
    with app.app_context():
        up = _uploader()
        _uploaded_file(up, key="dup")
        _uploaded_file(up, key="dup", commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_uploaded_file_fk_to_user_non_cascading(app):
    with app.app_context():
        up = _uploader()
        _uploaded_file(up)
        db.session.delete(up)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ===========================================================================
# Material -- payload exclusivity by kind
# ===========================================================================


def test_rich_text_requires_only_content_html(app):
    with app.app_context():
        lesson = _lesson()
        m = _material(lesson, kind=MaterialKind.RICH_TEXT.value)
        assert m.content_html == "<p>x</p>" and m.external_url is None and m.uploaded_file_id is None


def test_external_link_requires_only_external_url(app):
    with app.app_context():
        lesson = _lesson()
        m = _material(lesson, kind=MaterialKind.EXTERNAL_LINK.value)
        assert m.external_url and m.content_html is None and m.uploaded_file_id is None


def test_file_requires_only_uploaded_file(app):
    with app.app_context():
        lesson = _lesson()
        up = _uploader()
        f = _uploaded_file(up)
        m = _material(lesson, kind=MaterialKind.FILE.value, uploaded_file_id=f.id)
        assert m.uploaded_file_id == f.id and m.content_html is None and m.external_url is None


def test_rich_text_with_extra_payload_rejected(app):
    with app.app_context():
        lesson = _lesson()
        m = Material(
            lesson_id=lesson.id, title="Bad", kind=MaterialKind.RICH_TEXT.value,
            content_html="<p>x</p>", external_url="https://example.com", display_order=1,
            status="active", creation_nonce="bad-1",
        )
        db.session.add(m)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_external_link_without_url_rejected(app):
    with app.app_context():
        lesson = _lesson()
        m = Material(
            lesson_id=lesson.id, title="Bad", kind=MaterialKind.EXTERNAL_LINK.value,
            display_order=1, status="active", creation_nonce="bad-2",
        )
        db.session.add(m)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_file_without_uploaded_file_rejected(app):
    with app.app_context():
        lesson = _lesson()
        m = Material(
            lesson_id=lesson.id, title="Bad", kind=MaterialKind.FILE.value,
            display_order=1, status="active", creation_nonce="bad-3",
        )
        db.session.add(m)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_material_invalid_kind_rejected_by_model(app):
    with app.app_context():
        lesson = _lesson()
        with pytest.raises(ValueError):
            Material(
                lesson_id=lesson.id, title="X", kind="video_embed", display_order=1,
                status="active", creation_nonce="k-x",
            )


def test_material_invalid_status_rejected_by_model(app):
    with app.app_context():
        lesson = _lesson()
        with pytest.raises(ValueError):
            Material(
                lesson_id=lesson.id, title="X", kind=MaterialKind.RICH_TEXT.value,
                content_html="<p>x</p>", display_order=1, status="draft", creation_nonce="k-y",
            )


# ===========================================================================
# Material -- constraints
# ===========================================================================


def test_material_title_unique_within_lesson_including_archived(app):
    with app.app_context():
        lesson = _lesson()
        _material(lesson, title="Same", status=AcademicStatus.ARCHIVED.value, display_order=1)
        _material(lesson, title="Same", display_order=2, commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_same_material_title_allowed_in_different_lessons(app):
    with app.app_context():
        l1 = _lesson("L1")
        l2 = _lesson("L2")
        _material(l1, title="Shared", nonce="s-1")
        _material(l2, title="Shared", nonce="s-2")
        assert Material.query.filter_by(title="Shared").count() == 2


def test_material_display_order_must_be_positive(app):
    with app.app_context():
        lesson = _lesson()
        _material(lesson, display_order=0, commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_material_public_id_unique(app):
    with app.app_context():
        lesson = _lesson()
        a = _material(lesson, title="A", display_order=1, nonce="p-1")
        b = _material(lesson, title="B", display_order=2, nonce="p-2", commit=False)
        b.public_id = a.public_id
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_material_creation_nonce_unique(app):
    with app.app_context():
        lesson = _lesson()
        _material(lesson, title="A", display_order=1, nonce="same-nonce")
        _material(lesson, title="B", display_order=2, nonce="same-nonce", commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_material_uploaded_file_id_unique(app):
    with app.app_context():
        lesson = _lesson()
        up = _uploader()
        f = _uploaded_file(up)
        _material(lesson, title="A", kind=MaterialKind.FILE.value, uploaded_file_id=f.id, display_order=1, nonce="u-1")
        _material(lesson, title="B", kind=MaterialKind.FILE.value, uploaded_file_id=f.id, display_order=2, nonce="u-2", commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_material_fk_to_lesson_non_cascading(app):
    with app.app_context():
        lesson = _lesson()
        _material(lesson)
        db.session.delete(lesson)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_archiving_material_keeps_file_row(app):
    with app.app_context():
        lesson = _lesson()
        up = _uploader()
        f = _uploaded_file(up)
        m = _material(lesson, kind=MaterialKind.FILE.value, uploaded_file_id=f.id)
        m.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        assert db.session.get(UploadedFile, f.id) is not None
        assert db.session.get(Material, m.id).uploaded_file_id == f.id


# ===========================================================================
# FileAccessLog
# ===========================================================================


def test_file_access_log_defaults_and_relationship(app):
    with app.app_context():
        up = _uploader()
        f = _uploaded_file(up)
        log = FileAccessLog(uploaded_file_id=f.id, actor_id=up.id, action="upload")
        db.session.add(log)
        db.session.commit()
        assert log.occurred_at is not None
        assert log.uploaded_file.id == f.id and log.actor.id == up.id


def test_file_access_log_invalid_action_rejected(app):
    with app.app_context():
        up = _uploader()
        f = _uploaded_file(up)
        with pytest.raises(ValueError):
            FileAccessLog(uploaded_file_id=f.id, actor_id=up.id, action="delete")


def test_file_access_log_no_ip_or_user_agent_columns(app):
    with app.app_context():
        cols = {c.name for c in FileAccessLog.__table__.columns}
        assert "ip_address" not in cols and "user_agent" not in cols
        assert cols == {"id", "uploaded_file_id", "actor_id", "action", "occurred_at"}


def test_file_access_log_fks_non_cascading(app):
    with app.app_context():
        up = _uploader()
        f = _uploaded_file(up)
        db.session.add(FileAccessLog(uploaded_file_id=f.id, actor_id=up.id, action="inline"))
        db.session.commit()
        db.session.delete(f)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ===========================================================================
# Migration / table shape
# ===========================================================================


def test_m12_tables_shape(app):
    with app.app_context():
        insp = inspect(db.engine)

        uf_cols = {c["name"] for c in insp.get_columns("uploaded_files")}
        assert uf_cols == {
            "id", "public_id", "storage_key", "original_filename", "extension",
            "category", "content_type", "byte_size", "sha256", "uploaded_by_id", "created_at",
        }
        assert insp.get_foreign_keys("uploaded_files")[0]["referred_table"] == "users"

        mat_cols = {c["name"] for c in insp.get_columns("materials")}
        assert mat_cols == {
            "id", "public_id", "lesson_id", "title", "kind", "content_html", "external_url",
            "uploaded_file_id", "status", "display_order", "creation_nonce",
            "search_keywords",  # M13
            "created_at", "updated_at",
        }
        mat_fks = {(f["referred_table"], tuple(f["constrained_columns"])) for f in insp.get_foreign_keys("materials")}
        assert ("lessons", ("lesson_id",)) in mat_fks
        assert ("uploaded_files", ("uploaded_file_id",)) in mat_fks
        mat_uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("materials")}
        assert ("lesson_id", "title") in mat_uniques
        mat_indexes = {tuple(i["column_names"]) for i in insp.get_indexes("materials")}
        assert {("lesson_id",), ("status",), ("display_order",)}.issubset(mat_indexes)
        mat_checks = " ".join(c["sqltext"] for c in insp.get_check_constraints("materials"))
        assert "display_order >= 1" in mat_checks
        assert "kind" in mat_checks and "content_html" in mat_checks

        fal_cols = {c["name"] for c in insp.get_columns("file_access_logs")}
        assert fal_cols == {"id", "uploaded_file_id", "actor_id", "action", "occurred_at"}
        fal_indexes = {tuple(i["column_names"]) for i in insp.get_indexes("file_access_logs")}
        assert ("uploaded_file_id",) in fal_indexes
        assert ("actor_id",) in fal_indexes
        assert ("occurred_at",) in fal_indexes


def test_lesson_materials_relationship(app):
    with app.app_context():
        lesson = _lesson()
        _material(lesson, title="A", display_order=1, nonce="r-1")
        _material(lesson, title="B", display_order=2, nonce="r-2")
        assert {m.title for m in db.session.get(Lesson, lesson.id).materials} == {"A", "B"}


def test_no_schema_change_to_existing_tables(app):
    """M12 added no column to `lessons` / `units` (only the ORM
    `Lesson.materials` relationship). M13 then adds exactly one nullable
    `search_keywords` column to each -- and nothing else."""
    with app.app_context():
        insp = inspect(db.engine)
        lesson_cols = {c["name"] for c in insp.get_columns("lessons")}
        assert lesson_cols == {
            "id", "public_id", "unit_id", "title", "description",
            "search_keywords",  # M13
            "display_order", "status", "published_at", "created_at", "updated_at",
        }
        unit_cols = {c["name"] for c in insp.get_columns("units")}
        assert unit_cols == {
            "id", "public_id", "group_id", "title", "description",
            "search_keywords",  # M13
            "display_order", "status", "created_at", "updated_at",
        }
