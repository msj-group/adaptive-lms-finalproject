# Repair status

## Active Version A delivery — 2026-10-08

Final owner approval authorizes W0–W9 under Direction D + D-MOTION, protected
current Login, evolved Messages and teacher-configured text/single-file
assignments. Development schema/migrations and justified disposable-data work
are authorized. The active ledger and boundaries are in
[VERSION_A_DELIVERY.md](VERSION_A_DELIVERY.md). Version A implementation and
bounded local acceptance/Pilot are complete; source/UI baseline
YC-VA-D1-20261008 is frozen. A hosting-neutral data-free production artifact and
runbooks are delivered. [VERSION_A_ACCEPTANCE.md](VERSION_A_ACCEPTANCE.md),
[VERSION_A_BASELINE_FREEZE.md](VERSION_A_BASELINE_FREEZE.md) and
[VERSION_A_READINESS.md](VERSION_A_READINESS.md) record evidence, limits and
remaining actual-host/Study gates. No real production deployment or collection
is claimed; the Development Pilot is paused. No new test package or Git commit.
The entries below are historical evidence of preceding work.

Local date: 2026-10-07 (Africa/Tripoli).
Contract: [APPROVED_REPAIR_CONTRACT.md](APPROVED_REPAIR_CONTRACT.md).
Roadmap: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Version A redesign — W0 / W1 — 2026-10-07

The owner approved P01-P06 and authorized the Design Proposal / Architecture
Review for sequential implementation. W0 contracts are recorded in
[VERSION_A_DESIGN_CONTRACT.md](VERSION_A_DESIGN_CONTRACT.md). W1 shared tokens,
base components and four-role shell are implemented in source. Superseded shared
CSS declarations were removed in bounded sections; feature compatibility remains.

Current evidence: 197 template syntax parses, 297 Python AST parses, valid Arabic
catalog, shared-client JavaScript syntax, isolated four-role desktop rendering,
Student mobile/light/dark/RTL, native drawer/menu focus and no-JS fallback. Final
isolated browser review reported no JavaScript errors or document horizontal
overflow in the inspected previews. No application/database or collector was
started. Actual role/workflow, full responsive and accessibility acceptance is
still pending; W1's exit gate is not declared complete and W2 has not started.

[VERSION_A_W1.md](VERSION_A_W1.md) records scope, evidence and remaining gates.
No research repair, file feature/migration, automated tests, install, DB/Git
mutation, Version B, real collection or Baseline Freeze was performed.

## Quiz presentation refinement and stable position — 2026-10-07

The owner requested removal of save/bookmark success and explanatory text,
an icon-only upper-right Bookmark control inside the question card, stable
position between questions and a modern final-submission card beneath the
question list. These changes are implemented in source. Success interactions
are quiet; failure/retry feedback remains actionable. The bookmark retains
accessible labels, pressed state and reduced-motion-aware hover/press/mark
effects. The sidebar follows the question on narrow layouts.

Question links preserve document/list/sidebar positions after pending writes.
The one-use tab UI entry carries only public scope/destination paths, bounded
pixel coordinates and a five-minute timestamp; no academic content, selections,
flags, scores or secrets. It is consumed on the matching question page; later
layout restoration stops on user interaction. Native protected navigation
continues if browser storage is unavailable. Back/forward reauthorization is
retained with position preservation. Server saves, ownership, CSRF, tokens,
expiry, grading, bookmark persistence and research semantics are unchanged.

Verification: 197 Jinja sources compiled, 344 application/migration Python
sources parsed, 160 guarded offline Quiz renders passed full before/after native
POST protocol comparisons, including 320 answer/final-submit input comparisons.
Markup/placement confirms no nested forms and the intended card hierarchy;
source review confirms quiet success handling and bounded position restoration.
Protected server/query/transaction/grading/expiry/Listening sources and shared
workspace guards are unchanged. All 3,228 earlier Arabic entries are preserved;
one accessible icon label brings the catalog to 3,229. Node syntax and diff
whitespace checks passed; the index is empty.

No app factory/view dispatch, application database access, browser/server,
automated suite, migration, installation or Git mutation. Actual scrolling,
animation, asynchronous navigation and device usability remain unverified under
the owner's source-only constraint. See [QUIZ_INTERACTION.md](QUIZ_INTERACTION.md).

## Quiz autosave and question review — 2026-10-07

The latest owner request is implemented in source for ordinary Student Quizzes:
option-change saving replaces Save answer/Save and next, with a question sidebar
showing confirmed answered/unanswered state, current question and Bookmark flags.
Every shortcut targets its own nested question directly. The sidebar adapts to
small layouts; Arabic and shared light/dark tokens are supported. Initial and
pre-start instructions now match the interaction. Final submission remains
explicit, with server-authoritative unanswered confirmation and expiry.

Serialized complete-set saves keep the latest choice pending until acknowledged;
same-tab navigation/submission wait for it and for pending bookmark updates.
Failure offers retry or explicit discard/reload recovery. Clearing all checkbox
choices keeps the answer history row but returns the question to unanswered.
Review flags survive navigation/reload in the browser session only, in a bounded
signed HttpOnly account/auth-version-bound cookie. No migration is needed.

Verification: 197 Jinja sources compiled, 344 application/migration Python sources
parsed, 160 guarded offline Quiz markup states rendered and 320 protected native
input comparisons passed. Two MySQL query statements compiled without execution.
Pure selection/cookie helper review covered clearing, duplicate/foreign choices,
single/multiple cardinality, tampering, refresh/unmark, account/auth-version
isolation and the cookie bounds. Source comparison preserves other Quiz functions,
Listening route/template and shared transactions/grading/expiry. All 3,207 earlier
Arabic entries remain unchanged, with 21 additions (3,228 total). Node syntax and
Git diff whitespace checks passed, with an empty index.

No app factory/view dispatch, browser/server, application DB operation, automated
suite, schema/migration, installation or Git mutation. Actual asynchronous network,
concurrency and device usability acceptance remains unverified under the owner's
source-only constraint. See [QUIZ_INTERACTION.md](QUIZ_INTERACTION.md).

## Fewer clicks across ordinary workflows — 2026-10-07

The owner requested a platform-wide review of unnecessary steps and practical
removal/combination of them. Teacher dashboard/review/submission lists now open
the existing combined feedback editor directly, with a Group-filtered return
link. Gradebooks and draft attendance lists open their existing score/marking
tools directly. Administrator Group lists link to members/schedules; Researcher
export lists offer download for retained archives. Course content trees provide
an eligible nested Add lesson action. New question forms offer save-and-add;
new Lesson forms offer save-and-materials; Students may complete-and-next.
Attendance drafts offer form-only bulk present selection with undo. Duplicate
activity-type selection is removed while the chosen GET filter is preserved.
The nine new owned phrases extend the Arabic dictionary to 3,207 entries.

All shortcuts use the existing protected write paths or existing GET tools.
Navigation intents use fixed server destinations after successful saves.
Completion failures stay on the current Lesson; the next Lesson comes from the
same own-Group published outline. Bulk attendance never saves/finalizes or
changes notes/roster/state tokens. A correlated EXISTS adds archive availability
to the existing paginated provenance-scoped export query without per-row reads.
Finance, release/publication, final submission, collection, roster and membership
decisions remain explicit. See [WORKFLOW_SIMPLIFICATION.md](WORKFLOW_SIMPLIFICATION.md)
for the cross-role brainstorm and estimated before/after activation counts.

Verification: 197 Jinja sources compiled and 348 Python sources parsed. Guarded
offline fixtures rendered 430 states (340 activity/library and 90 additional
workflow states); 405 before/after protected native POST comparisons passed,
excluding only the intended new navigation submitters and inert bulk controls.
AST comparison found changes only to seven intended functions in six modules;
five existing functions retained their original logic after navigation additions
were removed for comparison. Four Research export count/list SQL statements
compiled with MySQL syntax for study/development provenance without execution;
the Teacher/Student activity unions also compiled. Four synthetic next-Lesson
outline boundaries were reviewed. The 3,198 earlier Arabic entries are unchanged,
the scoped filter CSS change was reviewed, JavaScript syntax passed Node check,
and final Git diff whitespace check passed with the index empty.

No app factory/view dispatch, browser/server, application database operation,
schema/migration, dependency installation, automated test suite or Git mutation.
Actual responsive/device usability and transaction/concurrency acceptance remain
unverified under the owner's source-only instruction. Counts in the workflow
document are source-derived estimates, not observed user-study measurements.

## Four activity workspaces and course integration — 2026-10-07

The owner requested redesign of all Student/Teacher activity pages and their
library. All 36 activity templates now use Quiz lab, Listening desk, Voice
studio or Writing room layouts, with dedicated authoring, participation,
receipt/results and review presentation. Library cards provide native filters,
actual states/deadlines and correct course/group destinations. Teacher Activities
is a new primary destination with a bounded own-assignment SQL library; a
read-only exact assignment overview joins the existing nested tools. Course
studio/Student overview/context links carry the actual Group. No Unit/Lesson
association or completion/grade policy is fabricated. Arabic/RTL and themes
extend to the new layouts; the owned UI dictionary now contains 3,198 entries.
See [ACTIVITY_DESIGN.md](ACTIVITY_DESIGN.md).

