"""remove superseded consent and protocol tables

Revision ID: d574ab56594f
Revises: 69c4bae553fe
Create Date: 2026-09-29

Phase 6 replacement, second of two revisions. It removes the six tables of
the superseded Phase 6 design -- the in-app consent workflow
(``research_consent_documents``, ``research_participants``,
``research_consent_events``) and the prescribed-task catalogue
(``experiment_definitions``, ``experiment_task_sets``, ``experiment_tasks``).
No runtime code reads them any more. The historical revisions that created
them (``f2a6d1c84b37``, ``b86838ce23db``) stay in the chain only so an
upgrade from any earlier revision works.

Preflight -- everything is checked before the first ``DROP``
-------------------------------------------------------------
MySQL DDL is not transactional: each ``DROP TABLE`` commits on its own, so a
failure half-way could not be rolled back. This revision therefore performs
every check first and drops nothing until all pass:

1. **Every legacy refusal or withdrawal must still be excluded, on its
   legacy basis.** For each ``declined`` or ``withdrawn`` participant row,
   the Student's account must be linked to a subject whose
   ``collection_status`` is ``excluded`` **and** whose ``status_basis`` is
   ``legacy_collection_exclusion`` (as ``69c4bae553fe`` created it). A
   missing link, a subject that is now ``included``, or any other basis
   refuses the upgrade outright: dropping the legacy rows would otherwise
   erase the only evidence behind a decision that no longer holds. An old
   ``active`` row is a decision about one specific consent document and is
   neither checked nor reinterpreted here.
2. **Populated legacy tables are never dropped by default.** If any of the six
   tables holds a row, the upgrade refuses with a message that contains only
   table names and row counts -- no identifier, name, wording or value. It
   proceeds only when **this run** passes both::

       -x legacy_research_disposition=discard
       -x legacy_research_reviewed_counts=<the exact counts the refusal printed>

   The second argument binds the decision to the data that was reviewed: if
   the counts changed since, the upgrade refuses again. A deployment setting
   or environment variable cannot authorize this; only the per-run
   arguments can, and the runbook requires a verified backup first.
3. Empty legacy tables -- the state observed in development MySQL -- are
   dropped by the ordinary upgrade.

Offline (``--sql``) rendering of the upgrade is **always refused**: an
offline script cannot verify step 1 or count the rows of step 2, and no
argument replaces those checks. Run the upgrade against a live database
connection. The downgrade below is plain DDL and does not read rows.

What is and is not kept
-----------------------
Kept: the exclusions ``69c4bae553fe`` carried forward. Not kept: legacy
consent wording, consent history, invitations, acceptances and protocol
catalogues -- the new method has no consent workflow and no prescribed
tasks, and none of them is reinterpreted as an inclusion.

Downgrade
---------
Recreates the six legacy tables **empty**, by running the historical
revisions' own ``upgrade`` functions, so the schema matches ``69c4bae553fe``
again. It cannot restore data: the pre-migration backup is the only way back
to the legacy rows.
"""
import importlib.util
from pathlib import Path

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd574ab56594f'
down_revision = '69c4bae553fe'
branch_labels = None
depends_on = None


#: Child before parent -- the order the tables are dropped in.
LEGACY_TABLES = (
    'research_consent_events',
    'research_participants',
    'research_consent_documents',
    'experiment_tasks',
    'experiment_task_sets',
    'experiment_definitions',
)

DISPOSITION_ARGUMENT = 'legacy_research_disposition'
REVIEWED_COUNTS_ARGUMENT = 'legacy_research_reviewed_counts'
DISCARD = 'discard'


def _x_arguments():
    """This run's ``-x key=value`` arguments, or ``{}`` outside an Alembic
    environment (a direct call has none)."""
    try:
        from alembic import context as environment

        return environment.get_x_argument(as_dictionary=True)
    except Exception:
        return {}


def counts_text(counts):
    """The canonical, reviewable form of the legacy row counts."""
    return ','.join(f'{table}={counts[table]}' for table in sorted(counts))


def _counts(bind):
    return {
        table: int(bind.execute(sa.text(f'SELECT COUNT(*) FROM {table}')).scalar())
        for table in LEGACY_TABLES
    }


def _missing_exclusions(bind):
    """Legacy refusals or withdrawals whose Student is not, right now,
    excluded on the legacy basis."""
    return int(bind.execute(sa.text(
        "SELECT COUNT(*) FROM research_participants p"
        " WHERE p.status IN ('declined', 'withdrawn') AND NOT EXISTS"
        " (SELECT 1 FROM research_subject_links a"
        " JOIN research_subjects s ON s.id = a.subject_id"
        " WHERE a.user_id = p.student_id"
        " AND s.collection_status = 'excluded'"
        " AND s.status_basis = 'legacy_collection_exclusion')"
    )).scalar())


def _reviewed(arguments, summary):
    return (
        arguments.get(DISPOSITION_ARGUMENT) == DISCARD
        and arguments.get(REVIEWED_COUNTS_ARGUMENT) == summary
    )


OFFLINE_REFUSAL = (
    "Refusing to render the removal of the superseded research tables offline: an offline "
    "script cannot verify that every legacy refusal or withdrawal is still excluded, nor "
    "count the rows it would drop. Run this upgrade against a live database connection."
)


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError(OFFLINE_REFUSAL)
    arguments = _x_arguments()
    bind = op.get_bind()
    missing = _missing_exclusions(bind)
    if missing:
        raise RuntimeError(
            f"Refusing to remove the superseded research tables: {missing} legacy refusal "
            "or withdrawal row(s) are not excluded with basis legacy_collection_exclusion "
            "(no linked subject, or the subject is now included or excluded on another "
            "basis). Apply 69c4bae553fe first, or resolve the conflict with its own "
            "authorization. Nothing was changed."
        )
    counts = _counts(bind)
    if any(counts.values()):
        summary = counts_text(counts)
        if not _reviewed(arguments, summary):
            raise RuntimeError(
                "Refusing to remove populated superseded research tables without a "
                f"reviewed disposition. Row counts: {summary}. Nothing was changed. "
                "After a verified backup and review, re-run with "
                f"-x {DISPOSITION_ARGUMENT}={DISCARD} "
                f"-x {REVIEWED_COUNTS_ARGUMENT}={summary}"
            )
    for table in LEGACY_TABLES:
        op.drop_table(table)


def _historical(file_name):
    path = Path(__file__).with_name(file_name)
    spec = importlib.util.spec_from_file_location(f'_historical_{path.stem}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade():
    # The empty legacy schema, exactly as its own revisions created it.
    _historical('f2a6d1c84b37_add_research_participants_and_consent.py').upgrade()
    _historical('b86838ce23db_add_experiment_protocol_catalogue.py').upgrade()
