# Approved repair contract

Approved by the owners in the final-discovery conversation; implementation
authorized on 2026-10-05. This is the canonical target for rehabilitation of
the existing application. A target in this document is not a claim that it
has already been implemented or verified.

## Authority and current checkpoint

- Owner release request, 2026-10-18: commit, push and deploy all reviewed recent
  application/documentation changes from checkpoint `491ef52`. This explicitly
  advances the preceding local-only changes to release. Use one allowlisted
  commit, ordinary fast-forward pushes to the two existing release branches,
  and the existing Railway web service pinned to the verified GitHub commit.
  Preserve unrelated unused Help/design-preview files, secrets, local data,
  research classifications and all hosting services/settings except the web
  source pin. No new migration, reset, seed, scheduler, dependency installation
  or automated tests are authorized. Existing Railway predeploy must verify
  the already deployed `8e5f3b0d7124` head and model/schema agreement.

- Owner Teacher Review queue follow-up, 2026-10-17: implement the approved
  desktop list/editor and phone list/standalone-editor composition. Keep ordinary
  assigned-Group/type/comment/date filters, oldest-first bounded paging and the
  existing shared feedback workflow. Source/localization/documentation and bounded
  static/memory-only manual browser review are authorized. Fictional examples stay
  in ignored presentation evidence; the application reads its actual scoped data.
  Preserve CSRF, signed stale tokens, authorization, locking and lifecycle/research
  meanings. No automated tests, actual database/seed/migration, scheduler, Git
  mutation, installation or deployment is included.

- Owner Teacher Activities image follow-up, 2026-10-17: apply the proposed
  Teacher desk composition, with readable activity rows beside a simple
  Group/task creation panel. Adapt it for phones: a compact native creation
  disclosure above activity cards and one-column choices. Keep closed Student-
  style Filters, English/Arabic, semantic themes and existing authoring/security
  contracts. This replaces the preceding full-width creation presentation only.
  Source/localization/documentation and bounded static/memory-only browser review;
  no automated tests, actual database/migration, scheduler, Git mutation or deploy.

- Owner Teacher Attendance follow-up, 2026-10-17: reuse the Student Attendance
  visual treatment where compatible with Teacher functions. Keep the original
  native recording, draft saving, bulk/undo, private notes and separate confirmed
  finalization flows. Teacher-owned templates/CSS may show percentages from
  already supplied bounded counts: finalized marks on the current list page,
  and the saved roster of one detail page. Explicitly distinguish marks from
  classes/unique Students and exclude drafts from the list ring. This supersedes
  the earlier staff counts-only presentation restriction for these Teacher views.
  Preserve routes/queries/transactions, Student UI and all unrelated local work.
  Source/localization/documentation and bounded static/memory-only manual browser
  review only; no automated tests, actual database/migration, scheduler, Git
  mutation or deployment.

- Owner Teacher Dashboard selection, 2026-10-17: implement Group Cockpit on
  desktop and Groups & Feedback on phones, using the existing authorized Group,
  next-class, feedback and Announcement payloads. Use page-owned presentation
  assets and accessible phone tabs, with both sections available without script.
  Retain the shared identity/navigation, Arabic/RTL, semantic themes, bounded
  previews and existing lifecycle meanings. Source/localization/documentation
  and bounded static/memory-only manual review only; no automated tests,
  actual database/migration, scheduler, Git mutation or deployment.

- Owner Teacher Activities follow-up, 2026-10-17: redesign the Teacher activity
  page and reuse the closed Student Grades/Attendance/My records filter pattern.
  Make activity creation understandable to Teachers with limited technical
  familiarity: one group/type entry, short explanations, clearly separated task
  details/dates, optional listening controls and explicit draft saving. Preserve
  ordinary type-specific authoring/publication, active-assignment authorization,
  archived reading, CSRF/stale/locking contracts, public IDs and all local work.
  Source/localization/documentation and bounded static/memory-only manual browser
  review; no automated tests, actual database/migration, scheduler, Git mutation
  or deployment.

- Owner photo-crop follow-up, continued 2026-10-17: let the account owner preview,
  reposition and zoom a newly selected photo to choose the visible region before
  saving. Require explicit Use photo confirmation in the ordinary scripted flow,
  support adjustment/cancellation and retain the earlier avatar menu/hidden Save.
  This supersedes automatic centre selection for that flow. Upload the original
  image with bounded crop coordinates and validate/apply the square after server
  orientation; retain original-file validation, private history and account guards.
  Source/documentation, static and bounded memory-only manual review only; no
  automated tests, actual database/migration, scheduler, Git mutation or deployment.

