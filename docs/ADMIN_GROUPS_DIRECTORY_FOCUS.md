# Admin Groups: Directory desktop and Focus phone — 2026-10-23

## Owner scope and checkpoint

The owner selected **Directory** for desktop and **Focus workspace** for phones
from `output/design/admin-groups-concepts-20261023/`, then requested local
implementation. Inspected HEAD: `10e5003`, with pre-existing and concurrent
changes retained. This selection supersedes the earlier Atlas card directory
for the Groups listing only; it does not redesign the existing child workflows.

## Implemented source

- `app/templates/admin/groups/list.html`: existing heading/create entry, native
  search GET, expandable Term/Course/Level/Status filters, phone reader host,
  desktop dialog host and scoped asset links.
- `app/templates/admin/groups/_query_results.html`: desktop Directory table and
  responsive phone group selector, count, existing empty states and native
  per-group disclosures.
- `app/templates/admin/groups/_directory_panel.html`: one server-rendered group
  information panel and native status form per group, with Members, Schedule,
  Edit and full Group details links.
- `app/static/css/admin/groups.css` and `app/static/js/admin_groups.js`: scoped
  presentation, desktop dialog, phone selection/back and stale-reader cleanup.
- `app/locales/ar.json`: two owned labels, Group directory and Select a group.
  Concurrent additions and all baseline values remain intact.

At widths of 768px and above, the table compares name/code, academic context,
configured capacity and lifecycle status. Opening a group shows its existing
information in a native dialog. At 767px and below, rows become the Focus group
selector; selection opens an in-flow information card with All groups to return.
The current platform navigation, account/appearance controls and sole phone dock
remain. Feature surfaces have exactly 20px corners and use semantic theme roles.

## Integrity and progressive enhancement

- All displayed data was already available to the original Groups query. No
  route, database read, model, transaction service or authorization change is
  needed. Capacity continues to mean the configured maximum, not enrollment or
  available places. Active, Archived and parent-archival warnings keep their
  original meanings and conditions.
- One information section and its existing form move between their native
  disclosure, desktop dialog and phone reader. Forms are not cloned. Their
  method/action, public-id URLs, CSRF control and activation/deactivation
  conditions are preserved. The original status-form source is byte-identical.
  Server-side locks, hierarchy/role guards and rollback remain authoritative.
- Search uses the original named GET controls, ordinary server query and query
  fragment allowlist. The existing shared query script owns request cancellation,
  history and errors. Pending/replaced results close the selected presentation
  and remove detached panels, rather than retaining an old action panel.
- Native name/full-detail/member/schedule/edit links remain. Without JavaScript,
  filters submit normally and each native details disclosure exposes its real
  information and status form. No mock save or simulated status action ships.
- Escape/Close restore dialog focus. All groups restores the selected phone
  entry. Crossing the responsive breakpoint moves the same selected section;
  a queued desktop-dialog close event cannot close its new phone reader.

## Executed verification

- Three actual consuming templates compile through `OwnedUIExtension`.
  `node --check app/static/js/admin_groups.js` exits 0.
- Bounded static receipt confirms byte-identical native status-form source and
  the original GET named-control sequence. SHA-256 matches the captured baseline
  for `admin/groups.py`, group transaction/membership services, `workspace.js`
  and `query_filters.js`.
- Locale JSON parses. Every baseline value remains and duplicated-key counts
  match the baseline; no new duplicate was introduced. Concurrent locale changes
  were preserved, not reformatted or normalized.
- Actual templates/assets were manually reviewed in an isolated Flask
  presentation fixture with fictional memory objects, strict template variables
  and CSRF. It creates no application factory or database engine; every write
  and unlisted read is blocked before any production view can execute.
- Reviewed desktop at 1280x900 and phones at 390x844 and 320x844, including
  English/light and Arabic/RTL/dark presentation, mixed long names/codes, academic
  context, Archived status and the active-group/archived-parent warning.
- Observed live search, combined filtering/empty results, Clear, opening details,
  phone back, closing stale details after search, breakpoint transfer, desktop
  Escape/focus restoration and cancellation of the existing Deactivate
  confirmation. The moved reader contains one status form; total form counts
  remain one per returned group.
- Without scripts, the native filter submit and group disclosure work and all
  four status forms remain. Reviewed table, filter and reader compositions have
  no horizontal document overflow. The reviewed phone card corner is 20px.
  Captured browser error log is empty.
- CRLF-aware `git diff --check` exits 0. HEAD remains `10e5003`; staging is empty.
  Current pre-existing and concurrent work remains in the worktree.

Ignored evidence is in `instance/groups_directory_focus_20261023/`: before-state,
static receipt, manual-only fixture and desktop/phone screenshots. The temporary
review browser and loopback server are closed at close-out.

No automated tests were created or executed. No actual database, migration,
reset/seed, scheduler, dependency installation, staging, commit, push or deployment
was performed. These checks establish bounded source/presentation evidence;
live-MySQL authorization, writes, contention and comprehensive regression
acceptance are not claimed. Stop point: the selected Groups layouts are
implemented locally.

