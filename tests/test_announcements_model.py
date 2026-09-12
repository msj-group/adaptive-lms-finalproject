"""Phase 4 / M09 -- the Announcement aggregate itself.

Model-level checks only: the closed enums, the scope/target rule, the
state/timestamp truth table, the positive version, the indexes, the
absence of any cascade, and the fact that nothing here carries a
recipient, a score, an attachment or a schedule.

Every constraint is driven at the **database**, not only at the
``@validates`` guard, because the CHECK is the final defense and a model
guard that silently diverged from it would be exactly the bug worth
catching. SQLite enforces CHECK constraints, so these are real
assertions on the test backend; the same expressions are verified against
real MySQL separately (``tests/test_announcement_migration.py`` and the
authorized development database).
"""

import pytest
import sqlalchemy as sa

import tests.announcement_fixtures as fx
from app.extensions import db
from app.models import (
    ANNOUNCEMENT_BODY_MAX_LENGTH,
    ANNOUNCEMENT_TITLE_MAX_LENGTH,
    Announcement,
    AnnouncementScope,
    AnnouncementStatus,
    Course,
    Group,
    User,
    UserRole,
)

_CENTER = AnnouncementScope.CENTER.value
_COURSE = AnnouncementScope.COURSE.value
_GROUP = AnnouncementScope.GROUP.value
_DRAFT = AnnouncementStatus.DRAFT.value
_PUBLISHED = AnnouncementStatus.PUBLISHED.value
_WITHDRAWN = AnnouncementStatus.WITHDRAWN.value


def _author():
    return fx.user("author@example.com", UserRole.ADMINISTRATOR.value)


def _raw_insert(**values):
    """Insert straight through Core, bypassing the ORM's ``@validates``
    guards, so the database CHECK is what answers."""
    payload = {
        "public_id": f"probe-{fx._next()}",
        "scope": _CENTER,
        "course_id": None,
        "group_id": None,
        "title": "T",
        "body": "B",
        "status": _DRAFT,
        "published_at": None,
        "withdrawn_at": None,
        "version": 1,
        "created_at": fx.NOW,
        "updated_at": fx.NOW,
    }
    payload.update(values)
    db.session.execute(sa.insert(Announcement.__table__).values(**payload))
    db.session.commit()


# ===========================================================================
# The two closed sets
# ===========================================================================


def test_the_scope_enum_has_exactly_three_members():
    assert [s.value for s in AnnouncementScope] == ["center", "course", "group"]


def test_the_status_enum_has_exactly_three_members():
    assert [s.value for s in AnnouncementStatus] == ["draft", "published", "withdrawn"]


def test_every_approved_scope_and_status_is_accepted(app):
    with app.app_context():
        author = _author()
        group = fx.hierarchy("A")
        for scope, course_target, group_target in (
            (_CENTER, None, None),
            (_COURSE, db.session.get(Course, group.course_id), None),
            (_GROUP, None, group),
        ):
            for status in (_DRAFT, _PUBLISHED, _WITHDRAWN):
                row = fx.announcement(
                    author,
                    scope=scope,
                    target_course=course_target,
                    target_group=group_target,
                    status=status,
                )
                assert row.id is not None


@pytest.mark.parametrize("bad", ["level", "user", "everyone", "", "CENTER"])
def test_the_model_refuses_an_unknown_scope(app, bad):
    with app.app_context():
        author = _author()
        with pytest.raises(ValueError):
            Announcement(author_id=author.id, scope=bad, title="T", body="B")


@pytest.mark.parametrize("bad", ["archived", "deleted", "scheduled", "", "DRAFT"])
def test_the_model_refuses_an_unknown_status(app, bad):
    with app.app_context():
        author = _author()
        with pytest.raises(ValueError):
            Announcement(author_id=author.id, scope=_CENTER, status=bad, title="T", body="B")


@pytest.mark.parametrize("bad", ["level", "user", "everyone"])
def test_the_database_refuses_an_unknown_scope(app, bad):
    with app.app_context():
        _author()
        with pytest.raises(sa.exc.IntegrityError):
            _raw_insert(scope=bad, author_id=1)
        db.session.rollback()


@pytest.mark.parametrize("bad", ["archived", "deleted", "scheduled"])
def test_the_database_refuses_an_unknown_status(app, bad):
    with app.app_context():
        _author()
        with pytest.raises(sa.exc.IntegrityError):
            _raw_insert(status=bad, author_id=1)
        db.session.rollback()


# ===========================================================================
# Exactly one target per scope
# ===========================================================================


def test_a_center_announcement_carries_neither_target(app):
    with app.app_context():
        row = fx.center(_author())
        assert row.course_id is None and row.group_id is None


