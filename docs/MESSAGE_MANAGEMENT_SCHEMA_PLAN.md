# Message display history — approved schema and activation

Prepared 2026-10-07 for the owner's messaging request. The owner separately
approved creation, local application and manual fictional-account review.

Applied single additive revision: `6b3a8c2d9041`, parent `085b7a4e9012`.
The local MySQL database was verified at that exact parent before execution
and at the new revision afterward. Unexpected state was not repaired.

## Additions

| Table | Columns | Integrity and indexes |
| --- | --- | --- |
| `message_changes` | BIGINT primary `id`; unique UUID `public_id`; `message_id`; `kind` VARCHAR(8); nullable `body` VARCHAR(5000); unique `creation_nonce` VARCHAR(64); UTC `created_at` DATETIME | FK to `messages.id`; index `(message_id, id)`; CHECK: `edit` with nonblank body, or `hide` with NULL body |
| `message_thread_clears` | BIGINT primary `id`; unique UUID `public_id`; `member_id`; `through_message_id`; unique `creation_nonce` VARCHAR(64); UTC `created_at` DATETIME | FKs to `message_thread_members.id` and `messages.id`; index `(member_id, id)`; index on the message FK |

Use the project's MySQL/InnoDB and utf8mb4 defaults. No existing column changes,
backfill, business-row deletion, seed, cascade, reset or account changes.
Original body and sender identity remain in `messages`; clear ownership derives
from its membership FK. ORM history records reject updates and deletes.

## Activation and behavior

- Until both tables exist, original sending/reading works and management routes
  reject safely. Management controls are unavailable. A read-only capability
  check refreshes after ten seconds; no runtime table creation occurs.
- Edit and hide lock the active acting account, thread, members and owned
  message, in the same suffix order as sends. Historical membership suffices
  for managing one's own messages; sending still requires an active academic
  relationship. Every action uses CSRF and a signed actor/thread/target token.
- Edit/hide tokens bind the displayed change UUID (or `original`). Concurrent
  stale changes are refused. Nonces prevent duplicate replay under the thread
  lock. Hide is terminal and excludes the message for both members.
- Clear tokens bind the newest original message UUID. A new reply invalidates
  the older form. A member clear hides messages through that watermark only;
  future messages reappear. Other members retain their original display scope.
- Effective-message queries apply these rules before pagination, inbox latest
  message selection and dashboard previews. Edited content is escaped text.

## Authorization boundary

`AGENTS.md`, section 2, requires separate authorization for migration
creation/application and real-MySQL mutation. The owner granted that approval
on 2026-10-07 for this exact revision and ordinary manual verification using
the existing fictional accounts. No reset, seed, automated tests, staging,
commit or push is included or performed.

## Executed checks

- Local MySQL 8.0.46, verified loopback/local host and parent `085b7a4e9012`.
- Applied only this revision through Flask-Migrate/Alembic; resulting revision
  is `6b3a8c2d9041`. Both new tables use InnoDB and utf8mb4.
- Before/after full-column fingerprints of the three original messaging
  tables match: 3 threads, 6 memberships and 7 messages retained unchanged.
- Inspected columns, keys, checks and indexes. A read-only Alembic comparison
  scoped to the two new tables found zero model/schema differences.
- The running application and local emoji/JavaScript assets return HTTP 200.
  The Administrator session receives HTTP 403 for `/messages`, as intended.

## Authenticated manual review

The owner supplied an existing fictional-account credential for ordinary
sign-in. The local server was restarted to load the changed Python routes;
no credentials or roles were changed and authentication was not bypassed.

- Student Enter sending persisted a new fictional message with a newline and
  picker-inserted emoji. Shift+Enter inserted the newline without sending.
- An empty edit was refused with the draft retained. A valid edit persisted
  and appeared with the Edited marker in both participants' views and in
  dashboard previews. The recipient had no controls for the sender's message.
- Deleting that new message hid it for both participants and removed it from
  the newest-message preview. There was no deleted-message placeholder.
- The Student cleared the existing fictional conversation through its current
  watermark: it disappeared from that Student's inbox while the Teacher's
  three original visible messages remained. A subsequent normal Teacher reply
  made the conversation reappear for the Student with exactly that one new
  message; older history stayed excluded for the Student.
- A read-only MySQL inspection afterward found 3 threads, 6 memberships,
  9 original messages (7 previous plus 2 ordinary manual-review sends),
  2 change rows (one edit, one hide) and 1 member clear. The new Student
  message's original body remains unchanged after its edit and hide.

These are bounded ordinary UI checks, not automated regression/security tests
or concurrent-transaction acceptance. InnoDB race/stale-form concurrency has
not been exercised. No reset, seed, password change or Git mutation occurred.
