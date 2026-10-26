# Administrator Focus planner

## Add Schedule Slot stays in the workspace — 2026-10-25

The owner additionally requested that Add Schedule Slot open without navigating
to another page. The existing planner dialog now switches from the Group reader
to the native create-slot form. Cancel/Back/Close protects a dirty draft and
returns to the Group reader. A native create response returns the current Group
schedule in the same dialog; closing refreshes the underlying planner after a
confirmed create while retaining its URL/filters. The same enhancement is
available when opening the ordinary Group schedules page directly, including
its empty-state Add entry. Native links remain recovery/no-script destinations.

Changes: `admin_planner_dialog.js`, scoped `focus_planner.css`, planner
`_base.html`, native Schedule `form.html`/`group.html` and three Arabic labels.
The native Group template includes the shared dialog and marks the existing
create-success flash. Slot responses must match the exact nested create/Group
path, actor/auth version/role, Group public ID and create mode. The native
Schedule field names, choices, CSRF, method and validation remain authoritative.
Post-create redirects can also carry lifecycle rejections; only the native
success marker confirms creation. Actual rejection messages remain visible.
An uncertain save keeps entered values and blocks repeat submission. Slot
editing/history/status actions retain their existing owners and transport.
No route, query, business validation, lock order, history or notification code
was changed. Compact form spacing keeps Save visible on desktop; 320px date/
time fields stack to retain readable values. Arabic translates only the owned
No room assigned choice, preserving its numeric value and authored room names.

Executed evidence: thirteen localized templates compile with `StrictUndefined`,
the dialog script passes `node --check`, fifteen protected backend/shared files
match the before-change hashes, and native POST form blocks in both Schedule
templates compare unchanged apart from the form marker. Prior locale values/
duplicate counts remain intact; all three added keys are unique. No trailing
whitespace and CRLF-aware diff check passes. HEAD remains `10e5003`, staging is
empty and unrelated/concurrent work is retained.

Real-browser manual review used only the ignored fictional-memory fixture at
`instance/focus_planner_slot_20261025/`, never the production application factory
or a SQL engine. Reviewed opening within the existing dialog and from direct
Group schedules, inline required-field errors, retained draft/declined discard,
Back/Cancel without navigation, and a presentation-only create redirect showing
the returned four-slot Group reader. Closing refreshed the original planner URL
from six to seven fictional records. The fixture explicitly stated no real slot
was created. A rejection redirect showed its warning without success; an
uncertain response kept its Location value and disabled Save. A mismatched
Group form was rejected with no imported form. The existing center-event form
still opened, validated and cancelled within its dialog.

Measured desktop 1440px and phone 390/320px, English/light and Arabic/RTL/dark:
no document/dialog horizontal overflow. Direct-page Cancel kept the same URL;
320px time/date fields use the full available width. Final warning/error log
was empty. Screenshots/receipts are ignored QA artifacts. The temporary server
and tab are closed. No automated tests, actual database, real Schedule creation,
migration, seed/reset, scheduler, dependency, Git mutation or deployment. Room/
duplicate/conflict/locking/delivery behavior was not executed against MySQL;
this is bounded static/manual presentation evidence. Stop point: the requested
Add Schedule Slot interaction is implemented locally.

## Owner interaction and phone correction — 2026-10-25

The owner identified that New centre event, Choose a group and Open group
schedules still navigated away, unlike the selected concept. These entries
now open centered native dialogs within Focus planner. The earlier standalone
enhancement choice is superseded for these three entry points; original links
and server-owned workflows remain available as native recovery/no-script paths.
On phones only, hide Your planning space, and place Choose a group and Filters
beside Calendar. Desktop placement remains unchanged.

Changes in this correction:

- `admin/planner/_dialog.html`, `_group_choices.html`, `_base.html`, `_entry.html`
  and the Schedule overview/result templates provide dialog triggers, a Group
  chooser from the already-returned native Group options, and the phone header.
  Chooser search is local; selection reads the existing public Group schedules
  GET. The original native filter form is moved on narrow screens, never copied.
- `admin/calendar/form.html`, `admin/calendar/detail.html` and
  `admin/schedules/group.html` gain actor/object/content markers. Their native
  POST controls, CSRF and signed state remain intact. No route or SQL changes.
- `admin_planner_dialog.js` binds response origin/path, actor public ID/auth
  version/role, editor mode and exact Group or event ID. It reuses the original
  event form GET/POST with inline validation, namespaced DOM IDs and draft
  protection, including after validation rebinds. Only a native redirected
  detail response confirms completion. Show that persisted summary and actual
  response messages, then refresh the current calendar when the dialog closes.
  Uncertain saves keep the draft, disable repeats and offer calendar review.