def test_a_group_announcement_carries_no_duplicated_course_id(app):
    """The Course of a group-scoped notice is ``group.course``, one answer
    rather than two that could disagree after a Group retarget."""
    with app.app_context():
        author = _author()
        group = fx.hierarchy("A")
        row = fx.for_group(author, group)
        assert row.group_id == group.id
        assert row.course_id is None


def test_the_database_refuses_every_illegal_scope_target_shape(app):
    with app.app_context():
        author = _author()
        group = fx.hierarchy("A")
        course_id, group_id, author_id = group.course_id, group.id, author.id
        illegal = [
            # center may carry no target at all
            {"scope": _CENTER, "course_id": course_id},
            {"scope": _CENTER, "group_id": group_id},
            {"scope": _CENTER, "course_id": course_id, "group_id": group_id},
            # course needs exactly a course
            {"scope": _COURSE},
            {"scope": _COURSE, "group_id": group_id},
            {"scope": _COURSE, "course_id": course_id, "group_id": group_id},
            # group needs exactly a group
            {"scope": _GROUP},
            {"scope": _GROUP, "course_id": course_id},
            {"scope": _GROUP, "course_id": course_id, "group_id": group_id},
        ]
        for values in illegal:
            with pytest.raises(sa.exc.IntegrityError):
                _raw_insert(author_id=author_id, **values)
            db.session.rollback()


# ===========================================================================
# The state / timestamp truth table
# ===========================================================================


def test_a_draft_carries_neither_publication_timestamp(app):
    with app.app_context():
        row = fx.center(_author())
        assert row.status == _DRAFT
        assert row.published_at is None and row.withdrawn_at is None


def test_a_published_announcement_carries_only_published_at(app):
    with app.app_context():
        row = fx.published_center(_author())
        assert row.published_at is not None and row.withdrawn_at is None


def test_a_withdrawn_announcement_carries_both_in_order(app):
    with app.app_context():
        row = fx.center(_author(), status=_WITHDRAWN)
        assert row.published_at is not None and row.withdrawn_at is not None
        assert row.withdrawn_at >= row.published_at


def test_the_database_refuses_every_illegal_state_timestamp_shape(app):
    with app.app_context():
        author_id = _author().id
        illegal = [
            {"status": _DRAFT, "published_at": fx.NOW},
            {"status": _DRAFT, "withdrawn_at": fx.NOW},
            {"status": _PUBLISHED},
            {"status": _PUBLISHED, "published_at": fx.NOW, "withdrawn_at": fx.LATER},
            {"status": _WITHDRAWN},
            {"status": _WITHDRAWN, "published_at": fx.NOW},
            {"status": _WITHDRAWN, "withdrawn_at": fx.NOW},
            # withdrawn BEFORE published
            {"status": _WITHDRAWN, "published_at": fx.LATER, "withdrawn_at": fx.NOW},
        ]
        for values in illegal:
            with pytest.raises(sa.exc.IntegrityError):
                _raw_insert(author_id=author_id, **values)
            db.session.rollback()


def test_withdrawing_at_exactly_the_publication_moment_is_legal(app):
    """``>=``, not ``>``: a notice taken down in the same whole second it
    went up is a real (if unlucky) event, and the column has whole-second
    precision."""
    with app.app_context():
        row = fx.center(
            _author(), status=_WITHDRAWN, published_at=fx.NOW, withdrawn_at=fx.NOW
        )
        assert row.withdrawn_at == row.published_at


# ===========================================================================
# Version
# ===========================================================================


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "2", None])
def test_the_model_refuses_a_non_positive_version(app, bad):
    with app.app_context():
        author = _author()
        with pytest.raises(ValueError):
            Announcement(
                author_id=author.id, scope=_CENTER, title="T", body="B", version=bad
            )


def test_the_database_refuses_a_non_positive_version(app):
    with app.app_context():
        author_id = _author().id
        for bad in (0, -1):
            with pytest.raises(sa.exc.IntegrityError):
                _raw_insert(author_id=author_id, version=bad)
            db.session.rollback()


# ===========================================================================
# Identity, text and the shape of the table
# ===========================================================================


def test_public_id_is_a_unique_uuid_and_is_generated_server_side(app):
    with app.app_context():
        author = _author()
        first, second = fx.center(author), fx.center(author)
        assert first.public_id and second.public_id
        assert first.public_id != second.public_id
        assert len(first.public_id) == 36
        with pytest.raises(sa.exc.IntegrityError):
            _raw_insert(author_id=author.id, public_id=first.public_id)
        db.session.rollback()


