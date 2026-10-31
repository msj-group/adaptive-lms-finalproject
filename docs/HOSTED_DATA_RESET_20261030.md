# Owner-authorized hosted data reset — 2026-10-30

## Authority and exact scope

The owner explicitly requested clearing the hosted database and adding only
two Teachers, their classes and one Administrator, all using the supplied
initial password. The owner confirmed the email addresses, academic hierarchy,
Saturday 18:00–20:00, the current term, and capacity 20 for each class. A separate
explicit approval covered one temporary SSH key and its immediate revocation.
This is a new bounded authorization after the completed v2 deployment; it does
not authorize clearing the local database, restoring old data or future resets.

Target: existing Railway AdaptiveEnglishLMS production, project
`c0fa2362-4e1e-4412-908a-1b4cbee38efd`, web service
`0134642d-f6f5-4e40-9f16-6fc389502d9c`, environment
`78c6b914-a09a-4a0e-81a9-ec82cd22a5da`, private MySQL database `railway`.
The running release remains `b33535e568ae5a2a3b9720aeedbbb8bfec1bb252` and the
schema remains `a6f2c9d84e71`. No deployment or migration was performed.

## Final accounts and teaching setup

| Account | Email | Role / assigned Group |
|---|---|---|
| Administrator | admin@youth.com | Administrator |
| امال المحجوب | amal.almahjoub@youth.com | Teacher / A1 |
| سلوى الاجنف | salwa.alajnaf@youth.com | Teacher / A2 |

All three accounts are active and use the owner's supplied initial password,
stored as Argon2id hashes. No plaintext password is recorded in this document
or the operator source. Self-service password change remains under Account
settings; no forced-change mechanism was added.

The owner-confirmed hierarchy is Level `Upper Intermediate`, Course `C1`,
Groups `A1` and `A2`. Each has capacity 20 and its own eligible Teacher. The
current existing term was `Autumn 2026`, September 3–December 27. New classes
begin Saturday October 31 at 18:00–20:00 Africa/Tripoli, recurring on Saturdays
through that term's end (last occurrence December 26). Group study start is
October 31 16:00 UTC. No room or Course price was invented; both remain unset.
Enrollment requires a separately configured Course price through normal admin
workflow before use. There are no Students or Researcher accounts.

## Execution, recovery copy and integrity

Read-only preflight confirmed MySQL/InnoDB, all 86 tables, the exact head,
22141 rows, no stored triggers/routines/events/views, paused collection and no
open Research sessions. The sole web worker drained while the master was
temporarily paused, with a bounded safety-resumption helper.

Automatic approval review refused a full database download to local storage
because that sensitive-data destination had not been explicitly authorized.
That download did not execute. Web service was resumed while preparing a
safer operation that kept all backup bytes on the existing hosting volume.

The fresh typed snapshot remains only at the private hosted directory
`/data/.owner-reset-backup-20261030` (directory 0700, files 0600). It contains
all 86 tables' DDL, foreign keys, column metadata and all 22141 pre-reset rows,
plus a manifest, verification helpers and recovery procedure. Full gzip/JSON,
typed value round-trip, table counts and multiset fingerprints passed.

- Compressed bytes: 1124038.
- Gzip SHA256: `562f40121434390a07067c110c7cc1c8ba111e666a8ef65907ccbf0db95d881e`.
- Inventory SHA256: `a1ad9728dbad2b0c0a492739e9d0e8c4783db9a2e74f40ec9297805dc3c8a677`.
- This is a same-volume recovery copy, not protection against volume loss.
- No restore rehearsal, backup download or automatic backup-deletion policy ran.
  Any restore, off-host copy or later deletion requires separately scoped owner
  authorization. Existing protected historical local backups were untouched.

Before deletion, the operator locked the rows and compared the complete
current inventory and typed row hashes against that backup. It then deleted
all 22140 non-Alembic rows and inserted the requested setup in one InnoDB
transaction. Child-first table ordering and explicit payment self-reference
leaf deletion retained FK enforcement throughout. No TRUNCATE, DROP, ALTER,
foreign-key disabling or reset of auto-increment counters was used. Retaining
the user counter prevents old signed login identities from resolving to new
accounts with reused IDs. Fresh public UUIDs were generated.

Post-reset review found the required Research control singleton was also
cleared. A bounded follow-up transaction restored only
`research_configuration_sequence(id=1,last_number=0)`; it contains no account,
configuration or study data. Final state is 14 rows: three Users, one Term,
one Level, one Course, two Groups, two Teacher assignments, two Schedules,
the Alembic head and the technical control singleton. Every other business
and Research table is empty. All existing private application files remain
on the host; old records no longer reference them. No physical disk-reclamation
claim is made.

## Verification and remaining boundary

A separate committed READ ONLY MySQL snapshot verified all table counts,
three exact active account identities, both correct Teacher assignments,
capacity and schedule values, non-reused old user IDs, enabled FK/CHECK
enforcement, the exact head and unchanged normalized DDL for all 86 tables.

Actual browser sign-in succeeded for all three accounts using the supplied
password. Admin displayed 0 active Students, 2 Groups, 2 Teachers and both
October 31 18:00–20:00 classes. Each Teacher saw only her own assigned Group
and correct next class. Verification sessions were signed out. Screenshots
and private operator receipts are in ignored `instance/hosted_reset_20261030`;
no database payload is stored there. The temporary SSH key was revoked,
provider absence verified and both local key files removed. The pre-existing
operations key was untouched. Provider inventory still reports the original
two successful services, original volumes and no staged changes.
The public `/health/ready` check returned HTTP 200 with `status=ready` after
resumption and key revocation.

No automated application tests or pytest ran. No application source, local
database, migration, scheduled task, hosting variable, deployment, paid
resource or Git staging/commit/push was changed. Only this operational report
and the three current authority/status/plan notices were updated in tracked
documentation. Existing mixed worktree changes were preserved.
The operation's tracked-document diff whitespace check passed (exit 0).
The full mixed-worktree `git diff --check` remains exit 2 with pre-existing
whitespace/CRLF findings outside these operational documentation additions;
no unrelated formatting cleanup was performed. The staged file list is empty.

The prior hosted Research configuration, subjects, collection history and
exports are intentionally gone from the active database. No collection is
active. Future Researcher provisioning, protocol/configuration setup and
study/pilot/monitoring acceptance require separate owner scope; none was
silently recreated by this reset.
