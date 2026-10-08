# Version A acceptance evidence — 2026-10-08

Authority: final consolidated owner implementation approval, followed by the
instruction to continue. Scope: Direction D + D-MOTION, protected Login,
evolved Messages, teacher-configured Text/Single File, current natural-use
instrumentation and hosting-neutral release. Development input is fictional.
Nothing below constitutes actual-host or Study acceptance.

## Verification method

Evidence includes source/AST/Jinja inspection, bounded scripted native browser
workflows in isolated Edge contexts, actual MySQL/Alembic operations, archive
inspection and byte/dump restore. Browser drivers and screenshots are external
review artifacts; they are not shipped or a recreated test package. No pytest,
comprehensive regression suite or background test supervisor was run.

The evidence directory on the review machine is:
`C:/Users/abdul/.codex/visualizations/2026/10/07/01a11772-b277-7c42-b248-f13e62d51e53/`.
Filenames below identify retained evidence; a copied production release is
independent of this directory. Its local paths are not hosting requirements.

## Presentation and access

| Area | Executed evidence | Result / boundary |
|---|---|---|
| Shared foundation | Four roles at 1440/390/320, native menu/keyboard round (`version-a-foundation-browser.json`) | PASS in sampled states; D integrated navigation, Student bottom navigation/More, protected Login |
| Student critical journeys | 30 actual renders (`version-a-learning-browser.json`); native lesson completion, Text/File final submission and private review | PASS; no sampled overflow or JS errors |
| Student communication | 48 actual renders; 19 workflow observations (`version-a-communication-browser.json`) | PASS; send/reply/edit/hide/clear, retained other-party history, search/no-results, mobile action sheet/Escape/focus |
| Teacher/Admin | 105 native GET routes per role × three widths = 630 renders (`version-a-operations-browser.json`) | PASS in bounded sample; no overflow/JS errors. Crawl was bounded, not every discovered destination |
| Researcher | Seven pages × two themes × three widths = 42 renders; invalid dates/version/order rejected, native GET filter | PASS (`version-a-research-browser.json`); read-only bounded analytics |
| Current cross-role acceptance | 193 renders: Light/Dark/System, RTL, 320/390/1440 and equivalent zoom reflow (`version-a-accessibility-browser.json`) | PASS for sampled layout/label/landmark/duplicate-ID checks; expected Admin Messages denial retained |
| Current Teacher/Researcher | Six final viewport references; Researcher native `version=4` scope displays 86 retained events (`version-a-final-reference-screens.json`) | PASS; narrow date fields use full width below 361px |

The current Login retains composition/artwork/Segoe UI/red action in nine
Light/Dark/System size references, plus a native invalid-credentials state.
Opening uses a ready redirect without fake wait/progress. Hidden/reduced-motion
timers are suspended. Login is not the D Student shell. Messages retains native
separate routes/forms, emoji/subject/recipient selection, ownership and ordering;
mobile sheets move existing action nodes rather than duplicate forms/tokens.

Sixteen cross-role keyboard checks passed: skip link/main, Help popover Escape
with focus return, mobile navigation Escape with focus return. Visible control
labels, one main landmark and no duplicate IDs were verified in the acceptance
sample. Sampled reduced-motion states had no running animation. Contrast review
covered 14 Student/Admin Light/Dark pages and 1,536 text segments with opaque
solid foreground/background: no failures under the applicable 3:1/4.5:1 checks
(`version-a-contrast-browser.json`). Gradients, artwork, alpha compositing and SVG
were outside that calculation and visually reviewed separately.

Zoom coverage is equivalent 200%/400% reflow (1440 physical width represented
by 720/360 CSS viewports), not actual browser zoom shortcuts. Native screen-reader
use, Safari/Firefox/iOS, every state and physical microphone recording were not
exercised. This is bounded accessibility evidence, not WCAG certification.
No known critical accessibility blocker remained in the inspected states.

## Native writes and file integrity

| Workflow | Actual checks | Evidence |
|---|---|---|
| Teacher review / Student receipt | Single-file feedback saved and visible only in the own receipt | `version-a-operational-writes.json` |
| Gradebook | Create existing-category item, draft 8.50, separate release, released correction 9.25, own Student visibility | Same; no grading policy change |
| Attendance | Real scheduled occurrence, signed roster, draft save, separate finalize, later Teacher editing unavailable | Same; no new attendance rule |
| Finance | Native cash 12.50 and correction 11.25, existing history retained | Same; no financial arithmetic/policy change |
| Quiz | Teacher draft/question/settings/publish; deliberate browser-local one-fetch 503, visible retry, actual server acknowledgement, final result | `version-a-media-browser.json` |
| Listening | Native Teacher authoring/publish, private WAV served, answer saved and final result | Same |
| Speaking | Existing unavailable/denied microphone fallback; valid fictional WAV submitted with final receipt | Same; no physical microphone check |
| Account | Invalid photo readable; sanitized own photo private/no-store; correct password changes and invalidates prior session | `version-a-final-shared-browser.json` |
| Incorrect current password | Native 409 with visible existing error | `version-a-password-rejection.json` |

Operational writes had nine passing observations; the media round had ten.
Speaking review exposed a real UI defect: fallback submit was disabled by the
state refresh, and permission failure kept fallback hidden. The corrected client
allows the existing file path, preserves retry/confirmation/server validation,
and its final submission passed. No new recording workflow or research type.

File-specific negative acceptance passed 13 checks
(`version-a-file-guards-browser.json`): missing/multiple/unsupported/MIME-mismatched/
oversized file, CSRF failure, stale signed Teacher context, duplicate finality,
unenrolled Student and unassigned Teacher task/download denial, and Admin denial.
Signature mismatch rejection and own Student/assigned Teacher 200/anonymous
redirect were separately exercised in the learning round. Downloads are private,
attachment/no-store/nosniff/Vary Cookie. Text and File freeze task type after a
submission. Existing episode/deadline/finality/feedback behavior remains.

