"""Phase 6 / M01 migration checks (``f2a6d1c84b37``).

The revision creates ``research_consent_documents``,
``research_participants`` and ``research_consent_events``. It alters
nothing, reads no existing row, and seeds nothing -- in particular it
creates no consent document, because a migration that invented consent
wording would be recording that people had been told something nobody wrote.

The execution probes build an isolated temporary SQLite database holding
representative **Phase 5** history -- accounts, a Group, an Enrollment, a fee
plan and assignment, an invoice with lines and a number sequence, a payment
transaction, a receipt and audit events, including M10's visible-deletion
tombstone -- at revision ``b3d8f1a6c472``, then run this revision forward
and back with foreign keys **enforced** and ``PRAGMA foreign_key_check``
asserted after each. Every Phase 5 row is compared before and after.

The MySQL checks render the revision's offline (``--sql``) MySQL script;
nothing connects to a database. The real upgrade of the authorized
development MySQL database is a separate, manually executed check.

**Historical.** The consent workflow this revision created was removed by
the Phase 6 replacement (``69c4bae553fe`` / ``d574ab56594f``) and its runtime
models no longer exist. This suite keeps proving what the historical revision
does -- it stays in the upgrade chain -- against the literal historical shape
below, never against a runtime model.
"""

import io
import os
import pathlib
import re
import tempfile

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

import tests.test_financial_deletion_migration as m10
import tests.test_payments_migration as m05
import tests.test_verified_webhooks_migration as m07

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "f2a6d1c84b37"
_DOWN_REVISION = "b3d8f1a6c472"

_DOCUMENTS = "research_consent_documents"
_PARTICIPANTS = "research_participants"
_EVENTS = "research_consent_events"
_NEW_TABLES = [_DOCUMENTS, _PARTICIPANTS, _EVENTS]

#: The historical closed sets, as the revision declared them.
_CLOSED_SETS = (
    ("status", ("draft", "active", "superseded")),
    ("status", ("invited", "active", "declined", "withdrawn")),
    ("action", ("accepted", "declined", "withdrawn")),
)

_EXPECTED = {
    _DOCUMENTS: {
        "columns": {"id", "public_id", "version_identifier", "title", "body", "body_digest",
                    "status", "current_marker", "created_by_id", "activated_at",
                    "activated_by_id", "superseded_at", "superseded_by_id", "created_at",
                    "updated_at"},
        "nullable": {"current_marker", "activated_at", "activated_by_id", "superseded_at",
                     "superseded_by_id"},
        "checks": {"ck_research_consent_documents_status_valid",
                   "ck_research_consent_documents_current_marker",
                   "ck_research_consent_documents_activation_pair",
                   "ck_research_consent_documents_supersession_pair",
                   "ck_research_consent_documents_lifecycle_state",
                   "ck_research_consent_documents_digest_format",
                   "ck_research_consent_documents_body_present",
                   "ck_research_consent_documents_timestamps_ordered"},
        "indexes": {"ix_research_consent_documents_status_id": ["status", "id"],
                    "ix_research_consent_documents_created_by_id": ["created_by_id"],
                    "ix_research_consent_documents_activated_by_id": ["activated_by_id"],
                    "ix_research_consent_documents_superseded_by_id": ["superseded_by_id"]},
        "fks": {("created_by_id", "users"), ("activated_by_id", "users"),
                ("superseded_by_id", "users")},
        "uniques": sorted([("", ("public_id",)),
                           ("uq_research_consent_documents_current", ("current_marker",)),
                           ("uq_research_consent_documents_version", ("version_identifier",))]),
    },
    _PARTICIPANTS: {
        "columns": {"id", "public_id", "student_id", "participant_code", "status",
                    "consent_document_id", "decided_at", "withdrawn_at", "invited_by_id",
                    "version", "created_at", "updated_at"},
        "nullable": {"consent_document_id", "decided_at", "withdrawn_at"},
        "checks": {"ck_research_participants_status_valid",
                   "ck_research_participants_code_format",
                   "ck_research_participants_version_positive",
                   "ck_research_participants_lifecycle_state",
                   "ck_research_participants_timestamps_ordered"},
        "indexes": {"ix_research_participants_status_id": ["status", "id"],
                    "ix_research_participants_invited_by_id": ["invited_by_id"],
                    "ix_research_participants_consent_document_id": ["consent_document_id"]},
        "fks": {("student_id", "users"), ("invited_by_id", "users"),
                ("consent_document_id", _DOCUMENTS)},
        "uniques": sorted([("", ("public_id",)),
                           ("uq_research_participants_code", ("participant_code",)),
                           ("uq_research_participants_student_id", ("student_id",))]),
    },
    _EVENTS: {
        "columns": {"id", "participant_id", "consent_document_id", "action", "actor_id",
                    "consent_version", "consent_digest", "occurred_at"},
        "nullable": set(),
        "checks": {"ck_research_consent_events_action_valid",
                   "ck_research_consent_events_digest_format",
                   "ck_research_consent_events_version_present"},
        "indexes": {"ix_research_consent_events_participant_id_id": ["participant_id", "id"],
                    "ix_research_consent_events_consent_document_id": ["consent_document_id"],
                    "ix_research_consent_events_actor_id": ["actor_id"]},
        "fks": {("participant_id", _PARTICIPANTS), ("consent_document_id", _DOCUMENTS),
                ("actor_id", "users")},
        "uniques": [],
    },
}

