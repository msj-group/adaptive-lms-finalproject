# Phase 6 — Natural-use research collection

> Repair scope (2026-10-05): see `APPROVED_REPAIR_CONTRACT.md` and
> `PROJECT_STATUS.md`. Shared login, password-only Researcher authentication
> and 15-day daily retention are preserved. F15 delivery-scope binding,
> operator attribution, usage/gap reporting and manual cleanup are planned
> changes, not completed here. Sampling/windows/session/provenance/labels
> remain unchanged; historical verification stays in `PHASE6_COMPLETION.md`.

This document is the current Phase 6 contract. It replaces the Phase 6 Parts
M01 (in-app consent workflow), M01R/M01R2/M01R3/M01S (their corrections) and
M02A (experiment protocol catalogue), whose records in `docs/DECISIONS.md`
remain as history and are marked superseded. The accepted decisions are
"Natural-use research collection (Phase 6 replacement)", its correction
"Phase 6 follow-up — population rule, platform-wide collection, immutable
exports" and "Phase 6 final data-integrity corrections" in
`docs/DECISIONS.md`, followed by "Phase 6 local close-out — 2026-10-04"
and "Phase 6 shared login — 2026-10-05".

Operational steps for a real deployment are in
`docs/RESEARCH_DEPLOYMENT_RUNBOOK.md`.

## 1. Method

- **Population rule.** While an authorized configuration is active,
  collecting and inside its collection period, collection runs automatically
  for **every eligible Student**: an authenticated Student account whose
  status is active and that is not excluded. This includes Students created
  or activated after collection started. There is no operator inclusion, no
  batch enrolment and no per-Student switch.
- **Outside the population.** Teachers, Administrators, Researchers,
  anonymous visitors, suspended accounts and excluded Students are never
  collected. Development and demonstration accounts are marked by an operator
  before collection starts; their data is stored as `demo` and never
  exported.
- **Natural use.** There are no researcher-assigned tasks, task sets, task
  order, task start button or task-completion declaration.
- **Signals.** A documented allowlist of interaction events (section 5),
  collected in the background on every page of the Student platform, plus
  server-confirmed outcomes of the Student workflows.
- **Labels.** Occasional, optional frustration ratings (section 6). Data
  without a rating stays unlabeled: no label is ever inferred from clicks,
  errors, inactivity, dismissal or nonresponse.
- **Version A only.** No adaptive intervention and no model prediction runs in
  this phase. Model training is Phase 7; approved-model inference and
  adaptation are Phase 8.

## 2. Basis, exclusions and what the application does not claim

The application records **no consent**: it presents no consent screen, no
"accept" or "decline" control and no Student-facing research information
page, and it never treats silence or continued use as consent. It does not
sign an institutional agreement or verify any external legal or ethics
approval. What it stores is truthful about what happened:

- an included subject records the basis `population_rule` (created
  automatically, see below) or `operator_reinstatement` (an operator lifted
  an exclusion);
