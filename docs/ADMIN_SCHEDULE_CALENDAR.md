# Administrator schedule calendar — 2026-10-23

## Owner request and scope

Apply the Student/Teacher calendar presentation to the Administrator's existing
`/admin/schedules` page. Preserve the current Admin shell, phone dock, filters,
public management URLs, schedule lifecycle and native write workflows. Existing
worktree changes remain in place. This is a local source/presentation change;
no automated tests, actual database, migration, reset/seed, scheduler,
installation, staging, commit, push or deployment is included.

## Implemented behavior and files

- `app/blueprints/admin/schedules.py` retains the existing Schedule query,
  filters, ordering and option reads. After fetching, it prepares a civil-date
  projection and normalized navigation in `APP_TIMEZONE`.
- `app/services/schedule_calendar_presentation.py` expands only those already-
  filtered schedules through canonical `slot_spec` / `iter_occurrences_between`.
  Reuse shared range bounds (62 days), navigation and date presentation. Keep
  at most the earliest 600 occurrences plus an overflow sentinel in memory;
  report omission visibly. No SQL, source writes or attendance creation occurs.
- Admin schedule templates use shared `calendar/_workspace.html`: Month,
  Week and Upcoming on desktop, a week selector with Day/Upcoming on phones,
  native GET date/filter controls and public Group schedule links. The original
  register is preserved in `_register.html`, inside a disclosure, so records
  outside the displayed range remain available. Filter counts still describe
  the complete original filtered schedule collection.
- `_calendar_filters.html` preserves Location, Term, Level, Course, Group,
  weekday and status filters. Date, Today and period links retain normalized
  administrative selections. These controls use full native GET navigation;
  the existing fragment response remains available without introducing an
  asynchronous calendar replacement/initialization workflow.
- `_entry.html` preserves Active/Archived and the original active-slot /
  archived-parent Not operational warning. Historical cards use dashed borders
  and neutral compact labels. These are recurring administrative slot records,
  not proof that a class or attendance session occurred. Academic names and
  locations stay escaped; object URLs use public Group identities.
- `app/static/css/admin/calendar.css` adapts only this page, retaining semantic
  themes, RTL, 20px feature cards, keyboard focus and the shared navigation.
- Shared workspace changes are conditional on `calendar.schedule_admin`.
  Student/Teacher entries and filters retain their original branch. The shared
  script now carries named GET filters across a period boundary rather than
  assuming only `kind`/`group`; date/view navigation retains its existing owner.
  Shared CSS changes only its introductory comment. Seven Arabic labels added.

The separate `/admin/calendar` aggregate and event-management workflows remain
available through the original Calendar tab. Their source queries, permission,
event cancellation and signed writes are unchanged. This change does not merge
new activity/event sources into the recurring Schedule overview.

## Executed verification and limitations

- Python AST syntax parses for the two affected Python files; nine actual
  localized templates compile through `OwnedUIExtension`, including the shared
  workspace and Student/Teacher consuming templates.
- `node --check app/static/js/calendar.js` exits 0. Static receipt confirms the
  original overview's filter/query/order source and 26 other route functions
  are unchanged. The moved register matches its captured source, excluding
  only its relocated count marker. Eight calendar/query/occurrence/transaction/
  resource/Student/Teacher files retain their captured bytes.
- Locale JSON parses; baseline translations and duplicate-key counts remain
  intact, and all seven additions have unique keys.
- Actual browser review used real templates/assets and fictional memory rows
  through an isolated Flask presentation fixture. It creates no application
  factory or database engine. All writes and unlisted reads are rejected before
  production views can execute. Reviewed desktop 1280x900, phones 390x844 and
  320x844, English/light and Arabic/RTL/dark, Month/Week/Day/Upcoming, empty days,
  archived/non-operational records, filter application and period-boundary
  retention. Phone measurements showed no horizontal document overflow and
  20px entry corners. Browser warning/error log returned an empty collection.
- With scripts removed from the fixture response, all matching dated entries
  and the original seven-record register remain readable through native HTML
  disclosures, including the future slot absent from October's date projection.
- CRLF-aware `git diff --check` exits 0. Final HEAD and staging are recorded in
  the ignored close-out receipt. The review tab and owned loopback server are
  closed after review. Ignored evidence: `instance/calendar_admin_20261023/`.

The first preview launch used the system Python, which lacks `flask_login`;
using the existing project `.venv` resolved it without installing anything.
No automated tests were created or executed. This is bounded static/manual
presentation evidence, not real-MySQL authorization/write/concurrency acceptance
or comprehensive regression verification. Stop point: requested local schedule
calendar implemented; live ordinary-workflow verification remains unperformed.