- Owner Teacher My groups follow-up, 2026-10-16, continued 2026-10-17:
  implement the selected Expandable list desktop/phone concept with native
  group disclosures, dates/schedules and existing workspace/Attendance/
  Gradebook/Activities links. Add name search and closed Course/Term/Context/
  Term timing GET filters, within the same active Teacher-assignment scope.
  Ended terms and archived academic contexts stay visible while assignment
  is active; removed assignments do not become a teaching-history archive.
  Preserve shared identity, themes, localization, research meanings and all
  other local work. Source/documentation and bounded static/memory-only manual
  review only; no automated tests, actual database/migration, scheduler, Git
  mutation or deployment.

- Owner Account settings follow-up, 2026-10-16: remove the separate Choose a
  photo upload control and default photo requirements paragraph. Open View,
  Edit and Delete photo from the enlarged circular avatar. Show Save photo
  only after selecting a valid replacement, retaining space for it in the card.
  Show only the relevant validation error in a localized popup. Photo removal
  clears the current revision projection while preserving private history/files.
  Retain native POST/CSRF, own-account authorization, signed stale snapshots,
  canonical locks and research classifications. Source/documentation and bounded
  static/memory-only manual review; no automated tests, actual database/migration,
  scheduler, Git mutation or deployment.

- Owner Dashboard follow-up, 2026-10-16: implement the selected Agenda image
  proposal for Student Dashboard on desktop and phones. Show a personalized
  greeting, a compact next-class strip, the two nearest existing upcoming
  activities and the actual continue-learning lesson/progress card. Retain the
  shared platform identity/navigation, semantic themes, localization, native
  links, authorized data and existing empty/completed/truncated states. Omit the
  dashboard announcement preview as in the selected essential-information design;
  Announcements remains in ordinary navigation. Source/presentation,
  localization, documentation and bounded static/memory-only manual review only;
  no automated tests, actual database/migration, scheduler, Git mutation or deploy.

- Owner navigation follow-up, 2026-10-16: remove desktop sidebar More and the
  sidebar avatar/name card. Replace Account settings, Help and Display in phone
  More with Language and Dark mode. Add those two preferences to the upper
  account-avatar menu on desktop. Preserve the existing preference endpoint,
  per-device storage, role destinations, authorization and local work. Source,
  localization, documentation and bounded static/memory-only manual review only;
  no automated tests, database/migration, scheduler, Git mutation or deployment.

- Owner Announcements correction, 2026-10-16: preserve the approved phone UI,
  round desktop cards and make layout respond to display scale/browser zoom.
  Open a desktop notice immediately in an in-page popup, without document
  navigation or reload. This explicitly supersedes the preceding desktop full-
  navigation side-reader decision. Fetch the existing authorized detail GET
  fragment afresh, preserve normal mobile/no-JavaScript links and private cache
  policy, and keep the current document's collector running without inventing
  a detail page_view for an overlay. Research definitions/sampling/session code,
  current visibility, data, paging and all unrelated work remain preserved.
  Static and bounded memory-only browser review only; no automated tests,
  actual database/migration, scheduler, Git mutation or deployment.

- Owner follow-up, 2026-10-16: make the phone Attendance summary resemble the
  supplied desktop reference, with readable spacing, prominent per-status
  percentages and visible status bars. Retain the ring alongside the metrics;
  allow a two-column wrap on narrow phones and four columns on wider phones.
  This supersedes the previous phone legend's four compact rows. Keep desktop,
  records, filters, calculations and visibility unchanged. Source/static/manual
  presentation review only; no automated tests, database/migration, Git or release.

- Owner follow-up, 2026-10-16: implement the approved Compact index design for
  Student Announcements. Use platform identity, a compact published-notice index,
  responsive phone rows and full phone reading, with a desktop side reader and
  retained list page. Preserve current published/scope authorization, public-ID
  links, plain text, 20-row paging and research page/session meanings. Source,
  localization, documentation and bounded static/memory-only manual review are
  authorized. No automated tests, actual database/migration, scheduler, Git
  mutation or deployment belongs to this follow-up.

- Owner follow-up, 2026-10-16: implement the approved Focus Student Discussions
  concept, including its phone presentation. Show an authorized Group's topic
  list beside the selected discussion on wide screens; use separate list and
  reading pages on phones. Provide normal Group selection, factual counts,
  open/locked states, chronological replies and the existing reply form. Source,
  localization, documentation, static checks and bounded memory-only manual
  browser review are authorized. Preserve current active-membership visibility,
  public identifiers, 20-topic/50-reply paging, signed stale/duplicate protection,
  CSRF, authoritative locked transactions and research meanings. Preserve other
  local changes. No automated tests, database/migration, scheduler, Git mutation
  or deployment is included.

- Owner follow-up, 2026-10-16: implement the approved Flow Messages design,
  including the selected compact conversation spacing and responsive phone
  presentation. Use existing platform typography and semantic theme colors;
  update the Inbox, Thread, recipient picker and composer presentation while
  retaining search, history, emoji and message-management controls. Preserve
  Student/Teacher scoping, native POST/CSRF/context tokens, historical read-only
  access, research identifiers and all local data. Source/presentation,
  localization, documentation and bounded static/memory-only browser review
  are authorized. This supersedes the previous Messages presentation freeze
  and narrow-header avatar omission for this design. No automated tests,
  database/migration, scheduler, Git mutation or deployment is included.