- an excluded subject records `external_exclusion` (recorded by an operator
  through the centre's external process) or `legacy_collection_exclusion`
  (carried over from a refusal or withdrawal in the removed consent
  workflow).

**Automatic, idempotent subjects.** An eligible Student gets a pseudonymous
subject and its account link at the first server-side collection write (a
batch or a server outcome), decided on the server from the locked account,
never from anything the client sends. The `users` row lock serialises a
Student's concurrent first writes, `uq_research_subject_links_user_id` is the
final defense, and a request that loses the race rolls back and reuses the
winner's subject. Opening a page writes nothing.

**Exclusions are final until lifted.** An exclusion can be recorded before
the Student was ever collected (it creates an excluded subject), closes any
open session at once, and is enforced on every write, including delayed
batches. Logging in or opening a page never re-enrols an excluded Student.
Only `reinstate` lifts it; a legacy exclusion needs an explicit override.
Excluded Students are visible only in the restricted places: by pseudonymous
code on the Researcher workspace's Exclusions page, and with their account
email in the operator tool (`list-excluded`).

The application cannot verify a Student's age or the external arrangements;
both stay the centre's responsibility.

## 3. Removed (not archived)

The following are removed from the running application, the route map, the
navigation, the data model and the tests. Historical Alembic revisions
`f2a6d1c84b37` and `b86838ce23db` remain only so the upgrade chain works.

- The Administrator research area (`/admin/research/...`), its forms, its
  navigation entry and heading.
- The Student consent pages (`/student/research-consent/...`) and the portal
  consent link and its context processor.
- Consent documents, consent events, consent-dependent participants, their
  tokens, queries, transactions, text rules, enums and fixtures.
- The experiment protocol catalogue (`/research/protocols/...`), its
  services, forms, templates, models, enums, dashboard counts and fixtures.
- The Student "usage research" information page (`/student/usage-research`),
  its template and context, and the portal's "Usage research on" link. No
  ordinary portal shows any research status, notice, indicator, information
  page, researcher identity or management surface.
- The operator `include` command and the per-area scope switches of a
  configuration.

Kept because they have a real new consumer: the Researcher role, the server
authorization decorator, safe redirects, Argon2 password hashing, public
identifiers, the path-scoped `private, no-store` hook, bounded pagination,
CSRF protection, secure random code generation and the interactive
Researcher provisioning script.

## 4. Domain model

| Table | Purpose |
|---|---|
| `research_subjects` | Stable pseudonymous identity (`RS-` code), collection status (`included`/`excluded`), its truthful basis (section 2), and provenance (`study`/`demo`). |
| `research_subject_links` | The only link from a subject to a `users` row; unique both ways. Never joined by a Researcher-facing query or an export. |
| `research_configurations` | Versioned collection configuration: event schema version, sampling policy, collection period, retention in force, and the operational collecting/paused state. No per-area scope. Frozen once activated; at most one active. |
| `research_sessions` | Natural-use sessions with start/last-seen/end, observation-run tracking, sampling eligibility counters and delivery-quality counters. |
| `research_events` | Validated client observations and server-confirmed outcomes, with separate client and server clocks. |
| `research_feedback_prompts` | Sampled prompts, their deferrals, display, exact pre-prompt window, the raw 1–5 rating, optional causes, dismissal and late-response flag. |
| `research_exports` | What each export contains: filters, the timezone its dates were read in, the cutoff of its read snapshot (`cutoff_ms`), counts, manifest digest, and the oldest data it holds. |
| `research_export_archives` | The immutable ZIP of one export, with its SHA-256 and size (`LONGBLOB` on MySQL, at most 16 MiB). |
| `research_audit_events` | Audit of configuration changes, collection state changes, exclusions, reinstatements, demo marks, exports, downloads and retention purges. No Student content. |

This is **pseudonymization, not anonymization**: the mapping exists in
`research_subject_links` and anyone with database access can follow it.

## 5. Event dictionary (`natural-use-events.v1`)

The authoritative declaration is `app/services/research_event_dictionary.py`;
a test proves this document names every declared event type, page, technical
exclusion and client-observed action.

Every client event carries: `id` (UUID idempotency key), `type`, `t` (client
clock, epoch ms), `page` (allowlisted page id), `view` (random page-view
reference), `tab` (random tab reference), `seq` (per-page sequence). The
server adds the session, `received_at_ms`, the batch clock offset and the
corrected `occurred_at_ms`.

| Event type | Source | Fields | Why it is collected |
|---|---|---|---|
| `page_view` | client | detail: `narrow`/`medium`/`wide`; progress pages: position, count | Page and activity transitions; device-size coverage without a fingerprint |
| `page_leave` | client | duration_ms (visible time) | Dwell time on visible pages |
| `visibility_hidden` | client | — | Hidden time is never counted as observed |
| `visibility_visible` | client | — | Return to the page |
| `heartbeat` | client | detail: `active`/`idle`/`media_playing` | Continuous observation coverage and contextual inactivity |
| `control_click` | client | element; position for search results | Meaningful control actions |
| `repeated_click` | client | element, count 2–50 | Bounded repeated-click aggregate |
| `non_interactive_click` | client | — | Clicks on non-controls, with no target or position |
| `input_change` | client | element | Answer/filter/file-field changes, never the value |
| `form_submit` | client | element (form id) | Submission attempts |
| `form_invalid` | client | element, count of invalid fields | Client validation failures |
| `media_event` | client | element (media kind), detail, position (s) | Media progress; media is never copied |
| `recorder_state` | client | detail | Recording/upload progress; no audio |
| `recorder_failure` | client | detail | Permission denied, unsupported, recorder error |
| `lesson_completion` | server | detail | Authoritative lesson completion |
| `search` | server | detail, count | Result count; never the search terms |
| `assignment_submission` | server | detail | Authoritative submission outcome; never the answer |
| `quiz_start` | server | detail | Attempt started/resumed/refused |
| `quiz_answer` | server | detail | Answer save accepted/refused; never the selection |
| `quiz_submission` | server | detail | Authoritative attempt end; never the score |
| `listening_start` | server | detail | As `quiz_start` |
| `listening_answer` | server | detail | As `quiz_answer` |
| `listening_submission` | server | detail | As `quiz_submission` |
| `speaking_submission` | server | detail | Authoritative upload outcome; never the audio |
| `discussion_reply` | server | detail | Posted/refused; never the text |
| `message_send` | server | detail | Sent/refused; never the body, subject or recipient |

### Platform coverage

The collector runs in the background on **every** page of the authenticated
Student platform (the `student`, `messages` and `notifications` blueprints),
for an eligible Student only. It shows nothing; the only visible element is
the optional question of section 6. A test fails when a route of those
blueprints has no intentional classification below, and another proves that
every full page template of the platform extends the layout that carries the
collector.

Pages (`page` values): `student.dashboard`, `student.group_units`,
`student.lesson_detail`, `student.search`, `student.assignments_list`,
`student.assignment_detail`, `student.quiz_list`, `student.quiz_detail`,
`student.quiz_question`, `student.quiz_result`, `student.listening_list`,
`student.listening_detail`, `student.listening_question`,
`student.listening_result`, `student.speaking_list`, `student.speaking_detail`,
`student.speaking_record`, `student.speaking_receipt`,
`student.attendance_list`, `student.grades_list`, `student.group_grades`,
`student.announcements_list`, `student.announcement_detail`,
`student.calendar`, `student.discussions_overview`,
`student.discussions_group`, `student.discussions_topic`, `messages.inbox`,
`messages.new_thread`, `messages.thread`, `notifications.inbox`.

POST routes that re-render a page are named by that page
(`student.assignment_submit`, `student.speaking_submit`,
`student.discussions_reply`, `messages.create_thread`, `messages.reply`).
Server outcomes come from the routes in the table above. Client-observed only
(their `form_submit` and the next `page_view` describe them):
`notifications.open_notification`, `notifications.read_notification`,
`notifications.read_all`.

**Technical exclusions** (data minimisation; each serves bytes, not a page,
and the click that opened it is already a `control_click`):
`student.material_open`, `student.material_download`,
`student.listening_audio`, `student.speaking_audio`,
`student.speaking_audio_download`.

A coarse area per page (`core`, learning, search, assignments, quizzes,
listening, speaking, communication, records) is used **only** to report page
coverage in the Researcher workspace; it never narrows collection.

Never collected, because no field exists for it: passwords, keystrokes or key
counts, typed text, answer content or option identity, grades or scores,
search terms, message or discussion bodies, file names or contents, audio or
video, payment data, URLs or query strings, DOM text or HTML, screen
coordinates, user-agent strings, IP addresses or browser fingerprints.
Unauthenticated login attempts are not attributed to anybody.

### Delivery rules

- `POST /collect/events`, JSON, CSRF header (`X-CSRFToken`), same-origin
  session cookie. No CSRF exemption and no `sendBeacon`: **every** batch is
  sent with `fetch(..., {keepalive: true})`, which carries the CSRF header,
  so a navigation (a submitted form, a followed link, a closed tab) never
  cancels a batch already handed to the browser.
- **Outbox and replay.** Each batch is written to a small per-tab outbox
  (`sessionStorage`, at most four batches) before it is sent and removed when
  the server answers. A batch whose answer never arrived — a network
  failure, a 409/429/5xx, or a page that navigated away first — is re-sent
  from the outbox with the same event ids and `"replay": true`, at most three
  times. A form submission records `form_submit` and hands its batch to the
  browser at once; the submission itself is never delayed or prevented.
- At most 50 events and 32 KiB per batch; unknown batch keys, a wrong schema,
  a non-boolean `replay` or a malformed body reject the whole batch. An
  unknown event type, unknown field, disallowed page/element/detail or
  out-of-range value rejects that event (counted as invalid).
- The acting Student comes from the authenticated session. The research
  session comes from the signed Flask session cookie and is re-proved to
  belong to the acting Student's subject; the client never sends one, nor a
  subject, account or email.
- Idempotency: `id` is globally unique and stored at most once. An unmarked
  re-send counts its stored events as duplicates. A batch is stored
  atomically, so a marked replay whose accepted events are all stored already
  is a re-delivery of a batch counted when it first arrived: it changes no
  counter (batches, accepted, duplicates, invalid, late, dropped).
- Clocks: `offset = server_receipt - sent_at`; `occurred_at_ms = t + offset`.
  An event whose corrected time is more than 60 s in the future or older than
  10 minutes, or earlier than the session start (minus 60 s), is rejected as
  late. Client time and server receipt time are stored separately.
- Every write rechecks, after its locks: active Student account, subject not
  excluded (provisioned if absent), configuration active and collecting, and
  the collection period.

### Sessions

- A session belongs to one subject and one browser login session (the signed
  cookie). Tabs of one browser share it; each tab has its own random `tab`
  reference. A second browser is a second session.
- It ends on logout, after the configured inactivity timeout (default 30
  minutes without an accepted event; closed when the next activity arrives),
  when the active configuration changes, or when the subject stops being
  eligible.
- Observation runs: a run starts at the first visible event and continues
  while accepted events arrive at most 75 s apart; a `visibility_hidden`
  event ends it. Only run time counts as observed.

## 6. Hybrid feedback sampling

Pilot defaults, stored per configuration version (they are not validated
scientific constants):

| Setting | Default |
|---|---|
| Automatic prompts per session | 1 |
| Automatic prompts per Student per day (`APP_TIMEZONE`) | 2 |
| Lookback window ending at display | 120 s |
| Minimum continuously observed time in that window | 120 s |
| Random-moment probability per eligible batch | 5% |
| Activity-end probability per natural activity ending | 50% |
| Offer lifetime before display | 10 minutes |
| Response window after display | 10 minutes |

- **Moments.** Random eligible moments and selected natural activity endings
  (lesson completed, assignment/quiz/listening/speaking submitted). Sampling
  never depends on errors, so smooth and troubled interactions are both
  sampled.
- **Deferral.** Timed quiz and listening pages, an active recording or upload,
  and hidden tabs defer the prompt; each deferral and its reason are stored.
- **Display grant.** The client asks the server immediately before showing
  the prompt. The server rechecks eligibility and the budgets under the
  subject lock; the grant moment is the display moment and ends the lookback
  window. Only a granted display consumes the budget.
- **The card, exactly as shown** (no research, study or usage wording; no
  claim of a purpose such as grades, performance or support):
  - title "A quick question";
  - "Answering is optional. Skipping does not affect your grades or your
    access.";
  - "Thinking about the last two minutes on this platform, how frustrated did
    you feel?" (the duration follows the configured lookback) with
    1 Not frustrated at all · 2 Slightly frustrated · 3 Moderately frustrated
    · 4 Very frustrated · 5 Extremely frustrated;
  - "What contributed to that feeling? (optional, choose any)": The interface
    was hard to use · A technical delay or problem · The learning content was
    difficult · Something else · I'm not sure. No free text;
  - Submit and Skip.
- **Honest acknowledgement.** A stored response (a rating with its causes,
  or a Skip) is final: the server never replaces it. A second response is
  compared with the stored one — the same choice (same rating and same set of
  causes, or a second Skip) is `already`; any other choice is `different`
  and changes nothing. The card says "Thank you." only when the server
  answers that **this exact choice** is stored (`recorded`, or `already` for
  a retry whose first answer was lost); a confirmed Skip simply closes the
  card. When an attempt's outcome is unknown (the request failed, or its
  answer never came back) the card keeps the choice it sent **frozen**: the
  inputs are disabled, it shows "We could not confirm that your response was
  saved. Please try again.", "Try again" resends exactly that choice and
  "Close" closes the card without sending anything or claiming anything. If
  the server reports `different`, the card says "Your earlier response to
  this question was already saved. It has not been changed." An answer after
  the response window shows "This question has expired. Nothing was
  recorded."; a prompt that is no longer available shows "This question is no
  longer available." Declining has no academic effect.