_DIGEST = "a" * 64
_T0 = "'2026-07-01 09:00:00'"
_T1 = "'2026-07-02 09:00:00'"
_T2 = "'2026-07-03 09:00:00'"


def _load_migration():
    return m05._load(_REVISION, "p6m01")


def _flat(source):
    return " ".join(source.split()).replace('" "', "")


def _declared_checks(source, table):
    """The CHECK names one ``op.create_table`` block of a revision declares."""
    return set(re.findall(r"name='(ck_[a-z_]+)'", m05._table_block(source, table)))


def _shape(conn, table):
    schema = inspect(conn)
    return {
        "columns": {c["name"] for c in schema.get_columns(table)},
        "nullable": {c["name"] for c in schema.get_columns(table) if c["nullable"]},
        "checks": {c["name"] for c in schema.get_check_constraints(table)},
        "indexes": {i["name"]: list(i["column_names"]) for i in schema.get_indexes(table)},
        "fks": {(fk["constrained_columns"][0], fk["referred_table"])
                for fk in schema.get_foreign_keys(table)},
        "uniques": sorted((u["name"] or "", tuple(u["column_names"]))
                          for u in schema.get_unique_constraints(table)),
    }


def _run(conn, module, direction):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()
    conn.commit()
    assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1
    assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []


def _rows(conn, table, columns="*"):
    return conn.execute(sa.text(f"SELECT {columns} FROM {table} ORDER BY id")).fetchall()


def _refused(conn, statement, rule):
    try:
        conn.execute(sa.text(statement))
    except sa.exc.IntegrityError:
        conn.rollback()
        return
    raise AssertionError(f"{rule} was not enforced")


# ===========================================================================
# Revision identity, and what the revision does
# ===========================================================================


def test_revision_identifiers_and_one_linear_head():
    module, _ = _load_migration()
    assert (module.revision, module.down_revision) == (_REVISION, _DOWN_REVISION)
    assert module.branch_labels is None and module.depends_on is None
    parents = {}
    for path in _MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        parents[revision] = re.search(
            r"^down_revision = (?:'([^']+)'|None)", source, re.M
        ).group(1)
    heads = set(parents) - {p for p in parents.values() if p is not None}
    # Phase 6 / M02A follows this revision; the Phase 6 replacement removed
    # both, and its destructive revision is now the single head.
    assert heads == {"d574ab56594f"}
    assert [r for r, p in parents.items() if p == _REVISION] == ["b86838ce23db"]
    assert [r for r, p in parents.items() if p == _DOWN_REVISION] == [_REVISION]
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_three_tables_and_alters_or_seeds_nothing():
    _, source = _load_migration()
    code = m05._code(source)
    assert set(re.findall(r"op\.create_table\('([^']+)'", code)) == set(_NEW_TABLES)
    upgrade = m05._function(code, "upgrade")
    for forbidden in ("op.add_column", "op.drop_column", "op.alter_column",
                      "op.drop_table", "op.drop_index", "op.drop_constraint",
                      "op.bulk_insert", "op.execute", "INSERT", "UPDATE ", "DELETE FROM",
                      "server_default", "ondelete", "onupdate", "mysql_engine",
                      "mysql_charset", "Enum(", "Float"):
        assert forbidden not in upgrade, forbidden
    # No seeded consent wording, and no tracking column anywhere.
    for forbidden in ("consent wording", "ip_address", "user_agent", "fingerprint",
                      "keystroke", "session_id", "experiment", "survey", "rating"):
        assert forbidden not in code, forbidden