Verification: 197 Jinja sources compiled; 340 offline activity/library markup
states and 315 before/after rendered POST protocol pairs reviewed; MySQL SQL
compiled without execution; 198 own-role navigation states reviewed. Existing
CSRF/state fields, selectors, research attributes, answer-key separation,
submission/publication guards and audio/recorder scripts are preserved. No app
factory/view dispatch, browser/server, application database, schema/migration,
dependency installation, automated suite or Git mutation. Actual visual,
responsive, device and workflow acceptance remains pending under the earlier
source-only instruction.

## Arabic, themes and utility panels — 2026-10-07

The owner authorized Arabic and dark mode across the platform, with language and
theme selected in Display. Display/Help now open shared native popover panels
from the navigation rail/account card instead of separate pages. Earlier direct
URLs redirect to the appropriate role home with the requested panel. Public
login/opening/error surfaces expose the same controls. Strict native POST/CSRF
preferences save only device presentation cookies; motion/spacing retain their
existing local settings. All four workspaces share themed surfaces and Arabic
RTL presentation. Owned UI/form/client messages use a 3,101-entry dictionary;
authored learning/messages/research content and machine values remain intact.
All 3,953 emoji receive local Arabic names/search terms with English aliases.
See [APPEARANCE_AND_ARABIC.md](APPEARANCE_AND_ARABIC.md).

Verification: 346 Python ASTs across app/scripts/migrations, all 191 Jinja sources,
195 role/menu/shared/section render states plus 48 public login/error states,
14 JavaScript syntax checks, dictionary substitutions, emoji glyph/order equality
and source-only preference response checks. No app factory/request dispatch,
application database, migration, browser/server, automated suite or Git mutation.
Actual visual/keyboard/device-persistence acceptance remains pending under the
source-only instruction. This follow-up supersedes the earlier Help/Display
page presentation described below; navigation/account/history contracts remain.

## Shared menu and list redesign — 2026-10-07

All four workspaces now share a light navigation rail with the original logo,
role badge, consistent icons, grouped links, blue current-page selection and
limited red accents. Account cards, context tabs, native selects, filters,
record/report sections, pagination, conversation/action/emoji menus, outlines
and data lists receive consistent presentation. All primary URLs and protected
forms remain; shared Administrator Help/Display pages retain the complete menu.
Single titles, full-page messaging and sidebar offset restoration are preserved.

Verification: 292 Python ASTs and 190 Jinja sources parse. Offline rendering
covers 65 role/destination/shared-page/section states, including all 47 primary
destinations and own-role menus on Help/Display/Account pages. URL inventory,
selected state, header title, unique IDs, account links and POST logout/CSRF
were checked. CSS received source/specificity/delimiter review; no parser was
available or installed. See [NAVIGATION_DESIGN.md](NAVIGATION_DESIGN.md).
No application database, migration, server/browser, automated suite or Git
mutation. Actual visual/responsive/keyboard acceptance remains pending under
the owner's source-only instruction.

## Own password and profile photo — 2026-10-07

All four roles have Account settings in the top-right account card. The page
offers current-password verification and confirmation of a new 15–128 character
password, then signs out after an auth-version bump. Own still JPG/PNG/WebP
photos are bounded, decoded, stripped of metadata and stored as private square
PNGs; they appear in the account toggle/card/page. CSRF, signed stale-form
checks, actor reauthorization, canonical lock order and append-only revisions
apply. Existing upload/account-history tables support this without a migration.
Pillow 12.3.0 was already installed transitively and is now a direct requirement;
no installation was performed. See [ACCOUNT_SETTINGS.md](ACCOUNT_SETTINGS.md).

Verification: 292 Python ASTs and 190 Jinja sources parse; account modules load
with the project's Python 3.14.6/Pillow 12.3.0. Offline rendering covers 16
four-role/photo/error states with single titles, unique IDs, blank password
values and native POST/CSRF forms. Four account URL rules resolve in blueprint
metadata. No request dispatch, application factory, application database,
browser/server, automated suite, schema changes or Git mutations. Actual
credential/photo persistence and concurrency remain unverified under the
owner's source-only instruction.

## Remaining UX/UI review recommendations — 2026-10-07

The owner requested implementation of the review folder's remaining findings.
Student/Teacher dashboards now prioritize real daily work, with a shared bounded
comment queue summary, exact lesson continuation context and compact secondary
sections. Teacher group tools use five main families with all existing URLs.
Desktop bell preview is read-only; mobile falls back to the inbox. Notifications
have normalized kind filters and descriptive native POST actions. Owned-thread
inbox search spans names/subjects with escaped LIKE and preserved pagination.
Later full-page messaging, account/scroll/title decisions remain intact.

Source evidence: 288 Python ASTs, 189 Jinja sources, two Node syntax checks;
offline rendering of 34 messaging, 10 dashboard, 16 notification and six search
states plus one grouped-navigation fragment. Changed URLs resolve in blueprint
metadata; 11 MySQL-dialect read statements compile without SQL execution and
the workspace time helper works. See [UX_UI_IMPLEMENTATION.md](UX_UI_IMPLEMENTATION.md)
for the failed fictional compiler probe and corrected pre-connection guard.
No application database, migration, server/browser, automated suite, installation,
scheduler/deployment or Git mutation. Visual/runtime acceptance remains pending
under the owner's source-only constraint; future AI/push/offline/Rubrics remain
independent features. An Arabic resolution record accompanies the review folder.

## One workspace title per tab — 2026-10-07

All shared workspace layouts now render one semantic page-title h1 below their
role eyebrow. Messages follows the same rule, replacing its prior suppressed
header/off-screen content heading. Explicit markers hide 148 duplicate page
headings on screen; section headings, learning content, dashboard greetings
and independent financial document identifiers remain. Marked headings remain
available when printing. Nine previously untitled pages now have source title
blocks; the grade-sheet title preserves Enter scores/Correct scores mode.

Bounded source review parsed all 186 Jinja sources: no missing page-title blocks
or unmarked duplicate page headings. Offline StrictUndefined rendering covered
34 messaging/dashboard states, four inherited role layouts and 15 actual
page/role combinations; headers appeared once and account cards remained.
Both grade-action title branches rendered correctly. Focused Git/untracked
whitespace review passed; CSS was reviewed from source. No automated tests,
platform/server/browser operation, database access, installation or Git mutation.
No fresh visual or comprehensive workflow acceptance is claimed.

## Full-page messaging surface — 2026-10-07

Messaging now uses the full available workspace below the account bar, without
the earlier 1580px page limit, capped panel height, outer page padding or shell
footer. Conversation lists/history scroll inside the surface, including the
mobile inbox. Print behavior and the no-JavaScript mobile navigation fallback
remain. Removed messaging timezone copy, the visible Enter/Shift+Enter hint,
and the sidebar slogan/View all footer. Character counts, keyboard sending,
native forms, timestamps and pagination are retained.

Six Jinja sources parsed; offline StrictUndefined rendering covered 32 fictional
messaging states and two dashboard previews. Requested phrases and messaging
shell footers were absent; textarea descriptions resolved and other-page
labels/footers remained. JavaScript syntax and focused whitespace checks passed.
CSS was reviewed from source; a CSS parser was unavailable. No platform/browser
operation, database access, automated tests, installation or Git mutation was
performed. Updated viewport geometry has not been visually reviewed in a browser.

## Login transition and workspace navigation — 2026-10-07

Latest source-only follow-up: the owner requested the same four changes without
launching the platform or using the browser. Removed the remaining topbar
Messages label through the shared messaging base template, retaining other
portal page labels and the accessible off-screen messaging h1. The existing
login transition, upper-right account card and sidebar offset restoration were
reviewed and retained. Offline checks passed: nine Jinja sources parsed, eight
fictional Student/Teacher states rendered, auth Python parsed and two JavaScript
syntax checks. No browser/server operation, database access, automated tests
or Git mutation was performed in this follow-up. Earlier live evidence below
describes the preceding checkpoint, not a fresh browser run.

The owner requested removal of the duplicate messaging title, the supplied
branded post-login transition, an avatar account card instead of sidebar
identity/logout blocks, and stable sidebar scroll position during navigation.
These refinements are implemented across all four shared workspace layouts.
Successful login retains its validated role-safe return path; the brief screen
also has native continuation and POST logout actions. No schema change is needed.

Live Student/Teacher review confirmed automatic login continuation, return
path/Back action, card keyboard/Escape/outside closure, ordinary sign-out and
stable actual-click sidebar offsets on desktop and mobile. The 320px card fits
without document overflow. Bounded syntax and StrictUndefined rendering cover
34 messaging states plus four role cards/four transition states. Temporary
viewport overrides were reset. No automated tests, credentials changed,
installation or Git mutation; broader live Administrator/Researcher follow-up
and concurrency acceptance are not claimed. See
[UX/UI Implementation](UX_UI_IMPLEMENTATION.md) for exact evidence.

## Messaging reference design and activation — 2026-10-07

Shared Student/Teacher messaging uses the supplied glass layout and platform
palette, Enter sending, a complete Unicode 17 emoji catalog, sender edits,
sender hiding for both participants and participant-only conversation clears.
Inbox, contact composer, thread and dashboard previews share the display rules.
Original correspondence remains stored unchanged; management appends history.

