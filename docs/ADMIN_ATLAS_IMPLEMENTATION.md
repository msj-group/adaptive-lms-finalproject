# Admin Atlas implementation — 2026-10-23

The owner subsequently selected refined Daily Desk for the Admin home and
labelled sidebar. Those parts supersede the earlier home/thin-rail arrangement
below; other child-page work remains. See ADMIN_DAILY_DESK_IMPLEMENTATION.md
for the direct enrollment entry, current verification and unchanged write policy.

Owner authorization: implement the selected Atlas desktop/phone concept, retain
platform colors and set every feature card to exactly 20px. The owner separately
authorized suitable private Admin correspondence with other platform users.
Checkpoint: `10e5003` plus existing and concurrent work, preserved in place.

## Implemented source coverage

| Area | Existing pages and child workflows |
| --- | --- |
| Overview | Factual counts, teaching agenda, setup attention queue, academic structure, quick links and readiness disclosure |
| People | Student/Teacher lists, profiles, create/edit, password reset, account history and Student enrollment history |
| Academic setup | Terms, levels, courses and rooms, including native forms and filters |
| Groups | Directory, detail, members/assignments, enrollment operation/history and forms |
| Schedules and calendar | Overview, Group slots, slot form, event list/detail/forms |
| Learning | Finalized attendance overview/Group/detail/correction; read-only gradebook overview/detail |
| Communication | Announcement overview/detail/form, own private Messages/picker/compose/thread and Notifications |
| Finance | Student accounts, signed balance and independently paged invoices/movements, registers, forms, receipts, documents, correction history and reports |

`layouts/admin_base.html` now owns `_atlas_shell.html`, `_atlas_navigation.html`,
`admin_atlas.css` and the presentation-only `admin_atlas.js`. Feature templates
use the corresponding cards, dossiers, guided forms and responsive record rows.
Colors resolve through existing `--ui-*` tokens. Appearance, density, Arabic/RTL,
search, account actions, native public-id URLs and the shared phone dock remain.
Complex tables keep a horizontal fallback; simple records gain header-derived
phone labels. No new client-side write authority or fabricated records are added.

Student detail adds five displayed active enrollment episodes from a capped
six-row eager read, plus the established financial balance calculation. Other
Admin write handlers and their guards are unchanged. Financial rows retain
independent pagination; attendance corrections and grade publication meanings
are preserved. Admin grades do not expose individual scores or staff comments.

## Administrative correspondence

Active Administrators can message other active Students, Teachers,
Administrators and Researchers. Other roles can contact active Administrators;
Student/Teacher academic contacts keep their existing operational-Group rule.
No self-contact, broadcast, attachment or supervisory inbox is introduced.
Only the two participants can read a thread. Inactive or no-longer-permitted
pairs keep historical reading but cannot send another message.

The existing generic schema requires no migration. `administrative_messages.py`
provides a separate atomic write path with locked current-role/activity checks,
deterministic User locks and exact two-member proof on reply. Existing signed
tokens, normalization, CSRF, nonce replay and safe IntegrityError rollback stay
in use. The original academic `message_transactions.py` remains byte-identical.
Sender-owned edit/hide and own-history clearing keep their existing semantics.

Message notifications reach the other active participant after message commit.
Admin/Researcher may open only the exact canonical `/messages/threads/<uuid>`
target; Student/Teacher academic target namespaces and announcement audiences
are unchanged. The destination still proves membership. Research authentication,
raw-data access, sampling, retention and provenance meanings are unchanged.

## Executed verification and boundary

- All 258 current HTML templates compile with the real OwnedUIExtension;
  twelve affected Python sources parse and `node --check admin_atlas.js` passes.
- Recipient SELECTs for all four roles compile against the MySQL dialect using
  an unbound Session. This checks SQL construction without an engine or query.
- Static comparison found matching native control sequences in all 39
  form-bearing Admin templates: method/action, named values, hidden fields,
  form widgets and confirmation attributes are retained.
- SHA-256 confirms unchanged academic message transactions, Message model and
  Notification model. MessageThread changes are documentation only. The locale
  catalog parses, missing owned literal labels were filled, and the pre-existing
  duplicate `Released grades` key is retained without introducing another key
  duplication.
- Actual browser review rendered production templates/assets in an ignored,
  CSRF-enabled fictional-memory fixture. All production views are intercepted,
  unlisted GETs are rejected and native writes are blocked. No application
  factory, SQLAlchemy engine or application database is used.
- Reviewed desktop overview, people list/profile, Group cards, Student creation,
  finance record, Messages and Researcher correspondence; reviewed 320px phone
  layouts, Arabic/RTL, light/dark, form errors, Notifications and notification
  preview. Checked native phone widths and message text contrast. The fixture
  and screenshots are presentation evidence, not stored business data.

Ignored local evidence: `instance/admin_atlas_20261022/` contains source snapshots,
manual fixture, static/form receipts and screenshots. No automated tests were
created or executed. No real database read/write, migration, reset/seed, scheduler,
dependency installation, staging, commit, push or deployment was performed.
Live-MySQL authorization, writes, delivery, contention and regressions have not
been exercised in this selection. Stop point: implementation is local; database
and end-to-end acceptance remain outside the authorized verification boundary.