def test_the_text_columns_have_the_approved_widths():
    columns = Announcement.__table__.columns
    assert columns["title"].type.length == ANNOUNCEMENT_TITLE_MAX_LENGTH == 150
    assert columns["body"].type.length == ANNOUNCEMENT_BODY_MAX_LENGTH == 5000
    assert not columns["title"].nullable
    assert not columns["body"].nullable


def test_the_table_declares_exactly_the_approved_columns():
    assert {c.name for c in Announcement.__table__.columns} == {
        "id",
        "public_id",
        "author_id",
        "scope",
        "course_id",
        "group_id",
        "title",
        "body",
        "status",
        "published_at",
        "withdrawn_at",
        "version",
        "created_at",
        "updated_at",
    }


def test_the_table_carries_no_recipient_grade_attachment_or_schedule_column():
    """An announcement is a communication. Nothing here stores who was
    told, a score, a file, a deadline, a reaction or a read receipt -- and
    there is no recipient snapshot table anywhere in M09 either."""
    names = {c.name for c in Announcement.__table__.columns}
    for forbidden in (
        "recipient_id", "recipients", "read_at", "acknowledged_at", "seen_at",
        "score", "points", "grade", "weight",
        "uploaded_file_id", "attachment_id", "file_id",
        "scheduled_for", "publish_at", "expires_at", "due_at", "starts_at",
        "comment", "reaction_count", "pinned", "deleted_at", "archived_at",
        "content_html", "body_html",
    ):
        assert forbidden not in names, forbidden
    assert "announcement_recipients" not in db.metadata.tables
    assert "announcement_reads" not in db.metadata.tables


def test_every_foreign_key_is_plain_with_no_cascade():
    for column in ("author_id", "course_id", "group_id"):
        foreign_keys = list(Announcement.__table__.columns[column].foreign_keys)
        assert len(foreign_keys) == 1, column
        fk = foreign_keys[0]
        assert fk.ondelete is None, column
        assert fk.onupdate is None, column
    targets = {
        list(Announcement.__table__.columns[c].foreign_keys)[0].column.table.name
        for c in ("author_id", "course_id", "group_id")
    }
    assert targets == {"users", "courses", "groups"}


def test_no_orm_relationship_is_declared_in_either_direction():
    """Every read goes through explicit joins in
    ``app/services/announcement_queries.py``; no template can lazy-load an
    announcement's Course, Group or author, and no cascade configuration
    exists that could remove one."""
    from sqlalchemy import inspect

    assert list(inspect(Announcement).relationships) == []
    for model in (Group, Course, User):
        assert "announcements" not in {r.key for r in inspect(model).relationships}


def test_the_declared_indexes_serve_the_five_real_query_paths():
    indexes = {
        index.name: [c.name for c in index.columns]
        for index in Announcement.__table__.indexes
    }
    assert indexes == {
        "ix_announcements_scope_status_published": [
            "scope", "status", "published_at", "id",
        ],
        "ix_announcements_course_status_published": [
            "course_id", "status", "published_at", "id",
        ],
        "ix_announcements_group_status_published": [
            "group_id", "status", "published_at", "id",
        ],
        "ix_announcements_author_status_created": [
            "author_id", "status", "created_at", "id",
        ],
        "ix_announcements_status_scope_created": [
            "status", "scope", "created_at", "id",
        ],
    }


def test_each_foreign_key_column_leads_an_index(app):
    """InnoDB requires an index whose leftmost column is the referencing
    column; each of the three is the leading column of one of the five
    above, so no separate single-column index is declared."""
    leading = {
        [c.name for c in index.columns][0] for index in Announcement.__table__.indexes
    }
    for column in ("author_id", "course_id", "group_id"):
        assert column in leading, column


def test_the_constraints_carry_the_approved_names():
    names = {
        constraint.name
        for constraint in Announcement.__table__.constraints
        if constraint.name
    }
    assert {
        "ck_announcements_scope_valid",
        "ck_announcements_status_valid",
        "ck_announcements_scope_target",
        "ck_announcements_status_timestamps",
        "ck_announcements_version_positive",
    } <= names


def test_a_referenced_course_group_or_author_cannot_be_deleted_out_from_under_it(app):
    """No cascade anywhere: an announcement is never removed by a Course,
    Group or account being deleted -- the delete is refused instead."""
    with app.app_context():
        db.session.execute(sa.text("PRAGMA foreign_keys=ON"))
        author = _author()
        group = fx.hierarchy("A")
        fx.for_group(author, group)
        fx.for_course(author, db.session.get(Course, group.course_id))
        fx.center(author)
        for statement in (
            sa.delete(Group.__table__).where(Group.__table__.c.id == group.id),
            sa.delete(Course.__table__).where(
                Course.__table__.c.id == group.course_id
            ),
            sa.delete(User.__table__).where(User.__table__.c.id == author.id),
        ):
            with pytest.raises(sa.exc.IntegrityError):
                db.session.execute(statement)
                db.session.commit()
            db.session.rollback()
        assert Announcement.query.count() == 3