- Group readers retain the native lifecycle warnings, slot controls and object
  links; subsequent slot editing/history/status actions retain native transport.
  The weekly pattern precedes academic context on phones. Escape, backdrop,
  close and native form Cancel restore the opener, subject to draft protection.
- Scoped CSS and seven new Arabic labels retain semantic themes, RTL, 20px
  corners and the sole shared phone dock. `admin_focus_planner.js` relocates the
  one existing filter disclosure and restores its desktop position on resize.

Executed correction evidence is under the ignored
`instance/focus_planner_actions_20261025/` folder:

- Twelve localized templates compile with `StrictUndefined`; both affected
  JavaScript files pass `node --check`. Thirteen protected backend/form/shared
  asset hashes match the before-correction baseline. All native POST form
  blocks in the event editor/detail and Group schedules templates compare
  unchanged, allowing only the presentation marker. Existing locale values and
  duplicate counts are unchanged; the seven new keys are unique. No source
  trailing whitespace; CRLF-aware diff check passes.
- Real-browser fictional-memory review covered desktop 1440px, tablet 768px
  and phones 390/320px, English/light and Arabic/RTL/dark. The 320px Arabic
  title and both buttons share the same vertical center; Your planning space
  is hidden. Measured document/dialog widths do not overflow. At 768px the
  mobile control is hidden and the sole native filter returns to its original
  desktop location. Applying Archived from the phone header returns one
  fictional record through the native GET form.
- Reviewed all three entry dialogs, chooser search/empty results/back,
  archived-parent warnings, validation errors within the event dialog, draft
  close cancellation, literal HTML escaping, native no-script URLs/filters,
  failed GET recovery and rejection of a mismatched actor with no imported
  form. An uncertain presentation POST kept its entered title and disabled
  resubmission. A presentation-only redirected detail response showed the
  returned summary/messages and closing refreshed the original calendar URL.
  That response explicitly stated that no event was created. No production
  route or business write was executed. Final browser warning/error log is empty.
- HEAD remains `10e5003`, staging is empty, and existing/concurrent work is
  retained. The isolated server/tab are closed after review. No automated
  tests, actual database, migration, reset/seed, scheduler, dependency, Git
  mutation or deployment. Real database-backed creation and comprehensive
  regression acceptance remain unperformed. Stop point: the requested
  interaction and phone corrections are implemented locally.

## Checkpoint and authority

The owner selected **Focus planner** from the proposed full Calendar redesign
and explicitly requested implementation on 2026-10-25. This supersedes the
earlier Student/Teacher date-grid composition for the Administrator pages only.
Work started from `10e5003` with existing and concurrent local changes retained.
The current shared shell, single People destination and phone dock remain.

Source, localization, documentation and bounded static/fictional-memory manual
review are in scope. Automated tests, actual databases, migrations, reset/seed,
schedulers, installations, Git mutation and deployment are outside this work.

## Implemented presentation

- **Center calendar**, `/admin/calendar`: compact month selectors beside a
  selected-day agenda on desktop, a week selector on phones, Day/Upcoming
  views and native disclosure cards. Each entry retains its source, time,
  group/location and authorized native destination. Cancelled center events
  remain visibly marked even when their details are closed; all-day events
  retain their meaning. Previous/Next/Today preserve normalized dates and
  presentation filters. Partial ranges may show more than one compact month.
- **Teaching schedules**, `/admin/schedules`: the complete native filtered
  Schedule collection as recurring-record cards. Each database record has
  exactly one highlighted weekday, its own time, effective dates, status and
  public Group schedule link. Archived-parent warnings remain. Future and
  archived records are not lost because they have no occurrence today.
- Both pages use semantic theme tokens, 20px feature cards, English/Arabic/RTL
  and the existing Admin navigation. Compact weekday initials avoid Arabic
  overlap while full dates/weekday titles retain context. Native filter forms
  open as desktop popovers and inline panels on phones. Escape and outside
  clicks close disclosures; visible keyboard focus is retained.
- The corrected New centre event, Choose a group and Open group schedules
  entries open native content in the planner dialog. Other Group management
  and event lifecycle controls retain native pages/forms. Native destinations
  remain available for recovery; no placeholder business actions are introduced.

## Files and integrity decisions

- `app/services/admin_calendar_presentation.py`: query-free adapter over the
  existing capped authorized calendar rows. Reuses `prepare_calendar`, builds
  civil-month selectors and corrects Admin-only action labels. It does not
  expand recurrence, change source records, query or authorize anything.