- **Skip.** No grade or access consequence, and no further prompt in that
  session after a dismissal.
- **Stored.** Sampling reason, offer, deferrals, display moment and local day,
  the window bounds, the observed seconds in it, response moment, dismissal,
  late-response flag and the configuration version. Delay and nonresponse are
  derived from those moments.
- **Feature window.** Events after the display moment, including the
  question's own interaction, are outside the window. One rating labels only
  its own window.

## 7. Researcher workspace

- Shared entry: `GET|POST /auth/login` authenticates all active account
  roles with the same email/password form. The role stored in the database
  determines the workspace and permissions; there is no Researcher choice,
  link or label in the shared form or ordinary portals. A Researcher's safe
  `next` target must remain within the research workspace. Ordinary roles
  retain their existing safe same-origin return targets; each destination
  separately enforces its role authorization.
- Compatibility entry: an anonymous `GET /research/login` redirects to
  `/auth/login` while preserving a safe `next` target. An authenticated
  request invokes the shared handler and redirects to the account's own
  workspace, retaining its signed-in session. `POST /research/login`
  delegates to the shared handler and its `account-login` rate-limit scope;
  it has no separate form, authentication implementation or login budget.
- `POST /research/logout` remains restricted to active Researchers and
  redirects to `/auth/login` after signing out. The research workspace
  remains separate and refuses access by every other role.