The owner separately approved the exact additive migration `6b3a8c2d9041` from
`085b7a4e9012` and manual review with existing fictional accounts. Local MySQL
is now at the new revision. Original messaging-row fingerprints match before
and after (3 threads, 6 memberships, 7 messages), and a read-only comparison
of the two new tables found zero model/schema differences.

Bounded Python/JavaScript/Jinja/SQL review and manual fictional-template
browser checks passed. Administrator access to messaging was correctly refused
by the live app. Using an owner-supplied existing fictional credential, ordinary
Student/Teacher UI review confirmed delivery, editing, hide for both members,
member-only clear and a new reply reappearing without cleared older history.
The original edited/hidden body remains stored. Two normal manual-review sends,
one edit, one hide and one clear are retained; no automated tests, data reset/
seed, credential changes or Git mutation occurred. Concurrency acceptance is
not claimed.
See [UX/UI Implementation](UX_UI_IMPLEMENTATION.md) and
[Message Schema and Activation](MESSAGE_MANAGEMENT_SCHEMA_PLAN.md) for evidence
and the verification boundary. Historical checkpoints below retain their scope.

## Enrollment after study start — 2026-10-06

The owner removed the admission cutoff. New enrollment, re-enrollment and
wrong-course correction now allow a started destination. A non-blocking notice
shows its actual study start in the center timezone before selection/review;
successful new-episode operations also explain late entry. The success notice
uses the episode creation time, including idempotent replays, rather than the
current viewing time. Eligibility, configured start, teachers, capacity,
duplicates, signed snapshots, lock order and atomic financial history remain.

Manual Administrator review confirmed the notice for Foundation A (start
2026-09-16 09:33:54 Africa/Tripoli), an enabled review-form save button, and
no notice for Upcoming Foundation. No enrollment operation was submitted;
the post-save flash and persisted transaction were not exercised in this
check. Static review passed: 285 Python files, 184 Jinja templates, 334 routes,
no unknown literal template endpoints; scoped CRLF-aware Git whitespace review
passed. No automated tests, schema/data change, installation, deployment or
Git publication occurred. The local source-preview server was restarted to
load the changed Python code. Evidence:
`../UX_UI_Review_2026-10-06/implementation/late-enrollment-notice.png`.

## Current-platform UX/UI delivery — 2026-10-06

Role-scoped workspaces, shared interface behavior, lesson-opening correction,
Teacher authoring/review context, Administrator account/financial previews,
Research presentation and calendar layouts are implemented. See
[UX/UI Implementation](UX_UI_IMPLEMENTATION.md) for the exact changes, manual
evidence and retained contracts. Current source checks: 285 application Python
files, 184 Jinja templates, 334 routes, no unknown literal template endpoints;
six JavaScript syntax checks and CRLF-aware Git whitespace review passed.

Four fictional roles were reviewed manually in localhost. Current narrow-screen
browser acceptance, physical microphone/network/timer scenarios and exhaustive
regression/security/performance checks are not claimed. No automated tests,
migration/reset, deployment or Git publication were performed. The owner kept
future AI/assessment/external notification/offline ideas outside this delivery.
The follow-up focused redesign study is documented separately from implemented
behavior. Historical verification records below retain their original scope.


## Local Student-to-Researcher rehearsal — 2026-10-06

The owner requested local ordinary usage, review and export before redesign,
and authorized necessary source corrections.

- Added server-selected operational-review exports. New manifests record
  `dataset_kind`, source and `natural-use-csv.v2`; session rows preserve their
  provenance. An append-only creation-audit code persists export scope without
  a migration. Lists, detail and download enforce that scope. Study exports
  still require study sessions and study subjects; historical bytes are intact.
- Started the existing local app on loopback port 5000 with the existing
  configuration: development source, disabled online payments, 15-day retention
  and Africa/Tripoli timezone. No secrets or environment values were changed.
- Through the Researcher UI, created and activated version 3, **Local
  operational collection**, then started collecting. Its period is
  2026-10-06 00:00 to 2026-11-06 00:00 Africa/Tripoli. Existing policy defaults
  are unchanged; version 2 is retained as retired history.
- Manually created and downloaded an operational-review ZIP of existing local
  data: 1 subject, 3 sessions, 12 events, 3 prompts. Inspected its source,
  session column and all four content checksums; the manifest digest matches
  the UI. This archive precedes the new Student navigation and is not its data.
- Signed in as the existing Student and opened Dashboard/Activities without
  starting or submitting an attempt. A new version-3 session recorded accepted
  events. Open assignments, quizzes, speaking and listening remain available
  for the owner to perform; the local page is left at Activities.
- Bounded syntax review compiled 334 Python sources and parsed 169 templates.
  ORM mapping, both scope queries and read-only MySQL readiness reads succeeded;
  Git whitespace review passed. No automated tests were created or run.

Schema stays `085b7a4e9012`. The normal UI actions wrote the new configuration,
audit, export and navigation session; there was no reset, seed, reclassification,
migration, dependency installation, scheduler change, commit or push. This is
local manual verification, not production acceptance or complete task execution.
See [Local Research Walkthrough](LOCAL_RESEARCH_WALKTHROUGH.md).

## Latest hosting/evaluation cleanup — 2026-10-06

The owner chose an empty future hosting database and retained the existing
local database. No LibyanSpider plan is selected or purchased. Normal role
workflows use manually entered fictional input during operational evaluation.

- Removed the payment simulation adapter, checkout/actions, webhook route,
  account link and templates. Startup accepts only disabled online payments.
  Historical provider tables/evidence remain; cash/bank workflows are retained.
- Removed Researcher data-mode selectors, the demonstration counter and the
  per-account demonstration operator command. Server settings select workspace
  scope automatically; review includes retained non-study history. Production
  HTTP settings now default to non-study provenance. Existing sessions and
  audit classifications are never rewritten or included in study exports.
- Removed the public component showcase and its sample-only form/page.
- Account provisioning helpers now use the explicitly selected deployment
  environment, with private interactive password input and no seeded content.
- Added [Hosting Preparation](HOSTING_PREPARATION.md), updated startup and
  research guides, and supplied the approved 15-day template retention value.

This is source preparation, not hosting acceptance. The target-specific WSGI,
HTTPS/proxy, shared rate-limit backend, persistent storage, backups and daily
job still need deployment configuration and manual review. No database,
scheduler, dependency installation, new automated test or Git mutation was
performed in this follow-up. The previous source/interface review below is
historical evidence of the earlier build.

Current static review compiled 334 Python sources in memory, parsed 169 Jinja
templates and resolved their static imports and literal URL endpoints. All 64
compatibility handlers retain their route/security decorators. Application
startup registered 320 rules and configured ORM mappers without a database
query or automated test. The Git whitespace check passed. This is bounded
source review, not a browser/deployment or comprehensive regression result.

## Latest owner scope and close-out — 2026-10-06

The remaining authorized source/interface integration and documentation are
complete. The owner requested deletion of the old tests and explicitly declined
a replacement package. No automated tests were recreated or executed for this
close-out. Historical full-suite/replacement-coverage gates below are superseded
for this request; their absence remains a verification limitation.

- The separately authorized development reset completed at `2026-10-06T07:33:58Z`,
  preserving schema head `085b7a4e9012`. It created exactly four active demo roles,
  three priced Courses and fictional platform content. A private verified SQL,
  upload and old-test recovery archive was retained under ignored `instance/`.
- Retired runtime finance services/forms and 58 templates were removed after
  checking their actual consumers. Twelve legacy controller modules now retain
  only current compatibility handlers and required helpers. Shared financial
  response headers, sequence allocation and report types have dedicated modules.
  Historical migrations and necessary compatibility models/tables remain.
- All four old Student activity lists lead to the unified Activities hub. Type
  and Course filtering were reviewed in the browser. The portal navigation and
  activity filters fit the narrow viewport without horizontal page overflow.
- Added the missing Administrator correction path for finalized attendance.
  It preserves the roster, class and finalization, rejects stale forms and
  writes the complete old/new academic revision under the shared attendance
  lock order. Student episode attendance/correction pages never reveal private
  staff notes. A real browser save on fictional data preserved the old note,
  actual Administrator and server time; the Student views hid it.
- Manual browser review covered login and representative pages for all four
  roles: Rooms/schedules/prices/accounts/history/reports/enrollment review,
  Teacher gradebook/attendance, Student Activities/listening/history, and
  Researcher demo summaries/configurations/storage/cleanup preview/export detail.
  Administrator access to Research and Student access to attendance correction
  returned Forbidden. The cleanup preview was reviewed without deleting data.

Detailed boundaries and evidence: [Repair Integration](REPAIR_INTEGRATION.md).
This is source completion with bounded manual/static review, not comprehensive
regression acceptance or a production deployment. No study collection period,
new scheduler task, dependency installation, `.env` change or Git mutation was
performed by this close-out.

Final static close-out: 340 Python sources compiled in memory, 172 Jinja
templates parsed with static references resolved, 64 legacy handlers retained
identical route/security decorators, 324 application rules registered and ORM
mappers configured. No database query or test was executed by that source check.
`git diff --check` passed; HEAD remains `fd500fdf6abcf57d90b541c9ad51320aba1fd95a`
and the index is empty. The local browser review server and temporary tabs are
stopped/closed after review; use the normal Quick Start command to run the app.