def test_the_downgrade_drops_child_before_parent_and_nothing_else():
    _, source = _load_migration()
    downgrade = m05._function(m05._code(source), "downgrade")
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == [
        _EVENTS, _PARTICIPANTS, _DOCUMENTS
    ]
    assert "op.create_table" not in downgrade
    assert "Refusing to downgrade" in downgrade


def test_every_declared_check_is_the_historical_check_set():
    module, source = _load_migration()
    for table in _NEW_TABLES:
        assert _declared_checks(source, table) == _EXPECTED[table]["checks"], table
    assert module.revision == _REVISION


def test_the_migrations_closed_sets_are_the_historical_ones():
    _, source = _load_migration()
    flat = _flat(source)
    for column, values in _CLOSED_SETS:
        expected = f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"
        assert expected in flat, expected


def test_the_current_marker_check_is_null_safe():
    """A CHECK that evaluates to NULL passes, and ``NULL = 1`` is NULL -- so
    without an explicit ``IS NOT NULL`` an active row with no marker would be
    accepted, and two of those would satisfy the unique index as well."""
    _, source = _load_migration()
    flat = _flat(source)
    assert ("(status = 'active' AND current_marker IS NOT NULL AND current_marker = 1)"
            " OR (status <> 'active' AND current_marker IS NULL)") in flat


# ===========================================================================
# Execution against an isolated SQLite database with Phase 5 history
# ===========================================================================

#: The two extra accounts M01's probes need. The Phase 5 chain already
#: created 11 (administrator), 12 (suspended administrator) and 13 (student);
#: these add a second Student and a Researcher, so "one participant per
#: Student" and the foreign keys can be probed against real rows.
_PHASE5_ROWS = [
    "INSERT INTO users (id, public_id, role, status) VALUES"
    " (14, 'u-14', 'student', 'suspended'), (15, 'u-15', 'researcher', 'active')",
]

_PHASE5_TABLES = ("users", "groups", "enrollments", "fee_plans", "student_fee_assignments",
                  "invoices", "invoice_items", "invoice_number_sequences",
                  "payment_transactions", "receipts", "receipt_number_sequences",
                  "payment_audit_events")

_DOCUMENT_INSERT = (
    "INSERT INTO research_consent_documents (id, public_id, version_identifier, title, body,"
    " body_digest, status, current_marker, created_by_id, activated_at, activated_by_id,"
    " superseded_at, superseded_by_id, created_at, updated_at) VALUES "
)
_PARTICIPANT_INSERT = (
    "INSERT INTO research_participants (id, public_id, student_id, participant_code, status,"
    " consent_document_id, decided_at, withdrawn_at, invited_by_id, version, created_at,"
    " updated_at) VALUES "
)
_EVENT_INSERT = (
    "INSERT INTO research_consent_events (id, participant_id, consent_document_id, action,"
    " actor_id, consent_version, consent_digest, occurred_at) VALUES "
)

_M01_ROWS = [
    _DOCUMENT_INSERT
    + f"(1, 'd-1', 'v1.0', 'Sheet', 'Placeholder wording.', '{_DIGEST}', 'superseded', NULL,"
    f" 11, {_T0}, 11, {_T1}, 11, {_T0}, {_T1}),"
    f" (2, 'd-2', 'v2.0', 'Sheet', 'Newer placeholder wording.', '{'b' * 64}', 'active', 1,"
    f" 11, {_T1}, 11, NULL, NULL, {_T1}, {_T1})",
    _PARTICIPANT_INSERT
    + f"(1, 'p-1', 13, 'RP-AAAAAAAAAA', 'active', 1, {_T1}, NULL, 11, 2, {_T0}, {_T1})",
    _EVENT_INSERT
    + f"(1, 1, 1, 'accepted', 13, 'v1.0', '{_DIGEST}', {_T1})",
]


def _probe(name):
    """An isolated database at ``b3d8f1a6c472`` holding representative
    Phase 5 history, with foreign keys enforced.

    The M04 -> M10 chain and its rows come from the existing Phase 5 suites
    rather than being rebuilt here, so this suite cannot drift from what
    those revisions actually produce. M07 and M10 rebuild tables other
    tables reference, which SQLite allows only with ``PRAGMA
    foreign_keys=OFF``; enforcement is turned back on -- and
    ``PRAGMA foreign_key_check`` asserted empty -- before this revision runs,
    so every M01 foreign key is really enforced in these probes.
    """
    tmp, engine, conn = m10._probe(name)
    m10_module, _ = m10._load_migration()
    m07._run(conn, m10_module, "upgrade")
    m07._foreign_keys(conn, True)
    assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []
    for statement in _PHASE5_ROWS:
        conn.execute(sa.text(statement))
    conn.commit()
    return tmp, engine, conn