- Every research page: active Researcher role, CSRF on writes, safe
  same-workspace redirects, rate-limited login, generic authentication errors,
  `Cache-Control: private, no-store` and `Vary: Cookie`.
- Pages: dashboard (status, readiness, population counts, delivery quality,
  prompt and label coverage, device and page coverage), configurations
  (draft, edit, activate, pause, resume — no scope setting), sessions
  (pseudonymous list and event timeline), exclusions (excluded subjects by
  code, basis and date), exports (create, list, download), audit log.
- No identity: research pages and exports never read `users` names, emails,
  ids or the subject mapping.
- **Authentication.** Approved Researcher email/password accounts use the
  shared login. The owners removed the second-factor requirement on
  2026-10-04 and unified the login page on 2026-10-05; existing rate limits,
  CSRF and server-side role checks continue to apply.

## 8. Exports

A ZIP of CSV files: `sessions.csv`, `events.csv`, `prompts.csv`,
`data_dictionary.csv` and `manifest.json` (schema version, configuration
versions, period, timezone and filters, `cutoff_ms` and its time contract,
exclusions,
missing-label rule, label transformation "none — raw 1–5 preserved", row
counts and SHA-256 of each file). Only `study` provenance of non-demo
subjects is exported. The pseudonymous subject code is the grouping key for
participant-grouped evaluation. Values that begin with `=`, `+`, `-`, `@`,
tab or carriage return are neutralised against CSV formula injection.