- Owner follow-up, 2026-10-16: implement the approved Refined ring and cards
  Student Attendance proposal. Use a compact status ring with matching class
  count in its center, readable four-status counts/percentages, compact phone
  cards showing Group/date/time/mark and native Location disclosures. Show the
  applied filter count/context and retain the current GET filters and pagination.
  Preserve the desktop table, existing owned-finalized visibility and all domain
  meanings. Source/presentation, localization, documentation and bounded static/
  memory-only manual review are authorized. No automated tests, database/migration,
  scheduler, Git mutation or deployment belongs to this follow-up.

- Owner follow-up, 2026-10-15, continued on 2026-10-16: implement the approved
  Course directory design for Student Grades and See my grades. Add a compact
  directory, name search, closed Course/Term/Group filters and retained paging;
  show overall grade first, then collapsible categories and Teacher comments.
  Use existing platform identity and responsive phone presentation. Read-only
  source/query/presentation and documentation changes are authorized. Preserve
  own-released visibility, historical access and the single calculation service.
  Publishing was explicitly cancelled; no automated tests, database/migration,
  scheduler, Git mutation or deployment belongs to this follow-up.

- Owner follow-up, 2026-10-15: update Student Attendance. Replace the entire
  Your recorded classes card with the percentage distribution of Present,
  Absent, Late and Excused; apply the page filters to this summary as well.
  Reuse My records' closed filter design and show only the Group name in the
  table's Group column. Course, Group, mark and inclusive class-date filters
  are read-only and cover the same owned finalized history in both summary
  and table. This explicitly supersedes the historical Student counts-only
  presentation restriction; no grading, progress or research rule changes.
  Preserve existing local work. No automated tests, database/migration, Git
  mutation or deployment is included in this follow-up.

- Latest My records follow-up, 2026-10-15: use the owner's compact Filters
  icon/label plus adjacent arrow at the physical upper right beside My records.
  Keep its dropdown closed initially. Remove the introductory enrollment status,
  start date/link and the All enrollment periods section. Default Course,
  Enrollment period and record type to All; combine the active Student's own
  visible historical records across the selected scope, retaining per-record
  course/episode provenance and all publication/private-audio restrictions.
  This supersedes the preceding newest-enrollment default and history section.
  Source/static review only; existing no-test/database/Git/deployment limits stay.

- Owner follow-up, 2026-10-15: implement the approved Student My records
  timeline proposal using the platform's existing typography, colors and density.
  Filters are closed by default, with their disclosure arrow at the physical
  upper right; choose Course, Enrollment period, then activity/record type.
  Keep achievement icons and factual submission counts; remove motivational
  prose. Read-only source/query/presentation changes are authorized. Preserve
  own historical records, source Group context, released/finalized boundaries,
  private audio access and all research/domain meanings. No automated tests,
  database/migration, Git mutation or deployment is included in this follow-up.

- Owner continuation, 2026-10-15: complete both local and hosted execution.
  The owner explicitly requests creating/configuring the local migration account
  and delivering its credentials privately. Use the configured local administrative
  connection, keep the runtime account unchanged, and store the new credentials
  only in ignored local environment/private artifacts. The exact local migration
  is now applied and verified. Continue the already-authorized reviewed GitHub
  push and Railway web deployment, preserving data and all other hosting config.

- Latest execution/release authority, 2026-10-13: the owner explicitly requests
  applying the prepared migration, GitHub push and Railway deploy. This
  supersedes preparation/local-only boundaries for the reviewed Login and
  Student/Listening work. Verify the exact local parent 7d4e2a9c6013, preserve
  private recovery snapshots, apply only 8e5f3b0d7124, inspect schema/row counts,
  then create one reviewed allowlisted commit and normal pushes to the existing
  follow-up/release branches. Deploy that exact SHA to the existing web service;
  its serialized predeploy must allow only the parent-to-target transition or
  an already-current target. Preserve data, private files, variables, volumes,
  all other services and research policy. No automated tests, data reset,
  additional migration, scheduler change or unrelated Help upload.

- Latest Student follow-up, 2026-10-13: use a solid page-colored mobile header
  at the top and glass only when scrolled; remove dashboard Groups and improve
  Next activities. Align Listening questions with Quiz, place the timer beside
  Back at the physical right and a compact audio strip under the title, omit
  Vocabulary during questions, and autosave choices before Next/final submission.
  Next becomes Submit attempt on the last/only question; completed answers expose
  a visible submission shortcut. Remove Listening's Finish this attempt card.
  The owner separately approves preparing a migration for per-activity playback
  choices, without applying it to any database. Preserve existing authored rates
  for old activities, authentication, authorization, locks, deadlines, finality,
  answer-key protection and research definitions. Preserve the local Login work.
  Source/static/manual review only; no automated tests, database mutation,
  migration application, Git mutation or deployment in this follow-up.