def _phase5_rows(conn):
    return {table: _rows(conn, table) for table in _PHASE5_TABLES}


def test_the_migration_applies_leaving_every_phase_5_row_untouched():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("m01-probe.db")
    try:
        before = _phase5_rows(conn)
        tables_before = set(inspect(conn).get_table_names())
        shapes_before = {t: _shape(conn, t) for t in _PHASE5_TABLES}

        _run(conn, module, "upgrade")

        assert set(inspect(conn).get_table_names()) - tables_before == set(_NEW_TABLES)
        for table in _NEW_TABLES:
            assert _shape(conn, table) == _EXPECTED[table], table
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        # Every Phase 5 row and every Phase 5 table shape is exactly as it was.
        assert _phase5_rows(conn) == before
        assert {t: _shape(conn, t) for t in _PHASE5_TABLES} == shapes_before
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_new_constraints_accept_real_history_and_refuse_broken_history():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("m01-constraints.db")
    try:
        _run(conn, module, "upgrade")
        for statement in _M01_ROWS:
            conn.execute(sa.text(statement))
        conn.commit()

        for statement, rule in (
            (_DOCUMENT_INSERT + f"(9, 'd-9', 'v9', 'T', 'B', '{'c' * 64}', 'retired', NULL,"
             f" 11, NULL, NULL, NULL, NULL, {_T0}, {_T0})",
             "the document status CHECK"),
            (_DOCUMENT_INSERT + f"(9, 'd-9', 'v9', 'T', 'B', '{'c' * 64}', 'active', 1, 11,"
             f" {_T0}, 11, NULL, NULL, {_T0}, {_T0})",
             "uq_research_consent_documents_current"),
            (_DOCUMENT_INSERT + f"(9, 'd-9', 'v9', 'T', 'B', '{'c' * 64}', 'active', NULL, 11,"
             f" {_T0}, 11, NULL, NULL, {_T0}, {_T0})",
             "an active document with no current marker"),
            (_DOCUMENT_INSERT + f"(9, 'd-9', 'v2.0', 'T', 'B', '{'c' * 64}', 'draft', NULL,"
             f" 11, NULL, NULL, NULL, NULL, {_T0}, {_T0})",
             "uq_research_consent_documents_version"),
            (_DOCUMENT_INSERT + "(9, 'd-9', 'v9', 'T', 'B', 'short', 'draft', NULL, 11, NULL,"
             f" NULL, NULL, NULL, {_T0}, {_T0})",
             "the document digest CHECK"),
            (_DOCUMENT_INSERT + f"(9, 'd-9', 'v9', 'T', '', '{'c' * 64}', 'draft', NULL, 11,"
             f" NULL, NULL, NULL, NULL, {_T0}, {_T0})",
             "the document body CHECK"),
            (_DOCUMENT_INSERT + f"(9, 'd-9', 'v9', 'T', 'B', '{'c' * 64}', 'draft', NULL, 11,"
             f" {_T0}, 11, NULL, NULL, {_T0}, {_T0})",
             "the document lifecycle CHECK"),
            (_DOCUMENT_INSERT + f"(9, 'd-9', 'v9', 'T', 'B', '{'c' * 64}', 'draft', NULL, 99,"
             f" NULL, NULL, NULL, NULL, {_T0}, {_T0})",
             "the document creator foreign key"),
            (_PARTICIPANT_INSERT + f"(9, 'p-9', 13, 'RP-BBBBBBBBBB', 'invited', NULL, NULL,"
             f" NULL, 11, 1, {_T0}, {_T0})",
             "uq_research_participants_student_id"),
            (_PARTICIPANT_INSERT + f"(9, 'p-9', 14, 'RP-AAAAAAAAAA', 'invited', NULL, NULL,"
             f" NULL, 11, 1, {_T0}, {_T0})",
             "uq_research_participants_code"),
            (_PARTICIPANT_INSERT + f"(9, 'p-9', 14, 'RP-SHORT', 'invited', NULL, NULL, NULL,"
             f" 11, 1, {_T0}, {_T0})",
             "the participant code CHECK"),
            (_PARTICIPANT_INSERT + f"(9, 'p-9', 14, 'RP-BBBBBBBBBB', 'enrolled', NULL, NULL,"
             f" NULL, 11, 1, {_T0}, {_T0})",
             "the participant status CHECK"),
            (_PARTICIPANT_INSERT + f"(9, 'p-9', 14, 'RP-BBBBBBBBBB', 'active', NULL, {_T1},"
             f" NULL, 11, 1, {_T0}, {_T1})",
             "an active participant with no consent document"),
            (_PARTICIPANT_INSERT + f"(9, 'p-9', 14, 'RP-BBBBBBBBBB', 'withdrawn', 2, {_T1},"
             f" {_T2}, 11, 1, {_T0}, {_T2})",
             "a withdrawal moment that is not the decision moment"),
            (_PARTICIPANT_INSERT + f"(9, 'p-9', 14, 'RP-BBBBBBBBBB', 'invited', NULL, NULL,"
             f" NULL, 11, 0, {_T0}, {_T0})",
             "the participant version CHECK"),
            (_PARTICIPANT_INSERT + f"(9, 'p-9', 99, 'RP-BBBBBBBBBB', 'invited', NULL, NULL,"
             f" NULL, 11, 1, {_T0}, {_T0})",
             "the participant student foreign key"),
            (_EVENT_INSERT + f"(9, 1, 1, 'revoked', 13, 'v1.0', '{_DIGEST}', {_T1})",
             "the consent action CHECK"),
            (_EVENT_INSERT + f"(9, 1, 1, 'accepted', 13, '', '{_DIGEST}', {_T1})",
             "the consent version CHECK"),
            (_EVENT_INSERT + f"(9, 1, 1, 'accepted', 13, 'v1.0', 'short', {_T1})",
             "the consent digest CHECK"),
            (_EVENT_INSERT + f"(9, 99, 1, 'accepted', 13, 'v1.0', '{_DIGEST}', {_T1})",
             "the consent participant foreign key"),
            (_EVENT_INSERT + f"(9, 1, 99, 'accepted', 13, 'v1.0', '{_DIGEST}', {_T1})",
             "the consent document foreign key"),
            (_EVENT_INSERT + f"(9, 1, 1, 'accepted', 99, 'v1.0', '{_DIGEST}', {_T1})",
             "the consent actor foreign key"),
            ("DELETE FROM users WHERE id = 13", "no cascade from a Student account"),
            ("DELETE FROM research_consent_documents WHERE id = 1",
             "no cascade from a consent document"),
            ("DELETE FROM research_participants WHERE id = 1",
             "no cascade from a participant"),
        ):
            _refused(conn, statement, rule)
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_downgrade_refuses_while_research_rows_exist_then_reverses_cleanly():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("m01-downgrade.db")
    try:
        before = _phase5_rows(conn)
        tables_before = set(inspect(conn).get_table_names())
        _run(conn, module, "upgrade")
        for statement in _M01_ROWS:
            conn.execute(sa.text(statement))
        conn.commit()
        with_rows = {t: _rows(conn, t) for t in _NEW_TABLES}

        # It refuses before touching anything, for every kind of row.
        for table in (_EVENTS, _PARTICIPANTS, _DOCUMENTS):
            with pytest.raises(RuntimeError, match="Refusing to downgrade"):
                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
            conn.rollback()
            assert {t: _rows(conn, t) for t in _NEW_TABLES} == with_rows
            assert set(inspect(conn).get_table_names()) - tables_before == set(_NEW_TABLES)
            conn.execute(sa.text(f"DELETE FROM {table}"))
            conn.commit()
            with_rows = {t: _rows(conn, t) for t in _NEW_TABLES}

        # Only once every probe row is gone does it reverse, and Phase 5 is
        # exactly as it was.
        _run(conn, module, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        assert _phase5_rows(conn) == before
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_shape():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("m01-again.db")
    try:
        before = _phase5_rows(conn)
        shapes = []
        for _ in range(2):
            _run(conn, module, "upgrade")
            shapes.append({t: _shape(conn, t) for t in _NEW_TABLES})
            _run(conn, module, "downgrade")
            assert _phase5_rows(conn) == before
        assert shapes[0] == shapes[1]
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# MySQL DDL, compiled and rendered offline
# ===========================================================================

_DDL_FRAGMENTS = {
    _DOCUMENTS: (
        "id BIGINT NOT NULL AUTO_INCREMENT", "public_id VARCHAR(36) NOT NULL",
        "version_identifier VARCHAR(40) NOT NULL", "title VARCHAR(200) NOT NULL",
        "body TEXT NOT NULL", "body_digest VARCHAR(64) NOT NULL",
        "status VARCHAR(32) NOT NULL", "current_marker SMALLINT,",
        "created_by_id BIGINT NOT NULL", "activated_by_id BIGINT,",
        "superseded_by_id BIGINT,",
        "FOREIGN KEY(created_by_id) REFERENCES users (id)",
        "FOREIGN KEY(activated_by_id) REFERENCES users (id)",
        "FOREIGN KEY(superseded_by_id) REFERENCES users (id)",
        "CONSTRAINT uq_research_consent_documents_version UNIQUE (version_identifier)",
        "CONSTRAINT uq_research_consent_documents_current UNIQUE (current_marker)",
        "UNIQUE (public_id)",
    ),
    _PARTICIPANTS: (
        "id BIGINT NOT NULL AUTO_INCREMENT", "student_id BIGINT NOT NULL",
        "participant_code VARCHAR(13) NOT NULL", "status VARCHAR(32) NOT NULL",
        "consent_document_id BIGINT,", "invited_by_id BIGINT NOT NULL",
        "version INTEGER NOT NULL",
        "FOREIGN KEY(student_id) REFERENCES users (id)",
        "FOREIGN KEY(invited_by_id) REFERENCES users (id)",
        "FOREIGN KEY(consent_document_id) REFERENCES research_consent_documents (id)",
        "CONSTRAINT uq_research_participants_student_id UNIQUE (student_id)",
        "CONSTRAINT uq_research_participants_code UNIQUE (participant_code)",
        "UNIQUE (public_id)",
    ),
    _EVENTS: (
        "id BIGINT NOT NULL AUTO_INCREMENT", "participant_id BIGINT NOT NULL",
        "consent_document_id BIGINT NOT NULL", "action VARCHAR(32) NOT NULL",
        "actor_id BIGINT NOT NULL", "consent_version VARCHAR(40) NOT NULL",
        "consent_digest VARCHAR(64) NOT NULL", "occurred_at DATETIME NOT NULL",
        "FOREIGN KEY(participant_id) REFERENCES research_participants (id)",
        "FOREIGN KEY(consent_document_id) REFERENCES research_consent_documents (id)",
        "FOREIGN KEY(actor_id) REFERENCES users (id)",
    ),
}


def _offline_upgrade():
    module, _ = _load_migration()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        module.upgrade()
    return " ".join(buffer.getvalue().split())


@pytest.mark.parametrize("table", _NEW_TABLES)
def test_the_offline_mysql_ddl_for_each_table(table):
    script = _offline_upgrade()
    block = script.split(f"CREATE TABLE {table} (", 1)[1].split(");", 1)[0]
    for fragment in _DDL_FRAGMENTS[table]:
        assert fragment in block, (table, fragment)
    for forbidden in ("ENGINE=", "CHARSET=", "ON DELETE", "ON UPDATE", "ENUM("):
        assert forbidden not in block, (table, forbidden)


def test_the_offline_mysql_script_creates_the_three_tables_and_nothing_else():
    """Rendered with ``--sql``; nothing connects to a database."""
    module, _ = _load_migration()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        module.upgrade()
    script = buffer.getvalue()
    for table in _NEW_TABLES:
        assert f"CREATE TABLE {table}" in script, table
    for forbidden in ("DROP TABLE", "ALTER TABLE", "INSERT INTO", "UPDATE ", "DELETE FROM",
                      "ENGINE=", "ON DELETE", "ON UPDATE"):
        assert forbidden not in script, forbidden
    assert script.count("CREATE INDEX") == 10


def test_the_offline_mysql_downgrade_does_not_count_rows():
    """With ``--sql`` there is no database to count, so the guard is skipped
    and the script is pure DDL. The guard still protects every real
    (online) downgrade."""
    module, _ = _load_migration()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        module.downgrade()
    script = buffer.getvalue()
    assert [line for line in script.splitlines() if line.startswith("DROP TABLE")] == [
        f"DROP TABLE {_EVENTS};", f"DROP TABLE {_PARTICIPANTS};", f"DROP TABLE {_DOCUMENTS};"
    ]
    assert "SELECT" not in script