- `app/blueprints/admin/calendar.py`: imports the adapter and adds one response
  context value. The original read, time reference, normalization, truncation,
  URL attachment, role gate and private/no-store response remain intact.
- `app/blueprints/admin/schedules.py`: removes the superseded date-projection
  presentation context. The native collection query, order, seven filters,
  option queries and every other function are unchanged. Date selection now
  belongs to Center calendar; recurring records retain the original Location,
  Term, Level, Course, Group, Day and Status filters. Dates never filtered this
  recurring-record query.
- `app/templates/admin/planner/{_base,_entry,_calendar_filters,_schedule_filters}.html`:
  feature shell, agenda entry and native GET filters.
- `app/templates/admin/calendar/index.html`,
  `app/templates/admin/schedules/{overview,_query_results}.html`: the two
  consuming pages and recurring-record list.
- `app/static/css/admin/focus_planner.css` and
  `app/static/js/admin_focus_planner.js`: scoped presentation, local date/view
  interaction and mobile relocation of the original filters. The correction's
  separate `admin_planner_dialog.js` reuses existing native GET/POST endpoints;
  no new endpoint, SQL query or browser storage is introduced.
- `app/locales/ar.json`: 24 owned labels/compact weekdays were added without
  replacing existing or concurrent catalog entries.

Calendar type/group choices narrow only the already-authorized bounded read.
The original normalization and truncation notices remain visible. Only classes
and center events have Admin object destinations, generated by the existing
URL attachment owner with public IDs. Activity entries remain informational.
Authored strings are escaped. No schema, domain rules, attendance, grades,
payments, research, retention, transaction locking or lifecycle writes change.
The prior shared calendar files and unused prior Admin projection assets were
left intact to preserve existing work outside the consuming pages.

Without JavaScript, the bounded nonempty dated agenda remains readable and
native GET filters/disclosure cards work. Day/Upcoming enhancement controls
are hidden until initialized. There is no dependency on a new client library.

## Executed evidence

The following records the initial implementation. Current correction evidence
and its superseding interaction behavior are recorded above.

Ignored local receipts and screenshots are under
`instance/focus_planner_20261025/`; they are not production feature assets.

- Python AST parsing passed for both affected route modules and the new adapter.
  Source comparison preserved all 16 other Calendar functions and 26 other
  Schedule functions, selected-route decorators, the Calendar read body and
  original response context, and the Schedule collection/option-query context.
- All seven localized consuming/new templates compiled with the real owned-UI
  extension and `StrictUndefined`. `node --check` passed for the feature script.
- Thirteen protected file hashes match the before-task baseline, including
  calendar/recurrence/transaction services and shared shell/navigation/calendar
  assets. The seven native Schedule filter field blocks match, allowing only
  explicit weekday-label localization. Existing locale values and duplicate
  counts were retained. Owned source files have no trailing whitespace.
- Real-browser manual review used production templates/helpers over fictional
  memory objects, served on an isolated loopback port. This fixture does not
  create the application factory or a database engine; it intercepts the two
  presentation reads and refuses every write/other application read before
  production views run. No calendar event or schedule was created or changed.
- Reviewed desktop 1440px, tablet 768px, phones 390/320px, English/light and
  Arabic/RTL/dark. Measured documents had no horizontal overflow. Native
  combined Schedule filters produced one archived Sunday record; an empty
  result and Clear restored all six fictional records, including future and
  archived-parent examples. Phone filter panels were corrected after review
  exposed clipping/overlap with the shared dock; the corrected controls were
  revisited and Clear reached the native six-record list.
- Reviewed local day/week navigation, Day/Upcoming, empty days, normalized
  partial/multi-month range, preserved type/group filters through period
  navigation, native detail accordion, cancelled/all-day events and authorized
  public object links. A literal HTML attack string rendered as text with no
  injected image. The no-script fixture showed 14 nonempty dates and 19 entries.
  The initial included-entry loop-scope error was fixed and the corrected page
  rendered successfully. Final browser warning/error log was empty.
- CRLF-aware `git diff --check` passed; staging remains empty and HEAD remains
  `10e5003`. No staging, commit, push or history change was performed.

## Limits and stop point

This is static and manual presentation evidence, not automated regression or
database-backed acceptance. Real MySQL queries, authorization rejection,
creation/edit/cancellation, concurrency and delivery were not executed. The
owner's prohibition on automated tests remains in force. No actual database,
migration, seed/reset, scheduler, dependency, deployment or Git mutation occurred.
The isolated presentation server/tab are closed after review. Stop point: the
selected Focus planner presentation is implemented locally in both Admin tabs.