## Historical scope of this record

The owner's authorization is to implement the agreed staged repair, not
a completed repair release. P0 prepared documentation/source recovery.
P1A changes runtime entry and environment selection with DB-free checks.
Later phases are not complete merely because their target is documented.

## Inspected checkpoint before P0 edits

- Branch `master`; HEAD `fd500fdf6abcf57d90b541c9ad51320aba1fd95a`.
- Pre-existing working tree: 15 modified tracked files, one deleted tracked
  file (`app/templates/research/login.html`) and one untracked browser test.
  These are the shared-login follow-up; P0 preserves their source/test state.
- All roles currently authenticate at `/auth/login`; the Researcher
  workspace remains role-gated. Compatibility `/research/login` shares the
  handler/rate limit. Canonical `/` is not implemented yet.
- MySQL is the existing development backend. TestingConfig, fixtures and
  browser/snapshot tests still contain SQLite. MySQL-only is approved for
  P1, not completed in P0.
- `PHASE6_COMPLETION.md` records earlier MySQL checks, development revision
  `d574ab56594f`, daily retention and shared-login verification. Those are
  earlier results; P0 did not query the database or scheduler or rerun them.
- Documentation now retains password-only Researcher authentication and
  15-day retention/daily expiry, explicitly reconfirmed in this repair chat
  on 2026-10-05. Prior MFA and pressure-only proposals are superseded.

## Source recovery baseline

Before any P0 source-document changes, captured:

`C:\Users\abdul\.codex\visualizations\2026\10\03\01a10335-6a2c-7180-8c88-3b2de6aa430f\repair-baseline-20261004T224318509726Z`

- `workspace-source.zip`: 753 Git-managed/current visible untracked source
  files (6,226,378 compressed bytes).
- `manifest.json`: SHA-256 for each entry and the ZIP, captured HEAD and scope.
- `git-status-before.txt`: original modified/deleted/untracked state.
- `tracked-working-tree.patch` and `staged.patch`: pre-existing diffs.
- Verified ZIP CRC and every archived/current entry digest; Git status stayed
  unchanged during capture. Capture timestamp is UTC; its date can differ
  from this local-date heading.

This is a source/document baseline, not a Git-history, database, credential,
private-upload or scheduler backup. Ignored secrets/DB/uploads/caches and
local tooling settings are excluded. Do not restore over later work blindly.
The existing committed parent remains in the repository's Git history.

The project virtual-environment executable was denied execution by this
session. Baseline/static validation used the bundled Python standard library
without importing the app, installing dependencies or modifying that environment.

## Phase tracker

| Phase | Status | Evidence needed |
|---|---|---|
| P0 contract/docs/recovery | Complete; static evidence below | Archive/digests, links, contract coverage, diff and preservation check |
| P1 runtime/MySQL | Source complete; four-role local startup reviewed | Historical P1F/full automated gate declined by owner |
| P2 accounts/Rooms/schedules | Source complete; representative Admin views reviewed | Fresh exhaustive concurrency checks not performed |
| P3 finance | Source complete; accounts/history/reports reviewed | Full automated regression not performed |
| P4 enrollment lifecycle | Source complete; scoped wrong-course review opened | All mutation scenarios not repeated in this review |
| P5 academic records | Source complete; finalized attendance correction connected and saved; own history/privacy reviewed | Deadline races not repeated in this review |
| P6 Activities | Complete; four types/filter/bookmark/mobile review | Every individual submission flow not repeated |
| P7 research safety/storage | Source complete; demo/storage/cleanup preview/export detail reviewed | Collection remains paused; no real study initiated |
| P8 legacy/service cleanup | Complete; retired services/forms/templates removed; compatibility routes retained | Historical schema chain deliberately retained |
| P9 database/close-out | Development head 085b7a4e9012; verified recovery and requested demo reset complete; docs current | Comprehensive automated acceptance declined; production deployment outside scope |

F15 can be a focused priority Part after P1. No phase's runtime acceptance
criterion is satisfied merely by documenting its intended behavior.

## P0 changes

- Added the approved contract, implementation roadmap and this tracker.
- Added README/Quick Start for current entry points and safe setup boundaries.
- Updated AGENTS and authority notices/links in decisions, inputs and the
  original master prompt. Existing historical decisions are retained.
- Added forward links to Phase 6 documents while preserving previous
  deployment/test evidence and the final password-only/15-day contract.

## P0 verification boundary

Record only static checks executed for P0. No pytest, application startup,
browser session, dependency scan, migration or real-MySQL check was performed.
Prior Phase 6 successful counts are not P0 test results. Source/test preservation
is checked against the baseline, including the previously deleted template.

## P0 close-out evidence

- Source ZIP CRC and all 753 archived entry SHA-256 digests passed.
- All 746 entries outside the seven modified P0 documents are byte-identical
  to the baseline. The previously deleted research login template stays absent.
- Five new documents passed local-link (20 links), code-fence, conflict-marker
  and whitespace checks. `git diff --check` passed.
- HEAD, staged bytes and the non-P0 working-tree status are unchanged.
- Independent read-only review found the final login/retention decisions
  consistent and runtime/research changes correctly marked as pending.
- Machine-readable evidence: `p0-static-verification.json` beside the source
  archive. These are static checks, not runtime, browser or MySQL test results.

## P1A runtime entry and environment selection

Scope: `app/__init__.py` and `tests/test_runtime_entry.py`, with current
entry/status documentation. The earlier shared-login changes in the factory
are preserved. No schema/environment/credential/scheduler/Git change.

- `GET /` sends anonymous users to `/auth/login` and loaded active accounts
  to their existing role dashboard. Query-string `next` does not override it.
- Unknown explicit or `FLASK_ENV` names, including empty strings, raise a
  generic configuration error before Flask/extension creation. Explicit
  valid names take precedence; missing environment retains development.
- The real Flask-Login loader still rejects suspended/missing accounts and
  stale `auth_version` identities. Login/workspace authorization is unchanged.
- Executed `tests/test_runtime_entry.py` with the existing project Python
  3.14.6, `--noconftest`, `-B`, no pytest cache and warnings as errors:
  **20 passed** after review extended the connection guard to factory creation.
- Tests use the real factory and transient account records at the repository
  boundary. A global engine hook refuses every database connection. No DB
  fixtures, HTTP server, browser or live MySQL verification was executed.
- The project Python is usable after sandbox execution approval; no package
  installation or virtual-environment modification was needed.

## P1B isolated MySQL resource infrastructure

P1B resource code and a concrete verification command are prepared:
`tests/mysql_runtime.py`, its pure guards, and
`scripts/verify_mysql_test_resources.py`. The two new test files passed
**42 strict-warning checks** (20 P1A, 22 ownership guards); no MySQL process
or connection was used by those pure tests. The owner-approved real smoke
check then passed on MySQL 8.0.46/InnoDB: foreign keys enabled, one synthetic
table/row verified, schema released, owned process stopped. Its evidence is
`instance/repair_mysql_tests/run-7f3b9fa8cf7445ca96bc6b597fbb10be/resource-verification.json`.
See
[MYSQL_TEST_TRANSITION.md](MYSQL_TEST_TRANSITION.md).

## P1C core fixture conversion

The owners additionally approved these owned resources throughout isolated
repair tests/migrations/browser checks. No repeated test-schema approval is
needed. Development DB, installs and machine/task/service actions remain
outside that scope.

- Added application test-target validation and a pre-connection lease guard.
  TestingConfig has an unallocated MySQL sentinel, no SQLite/development URI.
- Central app/material fixtures now allocate one owned schema per app.
  Payment/report factory APIs remain through an explicitly bound test factory.
- Verified that omitting `--isolated-mysql` fails before resource initialization.
- Hardened runtime/ownership/config/material checks through the clean runner:
  **108 passed**, strict warnings. Provider/foundation checks: **68 passed**.
  The provider settings check no longer requests an unnecessary DB fixture;
  unknown environment expectations follow the approved fail-closed behavior.
- Real MySQL role/session/authentication/redirect/rate-limit group:
  **28 passed** (247.23 seconds), after an initial five-check gate passed.
- Converted local fixtures in 42 account/academic/research/finance test files,
  preserving assertions and context lifetimes. Independent static review
  confirmed the 31-file core batch retains all 4,679 assertions.
- Ten representative local fixture checks through the clean runner:
  **9 passed, 1 failed**. Payment-intent detail in disabled-provider mode
  returned 404 where the existing read-only history contract expects 200.
  Diagnosis proved that a retained fixture context was carrying an InnoDB
  read snapshot across HTTP requests. Independent per-request scopes fixed
  that case; the next combined gate passed 37/38 and exposed the same stale
  snapshot in a direct fixture read after a successful webhook request.
- Added a test-only read boundary: end an outer AUTOBEGIN read before HTTP,
  preserve attached objects, and refuse uncommitted writes, explicit/nested
  transactions or locking reads without cancelling them. MySQL isolation
  and application write paths are unchanged. Eight MySQL boundary checks
  and both affected cases passed together: **10 passed** (35.96 seconds).
  HTTP/redirect/preserved-context/stream cleanup unit checks: **7 passed**.
  The complete role/session/authentication and representative local fixture
  gate then passed: **38 passed** (319.81 seconds), strict warnings. P1C is
  closed; browser/snapshot/CLI work begins as P1D.