**Immutable.** The ZIP is built once, when the export is created, and stored
in `research_export_archives` with its SHA-256 and size, together with the
timezone its period was read in. Every download of that export id serves
exactly those bytes after re-checking the digest; nothing is rebuilt. A
rating, an event or a session end after the export, or a change of
`APP_TIMEZONE`, cannot change what the id serves. A stored archive that fails
its digest is refused (409), never served. An export larger than 16 MiB is
refused with nothing written. Every served download is audited.

**Time contract.** Every file of an export is read inside **one read
transaction** whose snapshot is established by its first read (REPEATABLE
READ on MySQL/InnoDB; an explicit `BEGIN` on SQLite). `cutoff_ms` is read
from the server clock only **after** that snapshot exists, so:

- every row and value in the archive — including the mutable ones: session
  last activity, end and counters; prompt display, response, rating, causes,
  deferrals and late flag — is the committed state of one database snapshot,
  and everything in it was saved before `cutoff_ms`;
- a write still in progress at that moment is not included, even if its own
  server moment is earlier than `cutoff_ms`: the archive is the state saved
  by the cutoff, not a reconstruction of every moment up to it;
- derived prompt states are evaluated at `cutoff_ms`, and a display or a
  response counts only from its own stored moment;
- no included server moment (session start, last activity, end; event
  receipt; prompt offer, display, response) may be later than `cutoff_ms`.
  That can only happen when a server clock runs ahead of this one; the
  export is then **refused** with nothing stored — never clamped, filtered
  or relabelled — and the Researcher is asked to try again, when a later
  cutoff includes it.