## New Group creation correction — 2026-10-25

The owner corrected the creation entry: New Group must remain on the Groups
page and open a centered dialog. This supersedes the earlier native-navigation
presentation for this enhanced entry; the existing standalone route remains
the no-script and recovery path. HEAD remains `10e5003`, with pre-existing and
concurrent changes retained.

### Corrected source and integrity

- `app/templates/admin/groups/list.html` and `_query_results.html` mark the
  existing creation links and provide the centered dialog, actor/list binding,
  status, recovery and flash-message hosts. No native link is removed.
- `app/templates/admin/groups/form.html` adds presentation markers around the
  original native editor/form and its Cancel/level display. The form's original
  controls, method, CSRF, names, values and standalone behavior are retained.
- `app/static/js/admin_group_create.js` fetches the existing creation GET and
  transplants only its native form card. Exact same-origin paths, actor, create
  mode, action and expected fields are checked before use. DOM IDs and their
  label/ARIA references are scoped to avoid duplicate IDs with list filters.
  The native course-level display and existing owned translations are rebound.
  Obsolete GETs are cancelled; fetched scripts are never executed.
- Submission uses the existing POST and native FormData/CSRF. Native validation
  responses retain values/errors in the dialog and focus the first error.
  Shared draft confirmation and the original GET value baseline protect drafts
  even after validation rebinds. Close/Escape/discard restore trigger focus.
  Pending POSTs block dismissal and repeat submission. Unconfirmed responses
  retain the draft, disable repeat submission and offer Check group list;
  they are never automatically retried or labelled successful.
- A verified native list response supplies its actual server flash messages.
  An explicit `data-query-refresh` opt-in in `query_filters.js` reloads through
  its original query owner, preserving filters, cancellation and history.
  `layouts/admin_base.html` updates only that shared script's asset version.
  Other query forms do not opt in. No success message is fabricated.
- `app/static/css/admin/groups.css` adds centered dialog styling with semantic
  themes, backdrop shading, 20px corners, desktop columns and a phone column.
  Three Arabic labels are added in `app/locales/ar.json`; concurrent additions
  and all existing values/duplicate counts remain.

No Group route, form definition, transaction/membership service, permission,
validation, lock, database query or schema is changed by this correction. The
existing directory reader and status-form code remain unchanged.

### Executed correction verification and boundary

- Four real localized templates compile through `OwnedUIExtension` (list,
  form, query results and directory panel). Both `admin_group_create.js` and
  `query_filters.js` pass `node --check`.
- Bounded static comparison confirms the native form source is unchanged
  apart from presentation markers, and the shared query source is unchanged
  outside its explicit opt-in hook/force argument. Eight captured hashes match:
  Group routes, admin form definitions, three transaction/membership services,
  `workspace.js`, `admin_groups.js` and `_directory_panel.html`. Existing locale
  values and duplicate-key counts are retained.
- Actual templates/assets were manually reviewed in an isolated Flask
  presentation fixture with fictional memory data and CSRF. The fixture never
  creates an application factory or database engine and intercepts production
  views. It reuses declarative GroupForm fields with in-memory choices, without
  invoking database-backed validators. Its successful transport response is
  explicitly labelled "Presentation-only response: no group was created."
- At 1280x900, New Group opens centered without leaving `/admin/groups`, with
  one form and no duplicate DOM IDs. Changing Course updates its own Level.
  Invalid submission retains the draft and native errors inside the dialog.
  Cancel after validation opens the shared discard confirmation; Stay retains
  the entered values. The presentation-only completion closes the dialog,
  displays its actual response message and refreshes the retained Morning
  filter. This does not establish actual business creation acceptance.
- At 320px, Arabic/RTL/dark labels and Save are localized, corners remain 20px,
  and the reviewed dialog has no horizontal overflow. Browser warning/error
  log is empty. Desktop and phone screenshots are retained with the evidence.
- CRLF-aware `git diff --check` exits 0. Staging is empty and HEAD is `10e5003`.
  No automated tests, actual database, migration, reset/seed, scheduler,
  dependency, Git mutation or deployment occurred. Live-MySQL writes,
  authorization/locking execution and comprehensive regression acceptance
  remain unperformed.

Ignored evidence: `instance/groups_new_dialog_20261023/` contains the captured
baseline, bounded static inspection/receipt, manual-only fixture and final
desktop/Arabic-phone screenshots. The folder name retains its initial review
identifier. Stop point: the requested creation dialog is implemented locally.

## Edit Group correction — 2026-10-25

The owner corrected the remaining Edit/Edit Group navigation and requested
continuation. Scope is the same centered editor behavior for the existing
Directory/Focus entry and Group details entry. This supersedes native edit
navigation as the enhanced presentation; standalone edit remains recovery.
Checkpoint: `10e5003`, with pre-existing and concurrent work preserved.