- Independent source-preservation evidence: all 753 archived digests/CRC
  passed; 696 files outside the P0/P1C allowlist remain byte-identical.
  The 42 local fixture files retain 6,501 assertion ASTs exactly; 48 files
  including helpers/provider/foundation/audio retain 6,651 assertions with
  only one fixture-name normalization. HEAD/staged are unchanged and the
  previously deleted template remains absent. Evidence:
  `p1c-source-preservation.json` beside the recovery baseline.
- Legacy browser/migration resources and full MySQL-only acceptance remain
  pending; do not run the full suite yet.
- No development DB, environment, credentials, scheduler, install or Git
  mutation. Synthetic resource directories are ignored and retained.

## P1D browser, snapshot and CLI resources

P1D is complete. The evidence below is isolated verification, not a
development database migration or full-suite acceptance.

- Export snapshot/clock/workspace/derived-state checks now use owned MySQL:
  **6 passed** (69.78 seconds), strict warnings. The concurrent writer and
  exporter use distinct server connection IDs and real REPEATABLE READ;
  the original cutoff, contents/counts and immutable-first-archive checks
  are preserved. Writer failures reach the main test and writers are joined.
- Browser fixtures now use owned MySQL while preserving driver scripts,
  CSRF, role restrictions and collection/answer semantics. The server must
  stop accepting and join serving/request workers before releasing a schema.
- Shared resource cleanup refuses engine disposal/schema drop if a worker
  guard is false or raises; cleanup of unrelated owned apps still continues.
  A failed run then stops its owned MySQL process and retains its data files.
- The isolated CLI boundary passed **1 test** (12.19 seconds), checking
  the literal first revision on a blank owned schema, its BIGINT/InnoDB/FK
  schema, and the actual CLI downgrade to base.
  Historical replacement CLI/probes await P1E's exact-parent
  conversion; do not run those legacy resources or the full suite yet.
- The first complete Chrome gate passed **7 tests** (129.15 seconds), with
  no missing-browser skip. Review then removed a duplicated snapshot
  disposal, routed WSGI failures into a generic main-thread error and made
  server close continue after another close fails. The affected Chrome,
  concurrent export and fixture-cleanup gate passed **13 tests** (138.12
  seconds): eight real MySQL cases and five DB-free cleanup cases.
- Final DB-free WSGI/cleanup/client gate: **16 passed** (2.97 seconds),
  strict warnings. It proves live-worker refusal, response closure, generic
  error reporting, cleanup of other resources and ContextVar restoration.
- No application domain, historical migration, development DB, scheduler,
  environment or Git mutation occurred in P1D.

## P1E exact-parent migration probes

P1E begins with shared owned probe resources, then converts the financial,
academic and research probe families. Every parent is built through the
actual Alembic chain on a blank owned schema. Target revision unit probes
may execute their original Operations callbacks directly; their version
table still records the startup parent. They are not presented as proof of
version advancement. Actual full-chain CLI/model agreement belongs to P1F.

Pure source/offline checks stay DB-free. Do not fabricate INTEGER parent
tables, disable MySQL foreign keys, stamp a version, borrow a live URL or
weaken history/constraint assertions to obtain a passing test.

- Shared parent resource gate: **8 passed** (21.50 seconds), strict warnings:
  six DB-free guard checks and two real MySQL parents (`982f3059f8aa` and
  `d2b7e6a4c519`). The actual chain, version, BIGINT foreign keys, InnoDB and
  enabled foreign-key enforcement are verified.
- M02 fee plans/correction, M03 fee assignments and M04 invoices passed
  **60 tests** (177.23 seconds), strict warnings. Exact-parent probes retain
  row/history/refusal checks. Reflection accounts for MySQL exposing UNIQUE
  constraints as physical indexes, and raw reads quote reserved table names.
- M05/M06 initial gate: **33 passed, 1 failed** (152.07 seconds). MySQL
  correctly refused deleting a payment before its reversal. Synthetic cleanup
  now removes children first; the affected case passed in the next batch.
- Four converted academic parent probes exposed three real MySQL downgrade
  defects: dropping an FK-supporting index before its table fails with 1553.
  P1E corrects only those MySQL downgrade dependency paths; original upgrades
  and historical compatibility paths are retained. The affected academic
  probe/source/quiz-model and SQL literal gate passed **15 tests** (39.22 seconds).
- M05/M06/M07/M10 final gate passed **54 tests** (305.09 seconds), strict
  warnings. M07 also needed a real MySQL downgrade correction: temporarily
  release each account FK, restore NOT NULL, then recreate the same named
  FK/options. Foreign-key enforcement stays on, and protected-history refusal
  runs before any DDL. Combined financial migration gates: **114 passed**.
  Original upgrade functions and revision identifiers remain unchanged.
- Messaging/discussion/progress migration gate: **36 passed** (124.08 seconds).
  Quiz lifecycle/listening/speaking/attendance gate: **32 passed** (187.78
  seconds), after correcting the same MySQL index dependency in lifecycle,
  speaking and attendance downgrades. Original upgrades remain unchanged.
- Grade/announcement/calendar/reference/literal gate initially passed 38
  and failed two incomplete historical Schedule seeds (244.34 seconds).
  Schedule/attendance seed fields are now complete. The grade-only follow-up
  passed ten and exposed one omitted probe-local value list; it was recovered
  from the baseline. Five focused research/whole-schema/Decimal cases then
  passed **5 tests** (197.32 seconds).
- Research resources, replacement/exclusion/refusal checks and the real
  disposition CLI test are converted to exact owned parents, with no stamped
  version or borrowed URI. The initial complete gate passed 31, failed 24
  setup cases because the converted M01 helper lacked imports, and reported
  one generic owned cleanup error (929.54 seconds). Imports were restored;
  the next gate passed 33 before a real DDL read timeout at 20 seconds
  (704.74 seconds). No schema/FK assertion was weakened or DDL retried.
  Migration resources now use finite 120-second read/write bounds. The
  initial cleanup error's cause is unproved; full reverification is pending.
- SQLite model type variants (156) and runtime FK/export branches are removed;
  the MySQL LONGBLOB archive variant remains. MySQL target policy now applies
  to runtime configuration and secondary bind URLs. Runtime/config/resource
  and real snapshot gate: **82 passed** (152.85 seconds), strict warnings.
- One test launch was rejected because automatic approval review could not
  run at service capacity. It executed no command and made no unsafe-action
  determination. The authorized retry succeeded; no permission blocker remains.
- No development database, scheduler, environment or Git mutation occurred.

### P1E current MySQL integrity findings

- Grade/announcement/calendar and gradebook-delete gate: **35 passed**, with
  five additional raw CHECK cases failing because PyMySQL labels error 3819
  OperationalError. Application engines now normalize only that exact
  MySQL/PyMySQL code to IntegrityError; original driver evidence and parameter
  redaction survive. Syntax, connection, timeout and deadlock errors retain
  their classification.
- Follow-up runtime/policy/error/constraint gate: **44 passed, 3 failed**
  (124.73 seconds). The remaining failures prove real case-insensitive
  acceptance of `ASSIGNED`, `DRAFT` and `COURSE` at the database boundary.
  Exact code-column collations and a corrective source migration are being
  prepared. Name/title/email collation is outside that correction.
- Research replacement/fresh CLI gate passed five before synthetic empty
  cleanup tried to remove an experiment parent before its derived child
  (168.83 seconds). The synthetic child is now deleted first, with FKs on.
- Recovery audit verified all 753 archived digests/CRC, 607 unchanged files,
  and unchanged upgrade ASTs/identities for all seven corrected historical
  migrations. All model changes except the documented research-common
  docstring/import are exactly SQLite type-variant removal at this checkpoint.
  Assertion deltas are recorded for resource/backend adaptations and remain
  under review; this is not an assertion-identity claim for P1E. Evidence:
  `p1e-source-preservation.json` beside the recovery baseline.
- Final exact-code/real CLI/error/raw-constraint gate: **24 passed**
  (140.65 seconds), strict warnings. The actual blank-to-head CLI reaches
  `c1a7e4d9b203`; all runtime models agree and all 54 effective code collations
  are inspected. Ordinary text retains its collation. Target Operations
  round-trip preserves every synthetic parent row and original CHECK name;
  uppercase, trailing-space, accent and hidden-character variants are refused.
  Noncanonical parent rows refuse before any DDL. The preliminary 8-pass
  gate had one invoice-width refusal classified as DataError 1406; the raw
  test helper now accepts only that precise width refusal in addition to
  actual integrity errors. Runtime DataError handling was not broadened.
- Removed 13 unused Research replacement app-fixture parameters, proving
  test-body AST identity. Source/offline checks need no schema; independent
  owned-connection probes avoid an unrelated model schema. Full research
  reverification is running with finite 120-second migration timeouts.

### P1E close-out and P1F start

Research full gate: **55 passed** (490.17 seconds), strict warnings, including
the exact-parent history/exclusion/refusal round-trips and actual disposition
CLI. No cleanup error recurred. The first run's generic cleanup error has no
proved cause; that earlier failure remains recorded, rather than inferred to
be the subsequently observed DDL timeout.

