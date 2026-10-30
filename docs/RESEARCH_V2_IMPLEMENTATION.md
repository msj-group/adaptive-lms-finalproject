# Collection v2: local source handoff and acceptance

## Current source and fictional review - 2026-10-30

Local v2 schema is installed and verified. Oct 29 ordinary-workflow rehearsal
recorded 174 accepted events, one correctly excluded late answer and an exact
166-event grant snapshot, then was paused with its session closed. Operational
exports were created and downloaded; the post-answer export preserves the raw
answer without making it a training target. No Study data was collected.

Clock recovery, cross-page sampling cadence and equal-time ordering corrections
are implemented. Render validation now uses the full calibrated/causal interval
as specified in RESEARCH_V2_DATA_CONTRACT.md; raw estimates and old eligibility
are unchanged. The single Skip control is in a sticky header for phones.
An actual timely-answer attempt then exposed a query-triggered intermediate
flush, followed by the correct immutable-terminal refusal. Read-only inspection
confirmed rollback without a partial answer. The bounded correction now reads
frozen features and computes final quality before assigning the full terminal
transition in one flush; the guard and old rows are unchanged. A fresh natural
question then saved in 31,123 ms with timely=true/quality=eligible. All three
fictional sessions are closed and collection is paused. The final Oct 30
operational export contains one eligible target 0 while late/unanswered targets
remain blank; ZIP, manifest, membership cutoff and predictor-field checks pass.
Actual Android/iPhone acceptance remains separate from desktop viewport review.

Technical label-yield evidence is now separate private JSON with matured-rendered,
raw, timely, late, stored-eligible and excluded counts plus stored exclusion
reasons. Actual read-only review found three raw answers, two late, one eligible
and one unanswered prompt across separate releases. Each small group remains
insufficient evidence; SQL/Researcher report counters retain raw meanings.
The optional daily zero-eligible signal is prepared, not activated; its limits
are in RESEARCH_QUALITY_MONITOR_PLAN.md. No stored label was recomputed.

Hosting packaging and exact-head predeploy source are prepared; only the
predeploy's read-only schema helper has run on the verified local target.
Monitoring remains preparation only per owner decision. No automated tests,
actual purge, new migration, paid service, deployment or Git mutation. Current
evidence and stop point: RESEARCH_V2_LAUNCH_ACCEPTANCE.md. Earlier inactive or
pending-local-migration entries below are historical checkpoints.

## Verified local schema checkpoint - 2026-10-29

MySQL upgrade a6f2c9d84e71 and independent schema/preservation verification
passed; 11 new tables are empty and all original data is preserved. Temporary
rights were removed and original grants verified. Local readiness/diagnostics
now match the revision; diagnostic CLI passes. V2 remains disabled and Phase 6
phone/export/retention/pilot/monitor acceptance remains open. No hosting or real
collection activation. Current evidence: RESEARCH_V2_LOCAL_MIGRATION.md.
Earlier blocked/inactive/no-connection statements below are historical.

## Current local checkpoint - 2026-10-29

Owner approved exact local migration execution. The revision is now active
under migrations/versions/, but MySQL refused the attempt under the runtime
account before any schema/data change. The separate migrator is Listening-only.
DB remains 8e5f3b0d7124; all 75 table definitions and 21543 original rows match
the private backup. No grants/configuration/runtime/cloud activation occurred.
Review-only permission additions and exact resume gates are documented in
RESEARCH_V2_LOCAL_MIGRATION.md. Phase 6 remains open. The implementation-time
no-connection/no-application statements below are historical, not this checkpoint.

Date: 2026-10-28. The owner requested implementation and continuation.
Local source is prepared behind default-off RESEARCH_V2_ENABLED. Real
collection is not accepted yet. No database connection/application,
deployment, scheduler, Telegram message, paid activation, installation,
automated test or Git mutation was performed.

## Implemented source

| Area | Main files | Behavior |
| --- | --- | --- |
| Schema | app/models/research_v2.py; migrations/versions/a6f2c9d84e71_research_v2_collection.py | 11 empty tables installed and verified locally; immutable observations, release digest and exact prompt/event membership; collection remains disabled |
| Policy | research_v2_dictionary.py; research_v2_policy.py; research_v2_pages.py | Closed fields, fixed pilot settings, frozen Africa/Tripoli daily budget calendar, current Student page/context mapping |
| Backend | research_v2_collection.py; collector/routes_v2.py; collector/hooks.py | Actor/configuration/subject/session rechecks, CSRF, browser-bound scope, bounded acknowledgements, time sampling, cross-device budgets and separate render/answer states |
| Client | usage_collector_v2.js; _research_feedback_v2.html; research_v2.css | Touch/click actions, visibility/media intervals, bounded scoped sessionStorage retries, clock uncertainty and localized optional card |
| Workflow joins | quiz_autosave.js; speaking_recorder.js; admin_communication.js | Optional correlation around ordinary writers; transport completion never substitutes for a committed business outcome |
| Configuration | research_configuration_transactions.py; research forms/configurations | Gated v2 drafts, fixed reviewed policy, 45-day activation requirement; activation still does not start collection |
| Training inputs | research_v2_features.py; research_v2_exports.py; research_exports.py | Separate labels/features, raw 1–5 and predeclared 3–5 versus 1–2 target, missing/support/denominator flags, immutable ZIP/digests/expiry |
| Review | research/v2.py; research/v2.html | Locked/stale-protected participant group assignment, technical reports without ratings, quarantine of all unassigned/final-test review data |
| Retention | research_v2_storage.py; research_retention.py; research_storage.py | Per-version recorded duration, explicit FK deletion order, archive/report expiry and 45-day unresolved-receipt expiry; no purge executed |
| Cloud tooling | research_v2_quality.py; scripts/run_research_quality.py; deploy/railway-quality.toml | Bounded SELECT snapshot, separate aggregate publisher, protected reports, private deduplication state, optional Telegram and independent heartbeat; inactive |