- Latest owner follow-up, 2026-10-13: simplify the shared Login page. Remove its
  Help/Display entry buttons, welcome heading/subtitle and branded footer; show
  the existing logo without its background and match autofilled field colors to
  empty fields. Replace the heading area with Sign in on the physical right and
  Find your level on the left (Arabic label: "حدد مستواك"). The latter swaps the
  card body to Start test, leading to an empty public placement-test interface.
  This supersedes Login's presentation freeze for this bounded source change.
  Preserve authentication, CSRF, role redirects and existing data. No questions,
  attempts, grading, database/migration or automated tests are authorized. The
  preceding activity release is complete; this follow-up has no new delivery request.

- Latest release authority, 2026-10-13: the owner requests upload of the reviewed
  activity/mobile/Messages changes and explicitly confirms GitHub plus updating
  the Railway website. This supersedes the paused-delivery boundary for this
  bounded release. Commit the reviewed allowlist once, push normally to the
  existing follow-up/release branches, then advance only the existing web
  service's exact source pin. Preserve the unrelated Help template, local and
  production data, volumes, variables, schema and research settings. No new
  automated tests, migration, Study or authentication-policy change.

- Latest owner follow-up, 2026-10-13: hide the upper activity breadcrumb context
  on phones, move the availability/details card to the end of all four Student
  activity overviews, and make the shared mobile header hide on downward scroll
  and return over content on upward scroll without moving the page. Compact
  Messages conversations/contacts and chat typography/spacing. This explicitly
  permits the bounded Messages presentation changes previously protected from
  global density work. Preserve existing local work, workflows and data; local
  source/static/manual review only. Delivery remains paused; no automated tests,
  database/migration, Git mutation or deployment is included.

- Latest owner verification request, 2026-10-12: perform all available checks
  for the local activity presentation changes, including previously interrupted
  browser cases. Keep the existing no-automated-tests and no-database-mutation
  boundaries and paused delivery. Record actual evidence and findings; this
  verification request does not authorize unrelated repairs or release actions.

- Latest owner follow-up, 2026-10-12, superseding the intervening upload request:
  pause delivery and review all Student activity types (Listening, Quiz,
  Assignment and Speaking). Align their presentation with the latest Listening
  changes where appropriate. This includes full-width instructions after facts,
  consistent attempt/question summaries and bottom result/submission summaries.
  Preserve each activity's own answering, recording, save, finalization, feedback,
  authorization and research contracts. Local source/static/manual presentation
  review only; no automated tests, database/migration, Git mutation or deployment.

- Latest owner follow-up, 2026-10-12: locally reorganize Student Listening
  overview/results. Put Instructions below availability facts, close the attempt
  disclosure by default, remove the specified helper prose and result Back links,
  pair audio/Vocabulary and Transcript/Vocabulary, and use one visible summary
  with native selectors for attempts and per-question results. Remove the duplicate
  Final score card; move Score/Submitted to the bottom as Result summary using its
  blue gradient. Preserve authorized data, transcript policy and domain/research
  contracts. Source/static/manual presentation review only; no database, migration,
  automated tests, Git mutation or deployment is included in this follow-up.

- Latest owner follow-up, 2026-10-11: replace the Student Quiz overview's long
  attempt list with one visible result summary and an Attempt dropdown. Keep
  existing authorized summaries, native Result links and all domain/research
  contracts. Continue the existing GitHub/Railway delivery authorization for
  this bounded final Quiz addition. No database or authentication change.

- Latest release authority, 2026-10-11: the owner explicitly requests GitHub
  upload and Railway deployment of the implemented Back/breadcrumb, Quiz,
  browser-only Assignment draft and localized file-control changes. This
  supersedes local-only Git/deployment exclusions for this bounded release.
  Existing unimplemented login-session proposals remain proposals. Preserve
  unrelated local files, production data/schema, volumes, variables and services.

- Latest owner follow-up, 2026-10-11: remove the manual Assignment draft button,
  keep automatic browser saving, redistribute Student Quiz cards, remove the
  result-page Back to Quiz button, and add linked hierarchy beside shared Back.
  Session expiry is a review/opinion request only; no authentication policy
  change is included. The preceding local-only verification/release limits apply.

- Latest local owner authority, 2026-10-11: add shared Back navigation across
  all four roles, remove the Quiz attempt-policy disclosure, add automatic and
  manual Assignment text drafts, and localize file-selection controls. The owner
  explicitly chose same-device/browser draft storage without database changes.
  This supersedes the earlier no-draft exclusion for browser-only working text;
  final submissions remain immutable. No migration, data reset, automated test
  package, Git mutation or deployment is included. See PROJECT_STATUS.md.

