"""add verified mock webhook payments

Revision ID: e9c4b2d7a1f3
Revises: d4f7a2c9e1b6
Create Date: 2026-09-19

Phase 5 / M07. A verified, signed provider webhook becomes the only way an
online collection is recorded. One table is created and four existing tables
are extended; no existing row is rewritten or deleted, and nothing is seeded.

The new table
-------------
``payment_provider_events`` -- the immutable inbox of verified provider events:
``public_id``; ``provider`` (``mock``); ``provider_event_id``, unique per
provider; ``payment_intent_id``; ``event_type`` (``payment.succeeded`` or
``payment.failed``); ``amount DECIMAL(19, 4)`` and ``currency_code``
(``LYD``); the provider's own ``provider_occurred_at``; the LMS's
``received_at`` and ``processed_at``; the SHA-256 ``payload_digest``; the
``outcome`` (``confirmed``, ``failed``, ``duplicate``, ``ignored_terminal`` or
``reconciliation_required``); the nullable, unique ``payment_transaction_id``
of the one collection a ``confirmed`` event created; ``created_at``. No raw
body, signature, secret, provider reference, card or bank data.

The extensions
--------------
- ``payment_intents``: ``status`` also allows ``confirmed``, which is terminal;
  the lifecycle CHECK accepts a failure or confirmation by a verified webhook
  with or without an earlier browser-observed result.
  (``ck_payment_intents_status_valid``, ``_terminal_state`` and
  ``_lifecycle_state`` are replaced.)
- ``payment_transactions``: ``method`` also allows ``online``; the nullable,
  unique ``payment_intent_id`` names the one intent an online collection
  settles (``uq_payment_transactions_payment_intent_id``,
  ``fk_payment_transactions_payment_intent_id``); ``recorded_by_id`` becomes
  nullable, and the new ``ck_payment_transactions_online_origin`` allows NULL
  -- with no confirmer -- exactly for an online collection, which alone names
  an intent. ``_method_valid``, ``_bank_transfer_details``,
  ``_confirmation_pair`` and ``_immediate_confirmation`` are replaced; the
  M05 branches keep their exact meaning.
- ``receipts``: ``issued_by_id`` becomes nullable -- an online collection's
  receipt names no Administrator; the application proves the pairing.
- ``payment_audit_events``: ``actor_id`` becomes nullable, and the new
  ``ck_payment_audit_events_actor_origin`` allows NULL exactly for the two
  system-origin kinds, ``payment_online_confirmed`` and
  ``receipt_online_issued``; ``_kind_valid``, ``_version_transition``,
  ``_reason_required`` and ``_subject_links`` are replaced to know them.

**Every M04, M05 and M06 row satisfies every new expression exactly as it
satisfied the old one**: no existing row is online, confirmed, actor-less or
issuer-less, and each replaced expression only adds branches or values.

How, per backend
----------------
- **MySQL 8**: each replaced CHECK is dropped and added again under the same
  name; nullability changes are ``MODIFY`` statements, run while the CHECKs
  that read those columns are dropped; the new column's unique index is
  created before its foreign key, so MySQL uses it. MySQL validates every
  existing row as each CHECK is added. MySQL DDL is not transactional.
- **SQLite** (the isolated migration tests only): each altered table is
  rebuilt by batch mode from an explicitly declared ``copy_from`` definition
  -- never a reflected one. ``payment_intents``, ``payment_transactions`` and
  ``receipts`` are referenced by other tables, and SQLite refuses to drop a
  referenced table while foreign keys are enforced, so the revision requires
  ``PRAGMA foreign_keys=OFF`` on SQLite (it refuses otherwise) and proves
  ``PRAGMA foreign_key_check`` empty after the rebuilds.

Indexes
-------
``ix_payment_provider_events_intent_id_id`` (``payment_intent_id``, ``id``) and
``ix_payment_provider_events_outcome_id``; the two unique constraints index the
event id and the collection. The new transaction column is indexed by its
unique constraint. **No MySQL execution plan has been measured.** Every
foreign key is plain; no ``mysql_engine`` / ``mysql_charset`` is declared.

Downgrade
---------
The earlier CHECKs cannot represent an online collection, a confirmed intent,
a webhook failure, an actor-less event or an issuer-less receipt, and dropping
the inbox would destroy provider history. The downgrade therefore first counts
every such row and **refuses** before changing anything when any exists. With
none, it drops the inbox and restores the four tables to exactly their
``d4f7a2c9e1b6`` shape. In offline (``--sql``) mode that read cannot run, and
it is skipped. Development MySQL is never downgraded.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e9c4b2d7a1f3'
down_revision = 'd4f7a2c9e1b6'
branch_labels = None
depends_on = None


_ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')

_INTENTS = 'payment_intents'
_PAYMENTS = 'payment_transactions'
_RECEIPTS = 'receipts'
_EVENTS = 'payment_audit_events'
_INBOX = 'payment_provider_events'

_INTENT_FK = 'fk_payment_transactions_payment_intent_id'
_INTENT_UNIQUE = 'uq_payment_transactions_payment_intent_id'
_ONLINE_ORIGIN = 'ck_payment_transactions_online_origin'
_ACTOR_ORIGIN = 'ck_payment_audit_events_actor_origin'

# ---------------------------------------------------------------------------
# payment_intents
# ---------------------------------------------------------------------------

_INTENT_CHECKS = (
    (
        'ck_payment_intents_status_valid',
        "status IN ('pending', 'provider_succeeded', 'provider_failed', 'cancelled')",
        "status IN ('pending', 'provider_succeeded', 'provider_failed', 'cancelled',"
        " 'confirmed')",
    ),
    (
        'ck_payment_intents_terminal_state',
        "(status IN ('pending', 'provider_succeeded') AND terminal_at IS NULL)"
        " OR (status IN ('provider_failed', 'cancelled') AND terminal_at IS NOT NULL)",
        "(status IN ('pending', 'provider_succeeded') AND terminal_at IS NULL)"
        " OR (status IN ('provider_failed', 'cancelled', 'confirmed') AND terminal_at IS NOT NULL)",
    ),
    (
        'ck_payment_intents_lifecycle_state',
        "(status = 'pending' AND provider_result_at IS NULL AND cancelled_by_id IS NULL)"
        " OR (status = 'provider_succeeded' AND provider_result_at IS NOT NULL"
        " AND cancelled_by_id IS NULL)"
        " OR (status = 'provider_failed' AND provider_result_at IS NOT NULL"
        " AND terminal_at = provider_result_at AND cancelled_by_id IS NULL)"
        " OR (status = 'cancelled' AND provider_result_at IS NULL AND cancelled_by_id IS NOT NULL)",
        "(status = 'pending' AND provider_result_at IS NULL AND cancelled_by_id IS NULL)"
        " OR (status = 'provider_succeeded' AND provider_result_at IS NOT NULL"
        " AND cancelled_by_id IS NULL)"
        " OR (status IN ('provider_failed', 'confirmed') AND cancelled_by_id IS NULL"
        " AND (provider_result_at IS NULL OR terminal_at >= provider_result_at))"
        " OR (status = 'cancelled' AND provider_result_at IS NULL AND cancelled_by_id IS NOT NULL)",
    ),
)
_INTENT_KEPT_CHECKS = (
    ('ck_payment_intents_provider_valid', "provider IN ('mock')"),
    ('ck_payment_intents_currency_code', "currency_code = 'LYD'"),
    ('ck_payment_intents_amount_range', 'amount >= 0.001 AND amount <= 99999.999'),
    ('ck_payment_intents_version_positive', 'version > 0'),
    ('ck_payment_intents_provider_reference_present', 'LENGTH(provider_reference) > 0'),
    ('ck_payment_intents_idempotency_key_length', 'LENGTH(idempotency_key) = 64'),
    (
        'ck_payment_intents_provider_result_pair',
        "(provider_result_at IS NULL AND provider_result_by_id IS NULL)"
        " OR (provider_result_at IS NOT NULL AND provider_result_by_id IS NOT NULL)",
    ),
    (
        'ck_payment_intents_timestamps_ordered',
        "updated_at >= created_at"
        " AND (provider_result_at IS NULL"
        " OR (provider_result_at >= created_at AND updated_at >= provider_result_at))"
        " AND (terminal_at IS NULL OR (terminal_at >= created_at AND updated_at >= terminal_at))",
    ),
)


def _intents_table(after):
    """The ``payment_intents`` definition with the M06 (``after=False``) or
    M07 CHECKs -- used only as a SQLite rebuild's ``copy_from``."""
    checks = [(name, new if after else old) for name, old, new in _INTENT_CHECKS]
    return sa.Table(
        _INTENTS, sa.MetaData(),
        sa.Column('id', _ID, nullable=False),
        sa.Column('public_id', sa.String(length=36), nullable=False),
        sa.Column('invoice_id', _ID, nullable=False),
        sa.Column('provider', sa.String(length=16), nullable=False),
        sa.Column('provider_reference', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False),
        sa.Column('created_by_id', _ID, nullable=False),
        sa.Column('provider_result_at', sa.DateTime(), nullable=True),
        sa.Column('provider_result_by_id', _ID, nullable=True),
        sa.Column('terminal_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_by_id', _ID, nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *(sa.CheckConstraint(sql, name=name) for name, sql in checks + list(_INTENT_KEPT_CHECKS)),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['provider_result_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['cancelled_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider_reference', name='uq_payment_intents_provider_reference'),
        sa.UniqueConstraint('idempotency_key', name='uq_payment_intents_idempotency_key'),
        sa.UniqueConstraint('public_id'),
        sa.Index('ix_payment_intents_invoice_id_id', 'invoice_id', 'id'),
        sa.Index('ix_payment_intents_status_id', 'status', 'id'),
        sa.Index('ix_payment_intents_created_by_id', 'created_by_id'),
        sa.Index('ix_payment_intents_provider_result_by_id', 'provider_result_by_id'),
        sa.Index('ix_payment_intents_cancelled_by_id', 'cancelled_by_id'),
    )


# ---------------------------------------------------------------------------
# payment_transactions
# ---------------------------------------------------------------------------

_PAYMENT_CHECKS = (
    (
        'ck_payment_transactions_method_valid',
        "method IN ('cash', 'bank_transfer')",
        "method IN ('cash', 'bank_transfer', 'online')",
    ),
    (
        'ck_payment_transactions_bank_transfer_details',
        "(kind = 'collection' AND method = 'bank_transfer'"
        " AND bank_transfer_reference IS NOT NULL AND LENGTH(bank_transfer_reference) > 0"
        " AND bank_transfer_date IS NOT NULL)"
        " OR ((kind = 'reversal' OR method = 'cash')"
        " AND bank_transfer_reference IS NULL AND bank_transfer_date IS NULL)",
        "(kind = 'collection' AND method = 'bank_transfer'"
        " AND bank_transfer_reference IS NOT NULL AND LENGTH(bank_transfer_reference) > 0"
        " AND bank_transfer_date IS NOT NULL)"
        " OR ((kind = 'reversal' OR method IN ('cash', 'online'))"
        " AND bank_transfer_reference IS NULL AND bank_transfer_date IS NULL)",
    ),
    (
        'ck_payment_transactions_confirmation_pair',
        "(confirmed_at IS NULL AND confirmed_by_id IS NULL)"
        " OR (confirmed_at IS NOT NULL AND confirmed_by_id IS NOT NULL)",
        "(confirmed_at IS NULL AND confirmed_by_id IS NULL)"
        " OR (confirmed_at IS NOT NULL AND confirmed_by_id IS NOT NULL)"
        " OR (kind = 'collection' AND method = 'online'"
        " AND confirmed_at IS NOT NULL AND confirmed_by_id IS NULL)",
    ),
    (
        'ck_payment_transactions_immediate_confirmation',
        "(kind = 'collection' AND method = 'bank_transfer')"
        " OR (confirmed_at = recorded_at AND confirmed_by_id = recorded_by_id)",
        "(kind = 'collection' AND method = 'bank_transfer')"
        " OR (kind = 'collection' AND method = 'online' AND confirmed_at = recorded_at"
        " AND recorded_by_id IS NULL AND confirmed_by_id IS NULL)"
        " OR (confirmed_at = recorded_at AND confirmed_by_id = recorded_by_id)",
    ),
)
_ONLINE_ORIGIN_SQL = (
    "(kind = 'collection' AND method = 'online' AND payment_intent_id IS NOT NULL"
    " AND recorded_by_id IS NULL AND confirmed_by_id IS NULL)"
    " OR ((kind <> 'collection' OR method <> 'online') AND payment_intent_id IS NULL"
    " AND recorded_by_id IS NOT NULL)"
)
_PAYMENT_KEPT_CHECKS = (
    ('ck_payment_transactions_kind_valid', "kind IN ('collection', 'reversal')"),
    (
        'ck_payment_transactions_status_valid',
        "status IN ('pending', 'confirmed', 'rejected')",
    ),
    ('ck_payment_transactions_currency_code', "currency_code = 'LYD'"),
    ('ck_payment_transactions_amount_range', 'amount >= 0.001 AND amount <= 99999.999'),
    ('ck_payment_transactions_version_positive', 'version > 0'),
    (
        'ck_payment_transactions_rejection_state',
        "(rejected_at IS NULL AND rejected_by_id IS NULL AND rejection_reason IS NULL)"
        " OR (rejected_at IS NOT NULL AND rejected_by_id IS NOT NULL"
        " AND rejection_reason IS NOT NULL AND LENGTH(rejection_reason) > 0)",
    ),
    (
        'ck_payment_transactions_lifecycle_state',
        "(status = 'pending' AND kind = 'collection' AND method = 'bank_transfer'"
        " AND confirmed_at IS NULL AND rejected_at IS NULL)"
        " OR (status = 'confirmed' AND confirmed_at IS NOT NULL AND rejected_at IS NULL)"
        " OR (status = 'rejected' AND kind = 'collection' AND method = 'bank_transfer'"
        " AND rejected_at IS NOT NULL AND confirmed_at IS NULL)",
    ),
    (
        'ck_payment_transactions_reversal_link',
        "(kind = 'collection' AND reversal_of_payment_transaction_id IS NULL)"
        " OR (kind = 'reversal' AND reversal_of_payment_transaction_id IS NOT NULL)",
    ),
    (
        'ck_payment_transactions_timestamps_ordered',
        "recorded_at >= created_at AND updated_at >= recorded_at"
        " AND (confirmed_at IS NULL OR (confirmed_at >= recorded_at AND updated_at >= confirmed_at))"
        " AND (rejected_at IS NULL OR (rejected_at >= recorded_at AND updated_at >= rejected_at))",
    ),
)


def _payments_table(after, existing_m07=False):
    """The ``payment_transactions`` definition with the M05 (``after=False``)
    or M07 CHECKs -- used only as a SQLite rebuild's ``copy_from``. The M07
    ``online_origin`` CHECK and the ``payment_intent_id`` column are added by
    the batch itself. `existing_m07` describes the M07 table a downgrade
    starts from: a nullable ``recorded_by_id`` and a plain
    ``payment_intent_id`` column, without its keys, which the batch drops."""
    checks = [(name, new if after else old) for name, old, new in _PAYMENT_CHECKS]
    columns = [
        sa.Column('id', _ID, nullable=False),
        sa.Column('public_id', sa.String(length=36), nullable=False),
        sa.Column('invoice_id', _ID, nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('method', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False),
        sa.Column('bank_transfer_reference', sa.String(length=64), nullable=True),
        sa.Column('bank_transfer_date', sa.Date(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.Column('recorded_by_id', _ID, nullable=existing_m07),
        sa.Column('confirmed_at', sa.DateTime(), nullable=True),
        sa.Column('confirmed_by_id', _ID, nullable=True),
        sa.Column('rejected_at', sa.DateTime(), nullable=True),
        sa.Column('rejected_by_id', _ID, nullable=True),
        sa.Column('rejection_reason', sa.String(length=500), nullable=True),
        sa.Column('reversal_of_payment_transaction_id', _ID, nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    ]
    if existing_m07:
        columns.append(sa.Column('payment_intent_id', _ID, nullable=True))
    return sa.Table(
        _PAYMENTS, sa.MetaData(),
        *columns,
        *(sa.CheckConstraint(sql, name=name) for name, sql in checks + list(_PAYMENT_KEPT_CHECKS)),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
        sa.ForeignKeyConstraint(['recorded_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['confirmed_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['rejected_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(
            ['reversal_of_payment_transaction_id'], ['payment_transactions.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'reversal_of_payment_transaction_id', name='uq_payment_transactions_reversal_of'
        ),
        sa.UniqueConstraint('public_id'),
        sa.Index('ix_payment_transactions_invoice_id_id', 'invoice_id', 'id'),
        sa.Index('ix_payment_transactions_status_id', 'status', 'id'),
        sa.Index('ix_payment_transactions_method_id', 'method', 'id'),
        sa.Index('ix_payment_transactions_recorded_by_id', 'recorded_by_id'),
        sa.Index('ix_payment_transactions_confirmed_by_id', 'confirmed_by_id'),
        sa.Index('ix_payment_transactions_rejected_by_id', 'rejected_by_id'),
    )


# ---------------------------------------------------------------------------
# receipts
# ---------------------------------------------------------------------------

_RECEIPT_CHECKS = (
    ('ck_receipts_status_valid', "status IN ('issued', 'voided')"),
    ('ck_receipts_version_positive', 'version > 0'),
    (
        'ck_receipts_number_format',
        "receipt_number LIKE 'RCT-____-______' AND LENGTH(receipt_number) = 15",
    ),
    (
        'ck_receipts_void_state',
        "(status = 'issued' AND voided_at IS NULL AND voided_by_id IS NULL AND void_reason IS NULL)"
        " OR (status = 'voided' AND voided_at IS NOT NULL AND voided_by_id IS NOT NULL"
        " AND void_reason IS NOT NULL AND LENGTH(void_reason) > 0)",
    ),
    (
        'ck_receipts_timestamps_ordered',
        "issued_at >= created_at AND updated_at >= issued_at"
        " AND (voided_at IS NULL OR (voided_at >= issued_at AND updated_at >= voided_at))",
    ),
)


def _receipts_table():
    """The ``receipts`` definition -- used only as a SQLite rebuild's
    ``copy_from``; the batch changes ``issued_by_id``'s nullability."""
    return sa.Table(
        _RECEIPTS, sa.MetaData(),
        sa.Column('id', _ID, nullable=False),
        sa.Column('public_id', sa.String(length=36), nullable=False),
        sa.Column('payment_transaction_id', _ID, nullable=False),
        sa.Column('receipt_number', sa.String(length=15), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('issued_by_id', _ID, nullable=False),
        sa.Column('voided_at', sa.DateTime(), nullable=True),
        sa.Column('voided_by_id', _ID, nullable=True),
        sa.Column('void_reason', sa.String(length=500), nullable=True),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        *(sa.CheckConstraint(sql, name=name) for name, sql in _RECEIPT_CHECKS),
        sa.ForeignKeyConstraint(['payment_transaction_id'], ['payment_transactions.id'], ),
        sa.ForeignKeyConstraint(['issued_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['voided_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('payment_transaction_id', name='uq_receipts_payment_transaction_id'),
        sa.UniqueConstraint('receipt_number', name='uq_receipts_receipt_number'),
        sa.UniqueConstraint('public_id'),
        sa.Index('ix_receipts_issued_by_id', 'issued_by_id'),
        sa.Index('ix_receipts_voided_by_id', 'voided_by_id'),
    )


# ---------------------------------------------------------------------------
# payment_audit_events
# ---------------------------------------------------------------------------

_AUDIT_CHECKS = (
    (
        'ck_payment_audit_events_kind_valid',
        "kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'invoice_issued_edited', 'invoice_cancelled', 'payment_cash_recorded',"
        " 'payment_bank_transfer_recorded', 'payment_bank_transfer_confirmed',"
        " 'payment_bank_transfer_rejected', 'payment_reversed', 'receipt_issued',"
        " 'receipt_voided')",
        "kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'invoice_issued_edited', 'invoice_cancelled', 'payment_cash_recorded',"
        " 'payment_bank_transfer_recorded', 'payment_bank_transfer_confirmed',"
        " 'payment_bank_transfer_rejected', 'payment_reversed', 'receipt_issued',"
        " 'receipt_voided', 'payment_online_confirmed', 'receipt_online_issued')",
    ),
    (
        'ck_payment_audit_events_version_transition',
        "(kind = 'invoice_draft_created' AND invoice_version_before IS NULL"
        " AND invoice_version_after = 1)"
        " OR (kind IN ('invoice_cancelled', 'invoice_draft_edited', 'invoice_issued',"
        " 'invoice_issued_edited') AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before + 1)"
        " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_reversed',"
        " 'receipt_issued', 'receipt_voided') AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before)",
        "(kind = 'invoice_draft_created' AND invoice_version_before IS NULL"
        " AND invoice_version_after = 1)"
        " OR (kind IN ('invoice_cancelled', 'invoice_draft_edited', 'invoice_issued',"
        " 'invoice_issued_edited') AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before + 1)"
        " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_online_confirmed',"
        " 'payment_reversed', 'receipt_issued', 'receipt_online_issued', 'receipt_voided')"
        " AND invoice_version_before IS NOT NULL"
        " AND invoice_version_after = invoice_version_before)",
    ),
    (
        'ck_payment_audit_events_reason_required',
        "(kind IN ('invoice_cancelled', 'invoice_issued_edited', 'payment_bank_transfer_rejected',"
        " 'payment_reversed', 'receipt_voided') AND reason IS NOT NULL AND LENGTH(reason) > 0)"
        " OR (kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_cash_recorded', 'receipt_issued') AND reason IS NULL)",
        "(kind IN ('invoice_cancelled', 'invoice_issued_edited', 'payment_bank_transfer_rejected',"
        " 'payment_reversed', 'receipt_voided') AND reason IS NOT NULL AND LENGTH(reason) > 0)"
        " OR (kind IN ('invoice_draft_created', 'invoice_draft_edited', 'invoice_issued',"
        " 'payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_cash_recorded', 'payment_online_confirmed', 'receipt_issued',"
        " 'receipt_online_issued') AND reason IS NULL)",
    ),
    (
        'ck_payment_audit_events_subject_links',
        "(kind IN ('invoice_cancelled', 'invoice_draft_created', 'invoice_draft_edited',"
        " 'invoice_issued', 'invoice_issued_edited')"
        " AND payment_transaction_id IS NULL AND receipt_id IS NULL)"
        " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_reversed')"
        " AND payment_transaction_id IS NOT NULL AND receipt_id IS NULL)"
        " OR (kind IN ('receipt_issued', 'receipt_voided')"
        " AND payment_transaction_id IS NOT NULL AND receipt_id IS NOT NULL)",
        "(kind IN ('invoice_cancelled', 'invoice_draft_created', 'invoice_draft_edited',"
        " 'invoice_issued', 'invoice_issued_edited')"
        " AND payment_transaction_id IS NULL AND receipt_id IS NULL)"
        " OR (kind IN ('payment_bank_transfer_confirmed', 'payment_bank_transfer_recorded',"
        " 'payment_bank_transfer_rejected', 'payment_cash_recorded', 'payment_online_confirmed',"
        " 'payment_reversed') AND payment_transaction_id IS NOT NULL AND receipt_id IS NULL)"
        " OR (kind IN ('receipt_issued', 'receipt_online_issued', 'receipt_voided')"
        " AND payment_transaction_id IS NOT NULL AND receipt_id IS NOT NULL)",
    ),
)
_ACTOR_ORIGIN_SQL = (
    "(kind IN ('payment_online_confirmed', 'receipt_online_issued') AND actor_id IS NULL)"
    " OR (kind NOT IN ('payment_online_confirmed', 'receipt_online_issued')"
    " AND actor_id IS NOT NULL)"
)
_AUDIT_KEPT_CHECKS = (
    (
        'ck_payment_audit_events_versions_positive',
        "invoice_version_after > 0"
        " AND (invoice_version_before IS NULL OR invoice_version_before > 0)",
    ),
    (
        'ck_payment_audit_events_snapshots_present',
        "(kind = 'invoice_draft_created' AND before_snapshot IS NULL"
        " AND after_snapshot IS NOT NULL)"
        " OR (kind <> 'invoice_draft_created' AND before_snapshot IS NOT NULL"
        " AND after_snapshot IS NOT NULL)",
    ),
)


def _audit_events_table(after):
    """The ``payment_audit_events`` definition with the M05 (``after=False``)
    or M07 CHECKs -- used only as a SQLite rebuild's ``copy_from``; the M07
    ``actor_origin`` CHECK is added by the batch itself."""
    checks = [(name, new if after else old) for name, old, new in _AUDIT_CHECKS]
    return sa.Table(
        _EVENTS, sa.MetaData(),
        sa.Column('id', _ID, nullable=False),
        sa.Column('invoice_id', _ID, nullable=False),
        sa.Column('actor_id', _ID, nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('invoice_version_before', sa.Integer(), nullable=True),
        sa.Column('invoice_version_after', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('before_snapshot', sa.JSON(), nullable=True),
        sa.Column('after_snapshot', sa.JSON(), nullable=False),
        sa.Column('payment_transaction_id', _ID, nullable=True),
        sa.Column('receipt_id', _ID, nullable=True),
        *(sa.CheckConstraint(sql, name=name) for name, sql in checks + list(_AUDIT_KEPT_CHECKS)),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(
            ['payment_transaction_id'], ['payment_transactions.id'],
            name='fk_payment_audit_events_payment_transaction_id',
        ),
        sa.ForeignKeyConstraint(
            ['receipt_id'], ['receipts.id'], name='fk_payment_audit_events_receipt_id'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_payment_audit_events_invoice_id_id', 'invoice_id', 'id'),
        sa.Index('ix_payment_audit_events_actor_id', 'actor_id'),
        sa.Index('ix_payment_audit_events_payment_transaction_id', 'payment_transaction_id'),
        sa.Index('ix_payment_audit_events_receipt_id', 'receipt_id'),
    )


# ---------------------------------------------------------------------------
# The downgrade's refusal
# ---------------------------------------------------------------------------

_M07_ROWS_SQL = (
    "SELECT (SELECT COUNT(*) FROM payment_provider_events)"
    " + (SELECT COUNT(*) FROM payment_transactions WHERE method = 'online'"
    " OR payment_intent_id IS NOT NULL OR recorded_by_id IS NULL)"
    " + (SELECT COUNT(*) FROM receipts WHERE issued_by_id IS NULL)"
    " + (SELECT COUNT(*) FROM payment_audit_events"
    " WHERE kind IN ('payment_online_confirmed', 'receipt_online_issued') OR actor_id IS NULL)"
    " + (SELECT COUNT(*) FROM payment_intents WHERE status = 'confirmed'"
    " OR (status = 'provider_failed'"
    " AND (provider_result_at IS NULL OR terminal_at <> provider_result_at)))"
)


def _sqlite(bind):
    return bind.dialect.name == 'sqlite'


def _require_sqlite_foreign_keys_off(bind):
    if bind.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError(
            "On SQLite this revision rebuilds tables other tables reference, which SQLite "
            "allows only with PRAGMA foreign_keys=OFF; run it with foreign keys disabled."
        )


def _prove_sqlite_references(bind):
    if bind.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError("A rebuild left a dangling reference.")


def _replace_checks(table, checks, after):
    for name, old, new in checks:
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, new if after else old)


def _create_inbox():
    op.create_table('payment_provider_events',
    sa.Column('id', _ID, nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=False),
    sa.Column('provider_event_id', sa.String(length=64), nullable=False),
    sa.Column('payment_intent_id', _ID, nullable=False),
    sa.Column('event_type', sa.String(length=32), nullable=False),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('amount', sa.DECIMAL(precision=19, scale=4), nullable=False),
    sa.Column('provider_occurred_at', sa.DateTime(), nullable=False),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('processed_at', sa.DateTime(), nullable=False),
    sa.Column('payload_digest', sa.String(length=64), nullable=False),
    sa.Column('outcome', sa.String(length=32), nullable=False),
    sa.Column('payment_transaction_id', _ID, nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("provider IN ('mock')", name='ck_payment_provider_events_provider_valid'),
    sa.CheckConstraint(
        "event_type IN ('payment.succeeded', 'payment.failed')",
        name='ck_payment_provider_events_event_type_valid',
    ),
    sa.CheckConstraint(
        "outcome IN ('confirmed', 'failed', 'duplicate', 'ignored_terminal',"
        " 'reconciliation_required')",
        name='ck_payment_provider_events_outcome_valid',
    ),
    sa.CheckConstraint("currency_code = 'LYD'", name='ck_payment_provider_events_currency_code'),
    sa.CheckConstraint(
        'amount >= 0.001 AND amount <= 99999.999', name='ck_payment_provider_events_amount_range'
    ),
    sa.CheckConstraint(
        'LENGTH(provider_event_id) > 0', name='ck_payment_provider_events_event_id_present'
    ),
    sa.CheckConstraint(
        'LENGTH(payload_digest) = 64', name='ck_payment_provider_events_payload_digest_length'
    ),
    sa.CheckConstraint(
        "(outcome = 'confirmed' AND event_type = 'payment.succeeded'"
        " AND payment_transaction_id IS NOT NULL)"
        " OR (outcome <> 'confirmed' AND payment_transaction_id IS NULL)",
        name='ck_payment_provider_events_outcome_link',
    ),
    sa.CheckConstraint(
        "outcome <> 'failed' OR event_type = 'payment.failed'",
        name='ck_payment_provider_events_outcome_type',
    ),
    sa.CheckConstraint(
        'processed_at >= received_at AND created_at >= received_at',
        name='ck_payment_provider_events_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['payment_intent_id'], ['payment_intents.id'], ),
    sa.ForeignKeyConstraint(['payment_transaction_id'], ['payment_transactions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint(
        'provider', 'provider_event_id', name='uq_payment_provider_events_provider_event'
    ),
    sa.UniqueConstraint(
        'payment_transaction_id', name='uq_payment_provider_events_payment_transaction_id'
    ),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_payment_provider_events_intent_id_id',
        'payment_provider_events',
        ['payment_intent_id', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_payment_provider_events_outcome_id',
        'payment_provider_events',
        ['outcome', 'id'],
        unique=False,
    )


def upgrade():
    bind = op.get_bind()
    if _sqlite(bind):
        _require_sqlite_foreign_keys_off(bind)
        with op.batch_alter_table(_INTENTS, copy_from=_intents_table(True), recreate='always'):
            pass
        with op.batch_alter_table(
            _PAYMENTS, copy_from=_payments_table(True), recreate='always'
        ) as batch_op:
            batch_op.alter_column('recorded_by_id', existing_type=_ID, nullable=True)
            batch_op.add_column(sa.Column('payment_intent_id', _ID, nullable=True))
            batch_op.create_unique_constraint(_INTENT_UNIQUE, ['payment_intent_id'])
            batch_op.create_foreign_key(_INTENT_FK, _INTENTS, ['payment_intent_id'], ['id'])
            batch_op.create_check_constraint(_ONLINE_ORIGIN, _ONLINE_ORIGIN_SQL)
        with op.batch_alter_table(
            _RECEIPTS, copy_from=_receipts_table(), recreate='always'
        ) as batch_op:
            batch_op.alter_column('issued_by_id', existing_type=_ID, nullable=True)
        with op.batch_alter_table(
            _EVENTS, copy_from=_audit_events_table(True), recreate='always'
        ) as batch_op:
            batch_op.alter_column('actor_id', existing_type=_ID, nullable=True)
            batch_op.create_check_constraint(_ACTOR_ORIGIN, _ACTOR_ORIGIN_SQL)
        _prove_sqlite_references(bind)
        _create_inbox()
        return

    _replace_checks(_INTENTS, _INTENT_CHECKS, after=True)

    for name, _old, _new in _PAYMENT_CHECKS:
        op.drop_constraint(name, _PAYMENTS, type_='check')
    op.alter_column(_PAYMENTS, 'recorded_by_id', existing_type=_ID, nullable=True)
    op.add_column(_PAYMENTS, sa.Column('payment_intent_id', _ID, nullable=True))
    op.create_unique_constraint(_INTENT_UNIQUE, _PAYMENTS, ['payment_intent_id'])
    op.create_foreign_key(_INTENT_FK, _PAYMENTS, _INTENTS, ['payment_intent_id'], ['id'])
    for name, _old, new in _PAYMENT_CHECKS:
        op.create_check_constraint(name, _PAYMENTS, new)
    op.create_check_constraint(_ONLINE_ORIGIN, _PAYMENTS, _ONLINE_ORIGIN_SQL)

    op.alter_column(_RECEIPTS, 'issued_by_id', existing_type=_ID, nullable=True)

    for name, _old, _new in _AUDIT_CHECKS:
        op.drop_constraint(name, _EVENTS, type_='check')
    op.alter_column(_EVENTS, 'actor_id', existing_type=_ID, nullable=True)
    for name, _old, new in _AUDIT_CHECKS:
        op.create_check_constraint(name, _EVENTS, new)
    op.create_check_constraint(_ACTOR_ORIGIN, _EVENTS, _ACTOR_ORIGIN_SQL)

    _create_inbox()


def downgrade():
    bind = op.get_bind()
    if not op.get_context().as_sql:
        existing = bind.execute(sa.text(_M07_ROWS_SQL)).scalar()
        if existing:
            raise RuntimeError(
                f"Refusing to downgrade: {existing} provider event, online payment, online "
                "receipt, system audit event or webhook-decided intent row(s) exist. The previous "
                "schema cannot represent that history, and this revision never deletes it."
            )
    if _sqlite(bind):
        _require_sqlite_foreign_keys_off(bind)
    # One statement for the inbox. Dropping its indexes first would fail on
    # MySQL with errno 1553, and DROP TABLE removes them anyway.
    op.drop_table(_INBOX)
    if _sqlite(bind):
        with op.batch_alter_table(
            _EVENTS, copy_from=_audit_events_table(False), recreate='always'
        ) as batch_op:
            batch_op.alter_column('actor_id', existing_type=_ID, nullable=False)
        with op.batch_alter_table(
            _RECEIPTS, copy_from=_receipts_table(), recreate='always'
        ) as batch_op:
            batch_op.alter_column('issued_by_id', existing_type=_ID, nullable=False)
        with op.batch_alter_table(
            _PAYMENTS, copy_from=_payments_table(False, existing_m07=True), recreate='always'
        ) as batch_op:
            batch_op.drop_column('payment_intent_id')
            batch_op.alter_column('recorded_by_id', existing_type=_ID, nullable=False)
        with op.batch_alter_table(_INTENTS, copy_from=_intents_table(False), recreate='always'):
            pass
        _prove_sqlite_references(bind)
        return

    op.drop_constraint(_ACTOR_ORIGIN, _EVENTS, type_='check')
    for name, _old, _new in _AUDIT_CHECKS:
        op.drop_constraint(name, _EVENTS, type_='check')
    op.alter_column(_EVENTS, 'actor_id', existing_type=_ID, nullable=False)
    for name, old, _new in _AUDIT_CHECKS:
        op.create_check_constraint(name, _EVENTS, old)

    op.alter_column(_RECEIPTS, 'issued_by_id', existing_type=_ID, nullable=False)

    op.drop_constraint(_ONLINE_ORIGIN, _PAYMENTS, type_='check')
    for name, _old, _new in _PAYMENT_CHECKS:
        op.drop_constraint(name, _PAYMENTS, type_='check')
    op.drop_constraint(_INTENT_FK, _PAYMENTS, type_='foreignkey')
    op.drop_constraint(_INTENT_UNIQUE, _PAYMENTS, type_='unique')
    op.drop_column(_PAYMENTS, 'payment_intent_id')
    op.alter_column(_PAYMENTS, 'recorded_by_id', existing_type=_ID, nullable=False)
    for name, old, _new in _PAYMENT_CHECKS:
        op.create_check_constraint(name, _PAYMENTS, old)

    _replace_checks(_INTENTS, _INTENT_CHECKS, after=False)