Shared /auth/login and the separate Researcher workspace remain. Legacy v1
records/dashboards are separate. No audio, answer values, message body,
password, filename, coordinates, raw URL or DOM text enters v2 events.
Ordinary academic uploads retain their own workflows and operation tokens.

## Decision and label contract

Read committed event rows before grant. Define G after that snapshot and
persist exact event membership with the reservation. W=[G-120000,G).
Features require membership plus occurrence and receipt strictly before G;
a receipt timestamp alone cannot prove commit availability. Later arrivals
cannot improve this training example. Raw events remain immutable.

The question asks about the two minutes before it was sent. Browser render T,
receipt, uncertainty, locale and help-question exposure are separate metadata.
Unknown timing, uncertainty >5 seconds, T-G outside 0..5 seconds, insufficient
coverage, overflow or late answers exclude the primary target. Unknown render
time uses an explicitly marked receipt bound, never a claimed precise render.
Keep raw answers/reasons. Skip, nonresponse and unprompted time are not
negative labels. Help preference is separate from frustration severity.

Reservations consume burden even when rendering is unconfirmed: max 2 per
session, 3 per Tripoli day, 10-minute spacing across devices. Skip stops that
session. Replay cannot extend last activity or trigger another random draw.
No catch-up draws. Application-content SHA256 is immutable per session.
New-source observations refuse an old-release session: pause/close collection
before every deployment, and version any change to the collection method.

## Coverage and honest limits

- Phone/tablet/desktop are reported viewport sizes, not device fingerprints.
  No actual Android/iPhone browser review has been performed here.
- Request waits cover declared workflow writers, not every native navigation
  or background fetch. They are a lower bound for those requests, and missing
  without instrumented-request evidence. Unknown endings are censored at
  page exit/successor page or 120 seconds. HTTP status is not business success.
- Tabs contribute union time; ambiguous simultaneous contexts remain unknown.
  Reported rendering/support does not prove attention or emotion.
- Timed quizzes/listening, recording, uploads and blocking dialogs defer
  questions. Review the resulting context coverage in the pilot.
- Decision snapshots over 10000 session events are excluded, never silently
  truncated into valid examples. Exports cap at 500 sessions, 100000 events/
  opportunities/receipts, 5000 prompts, 500000 memberships and 16 MiB ZIP.
- Participants begin unassigned. Use a documented group plan before reviewing
  ratings. Normal exports quarantine unassigned and final-test events,
  features and ratings. Final-test release is a later frozen-model step.
- Adaptive detection/help and evaluation of benefit are later phases.
  This collection change does not guarantee future model accuracy.

## Cloud setup to review before activation

Use the separate inactive railway-quality.toml example, one hourly scheduled
instance and one private persistent RESEARCH_QUALITY_OPERATIONS_DIR. Process
ceiling is 120 seconds; SELECTs are bounded. The OS lease coordinates one
host/volume, not independent instances. Do not replace the web start command.

Source credentials use only RESEARCH_QUALITY_DATABASE_ with suffixes USER,
PASSWORD, HOST, PORT, NAME and optional SSL_CA. Grant SELECT only on the
research configurations/sessions and required v2 protocol/state/partition/
event/opportunity/prompt/membership/receipt tables. No users or account links,
DDL, business writes or LMS credential fallback. No dotenv loading.

For web reports, explicitly enable RESEARCH_QUALITY_REPORTS_ENABLED=1 and a
different RESEARCH_QUALITY_REPORT_DATABASE_ credential set with the same
suffixes and same destination. Grant SELECT/INSERT only on research_quality_runs
and research_quality_reports. No raw/partition/configuration updates, DELETE
or DDL. One transaction plus unique group/period keys makes publication
idempotent. Technical rows omit ratings and expire at most 45 days after the
report period began. The web page identifies reports older than two hours.
Daily development reports separately summarize raw ratings only for assigned
development participants, with a small-sample/rare-class review notice. Raw
counts include late/excluded answers and do not count as usable training labels.
Reserved/unassigned final-evaluation answers never enter these queries.
Current incident deduplication uses the private operations file, not a new
incident-table writer. Keep backup/log/download retention within approval.