- Latest release authority, 2026-10-11: the owner's subsequent request explicitly
  authorizes commit, push and deployment of the reviewed UX implementation.
  Preserve the existing remote follow-up and Railway production database,
  services, volumes and variables. Deployment is limited to the existing web
  service; no schema change, Study, Version B or Baseline Freeze. This supersedes
  the earlier local-only release exclusions below. See APPROVED_UX_RELEASE.md.

- Latest owner authority, 2026-10-11: direct local implementation of all
  remaining compatible approved UX proposals, including selected dashboards
  and exact mobile navigation. Preserve completed source, density, content
  cleanup, Dark logo, protected Login/Messages and all domain/research contracts.
  Use the existing owned disposable Development dataset for bounded checks.
  NO COMMIT, PUSH, DEPLOY, RAILWAY MODIFICATION, MIGRATION or production data
  change. No Study, Version B or new Baseline Freeze. Earlier release permissions
  below are historical and superseded for this task.

- Latest release authorization, 2026-10-10: the owner approved final four-portal
  removal of nonessential platform-use explanations and redundant upper context,
  precise blue-to-white Dark logo rendering, bounded actual-app acceptance,
  commits, safe branch integration, official GitHub push and deployment to the
  existing Railway web service. Preserve the preceding density/A1 work, protected
  Login/Messages, MySQL/data/volumes/secrets and all domain/research contracts.
  Use only the owned disposable local MySQL dataset for write verification.
  No production schema/data mutation, new paid resources, Study, Version B or
  scientific Baseline Freeze. This supersedes earlier Git/deployment exclusions
  for this presentation release. Evidence and honest limits are recorded in
  [FINAL_UX_RELEASE.md](FINAL_UX_RELEASE.md).

- Latest presentation authorization, 2026-10-10: the owner approved global
  removal of nonessential explanatory prose and introductory structures, and
  responsive density corrections across all four roles and shared components.
  Implement in the existing main checkout using ST2 glass and D-MOTION. Protect
  Login and the Messages composition/content. Preserve authored content,
  required disclosures, explicit operations, stable research IDs and all domain
  contracts. Earlier A/B/C behavior proposals are not included. Evidence and
  runtime/accessibility limits are recorded in
  [UX_DENSITY_IMPLEMENTATION.md](UX_DENSITY_IMPLEMENTATION.md). No new Baseline
  Freeze, Study activation, Git mutation, database work or Railway deployment.

- Latest redesign authorization, 2026-10-07: P01-P06 and the Design Proposal /
  Architecture Review are approved. Begin W0, then W1, and follow dependency
  gates. The canonical visual/wave target is
  [VERSION_A_DESIGN_CONTRACT.md](VERSION_A_DESIGN_CONTRACT.md). Keep the shared
  Flask monolith, domain/security/research semantics and stable identities.
  No Version B or real study collection. Assignment file policy/migration and
  instrumentation are later, separately gated wave work. Source completion is
  not responsive/accessibility/runtime acceptance or Baseline Freeze.
- Latest enrollment decision, 2026-10-06: the owner removed the study-start
  admission cutoff. New enrollment, re-enrollment and wrong-course correction
  may use a started Group, with an informational notice before saving and after
  success. Keep the configured study start, eligibility, capacity, teacher,
  duplicate-enrollment, signed-preview, history and atomic-finance protections.
- Latest local-rehearsal decision, 2026-10-06: the owner authorized source
  corrections so ordinary Student usage can be reviewed and exported locally
  before redesign. Operational-review exports are allowed as a separate,
  server-selected dataset, explicitly identified in the manifest, session
  rows and creation audit. Study exports still exclude all non-study data.
  Preserve classifications, 15-day retention and sampling; no schema change,
  reset, automated tests, scheduler changes or Git mutation is included.
- Latest hosting/evaluation decision, 2026-10-06: a future deployment starts
  with schema and technical control rows only, no seeded users or business
  content. Preserve the current local database. Remove payment simulation and
  demonstration selectors/operator actions. Evaluation uses ordinary forms
  with manually entered fictional data. Its research sessions remain excluded
  from study exports; genuine study provenance requires a separate explicit
  deployment decision. This supersedes earlier demonstration-mode provisions.
- Latest owner decision, 2026-10-06: old tests were deleted and a new automated
  test package was explicitly declined. Complete the remaining source/interface
  integration, static review and documentation without recreating or running tests.
  Historical test gates below no longer block this owner-approved close-out;
  their absence is a verification limitation, not evidence that all cases passed.
- The separately authorized development reset and fictional four-account seed
  are complete at schema head `085b7a4e9012`. All three current Courses are priced.
  Research data remains demo/development, with collection initially paused.

- Direct owner instructions take precedence. Read this contract together
  with `DECISIONS.md` and the current approved implementation Part.
- Preserve the Flask modular monolith; no rewrite, framework replacement or
  automatic import of the separate `design/` prototype.
- The original from-scratch instructions in `MASTER_PROMPT.md` and the
  inputs describe the project's origin, not this repair's starting point.