Final source audit rechecked all 753 recovery entries and 599 unchanged files.
Every changed model AST agrees exactly with SQLite type-variant removal,
the 54 declared code-collation keywords/imports and the research-common
docstring/import adjustment. The seven historical upgrade functions and
revision identities remain unchanged. HEAD and staged state remain unchanged;
the pre-existing removed Research login template is still absent.

P1E is complete. P1F begins with the complete **7,379-test** collection across
179 test files. Fresh current-head CLI/model agreement already passed in the
24-case gate. Full strict-warning acceptance and final affected browser gates
remain pending; later business phases have not started.

### P1F resource ownership correction

The first full strict run was deliberately interrupted before a summary;
its partial progress is not a successful gate. A scoped process inventory
confirmed that no owned temporary mysqld from that run remained running.

Review found that central/local fixture teardown could drop tables or
dispose engines before the common factory's worker guard. The common factory
is now the sole owner of engine/schema release. Fixtures end only their own
scoped session/context; intentional in-test resets remain, with ownership
and worker-quiescence checks before any reset.

For blank owned model schemas, test-only DDL includes the same indexes inside
CREATE TABLE. An actual paired MySQL comparison proved all 61 tables' column,
PK/index/unique/FK/CHECK/options equivalence; measured creation was 7.04 seconds
standard versus 4.27 seconds inline. No server durability setting was changed.
Existing tables, deferred/cyclic foreign keys and unsupported index syntax use
standard creation, with no speculative DDL executed before that fallback.
Explicit cross-schema tables/references are refused. Runtime and Alembic do
not use this helper.

The bounded conversion covered 59 test files and proved AST identity for all
other operations and assertions. The new ownership/equivalence gate passed
**19 tests** (17.79 seconds). Connection-cleanup failure now also retains its
schema rather than dropping it; **11 DB-free cleanup tests** passed (0.15 seconds).
The affected reset/model/cleanup/real browser/export-concurrency gate passed
**51 tests** (485.41 seconds), with warnings as errors. Effective column
collations were included in the paired model-schema comparison.

A fresh full strict gate is running. No partial full-run progress is claimed
as acceptance. Later business phases remain unimplemented; no development
database or Git mutation was performed.

Full-gate execution now uses four independent owned instances with a whole-file
partition: **7,395 cases**, split **1,920 / 1,806 / 1,842 / 1,827**. The serial
attempt was interrupted to use this partition; pytest reported KeyboardInterrupt
and a tmp_path finalizer KeyError after setup interruption. A scoped process
inventory confirmed its owned server stopped. That attempt has no acceptance
result. The partition audit proves every file and collected function/case count
appears exactly once, and all four processes use identical source digests.
Some unchanged tests generate UUID parameter IDs during import; their actual
random data remains untouched, so coverage is verified by whole-file ownership
and case multiplicity rather than equal UUID text across processes.

Evidence is retained under ignored
`instance/repair_mysql_tests/full-8b2243bb83de4ef384bb2321c3d3fd62/`.
`coverage-plan.json` proves selection only; completed shard results and the final
coverage audit are still required. No dependencies or machine settings changed.
The independent P1F source audit reverified 753 archive entries, 590 unchanged
files, all declared model transformations and seven preserved historical
upgrade functions, recorded separately in `p1f-source-preservation.json`.

### P1F SQL capture correction and fresh full gate

The first parallel attempt exposed a real test-adapter defect: SQLite-style
tuple/key assumptions read PyMySQL parameter names rather than values; qmark
matching missed actual named by-id statements. The attendance-bound case was
reproduced (**1 failed**, 38.80 seconds). A bounded AST audit corrected capture
assumptions in 11 test files while preserving pagination and lock-order
contracts. The affected real MySQL gate passed **26 tests** (212.28 seconds).
No application behavior or assertion threshold was changed by this correction.

The interrupted parallel attempt is diagnostic only. Other teardown/setup
error markers had no captured cause and are not treated as explained by the
parameter correction. Owned old-process roots stopped; the only remaining
process pair belonged to the then-active capture gate (mysqld parent/child).

Schema CREATE/DROP now have finite 120-second DDL waits, matching the migration
resource bound. Endpoint/ordinary verification keeps five seconds and connection
setup two seconds; exact ownership, foreign-key enforcement and server durability
are unchanged. This is not a claim about the earlier uncaptured errors' cause.
Cleanup diagnostics retain only stage, exception class and numeric driver code;
no message, SQL, parameters, URL or credentials. Resource/cleanup/privacy guards
passed **37 DB-free tests** (0.68 seconds).

Fresh full execution is retained under ignored
`instance/repair_mysql_tests/full-40e1441249054cf8b837663d86c653da/`.
It records each test phase and immediate failures, with whole-file coverage and
identical-source audit. No full acceptance is claimed before all four summaries
and the completed-coverage audit pass. Existing named IANA-zone checks may skip
on this host without tzdata; those pure checks must be reported explicitly, and
no DB/browser skip is accepted. No dependency installation is authorized here.

The refreshed collection has **7,398 cases**, partitioned
**1,918 / 1,809 / 1,843 / 1,828**; the plan audit passed with identical source and
complete whole-file coverage. Completed summaries remain pending.

To reclaim storage occupied by the repair itself, retired only the synthetic
data subdirectories of **51 stopped owned runs** created after this repair began
(9,742,866,913 bytes). All exact parent/descendant boundaries and absence of
reparse points were checked; process inventory was rechecked before each removal.
The four active runs were excluded. Run directories, initialization/server logs,
test reports, source and development/private data were retained. The bounded
plan and completion inventory are in ignored `retired-data-plan.json` and
`retired-data-completed.json`. This is temporary test-resource retirement,
not research retention or an application-data cleanup operation.

### P1F full-gate diagnostics: MySQL reflection and quoted identifiers

The immutable-source full gate exposed an exact-index expectation inherited
from SQLite: MySQL also lists UNIQUE constraints in `get_indexes()`. A
separate owned MySQL reproduction produced **4 failed / 4 passed** (133.31
seconds): Assignment, Submission, Attendance and Calendar ordinary-index
inventories failed, while unique/prefix checks passed. A bounded proposal
covers five files, including Quiz, with six non-unique reflection filters.
All expected names/columns and every other AST operation remain unchanged.
The proposal is retained in ignored `index-adapter-plan/`; it is **not yet
applied** to the source under the running full gate.

The enrollment raw-SQL order observer also failed. An independent trace
reproduced **1 failed** (42.86 seconds), while recording the actual protected
sequence: rollback, Group FOR UPDATE, User FOR UPDATE, then ordinary reads.
The observer missed MySQL's backtick-quoted `groups` name. This is a proved
capture defect, not evidence that an unlocked read interrupted that lock pair.
A reviewed proposal covers this observer and ten related table/by-id capture
helpers. Its AST audit preserves every assertion and all operations except
the eleven table-name capture expressions. Source files remain unchanged.

An owned diagnostic run is trying those prepared adapter changes in memory,
with original assertions/scenarios and no application/migration change.
That diagnostic is explicitly separate from full immutable-source acceptance;
it cannot close P1F. The four original full shards continue collecting results.

The prepared-adapter diagnostic passed **35 cases** (549.44 seconds), and the
separate form-expiry diagnostic passed **6 cases** (112.50 seconds), both with
strict warnings. Their original source hashes remained unchanged throughout.
The expiry failure was independently reproduced (**1 failed**, 47.43 seconds):
the global TimestampSigner clock patch also invalidated the authenticated
cookie, so the GET produced no form token. The correction clones the form's
purpose-specific serializer and ages only its signed payload; cookie time,
payload/bindings/salt/nonce and all route assertions remain intact.

The full run also exposed three ordinary-text expectations tied to SQLite
comparison behavior: the two account email tests queried a mixed-case value,
and the fee-plan race test expected a `Books` query to ignore the injected
`books` item. MySQL's existing text collation correctly matches those values.
The corrected assertions inspect the actual stored lowercase email and prove
that only the original injected `books` row remains. No application text
collation or business behavior was changed.

The known-failing full shards were interrupted before summaries; this run is
diagnostic, **not completed full-suite evidence**. A scoped process inventory
confirmed **zero** remaining owned temporary mysqld processes before mutation.
After bounded AST/hash preflight, applied the proposals to **21 existing test
files** and added `tests/aged_tokens.py`. A strict **44-case applied-source**
gate is running. Its acceptance and a fresh complete full gate remain required.
The explicit receipts and proposal/detailed diagnostic evidence remain in the
ignored repair resource directory. P2-P9 have not started.

### P1F applied adapters and empty-template isolation

The applied-source adapter gate passed **44 cases** (289.77 seconds) with
strict warnings. No application/migration behavior changed in those adapters.
Independent source recovery verification passed all 753 archived entry hashes;
578 original files remain unchanged, all declared model transformations and
seven historical migration upgrade functions remain accounted for.

Current-model test schemas now have an optional owned empty-table template.
Every application still receives a new schema name and lease. Only physically
unchanged tables with no rows may move atomically into that fresh blank schema.
Cleanup checks stopped workers, ORM sessions and checked-out connections,
including connections from disposed pools; old leases are revoked before
cleanup. Fresh Alembic targets stay blank. Foreign keys, server durability,
collations, constraints and index definitions remain unchanged. Table data is
deleted transactionally in dependency order, and actual auto-increment counters
are reset; retained templates are limited to two per template manager. Normal
session resources attach one manager per owned temporary server. Template
safety tests deliberately use separate scoped managers within that owned server.