The manifest states this contract (`time_contract`), and the archive row is
written afterwards in a separate transaction that re-proves the Researcher.

## 9. Retention

`RESEARCH_RETENTION_DAYS` is supplied by the deployment. A configuration
cannot be activated while it is unset, and the value in force is recorded in
the activated version. `scripts/research_operator.py purge-expired` reports
expired sessions (with their events and prompts) and every export archive
whose oldest data is older than the retention, and deletes them only with
`--execute`; an archive therefore never outlives the data it copied. The
export description stays, and its download then answers 410 Gone; an export
is never rebuilt. Running the purge against live data needs separate
authorization.

## 10. Migration and data-loss plan

- `69c4bae553fe` (parent `b86838ce23db`) — additive: creates the nine
  tables. For every legacy participant that `declined` or `withdrew`, it
  creates an `excluded` subject with basis `legacy_collection_exclusion` and a
  migration audit row. Legacy `invited` and `active` participants are **not**
  carried over: an old `active` row recorded an acceptance of one specific
  consent document and is not reinterpreted; such Students are collected by
  the population rule like everyone else. It deletes nothing.
- `d574ab56594f` (parent `69c4bae553fe`) — destructive: before any DDL it
  verifies that every legacy refusal/withdrawal is linked to a subject that is
  **still** `excluded` with basis `legacy_collection_exclusion` (a missing
  link, an included subject or any other basis refuses), and counts all six
  legacy tables. With legacy rows present it refuses unless the run passes
  `-x legacy_research_disposition=discard` **and**
  `-x legacy_research_reviewed_counts=<exact counts from the refusal>`; the
  message contains only table names and counts. Empty legacy tables are
  dropped by the ordinary upgrade. Its downgrade recreates the empty legacy
  schema; data is recoverable only from the pre-migration backup.
- **Online only.** Both directions of `69c4bae553fe` and the upgrade of
  `d574ab56594f` read rows first, so offline `--sql` rendering is refused
  with a clear error instead of emitting a script that skips those checks.