- Current inspected HEAD: `fd500fd` (natural-use Phase 6 completion).
  The shared-login correction is pre-existing working-tree work and must
  be preserved. All roles use `/auth/login`; Researchers retain a separate,
  server-protected workspace. `/research/login` is a compatibility entry.
- `PHASE6_COMPLETION.md` records prior MySQL/browser checks and development
  revision `d574ab56594f`. Those checks were not rerun during Part P0.
- Work proceeds in bounded Parts with acceptance gates. A Part authorizes
  its own source/docs changes and isolated checks; database provisioning,
  migrations, live writes, dependency installation, scheduler changes and
  Git mutations must have an explicit scope before execution.

## 1. Runtime, database and documentation

- MySQL/InnoDB is the only approved database engine for development,
  production and every database-backed test, including browser and migration
  tests. Pure unit tests use no database or database fixtures.
- SQLite was present at the pre-P0 inspection. P1C has replaced the named
  TestingConfig/central fixtures with guarded owned MySQL resources; P1D
  converted browser resources and P1E converted historical migration probes.
  Runtime SQLite branches/type variants are removed. No full MySQL-only completion claim
  before the remaining P1 gate passes.
- Use an explicitly isolated test target and credentials. Fail closed if a
  test could use a development/production database or a non-MySQL engine.
- Prepare a new, clean MySQL development database later. Current development
  rows are disposable test data; this does not authorize an immediate drop
  or deletion of source, private files, credentials or history.
- Add canonical `GET /`: unauthenticated visitors go to the shared login;
  authenticated users go to their role's home. Preserve shared login,
  authorization, CSRF, safe return targets and its common rate-limit budget.
- Reject unknown environment names. Document setup, current entry points,
  migrations and verification without exposing credentials.
- Keep repository documentation, code and application UI in English; owner
  reports remain Arabic.

## 2. Money, course price and discounts

- Course owns the default price and currency; Group has no price override.
- Capture the price used at enrollment in the invoice. Later Course price
  changes affect future enrollment quotes, not existing liabilities.
- Operational currency is LYD, with a `0.001` monetary quantum. Use Decimal,
  never float. Calculate a percentage discount at sufficient precision and
  round once to the nearest quantum; use deterministic `ROUND_HALF_UP` for
  an exact half-quantum tie. Validate the final amounts before persistence.
  The rounding tie rule is an implementation convention, not a research rule.
- Display up to three fractional digits and remove trailing fractional
  zeroes only: `125.500 -> 125.5`, `125.000 -> 125`, `0.005 -> 0.005`.
  Formatting must not change stored values or remove significant zeroes.
- A discount is either an amount or a percentage, with the resulting amount
  snapshotted. Validate that the discount cannot exceed the course charge.
  A fully discounted course has a zero obligation with a traceable invoice.
- The authenticated actor granting the discount is recorded automatically;
  no editable actor selection. The reason is optional.

## 3. General student account and document history

- The student account is derived from effective invoices, confirmed
  collections and confirmed refunds/payouts. No mutable stored balance,
  `ReceiptAllocation`, receipt-to-invoice distribution or installment table.
- Collections reduce the student's general account. An optional invoice
  reference supplies context only, not an allocation or balance dependency.
- Multiple partial payments are supported. Pending/rejected movements are
  not confirmed money. Pagination and request limits must not become a
  maximum lifetime count of collections or payment attempts.
- Signed balance:
  `net obligations - net confirmed collections + net confirmed payouts`.
  Positive means the student owes the center; negative means the center
  owes the student; zero means balanced. Do not reject a valid credit balance.
- Invoices and receipts are operationally editable/deletable, including
  after collection. Preserve old/new values, actor, time and document
  identity/revisions. No hard deletion of financial history.
- A receipt and its underlying money movement must not be counted twice.
  Correction/deletion of a recorded movement is distinct from an actual
  cash/bank payout. A refund records money really returned to the student.
- Present the corrected current account separately from chronological
  financial movements and correction history. A correction today must not
  silently erase the evidence of an older movement.
- Numbers, public IDs, audit records, idempotency and authorization remain
  protected. Future real online payments require their own approved adapter;
  no card processing or card credentials are added by this repair.

## 4. Enrollment lifecycle

### New enrollment

- One operation validates Student eligibility, Group eligibility, capacity,
  teacher availability, configured group study start and authoritative price.
- Create Enrollment, invoice, discount history and optional confirmed
  initial collection/receipt in one database transaction. Failure rolls
  back every part. Notifications are secondary to that transaction.
- With no received amount, the invoice remains due for its net amount;
  a zero net charge is balanced rather than artificially outstanding.
- New enrollment and re-enrollment into a Group remain allowed at or after
  its study start, with a clear informational notice. Use the authoritative
  Group study-start timestamp to describe late entry, not to refuse it.
  Do not backdate membership or automatically grant earlier learning records.