The focused template/schema/cleanup/actual CLI gate passed **39 cases**
(117.78 seconds). Extended adapter/Chrome/export verification then reported
**56 passed / 1 teardown error** (249.26 seconds), so it did not pass. A single
owned reproduction confirmed **1 passed / 1 teardown error** (15.38 seconds):
cleanup raised FK rejection 1451 for payment reversal data. The template path
now declines reuse before any revocation/data mutation when populated self-FK
links are present, leaving that schema to the existing owned DROP path.
Regression and extended reverification remain pending. The scoped inventory
after reproduction found zero remaining owned temporary mysqld processes.

The interrupted full runs remain diagnostic only. A new complete immutable
source full gate is still required before P1 closes; P2-P9 have not started.

Self-FK reverification: the first new regression attempted an ORM bulk delete
and correctly encountered the application's financial-history protection
before reaching MySQL (**38 passed / 1 failed**, 130.13 seconds). The regression
was corrected to exercise the owned synthetic engine directly, preserving
that application protection. The explicit MySQL 1451 reproduction, unchanged
rows/active lease when reuse is declined, subsequent owned schema removal and
the original reversal-route case now pass (**2 passed**, 22.96 seconds).
The extended adapter/Chrome/export gate is running again.

The extended applied-source gate now passes **56 tests** (277.42 seconds),
including real Chrome and independent-connection export checks, with strict
warnings and no cleanup errors. Its per-phase evidence and exact summary are
retained as `applied-adapter-extended-reports.jsonl` / `-result.json` in the
ignored repair resource directory.

A static reflection review found two further exact-index observers in the
Question/Option model tests. Real owned MySQL reproduced **2 failed** (29.67
seconds), solely because UNIQUE(public_id) also appears in `get_indexes()`.
Added two non-unique reflection filters in that single test file. A bounded
AST/hash audit preserves every assertion and all other operations; its receipt
is `question-index-adapter.json`. No application schema behavior changes.
Affected reverification and a fresh full gate remain required.

The Question/Option reflection gate passes **3 tests** (22.73 seconds), including
the existing no-redundant-FK-index invariant. Refreshed preservation verification
passed all 753 source archive hashes, with 577 unchanged original files, zero
undeclared model AST deltas and seven preserved historical migration upgrades.
Whitespace verification with the repository's normal line-ending policy passed;
HEAD and staged state remain unchanged. No development DB was accessed.

The first post-template full launch (`full-17a29b83ebb1461db67ebeae0ea79edb`)
incorrectly discovered ignored proposal copies as tests: 8,778 collected,
40 missing-fixture setup errors and three pure passes before its per-shard
failure bound. This is a collection-harness failure, not full application
acceptance. The runner now explicitly collects `tests/` and rejects any other
node path. The repository's discoverable source contains no test modules
outside that canonical tree.

A DB-free collection audit proves **7,414 canonical cases**: all 7,398 prior
cases are preserved with parameter multiplicity, plus exactly 16 new template
safety cases. No former case was excluded; ignored proposal copies are outside
the acceptance source. The exact inventory is `canonical-collection.json`.
The four fresh full shards use this canonical scope and unchanged source.

Fresh canonical full run: `full-26c1ca4a5145440181d5cfb9155f2038`, with
**1,982 / 1,874 / 1,752 / 1,806** cases. The partition audit passed: all 7,414
cases selected exactly once, complete whole-file coverage, dynamic UUID
parameter multiplicity preserved and identical application/test/migration
source in all shards. Final summaries and completed audit remain pending.

### P1F application lifetime and bounded full resources

The canonical four-part run `full-26c1ca4a5145440181d5cfb9155f2038`
was stopped diagnostically after **2,845 complete setup/call/teardown passes**.
It is not full acceptance. One Material reflection assertion did not recognize
MySQL's identifier quotes; MySQL later reported allocation error 1041, followed
by eight inventory-refusal setup errors in that part. A separate password-hashing
case also reported memory allocation failure. Four known DB-free IANA cases were
skipped because this host lacks the time-zone database. Other parts were
interrupted; their partial results remain preserved.

A scoped process inventory showed completed-test Python processes retaining
roughly 2.5-2.8 GB each. An independent DB-free regression reproduced a retained
application after garbage collection (**1 failed**, 1.09 seconds). Model-template
engine listeners held the application strongly, completing a cycle through
Flask-SQLAlchemy's weak-key application/engine map. Template state now holds a
weak application reference; cleanup still requires a live application and all
worker/connection guards. Revoked lease checks remain attached to engines.

The corrected Material observer strips identifier quotes before its original
29 assertions. Bounded AST/hash verification accounts for this adapter and its
updated MySQL docstring, with all 314 application/migration/script files unchanged
from the diagnostic build. `pytest.ini` now limits default discovery to `tests/`.
A DB-free default-discovery audit preserves all 7,398 prior canonical cases and
adds 18 template/lifetime safety cases: **7,416 total**.

The actual lifetime/template/schema/cleanup/Material/Message/payment-cancellation
gate passed **97 tests** (314.29 seconds), with strict warnings and no errors.
Both application collection and continued empty-template transfer/stale-URL
refusal are covered. Source recovery reverified all 753 archive entries: 576
unchanged original files, no undeclared model AST deltas, seven historical
upgrade functions preserved. Normal-policy whitespace verification passed;
HEAD and staged state remain unchanged. No development database was accessed.

All owned temporary database processes were confirmed stopped before the
interrupted runner processes were retired. A fresh complete gate will use eight
whole-file parts with at most two database-backed parts running simultaneously.
P1F full acceptance remains pending; P2-P9 runtime work has not begun.

Fresh bounded canonical gate: `full-dc4b3bc848c649f0bfc11f95f64858e8`.
Its DB-free eight-part inventory audit passed: **988 / 894 / 883 / 890 /
933 / 1,016 / 903 / 909** cases, all 7,416 selected exactly once, whole-file
coverage, dynamic parameter multiplicity preserved, identical Python source
and pytest configuration in every part. Zero owned temporary mysqld processes
were observed before launch. Parts 0 and 1 are running first; at most two
database-backed parts run concurrently. Completed acceptance remains pending.

The first two parts of that bounded run stopped before final summaries after
**380 complete per-test passes**, with no recorded failure. Their runner
sessions no longer existed; scoped inventory confirmed zero owned mysqld
processes. These are partial diagnostic results, not completed acceptance.
No application/test/migration source changed before the replacement launch.

Replacement full run: `full-1f24cd975561469fb9521207028eb531`. An ignored local
supervisor persists per-part console/report/summary records and runs the eight
parts automatically with a maximum of two active parts. It records the source
inventory and stops launching parts on a failure, source drift or explicit
stop marker, allowing already active parts to finish their owned cleanup.
It performs the completed coverage audit only after all eight exit successfully.
The helper starts hidden, uses the existing isolated runner, installs nothing,
and does not read development credentials or change machine settings. Full
acceptance remains pending; no later runtime phase has begun.

### P7A candidate preparation while P1F source is frozen

Prepared a separate ignored delivery-binding candidate in
`instance/repair_mysql_tests/p7a_candidate`; it is **not applied** to the checked
runtime. It uses stable account/configuration public identities and authentication/
immutable configuration versions, with a purpose-specific opaque HMAC. Verification
precedes subject lookup/provisioning and repeats on each provisioning retry. Browser
batches retain their original proof and event IDs; cross-scope startup/late callbacks
do not transfer dropped counts or clear a newer scope's pending data.

Executed preparation checks: **8 pure proof tests**, **13 pure protocol/ordering
tests**, **10 JavaScript delivery simulations**, and JavaScript syntax verification.
All passed. The ordering tests execute the prepared functions with synthetic lock
providers; they do not prove MySQL blocking. The JavaScript checks use isolated
delivery contexts, not a real browser. Bounded AST/hash receipts preserve all other
top-level functions in six existing candidate Python files; the current application,
canonical tests and migrations remain unchanged. Server-side outcome recording,
sampling, session resolution, clocks, event age, dictionaries/export descriptions
and 15-day daily retention are untouched.

The early durable P1F checkpoint recorded 347 complete per-test checks. A scoped
process check measured about 0.8 GB total committed memory for its two Python test
workers (including their small launchers), without changing resource limits or
machine settings. Its final first-pair result is recorded below; that early
checkpoint was not acceptance.

Additional preparation checks now pass on the ignored P7A overlay: **13 actual
owned MySQL tests** (113.93 seconds), **4 actual Chrome delivery scenarios** on
isolated synthetic pages (19.39 seconds), and **46 existing overlay regressions**
(424.94 seconds). The 46 include six actual LMS/Chrome and six export/snapshot
cases. The first regression invocation incorrectly loaded three canonical direct
ingestion tests instead of their prepared fixtures (**13 passed / 3 failed**);
the corrected explicit-path/loaded-source invocation is the 46-case passing gate.

