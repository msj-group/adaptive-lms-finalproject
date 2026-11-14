# Personal book notes — 2026-11-14

Owner scope: add the pen toolbar to book reading and keep each account's notes
on all its devices. Also cancel the newly added games and correct the media
labels without changing the original course PDFs, publication or progress.

- Student book and Workbook readers, and the authorized Teacher/Admin course
  book readers share pen, highlight, text, eraser and session undo/redo tools.
- Page coordinates are normalized. Notes follow book zoom and fullscreen.
  Two-finger zoom cancels an unfinished ink gesture instead of saving stray ink.
- Migration `6cd45123ae90` adds `book_annotations` after `4bc2831bd401`.
  Ownership is the authenticated account plus exact PDF SHA256 and page number.
  Original PDFs are unchanged; notes are not inserted into downloaded PDFs.
- Existing enrollment, assignment, book and publication guards authorize each
  request. Writes require CSRF, an explicit matching account header, bounded
  validated marks and a current-account lock. UUID operations are retryable;
  separate device additions merge without replacing the entire page snapshot.
- Connected visible pages synchronize automatically. Unsent operations are
  retried from an account/PDF-scoped local queue. Cross-device availability
  requires successful synchronization; offline edits remain on the originating
  device until it reconnects with the same account.
- Games, their player and editor routes/assets are removed. Existing game
  records are unpublished; the applied historical migration/table is retained.
  Native course activity help and synchronized media captions remain available.
- Arabic media labels are `انقر للمشاهدة` and `انقر للاستماع`, on one transparent
  line with page-relative sizing/position and reduced-motion-aware animation.

Verified: bounded assertions for mark validation; real headless Chromium ink,
text, undo/redo, reload, responsive widths 320/390/1280, emulated touch pinch
and fullscreen. The actual migration, database constraints, account isolation,
two-device reads/writes, idempotency, CSRF and account-switch protection passed
in a separately owned MySQL schema; its synthetic rows/schema/user were removed.
No synthetic observations were sent to Study collection.

VPS release `20261114T072500Z-77b8f39f63` is active at schema `6cd45123ae90`.
HTTPS login/readiness returned 200; web, push and Study collection were active
after deployment. A private full database backup and prior source release exist.
The initial rollout preceded the GitHub synchronization. Later reader changes
use a draggable Circle toolbar, account-scoped device preferences, idle edge
docking, and redesigned draggable media controls. They were checked in
Chromium and on the connected Galaxy S23 Ultra using a local isolated preview.
A physical stylus was not used for calibration. Current release and Git
verification are recorded in the private deployment receipts.