### Source and integrity decisions

- `_directory_panel.html` marks the native public-id Edit link.
  `form.html` adds the editor's Group public ID and enables the existing Cancel
  marker for edit mode. Native form source is otherwise unchanged: hidden CSRF,
  signed `edit_snapshot`, academic identity lock display/hidden values, editable
  controls and error conditions retain their original owners.
- New `_editor_dialog.html` shares one centered native editor dialog between
  `list.html` and `detail.html`. The latter adds actor/public-id binding, an
  isolated content host, response-message host and native reload recovery.
  The editor and scoped style assets use the edit correction version.
- `admin_group_create.js` now handles both existing creation and edit GET/POST
  endpoints. Edit responses must match the exact selected URL, actor, edit mode
  and Group public ID and include the native signed snapshot. Form names,
  hidden values, native FormData/CSRF and backend validation remain authoritative.
  Locked identity does not acquire replacement editable controls. Existing
  course-level display, owned localization, ID namespacing, error focus, shared
  draft protection and uncertain-result lock remain.
- Validation rebinds preserve the server-returned original snapshot/attempted
  values. A native stale-edit redirect intentionally replaces those with its
  fresh persisted values and correctly paired snapshot. The client never
  generates snapshots or combines a new snapshot with attempted old values.
  Actual server flash messages are retained; no successful save is invented.
- `admin_groups.js` adds only editor lifecycle guards: suspend its desktop
  information dialog while editing, restore it/focus on cancellation, defer
  breakpoint movement while the editor is open, and keep phone Escape from
  dismissing the reader beneath the editor. One feature dialog is visible.
  Native per-group status-form source is byte-identical.
- A verified list response refreshes current filters through the unchanged
  query owner. Group details instead refetch their existing ordinary GET and
  replace only their content after exact path/actor/Group binding. A failed
  detail refresh leaves the actual response message plus a reload notice/link.
  `groups.css` adds full-width placement for native locked-identity information.
  Two owned Arabic labels cover detail refresh recovery; concurrent locale and
  documentation changes are retained.

No route, endpoint, GroupForm definition, Group transaction/membership service,
SQL, schema, status policy or server authorization/lock/staleness rule changes.

### Executed review and limits

- Six localized templates compile through `OwnedUIExtension`: list, detail,
  form, directory panel, query results and the shared editor dialog.
  Both affected JavaScript files pass `node --check`.
- Bounded source comparisons confirm native form equality apart from its
  Cancel marker and byte-identical native status-form source. Original directory
  reader code remains outside the explicit editor lifecycle guards. Seven
  baseline hashes match: Group routes, admin form definitions, three shared
  transaction/membership services, `query_filters.js` and `workspace.js`.
  Existing locale values and duplicate-key counts are retained.
- Manual browser review uses real templates/assets in an isolated Flask
  presentation fixture with fictional memory data, native CSRF, declarative
  fields and pure signed-snapshot helpers. Production views and database-backed
  validators never execute; no application factory or database engine is created.
  Successful POSTs explicitly respond "Presentation-only response: no group was
  updated." They establish transport/presentation evidence, not actual editing.
- At 1280x900, Edit opens with native populated values on `/admin/groups`.
  Locked and unlocked academic identities both render. Invalid Name retains
  native errors and the original snapshot inside the dialog. Cancel after an
  invalid submission opens the existing discard confirmation; Stay retains the
  draft. Presentation-only completion refreshes the same list and closes its
  stale reader, with the Evening filter retained.
- The native Group details entry also opens in place. A deliberately generated
  fixture stale response shows its actual warning and fresh current values/
  snapshot inside the dialog. Its presentation-only completion refreshes the
  same Group detail content, preserves the response message, and restores Edit
  focus without navigating to the Groups directory.
- At 320px, Arabic/RTL/dark editing has no duplicate DOM IDs or horizontal
  dialog overflow; corners remain 20px and vertical content scrolls. Changing
  Course updates its scoped Level. Escape closes the editor and returns to the
  phone reader/Edit focus. Crossing desktop-to-phone while editing keeps only
  the editor open and transfers the reader on close. Opening New Group still
  uses create mode/endpoint without an edit snapshot. Final browser warning/
  error log is empty. An expired-CSRF response after the review pause retained
  the draft and blocked repeat submission; a renewed review session completed
  the validation/completion observations above.
- CRLF-aware `git diff --check` exits 0; staging is empty and HEAD remains
  `10e5003`. No automated tests, actual database, migration, reset/seed,
  scheduler, dependency, Git mutation or deployment occurred. Actual MySQL
  writes, authorization/locking execution and comprehensive regression
  acceptance remain outside this bounded review.

Ignored evidence: `instance/groups_edit_dialog_20261025/` contains the baseline,
static inspection/receipt, manual-only fixture, transport receipt and final
desktop/Arabic-phone screenshots. Stop point: the requested local Edit Group
dialog correction is implemented.