Before/after negative-round counts were identical (27 file metadata rows,
9 submissions, 37 private files). A separate native form was captured before
its unchanged deadline and posted after actual server time passed; the server
refused it with counts still 31/9/41. Evidence:
`version-a-file-cleanup-verification.json`, `version-a-file-deadline-browser.json`
and `version-a-final-evidence.json`. No filesystem failure/crash was injected;
cleanup-failure logging/reconciliation is source-reviewed. Existing format checks
are bounded signature/container checks, not malware scanning or full document
grammar validation. Production storage/network operations require host acceptance.

Initial driver expectation errors are retained rather than disguised as product
failures: a password validation POST renders at `/account/password` with 409,
not GET `/account`; an early listener used the wrong collector URL; some selected
seed activities were closed/finalized; CSS zoom did not model media-query reflow.
Corrected native checks/evidence above supersede those incomplete records.

## Database and cold recovery

Actual Development MySQL migrated to `7d4e2a9c6013`; final Alembic check reports
no new operations. Teacher type has a closed-set CHECK. Submission has an XOR
text/file CHECK, unique assignment/enrollment finality, private-file relationship
and Student/enrollment episode ownership defenses. File downgrade refuses when
file tasks/submissions exist; forward production upgrades remain required.

An owned isolated MySQL server, independent empty schemas and production factory
settings (secure cookies/CSRF/debug off/development provenance/15 days) executed
the complete migration path: 75 tables, zero users/groups/enrollments/research/
uploaded-file/assignment/submission rows. MySQL dump restored to a second owned
empty schema with the same head and 75 tables. Private byte archive restored
with an equal hash. Server stopped afterwards. This is real MySQL evidence, not
`create_all`, SQLite or a hosting claim (`version-a-database-rehearsal.json`).
Private-file restoration used a byte fixture; authorized LMS download was a
separate live Development check, not a single hosted disaster-recovery exercise.

## W7 delivery, separate Pilot and export

The wire event types/schema, sampling/window/rating meanings and retention
remain. Generator dictionary `va-w7-r1`; scope `va-scope-r1`; CSV layout
`natural-use-csv.v2`. Every Student route in the frozen source is deliberately
classified; technical downloads/fragments/utility redirects are excluded.
Minimal Account observes attempts/validation/page/navigation, never field values.
Search preserves ranking/grouping and stores rendered global ordinal 1..500
(above 500 absent). Quiz capture/resumed-submit ordering and automatic-save
meaning are documented in `VERSION_A_RESEARCH_CONTRACT.md`.

Separate configuration v4 copied existing policy exactly: inactivity 30 min,
max one prompt/session and two/day, lookback/minimum observed 120 s, random 50‰,
activity-end 500‰, offer TTL/response window 600 s. Local period 2026-10-08
00:00–2026-10-09 23:59 Africa/Tripoli. No forced sampling. Ordinary visible use
produced the optional non-modal Survey: neutral heading focus, five choices,
none preselected; fictional rating 3 confirmed by the server and focus restored
outside the closed card (`version-a-survey-delivery-browser.json`).

The final Pilot archive contains one pseudonymous subject, two sessions,
86 stored events and one answered prompt. Global ordinal 32 and final three-click
Help burst are stored. Account has no input-value event; inspected exports have
no private content/filename/email markers. Internal hashes/counts, unique event
IDs, immutable archive, wire/CSV compatibility and 120-second lookback passed
(`version-a-pilot-export-verification.json`). Historical fictional subject
provenance remains `demo`, never relabelled: the deployment is Development and
the dataset is `operational_review`, not Study. Later current-source export
passed again after bounded export queries/dictionary clarification, with the
same configuration/data and collection paused (`version-a-final-evidence.json`).

Pilot archive SHA-256:
`60729e31614f3ead2a9133d1d1fa929617e45eefb5049f07ab8be99859cf698c`.
Pilot/export data are external and excluded from the production package.

Browser responses include idempotent replay duplicates. Explicit replay-only
redelivery does not increment session counters; explicit replay duplicates are
excluded from the duplicate counter. Recorded duplicate count zero does not
mean no transport replay. Browser loss remains partly unobservable. Failed
automatic save attempts without confirmed outcome are not a new event type.
The only Pilot prompt was answered: a missing-rating assertion in this archive
is vacuous. Missing-state/label-window rules are source-reviewed; this Pilot does
not empirically cover every missing-response lifecycle or expiry boundary.

Daily retention entrypoint executed once successfully in Development with the
approved 15 days and zero expired rows; no deletion occurred
(`version-a-retention-rehearsal.json`). Daily scheduling and actual expired-row
deletion on the selected host remain operational gates. No machine scheduler
was installed. No real collection was started.

## Static close-out and limits

Current source: 355 Python AST parses, 200 Jinja compilations, zero syntax errors
and zero unclassified Student routes. All 16 application JS files passed Node
syntax inspection. Final Git whitespace check passed with CRLF recognized;
the earlier non-CRLF-aware invocation flagged existing Windows line endings.
The extensive starting dirty tree/indexed deletions are preserved. No stage,
commit, push, dependency install or unrelated automatic CSS rewrite.

Local evidence supports the documented source/UI freeze without a known critical
defect in the reviewed scope. Coverage limitations above remain explicit.
Actual Linux runtime build, hosting/TLS/proxy/private storage, scheduled retention,
host backups/restore, production smoke and approved participant/consent handling
remain outside local acceptance. See `VERSION_A_READINESS.md` for final status.