- MySQL DDL is not transactional, so the destructive revision does all
  checks before its first `DROP`, and the runbook requires a backup first.

Development MySQL was verified at `b86838ce23db` with all six legacy tables
empty. On 2026-10-04 the owners authorized close-out; a whole-database
backup was restored and checked on isolated MySQL before both revisions
were applied to development. Its current revision is `d574ab56594f`;
original LMS rows were preserved. See `docs/PHASE6_COMPLETION.md`.

## 11. Milestones and acceptance criteria

| # | Milestone | Acceptance |
|---|---|---|
| 1 | Contract and plan | This document, the overrides section and the superseding decision exist before code changes. |
| 2 | Removal, foundation, Researcher access, migrations | Old routes/imports/models absent from the running app; new models and both revisions tested (fresh install, upgrade from `b86838ce23db`, empty and populated legacy, refusal, reviewed disposition, changed exclusion state, offline refusal); shared login, compatibility entry and workspace role separation tested. |
| 3 | Sessions and population-rule ingestion | Automatic provisioning for existing and new Students, concurrency, exclusions on every path, and every rejection (roles, suspended, excluded, paused, ended, forged, replay, duplicate, oversized, invalid, late) tested. |
| 4 | Instrumentation and outcomes | Route classification and template coverage; collector on every Student page and absent for other roles; no research UI in ordinary portals; outcomes after commit; telemetry failure never breaks an LMS workflow; privacy tests. |
| 5 | Hybrid feedback | Budgets across tabs and day boundaries, deferral, dismissal, nonresponse, repeated, different and late answers, window linkage, frozen retries and honest acknowledgement tested. |
| 6 | Workspace, exports, audit | Real counts and empty states; exclusions page; immutable archives; snapshot time contract under a concurrent writer and a clock ahead; export manifest and dictionary; formula injection; audit; no identity in pages or exports. |
| 7 | Integration | Complete strict-warning suite, migration checks, real-browser checks, runbook, honest report. |

### Progress (source and tests)

| # | Status | Evidence |
|---|---|---|
| 1 | Done | This document; the overrides section in `_project_inputs/03_research_documents/CURRENT_PROJECT_OVERRIDES.md`; superseded markers and the decisions in `docs/DECISIONS.md` |
| 2 | Done in source | `tests/test_research_removal.py`, `test_research_schema.py`, `test_research_access.py`, `test_research_replacement_migration.py`, `test_research_operator.py` |
| 3 | Done in source | `tests/test_research_collection.py` |
| 4 | Done in source | `tests/test_research_outcomes.py`, `tests/test_research_collector_browser.py` (headless Chrome against a local server) |
| 5 | Done in source | `tests/test_research_feedback.py`, `tests/test_research_collector_browser.py` |
| 6 | Done in source | `tests/test_research_workspace.py`, `tests/test_research_export_snapshot.py` |
| 7 | Complete; local development deployed | Full strict-warning suite after shared-login correction (7,240 passed, 4 skipped), shared-login Chrome check and existing Researcher login on MySQL; earlier fresh/restored MySQL migrations, concurrency, retention and six collector Chrome scenarios. Exact evidence: `docs/PHASE6_COMPLETION.md` |

The deployment state and close-out verification are maintained in
`docs/PHASE6_COMPLETION.md`. The owners selected 15-day retention and
email/password Researcher authentication on 2026-10-04, then approved the
shared login with separate role destinations on 2026-10-05. Completing the
implementation and its synthetic checks does not claim real study data.

## 12. Inputs for later phases

- **Phase 7** reads exports: grouped by `subject_code`; labels only from
  answered prompts, each with its own window; the transformation must be
  chosen and recorded then (raw 1–5 is preserved here).
- **Phase 8** will need runtime windows computed from the same event
  dictionary and the model registry; nothing in this phase predicts.
- **Phase 9** can compare natural-use A/B periods by configuration version
  without reintroducing mandatory task sets.
