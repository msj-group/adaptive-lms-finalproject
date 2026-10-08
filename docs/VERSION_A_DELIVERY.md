# Version A delivery authority and evidence

Authority: final consolidated owner approval, 2026-10-08. This document records
the active implementation scope. Earlier preview stop gates and the rejected
W1 appearance are superseded. Historical acceptance results remain historical.

## Approved contract

- Direction D (Branded Living Learning Experience) and D-MOTION are canonical.
- Login is a protected visual surface. Preserve its current computed appearance,
  composition, artwork, type and red action. Isolate its styles; honor reduced
  motion and hidden-page suspension. Remove the artificial Opening delay and
  fake progress without changing authentication or redirect authorization.
- Messages evolve the current feature, including separate routes, existing
  desktop split layout, reply/edit/hide/clear, subject behavior, emoji, search
  meanings, ordering, ownership, CSRF and signed revisions. No new messaging
  capability or research event is authorized.
- Complete meaningful Student, Teacher, Administrator, Researcher and shared
  surfaces, with dedicated mobile compositions, deliberate dark mode and RTL.
- Preserve Flask modular monolith, Jinja, WTForms, SQLAlchemy and MySQL.
  Version A = Core LMS + shared Research Instrumentation. Future Version B adds
  an isolated adaptive intervention layer to the same application. No B runtime.
- Teacher chooses TEXT or SINGLE FILE. Preserve text assignments. One final
  private file, server validation, episode/ownership binding, existing deadline
  and grading/feedback rules. No drafts, resubmission, multiple files or new
  late-submission policy. Reuse the established private-file policy where suitable.
- Development data is disposable; justified development migrations, resets and
  reseeding are authorized. Once production contains real records, use safe
  forward migrations and backups. Never ship development/pilot records or secrets.
- W7 preserves current event types and approved natural-use methodology. Cover
  Courses/Course and minimal Account; fix identities/mappings, Quiz ordering,
  autosave interpretation, final repeated-click burst, survey focus and delivery.
  Search position is the GLOBAL ORDINAL across the rendered result list; ranking
  and grouping stay unchanged. Never collect passwords, answers, raw field
  values, message/subject/recipient content, filenames or file/photo content.
- Sampling, prompt windows, rating 1–5, session meaning, study provenance,
  exclusions and daily 15-day retention retain their approved semantics.
- W8 includes actual acceptance, separate Development Pilot, delivery and
  export verification. Freeze only after critical gates pass; record source
  hash, migration state, UI/design-system/event-dictionary versions, tracking
  scope, configuration, provenance, evidence and nonblocking limitations.
- W9 delivers a hosting-neutral release, production configuration, migrations,
  bootstrap, private storage, startup, HTTPS/proxy assumptions, logging, health,
  scheduling, backup/restore, centre handover and research operations.
- Real deployment and production smoke evidence must be distinguished from
  local readiness. Real study collection requires production Study configuration.
- No new automated test package: the owner's existing verification boundary
  remains. Use bounded browser/manual workflows, source checks and real MySQL
  migration checks; never describe them as a comprehensive regression suite.
- No routine wave approval stops. Stop only an affected portion for a genuinely
  new research/domain/security/adaptive/dependency/production-destructive decision.

## Ownership

| Layer | Authority |
| --- | --- |
| tokens.css | D light/dark semantic roles, brand, type, spacing and fixed motion |
| base.css | Native shared controls, form states, status, containment and focus |
| navigation.css | Integrated edge navigation, app header, account, mobile More/bottom navigation, context/tabs/records |
| workspace.css | Shared composition; no independent palette |
| login.css | Protected Login compatibility roles and visual ownership; no D redesign |
| Feature CSS | Feature internals and purposeful mobile transformations |
| appearance.css | Preferences, utility panels, RTL and print; temporary compatibility is removed as its owner migrates |
| Presentation JS | Deterministic UI only; no domain writes or adaptive triggers |
| Services/transactions | Existing domain invariants and server-authoritative writes |
| Research subsystem | Existing dictionary, allowlists, sampling and delivery |
| ml/ | Reserved only; no inference/intervention runtime |

## Sequence and current evidence

| Wave | Scope | Status / evidence |
| --- | --- | --- |
| W0 | Authority, boundaries and delivery ledger | Source/docs inspected; contract recorded |
| W1 | D tokens/components/shell/mobile/themes/RTL/motion; Login isolation | Implemented; cross-role native keyboard/theme/RTL/reduced-motion and sampled contrast verified; Login original preserved |
| W2 | Student critical journeys and Login preservation | Implemented; native lesson completion, assignment and Quiz final-result/retry verified; 30 critical renders |
| W2-D02 | Text/single-file assignment and migrations | Implemented; actual MySQL head/no delta; own/assigned download, type freeze, 13 negative checks, real deadline and no-row/byte-leak checks pass |
| W3 | Remaining Student, evolved Messages, shared utilities | Implemented; 48 renders/19 communication observations; media, Account/photo/password/session invalidation and mobile sheet verification pass |
| W4 | Teacher operations/authoring/reviews/visualizations | Implemented; 105 GET destinations × three widths; native file feedback, draft/released/corrected grade, attendance finalize, Quiz/Listening authoring verified |
| W5 | Administrator operations and justified analytics | Implemented; 105 GET destinations × three widths; native cash/correction/history verified; no financial/business policy change |
| W6 | Bounded Researcher analytics with sources/denominators/missing states | Implemented; 42 Light/Dark renders/range guards plus exact nonempty v4 scope (86 events); no invented ML/A-B/causal results |
| W7 | Approved instrumentation repair and dictionary | Implemented; va-w7-r1/va-scope-r1, existing wire/CSV schema; all Student routes classified; stored global ordinal/final burst/Survey/Account evidence and current export pass |
| W8 | Actual acceptance, Development Pilot, delivery/export and justified freeze | Local acceptance recorded; separate non-Study Pilot; YC-VA-D1-20261008 source/UI freeze with explicit coverage limitations |
| W9 | Production artifact, operations/handover and readiness | Neutral data-free release prepared/verified; runbooks/production environment/entrypoint/proxy/retention/bootstrap/health/backup/restore prepared. Actual hosting/smoke/Study remain external gates |

The starting working tree already contains extensive uncommitted repair/UI work
and nine indexed paths missing from disk. Those changes are preserved. Git HEAD
alone is not an attribution baseline for this delivery. No commit or push is
assumed. The local source/UI freeze is recorded in VERSION_A_BASELINE_FREEZE.md;
executed evidence is in VERSION_A_ACCEPTANCE.md and final fourteen statuses in
VERSION_A_READINESS.md. No production deployment or Study collection is claimed.