# ===========================================================================
# The convenience predicates
# ===========================================================================


def test_the_lifecycle_predicates_agree_with_the_stored_status(app):
    with app.app_context():
        author = _author()
        draft = fx.center(author)
        published = fx.published_center(author)
        withdrawn = fx.center(author, status=_WITHDRAWN)
        assert (draft.is_draft, draft.is_published, draft.is_withdrawn) == (
            True, False, False,
        )
        assert (published.is_draft, published.is_published, published.is_withdrawn) == (
            False, True, False,
        )
        assert (withdrawn.is_draft, withdrawn.is_published, withdrawn.is_withdrawn) == (
            False, False, True,
        )


# ===========================================================================
# Lifecycle integrity -- what M09 deliberately does NOT add
# ===========================================================================


def test_a_group_retarget_moves_the_derived_course_and_rewrites_no_row(app):
    """A group-scoped announcement stores only ``group_id``.

    Its Course is resolved **through** the Group, which is exactly what
    makes an administrator's Group retarget safe: the announcement's own
    stored data -- its text, its target, its lifecycle, its version -- is
    untouched, and the Course it is displayed under simply follows the
    Group, because there is only ever one answer to "which Course is this
    Group in".

    This is why M09 adds **no** new link to the Group academic-identity
    freeze (``app.blueprints.admin.groups._group_identity_frozen``). A
    GradeCategory's weight and a GradeItem's captured roster are
    statements about a specific Course and Term, so retargeting would
    silently reinterpret them; an announcement is a statement *to a
    Group*, and it follows that Group by design. Adding a freeze here
    would be an unrelated lifecycle rule, not a preserved meaning.
    """
    from app.services.announcement_queries import group_announcement

    with app.app_context():
        author = _author()
        group = fx.hierarchy("A")
        other = fx.hierarchy("B")
        ann = fx.published_group(author, group, title="Notice", body="Body.")
        before = (
            ann.title, ann.body, ann.scope, ann.course_id, ann.group_id,
            ann.status, ann.version, ann.published_at, ann.updated_at,
        )
        other_course_title = db.session.get(Course, other.course_id).title
        view = group_announcement(group.id, ann.public_id)
        assert view.group_course_title != other_course_title

        moved = Group.query.filter_by(public_id=group.public_id).one()
        moved.course_id = other.course_id
        moved.academic_term_id = other.academic_term_id
        db.session.commit()

        after = Announcement.query.filter_by(public_id=ann.public_id).one()
        assert (
            after.title, after.body, after.scope, after.course_id, after.group_id,
            after.status, after.version, after.published_at, after.updated_at,
        ) == before
        # The derived context follows the Group -- one answer, never two.
        view = group_announcement(moved.id, after.public_id)
        assert view.group_course_title == other_course_title


def test_the_group_identity_freeze_is_unchanged_by_this_milestone(app):
    """Stated as a contract so a future change here has to be deliberate:
    an announcement is not one of the rows that freeze a Group's
    AcademicTerm / Course."""
    from app.blueprints.admin.groups import _group_identity_frozen

    with app.app_context():
        author = _author()
        group = fx.hierarchy("A")
        assert _group_identity_frozen(group.id) is False
        fx.for_group(author, group)
        fx.published_group(author, group)
        fx.for_group(author, group, status=_WITHDRAWN)
        assert _group_identity_frozen(group.id) is False


def test_no_lifecycle_change_ever_removes_or_rewrites_an_announcement(app):
    """Archiving the Group, the Course, the Level or the Term hides a
    scoped announcement from every reader -- and touches no row. There is
    no cascade and no hard delete anywhere in M09."""
    from app.models import AcademicTerm, Level

    with app.app_context():
        author = _author()
        group = fx.hierarchy("A")
        course = db.session.get(Course, group.course_id)
        rows = [
            fx.published_center(author),
            fx.published_course(author, course),
            fx.published_group(author, group),
        ]
        before = {
            row.public_id: (row.status, row.version, row.updated_at) for row in rows
        }
        for row in (
            AcademicTerm.query.one(),
            Level.query.one(),
            course,
            Group.query.one(),
        ):
            row.status = "archived"
        db.session.commit()
        assert Announcement.query.count() == 3
        for public_id, expected in before.items():
            row = Announcement.query.filter_by(public_id=public_id).one()
            assert (row.status, row.version, row.updated_at) == expected
