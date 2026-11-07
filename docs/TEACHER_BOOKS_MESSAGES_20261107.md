# Teacher book controls, message acknowledgement and device previews

## Owner scope

The owner selected independent Student book and Workbook publication, replacing
their previous shared release decisions. Teacher Home uses Groups, Students and
My groups, removes explanatory review/enrollment copy, and exposes Manage course
(book choice), Add activity (type choice), and Teacher book. Preserve the native
role/assignment, academic hierarchy, CSRF, lock and stale-form checks.

The owner also requested actual notification title/message previews and removal
of misleading Messages saving/uncertain-result banners. Remove the duplicate
device-notification controls from Account settings; keep the account-menu entry.

## Publication and preservation

Revision `d6e8f9012abc`, parent `c5d7e8f901ab`, adds one InnoDB table,
`workbook_lesson_releases`. Existing lesson/material releases continue to govern
the Student book. Workbook decisions require their own explicit release row.
Backfill current mapped Workbook lessons using the original configured opening
times, including hidden, scheduled and group-owned default-visible content.
Deduplicate page mappings before insertion. Previously published notices are not
replayed; future scheduled batches have a separate deterministic book notice key.

Publication forms carry the selected book and a snapshot bound to that book,
its current document/map and releases. A write rechecks the native locked Group
scope. Publish/hide/schedule for one book never writes the other book's releases.
Workbook files/media, outlines and completion recheck Workbook access. A physical
page shared by several lessons stays hidden until every mapped lesson is visible;
unmapped answer pages remain private. Group curriculum copies preserve Workbook
maps and independent decisions. Existing episode-bound Lesson progress remains
shared and unchanged: this request separates publication, not progress history.

## Messages and push

Filtered success flashes were also used as AJAX send proof, so a committed send
could appear unconfirmed. A successful native transaction now returns a private
JSON acknowledgement to the enhanced Messages client. Ordinary HTML forms keep
their redirect workflow; older clients retain explicit raw outcome markers.
The client checks actor/session and same-origin thread identity, clears the sent
draft only after acknowledgement, then performs a read-only conversation GET.
POST is never automatically retried. An unknown outcome preserves the draft and
prevents unsafe resend; read recovery stays beside the composer. Generic dirty
status prose is hidden in Messages, while real draft/unload protection remains.

Encrypted Web Push uses the recipient's latest unread stored notification title
and message rather than a generic update. It does not retrieve extra private
message bodies. Permission, subscription ownership, auth-version revocation,
expiry, cursor/retry and the safe Notifications destination remain intact.
This explicitly supersedes the earlier generic lock-screen preview policy.

## Verification and deployment boundary

Use Python AST, Node syntax, Jinja compilation, mapper/MySQL DDL and Alembic-chain
review, then an exact local MySQL migration with a private backup and row
fingerprints. No automated application test package or fabricated sends,
notifications, progress or research events. Browser/physical-device acceptance
remains owner review under the existing browser-access limitation.

Deployment is only to the new VPS, after the standing service-impact approval.
Take a protected database backup before the additive migration. Preserve source
rollback and all data; do not downgrade/drop the new table. Before first promotion
the backfill preserves old visibility, allowing automatic recovery to the prior
source if activation fails. After independent publication choices are used, a
rollback to old coupled-publication code needs a separate compatibility review.
Existing accepted readiness-status limitations are outside this scope. Private
operator receipts establish executed migration, source/Git and activation facts.