Telegram remains off until RESEARCH_QUALITY_TELEGRAM_ENABLED=1. Supply private
RESEARCH_QUALITY_TELEGRAM_BOT_TOKEN and RESEARCH_QUALITY_TELEGRAM_CHAT_ID through
secret configuration, never chat/source. Only private user chats are accepted.
Optional RESEARCH_QUALITY_WORKSPACE_URL is the real HTTPS /research/v2 link.
Messages carry generic aggregate incident/recovery notices. Unchanged state
is quiet; failed sends retry. Confirm real alert and recovery delivery.

Optional RESEARCH_QUALITY_HEARTBEAT_URL is a private hc-ping.com endpoint.
Configure its independent missed-run destination/grace period separately.
Success is sent only after required query, publishing, files and notifications
succeed. Stopping the checker must produce an external missed-run alert.

Default checks include integrity and severe malformed delivery: at least 20
HTTP receipt attempts, >=95% invalid/oversized/stale-scope rejection while a
configuration says collecting. Paused/ineligible requests do not count as
malformed. Missing/small samples do not certify recovery. Unresolved receipts
have no actor/subject/content, expire in 45 days and cannot be study-attributed.

Optional RESEARCH_QUALITY_RATE_ALERTS_ENABLED=1 adds provisional pilot triggers:
invalid events >=5%/100 events; bad alignment >=20%/10 renders; low coverage
>=30%/10 renders; expired delivery >=50%/10 reservations; nonresponse
>=80%/20 matured questions. Require two distinct consecutive hourly cohorts
for an alert and two clean cohorts for recovery. These are engineering
investigation thresholds to review in the pilot, not validated science.

No independent eligible-traffic signal exists: empty research tables cannot
distinguish no users from a stopped browser collector. Silence is missing
evidence, not proof of health. Heartbeat detects checker failure, not every
ingestion failure. Daily review uses the previous Tripoli day, catching up
after 09:00. Technical monitoring never reads final-test ratings.

No AI API call is implemented or required. Cloud resources/optional API usage
are not included merely by having ChatGPT Plus. Obtain a current cost estimate
and approval before activation. No paid service was enabled.

## Acceptance sequence and stop point

1. Review inactive revision a6f2c9d84e71 after exact source parent 8e5f3b0d7124.
   Obtain separate authorization for the named local database, promotion/
   application and quiescent-writer recovery procedure. A different revision
   is a stop condition. No reset, seed, downgrade or reclassification.
2. Verify actual MySQL revision/backup readiness before DDL, apply only the
   approved revision, inspect schema/model parity. MySQL DDL is not collectively
   transactional. No real MySQL verification has been performed here.
3. Rehearse ordinary fictional workflows with development provenance, Android
   Chrome and iPhone Safari if available. Check slow/offline/resume, screen
   lock, background, tabs, account switch/logout, duplicate delivery, autosave,
   recording upload, messages and critical deferral. Never fabricate study data.
4. Verify exact membership/G/T, timely/late/Skip/no-response/exclusion flags,
   group quarantine, export contents/digests/download immutability, per-version
   expiry preview and retention FK order. No automated tests are implied.
5. Before hosted release, promote the reviewed migration and update the exact
   Railway predeploy head/parent and approved source baseline/manifest. The
   current Railway script/frozen baseline deliberately remain at the old head
   and reject this unapproved release. Do not deploy the intermediate tree.
6. After separate cloud/cost approval, configure quality and daily retention
   jobs, least-privilege credentials, private storage, Telegram/recovery and
   independent missed-run alarms. Examples do not prove an active schedule.
7. Run a small consented real pilot (suggested 3–5 adults) before the main
   collection. Check Arabic scale understanding, actual phone coverage,
   natural rating variation, aligned usable examples and burden. Review the
   first day and early days; do not wait a month. Do not force emotional
   labels to balance classes. Version any collection change.
8. Start main collection only after the documented pilot decision. Thirty
   participants alone does not guarantee informative labels: review usable
   examples, missingness, positive prevalence and participant/device coverage
   before Phase 7 training. Phase 6 remains open at this acceptance boundary.

## Verification boundary

Executed bounded source review, Python AST/import compilation without an app
factory/database, JavaScript syntax, Jinja syntax, JSON/TOML parsing and
in-memory MySQL upgrade DDL/model comparison. Current proposal prepares 11
tables, 16 FKs, 70 new-table CHECKs and 3 grouped ALTERs, no upgrade DML or
DROP TABLE. Active source remains 48 migrations, head 8e5f3b0d7124. Use
CR-at-EOL-aware git diff --check for this Windows tree; inspect untracked
research files separately. These are not automated application tests or
comprehensive regression acceptance. No real MySQL/browser/pilot/cloud
acceptance is claimed. Pre-existing Finance changes are preserved; new
research translations were appended to the shared locale file.