- Repeated requests and competing requests for the last seat cannot create
  duplicate enrollment, charges or collections.

### Withdrawal and payout

- End the active membership, release its seat and preserve the full record.
- Offer an optional reversal of the course's net obligation after discount.
  If it is not chosen, the obligation remains; preserve that choice in history.
- Reversing the obligation does not reverse a real collection or claim that
  cash was returned. Credit becomes a negative general-account balance.
- A separate actual refund/payout settles money owed to the student.
  Replays must not repeat withdrawal, reversal or payout.

### Transfer and re-enrollment

- Transfer is to another Group of the same Course, subject to capacity and
  other authoritative target eligibility checks. It is allowed even when
  the destination has started studying. New admission and re-enrollment now
  also allow a started destination, with their own notice and new episode.
- Preserve source membership and transfer events, release/reserve the
  respective seats atomically, and create no new invoice or obligation.
- Current Course Progress uses the destination Group's learning content.
  Keep source progress/history separately; do not copy completion by title
  or fabricate equivalence. No automatic progress carryover is authorized.
- Re-enrollment creates a new Enrollment episode, not reactivation of the
  previous episode. Previous attempts, submissions and progress remain
  attached to their original episode.

### Wrong-course correction

- Correcting a mistakenly entered course must correct the actual Course and
  Group relationship as well as the financial effect. It is not a free-form
  invoice-description change and is not a same-course transfer.
- Show affected academic/financial facts and recheck target eligibility,
  capacity and configured study start. Show the late-entry notice when the
  destination has started. Keep old/new membership and financial
  snapshots. Preserve source learning records under their original context;
  do not relabel prior answers/grades as work for the replacement course.

## 5. Accounts, teaching and scheduling

- Suspend/reactivate accounts without deleting their history. A reactivated
  Student can read their old and current authorized records.
- Do not suspend a Student while any active Enrollment remains. After a
  withdrawal do not disable their account if another active Enrollment exists.
- Protect account edits/status/password changes against stale forms and
  competing writes. `auth_version` invalidation is not a substitute for
  domain change history or optimistic concurrency.
- Apply the last-eligible-Teacher guard to account suspension as well as
  Group assignment removal; require a valid replacement when needed.
- Room management is in scope now. Validate overlapping effective slots
  for Group, Teacher and Room, with authoritative server rechecks under
  appropriate locks, including concurrent schedule/assignment changes.
  Adjacent half-open time slots are not overlaps.

## 6. Academic records, progress and deadlines

- Learning records and their uniqueness/authorization must distinguish
  Enrollment episodes. Review submissions, speaking submissions, quiz
  attempts, grade/attendance rosters and progress together.
- Course Progress is enrollment-owned content progress, not mastery or a
  claim of language competence. New enrollment starts independent progress;
  a same-course transfer reports the destination content with source history.
- Read access to the student's own released results, submission evidence,
  attendance and feedback must survive withdrawal/archive and be available
  after account reactivation. Historical reading grants no new write access,
  access to other students or disclosure of quiz answer keys.
- Correct grades, finalized attendance and assignment/speaking feedback via
  current values plus preserved revisions: old/new, actor, server time and
  optional reason. A version counter alone is not history.
- Use trusted server request-arrival time for timed quiz/listening submission
  deadlines. Lock/processing delay must not penalize a request received on
  time. Never trust a client timestamp; still recheck ownership, activity
  identity, stale state and terminal-attempt rules under the required locks.
- Keep GET side effects explicit. Health/root/docs verification must not
  imply that visiting a learning/material/result page is a read-only action.

## 7. Unified Student Activities

- One Student navigation entry and hub: `Activities`.
- Include Assignments, Quizzes, Listening and Speaking, with All/type,
  action-state and Course filters. Show type, Course, deadline, state and
  the applicable action (Submit, Continue, Record, View result).
- Prioritize actionable work and nearest deadlines; keep completed/past
  records available and distinguish Enrollment episodes.
- Dashboard `Upcoming activities` covers all four types.
- Each activity retains its appropriate execution page and domain rules.
  Lessons/materials remain within Course content; grades and attendance
  remain separate records.
- Queries must enforce ownership/publication and be bounded. Instrument
  new routes intentionally without silently changing research measurements.

## 8. Research safety and the final retention decision

### Latest owner clarification on 2026-10-05

The owners explicitly resolved the cross-chat conflict:

- Keep password-only Researcher login; no MFA implementation or MFA
  readiness blocker in this repair.
- Keep 15-day retention and daily expiry. This supersedes this conversation's
  earlier pressure-only automatic purge proposal. Do not add pressure-based
  automatic raw-data deletion or high-water/target gates as a requirement.
- Preserve the shared login and separate authorized Researcher workspace.

### Collection and measurement contract

- Version A natural use only. Preserve population rule, exclusions,
  pseudonymous identity, raw optional self-report 1-5 and cause choices.
  Unrated/Skip data has no inferred label. No binary transformation, ML,
  prediction or adaptive intervention in this repair.
