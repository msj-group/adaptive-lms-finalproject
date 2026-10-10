# A1 actual-application acceptance — 2026-10-10

Overall assessment: **PARTIAL**. The current main-checkout source was exercised
through ordinary four-role authentication against an explicitly owner-authorized,
disposable local MySQL instance. This replaces the earlier fixture-only evidence
for the workflows listed below; it is not comprehensive regression acceptance,
production readiness or a new baseline freeze.

## Verified source and environment

- Branch: `ui/platform-unification-20261009`.
- HEAD: `d9c7e1bef63800d8500d4bdd865fe4c93a0fcbff`.
- The previous 119-template, nine-CSS-file and Arabic catalog implementation was
  present. Initial app hashes matched that implementation's evidence.
- Real Flask factory, `DevelopmentConfig`, `TESTING=False`, CSRF enabled,
  existing CPython 3.14.6 / Flask 3.1.3 / SQLAlchemy 2.0.51 / PyMySQL runtime.
- Existing MySQL 8.0.46, unique ignored `instance/a1_qa_*` directory, loopback
  port 2493, owned synthetic schema and newly generated private credentials.
  Connection guards verified schema, port, datadir and server UUID. The tracked
  migration chain reached `7d4e2a9c6013` only on that disposable target.
- Actual app served on `127.0.0.1:5006`. No existing local or Railway database
  was accessed by these QA connections. No shared test password was reused.
- `RESEARCH_DATA_PROVENANCE=development`, `RAILWAY_ALLOW_STUDY=0`.

## Bounded functional results

Ordinary Login succeeded for Student, Teacher, Administrator and Researcher.
Real CSRF-protected forms verified text/file assignment submission and PDF
download integrity, Quiz Start/autosave/reload, Listening manual Save/reload,
speaking upload fallback, Student search, Teacher lesson/quiz/question drafts,
submission feedback, attendance drafts and gradebook drafts. Blank forms returned
real server validation. Admin enrollment impact was previewed; list, scheduling,
financial and membership views were read. Research configuration, sessions,
audit, storage and an authenticated operational-review export were exercised.

The isolated Development collection rehearsal generated one session and eight
events (three page views, three page leaves, two heartbeats), then ended
**Active Paused**. The immutable Development export contains one subject, one
session, eight events and no feedback prompts. Its manifest excludes Study and
identity/content columns. Control-click/form event delivery was inspected in
source, but not exercised during the collecting interval.

No attendance was finalized, no grade item released and no invoice/payment or
financial transaction created. Financial populated-record and confirmation paths
therefore remain unverified. Physical microphone recording is unverified.

## Incremental corrections

Eight application files changed in A1, without route, Python, JavaScript,
business-rule, model, migration or Event Dictionary changes:

- Shared product shell: remove generic non-Research footer slogans.
- Student assignment/speaking receipts: remove duplicated empty-feedback prose.
- Research configuration template/CSS: remove duplicate unit explanations and
  spacing, retain all policies/bounds/errors/disclosures, group fields using the
  existing responsive container and density tokens.
- Foundation CSS: darken the non-Messages primary-gradient end after measured
  small-text contrast near the last Assignment button letters was about 4.44:1;
  remeasurement was above 5.33:1. Protected Messages uses the original fallback;
  Login does not load this owner.
- Previously unmodified Teacher units list: remove duplicated Course context
  and empty-state guidance; preserve Level, term dates and archive warnings.
- Research dashboard/CSS: keep Coverage and Event types as compact native mobile
  tables rather than repeated record cards; preserve all values and headings.

Matched live measurements at 390px: configuration action 2164 → 1768px in the
same no-active-baseline state; Teacher Add Unit 679 → 607px; populated Research
dashboard height 10700 → 9400px. Assignment, quiz-authoring and enrollment action
positions were retained where authored content, required fields or financial
review justified the height. No action-position threshold or required-content
concealment was introduced.

## Responsive and accessibility evidence

The core directed browser review covers 12 page families × 10 CSS widths
(320, 360, 390, 430, 540, 768, 900, 1024, 1200, 1440) × English/Arabic and
Light/Dark: 480 actual authenticated captures. Later focused captures reverify
affected owners, fresh narrow-page loads, empty/long/validation states, density,
system appearance and intermediate configuration widths. Final evidence counts
are in `evidence-summary.json`.

More passed Escape, outside dismissal and focus return for four roles at
390/768/1440; backdrop `tabindex=-1`, Tab/Shift+Tab and role destinations were
checked. Native confirmation and linked validation focus were inspected. System
appearance, Comfortable/Compact and reduced motion were verified. Directed
coarse-pointer emulation and browser accessibility-tree inspection were performed.

Native Chrome zoom shortcuts did not change viewport/DPR in headless mode.
720/360px reflow equivalents were captured, but actual 200%/400% browser zoom is
**not verified**. Physical screen-reader use, device safe areas, real touch
hardware and microphone capture remain open. This is not a WCAG certification.

Protected Login/Messages have 24 live before/after pairs: 19 pixel-identical,
five with small raster differences and no observed layout difference. Public
Login is explicitly unauthenticated. Messages has only the empty state in this
disposable dataset; populated conversations are not claimed.

## Static and research boundaries

215 Jinja templates parse, 302 Python source files parse, Arabic JSON is valid,
and A1-edited files have no actual trailing spaces. Default `git diff --check`
flags CR line endings; the per-command Windows CRLF-aware whitespace check
passes without saving Git configuration. The Event Dictionary and collector
hashes are unchanged; research attribute multisets match HEAD across template
diffs. IDs alone do not establish exposure/timing equivalence. Student page
geometry and reading changes require separate post-change baseline acceptance
before any Study. There is no declared scroll-depth/exposure event to compare.

Per `AGENTS.md`, no replacement automated test package or pytest suite was
created/executed. Directed browser/static checks are not a comprehensive suite.
Two malformed reviewer URLs and an expected rich-text/file-route 404 are recorded
as review-protocol negatives, not application regressions; cross-role requests
correctly returned 403. No uncaught page JavaScript error was observed.

## Evidence and next release stage

The Arabic 17-section report, labelled offline gallery, source-only A1 patch,
measurements, screenshots, role/workflow records, hashes and export inspection
are under:

`C:\Users\abdul\.codex\visualizations\2026\10\10\01a12430-b1a2-7e62-a185-8944e4c04d3c\a1-acceptance`

The disposable instance is preserved, not deleted; QA processes are shut down
at close-out with evidence in `shutdown.json`. Secrets, datadir, private uploads
and runtime logs are excluded from the evidence archive.

Before a separately authorized Git/Railway release: review both prior density
and A1 patches, complete native zoom/device/accessibility and populated protected
Messages checks, resolve unverified financial/academic paths on a disposable
dataset, approve a new research baseline, and validate the exact deployed build
with safe host smoke checks. No commit, push, deployment, production artifact
refresh, Study activation, Version B or Baseline Freeze occurred in A1.