The independent-connection configuration race first failed with MySQL deadlock
1213 (28.87 seconds). The candidate's configuration discovery/locking now follows
the writer's primary-key-first acquisition: discover the current ID without a
lock, lock that row, recheck collection liveness and verify the original proof.
The isolated race then passed (42.87 seconds); the complete 13-case result above
includes that corrected race. No canonical source or migration changed, and these
candidate checks do not close P1F or establish applied P7A acceptance. Sampling,
prompt/answer, session and retention meanings remain unchanged.

### P1F first bounded pair and remaining SQL observers - 2026-10-06

The durable run `full-1f24cd975561469fb9521207028eb531` completed parts 0 and 1:
**1,871 passed / 7 failed / 4 known pure IANA skips**, out of 1,882 selected
cases. There were no cleanup failures or database/browser skips. Its supervisor
stopped before parts 2-7, after both active parts finished. Application/test/
migration source remained byte-identical to its launch inventory. This failed
partial run is diagnostic, not full acceptance.

Three Message bound observers confused MySQL mapping keys with bound values or
its quoted INNER JOIN syntax. A prepared adapter preserves all 186 assertions,
reads actual driver-bound pagination values in logical limit/offset order and
normalizes only those identifier/join spellings. The three affected candidate
checks pass (the accompanying unchanged dashboard diagnostic still failed).

The dashboard recorded one real per-request authenticated-account reload plus
ten bounded feature reads. The prepared observer explicitly requires exactly one
complete primary-key account lookup and retains the ten-query feature bound for
six enrolled groups. Three webhook-model matrix failures expected SQLite error
text even though MySQL enforced the requested CHECKs (3819). Exact MySQL error/
constraint assertions are under isolated reverification before application.
P1F full acceptance and P7A application remain pending; later runtime Parts have
not started. No development database, Git mutation or installation occurred.

The first three-file candidate gate completed **139 passed / 1 failed**
(455.35 seconds); its sole remaining failure incorrectly treated the deliberately
provided CHECK family prefix as a complete name. The corrected adapter matches
3819 plus an exact MySQL message naming a registered CHECK from that family;
the complete offending matrix passed (25.73 seconds). All three reviewed observer
adapters are now applied. The seven originally failing canonical cases pass
**7 tests** (39.26 seconds), warning-strict, on a new owned MySQL resource.

A bounded source audit proves exactly these three test files changed since the
failed pair: `test_messages_routes.py`, `test_student_dashboard.py` and
`test_verified_webhooks_model.py`. All 314 application/migration/script Python
files and pytest configuration remain unchanged. The archive audit reverified
all 753 entries, with 575 unchanged original files, no undeclared model AST
deltas and all seven edited historical upgrade functions preserved. Every prior
case remains in the refreshed DB-free collection: **7,416 = 7,398 prior + 18
template/lifetime safety cases**. Normal-policy whitespace verification passes;
HEAD and the empty staged state remain unchanged.

Fresh canonical full gate: `full-6d1f61f213304454aae3400d1a7bc6ac`. Its partition
audit covers **988 / 894 / 883 / 890 / 933 / 1,016 / 903 / 909** cases, all selected
exactly once, with whole-file coverage, preserved dynamic parameter multiplicity
and matching source/configuration. The hidden supervisor additionally freezes
application templates/static assets. At most two database-backed parts run at
once. Normal call failures may continue diagnostic coverage after clean teardown;
ownership/setup/worker/cleanup failures, missing summaries or source drift stop
new launches. The completed acceptance audit still requires all eight parts to
exit successfully. Latest checkpoint: **172 complete per-test passes**, without
failure or skip; full acceptance remains pending.

A scoped resource inventory after the integrated browser gate found no remaining
owned Chrome process. Only the two full parts and their owned MySQL resources
were active; their Python workers used approximately 0.59 GB total committed
memory. No resource limit or machine setting changed.

Two additional ignored P7A **integrated LMS/MySQL/Chrome cases pass** (46.94
seconds). Real Chrome holds the actual collector's first batch before HTTP,
switches its cookie to a second synthetic Student or activates a successor
configuration, and sends the original body under the new context. The real
route refuses it with `stale_delivery_scope`; none of its event IDs is stored.
The real collector on the next page delivers new-scope events successfully,
with unchanged keepalive and an empty outbox. Database assertions verify the
correct subject/configuration and absence of old-context rows. This browser
driver uses the established CSRF-disabled test sign-in shortcut; the separate
13-case owned gate proves real CSRF enforcement. Canonical source remains
unchanged, and the P7A runtime candidate is still unapplied.

### P1F completed diagnostic gate and test isolation corrections - 2026-10-06

The durable run `full-6d1f61f213304454aae3400d1a7bc6ac` finished all eight parts:
**7,402 passed / 10 failed / 4 known pure IANA skips**. Its completed diagnostic
coverage audit proves all **7,416 cases executed exactly once**, with matching
source/whole-file partitions, no database or browser skips, and no resource or
cleanup failures. Application/test/migration/template/static source remained
unchanged during that run. This is a complete diagnostic result, not acceptance.

All ten failures were traced to test observations or isolation. In-process
Alembic CLI logging configuration disabled pre-existing loggers and affected
four later logging assertions. A test-only autouse fixture now restores each
pre-existing logger's original disabled flag after a test; runtime migration
logging remains unchanged. Historical DDL shape comparisons omit only the new
explicit closed-code collation. The standalone purge test ends the existing
read-only AUTOBEGIN snapshot through the guarded fixture boundary before
invoking the independent script. The two overlength-code rejection observers
require the exact MySQL 1406 error and column. The growing dashboard observer
requires exactly one authenticated-account lookup and preserves its ten-query
feature limit. The non-UTC receipt test explicitly selects Africa/Tripoli and
checks the independent expected local time.

Exactly ten canonical test files changed. A bounded source audit preserves all
**651 existing assertions**, adds six meaningful assertions, and confirms that
all application/migration/script/template/static files remain unchanged from
the completed diagnostic gate. The focused canonical gate passed **15 tests**
(75.57 seconds), with strict warnings, no skips/errors and no cleanup failures.
It runs both actual migration CLI cases before the four logging checks, includes
all ten original failures, and retains the dashboard and real-parent checks.
Refreshed DB-free collection still preserves **7,416 = 7,398 prior + 18 safety
cases**; no test was excluded. The source recovery audit reverified all 753
archive entries, with 570 unchanged original files, no undeclared model AST
deltas, and all seven edited historical upgrade functions preserved. Whitespace
verification passed; HEAD and the empty staged state remain unchanged.

A new frozen canonical gate, `full-1533e860f5ad407eba9c5a20db04475d`, is running
under the same durable supervisor and maximum of two active database-backed
parts. Completed full acceptance is still required. P1F remains pending, the
P7A candidate remains unapplied, and P2-P9 runtime work has not begun. No
development database, installation, Git mutation or machine setting was used.

### Owner stopped tests and changed implementation order - 2026-10-06

The owner requested all tests to stop and prohibited further test execution
until every approved repair change is implemented. Run
`full-1533e860f5ad407eba9c5a20db04475d` was interrupted by Ctrl+C sent only to
its verified owned worker consoles. The supervisor launched no later parts;
both active parts ended and its final record confirms no active workers, no
source drift and no resource failure. Scoped process inspection confirms no
remaining workers for this run or owned temporary mysqld process. Interruption
output is diagnostic, not a new application failure or completed acceptance.

P1F is deferred, not passed. Continue bounded P2-P8 source implementation and
apply the prepared P7A candidate; prepare meaningful coverage without running
tests, browser gates or collection-only pytest. Final verification follows all
source changes. Live database/scheduler/release and Git operations remain outside
this source-only continuation. No tests were launched after this instruction.

### Integrated repair source and applied development upgrade - 2026-10-06

The owner subsequently requested finishing integration, remaining legacy routes,
documentation and the new database migrations. P2-P7 source workflows are present;
P8 active HTTP consumers use the new episode/general account operations. Historical
private helpers are retained while their tests/migrations remain supported.

After source integration was complete, resumed selected isolated verification.
New domain/scope coverage passed 44 cases plus 35 subtests; the single account-switch
test setup failure was corrected and its historical-progress access case passed in
a targeted rerun. New HTTP/reports/provider/last-seat integration passed 4, actual
four-type Activities passed 1, research cleanup/retention/operator passed 3,
Chrome/entry/auth passed 51 (including two real Chrome scope-switch scenarios),
and clean-chain/private-backup restore-upgrade passed 2. There are 106 distinct
passing selected cases, not a full-suite acceptance result.

Captured a private consistent SQL backup and three private files, verified archive
CRC/digests, restored them in owned resources, and upgraded both a clean schema and
the restored database to the current models. Applied the exact local development
chain from `d574ab56594f` to `085b7a4e9012`; read-only post-upgrade inspection found
73 InnoDB tables, matching models/CHECK names and preserved every original table's
row count. Existing Course prices remain unset deliberately. Recovery metadata,
executed gates and remaining release acceptance are in `REPAIR_INTEGRATION.md`.

The earlier stopped full supervisor was not restarted. P1F/full strict acceptance
and broader superseded-contract test review remain pending; selected passing
checks do not close that gate. No scheduled task, collection period, installation,
.env change, staging, commit or push occurred. HEAD and staged state are preserved.