- Fix F15: browser pending batches must be bound to their original account
  and configuration context using opaque server-verified delivery scope.
  Never attribute a delayed batch to the current account/configuration just
  because the cookie changed. Preserve event IDs, replay/idempotency and
  minimization; do not place account identity in research exports.
- Scope binding does not authorize redefining session start/end, inactivity,
  tab behavior, sampling chance/budgets, lookback, minimum observation,
  display-grant semantics, provenance/population or labels. Changes to
  those meanings require a separate research decision.
- Preserve final stored answers, frozen uncertain retries, accurate
  acknowledgements, question interactions outside the feature window and
  best-effort failure isolation from the ordinary LMS workflow.
- Fix concurrent configuration-number error handling without changing
  methodology. Dashboard/export population differences and best-effort
  outcome delivery are documented limits, not automatic redesign authority.
- Only Researchers access research management/raw data through the app;
  Administrator/Teacher accounts do not. Record the real authenticated
  Researcher/operator or trusted service identity for actions. This is
  application isolation, not isolation from a server/database administrator.

### Exports, storage visibility and cleanup

- Retention remains an environment setting, with the approved deployment
  value 15 days; no hard-coded credentials or retention constant in source.
- Preserve session expiry by last activity, dependent event/prompt removal,
  archive expiry with its oldest included data, and daily operation. Do not
  change retrospective configuration-policy semantics during this repair.
- Date-range export/manual purge selects sessions whose start falls in the
  requested local-date interval, with the full dependent data for each
  selected session. Do not clip events/windows at the range boundary.
- Display storage usage and last measurement in the Researcher workspace,
  and explicitly describe missing/purged periods. Usage display must not be
  mistaken for proof that deleting rows released physical disk space.
- Add auditable manual range/all raw-data cleanup with a concrete preview.
  Preserve subjects/links, exclusions, configurations, export descriptions
  and audit/gap records. Never delete ordinary LMS records through cleanup.
- Archive bytes may be cleaned under the approved cleanup/retention rules;
  keep metadata/digest/availability state. Each ID serves its original
  bytes while present, or reports unavailability; it is never rebuilt.
- Preserve single-snapshot export, truthful cutoff/timezone/digest, privacy
  and CSV-injection defenses. Larger exports need a bounded-resource design
  that still reads one consistent snapshot.
- Installing/changing/running a scheduled task or starting a real study
  collection period is a separate explicitly scoped operational action.

## 9. Target schema and implementation boundaries

Design incrementally around real workflows, not speculative enterprise tables:

| Area | Required direction |
|---|---|
| Catalogue | Course price/currency/version; Rooms and scheduling relations |
| Enrollment | Episode identity; historical Group memberships and events; active-membership integrity |
| Learning | Episode-aware progress, attempts, submissions and roster relationships |
| Revisions | Grade, attendance, feedback and account/domain change history |
| Finance | Direct student/enrollment invoice context, price/discount snapshots, general collections, actual payouts, receipt/document revisions and chronological financial effects |
| Research | Delivery-scope validation, operator attribution, usage/cleanup/gaps while retaining existing collection and expiry contract |

Keep public identifiers, indexed foreign keys, local CHECKs/unique constraints,
Decimal amounts, consistent UTC server timestamps and protected history.
Composite invariants belong in the schema where practical; dynamic role and
active eligibility still need authoritative application operations.

Routes handle HTTP/forms/rendering; operation services own locks, rechecks,
transactions and audit; query services enforce scope and bounded reads;
pure calculations remain independently testable. Extract existing behavior
gradually rather than adding one large generic service.

## 10. Testing, removal and recovery

- KEEP valid security, privacy, idempotency, answer-key and immutable-export
  contracts. REWRITE changed enrollment/finance/history and SQLite contracts.
  MERGE repeated setup/invariants without dropping meaningful scenarios.
  DELETE obsolete runtime contracts only after replacements pass.
- Use MySQL connections for actual locking, capacity, numbering, migration,
  Decimal/collation and export-snapshot checks. Existing successful tests
  are evidence of their checked build, not authority over the new domain.
- Replace consumers before removing fee-plan/assignment runtime, old
  enrollment reactivation, invoice-scoped account assumptions, lost-history
  corrections and SQLite support. Retain historical migration upgrade
  coverage while the historical chain remains supported.
- Preserve shared-login and Phase 6 retention work. Do not erase historical
  decisions/test results or treat a changed document as a runtime repair.
- Maintain a recoverable source baseline and verify database/file backups
  before authorized live operations. A source ZIP is not a DB/private-file
  backup. MySQL DDL/data loss recovery needs a tested restore plan.

The phase sequence and completion evidence are in `IMPLEMENTATION_PLAN.md`
and `PROJECT_STATUS.md`. No runtime phase is completed by Part P0 documentation.
