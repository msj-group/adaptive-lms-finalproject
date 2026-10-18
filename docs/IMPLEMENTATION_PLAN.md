# Repair implementation plan

> Owner release request, 2026-10-18: commit, push and deploy the reviewed recent
> application/documentation work from `491ef52`. Perform bounded static checks,
> create one explicit allowlisted commit, fast-forward both existing release
> branches, verify their exact GitHub SHA and advance only the existing Railway
> web source pin. Observe build/predeploy/startup and public readiness/Login/assets.
> Preserve local data, unrelated unused Help/design previews and all other hosting
> resources/settings. No automated tests, new migration, reset, seed, scheduler or
> installation. The existing predeploy validates the current schema head.

> Owner Teacher Review queue follow-up, 2026-10-17: apply the approved desktop
> list/editor and phone list/standalone-editor design with shared semantic themes
> and Arabic/RTL. Retain existing scoped filters, oldest-first 25-row paging and
> the protected assignment/speaking feedback endpoints. A request header selects
> their editor fragment; ordinary full-page links/forms remain available. Use
> memory-only fictional browser examples and static checks, preserving all local
> work. No automated tests, actual database/seed/migration, scheduler, Git mutation,
> installation or deployment. Current evidence and browser limits: PROJECT_STATUS.md.

> Owner Teacher Activities image follow-up, 2026-10-17: apply the Teacher desk
> image using a library/creation grid on wide workspaces. Reflow to activity
> cards and an initially closed native creation disclosure on compact workspaces;
> without the page script the native disclosure starts open. Explain task types
> by what Students do, preselect Assignment when no type is selected, and retain
> the existing Group/type GET entry and protected type-specific draft workflows.
> Use workspace/container width for responsive layout, preserving focused creation
> controls on reflow. Static and memory-only manual browser review only. No
> automated tests, actual database/migration, scheduler, Git mutation or release.

> Owner Teacher Attendance follow-up, 2026-10-17: transfer the compatible Student
> ring/metrics and responsive card treatment into Teacher-owned presentation.
> Use current-page finalized mark counts in the Group list and saved per-class
> counts in detail, with explicit scope/units. Keep draft rows and every Teacher
> action, one native marking control set, private notes, CSRF/signed tokens and
> separate finalization. Retain existing bounded queries, routes, transactions
> and Student assets. Static and bounded memory-only manual review only; no
> automated tests, actual database/migration, scheduler, Git mutation or release.
> Current evidence: PROJECT_STATUS.md.

> Owner Teacher Dashboard selection, 2026-10-17: apply the Group Cockpit desktop
> cards and compact next-class strip, with Groups & Feedback phone tabs and
> compact Group tiles. Reuse scoped payloads/native links; keep the four-Group
> preview and oldest-three feedback query, showing two feedback rows on phones
> with the full queue link and honest count labels. Add page-owned CSS/script,
> Arabic labels and bounded static/memory-only manual review. Preserve existing
> local work; no automated tests, actual database/migration, scheduler, Git
> mutation or publishing. Current evidence: PROJECT_STATUS.md.

> Owner Teacher Activities follow-up, 2026-10-17: use a Teacher-owned library
> template and assets, a closed native GET filter disclosure, readable responsive
> activity rows and one native group/type creation entry. A role-gated GET checks
> the assigned operational Group before redirecting to existing draft forms;
> saving/publication remain in their original protected workflows. Group choices
> stay bounded and include an explicitly selected assigned Group. Reorganize the
> four authoring templates without changing their field/transaction contracts;
> collapse optional listening fields, opening them for their validation errors.
> Static and memory-only manual review only; no automated tests, actual database,
> migration, scheduler, Git mutation or release. Evidence: PROJECT_STATUS.md.

> Owner photo-crop follow-up, continued 2026-10-17: open an accessible crop
> dialog after selection, with a circular display guide, pointer/keyboard panning,
> 100–400% zoom, reset and explicit Use photo/Cancel. Preview the chosen square,
> permit Adjust crop before Save, and preserve the previous confirmed selection
> when that adjustment is cancelled. Keep the original file in the native POST;
> validate normalized square coordinates server-side after EXIF orientation and
> retain image safety checks and existing account transactions. Static and bounded
> memory-only manual review only; no automated tests, actual database, migration,
> scheduler, Git mutation or deployment. Current evidence: PROJECT_STATUS.md.

> Owner Teacher My groups follow-up, 2026-10-16, continued 2026-10-17: apply
> the selected Expandable list in a Teacher-owned template and page assets.
> Filter the existing authorized card payload on the server using public Course/
> Term identifiers, context, inclusive term timing and bounded name search.
> Use native GET forms and details, preserving no-script links, active-assignment
> visibility, dates, eligible Student counts and existing tool authorization.
> Preserve Student My courses and all unrelated work. Static and bounded
> memory-only manual review; no automated tests, actual database/migration,
> scheduler, Git mutation or publishing. Evidence: PROJECT_STATUS.md.

> Owner Account settings follow-up, 2026-10-16: replace the exposed upload
> field with avatar-owned View/Edit/Delete controls, native photo viewing,
> local preview and a Save photo button revealed only for a valid selection.
> Enlarge/center the circle and reserve action space in the responsive card.
> Move specific client/server validation errors to a localized dialog. Use the
> existing photo POST and locked account revision service to clear the current
> photo reference without deleting file rows/bytes or history. Preserve CSRF,
> signed snapshots, authorization, limits and research definitions. Static and
> bounded memory-only manual review; no automated tests, actual database,
> migration, scheduler, Git mutation or publishing. Evidence: PROJECT_STATUS.md.

> Owner Dashboard follow-up, 2026-10-16: apply the selected Agenda proposal to
> Student Dashboard. Reuse the existing scoped query payloads and common shell;
> render the next class above a two-column activities/continue-lesson composition,
> stacking the same sections on phones. Limit the presentation to two upcoming
> activities and retain actual progress labels and conservative completion states.
> Add page-owned CSS, Arabic labels and bounded static/memory-only manual review.
> Preserve all other local work; no automated tests, actual database, migration,
> scheduler, Git mutation or deployment. Evidence: PROJECT_STATUS.md.

> Owner navigation follow-up, 2026-10-16: remove the shared desktop sidebar
> footer controls. Keep More mobile-only and replace its three utility entries
> with separate Language and Dark mode options. Expose both in the shared header
> account menu. Reuse protected preference POSTs, preserve the other preference
> when editing one, and retain keyboard dismissal and legacy utility redirects.
> Refresh shared assets in all role layouts. Static and bounded memory-only
> manual review only; no automated tests, database, migration, Git or release.
> Current evidence and limitations: PROJECT_STATUS.md.

> Owner Announcements correction, 2026-10-16: supersede the earlier desktop
> full-navigation reader with an immediate native popup in the index document.
> Reuse the existing authorized GET-fragment mechanism, loading/close/retry and
> normal-link fallback; abort stale loads and clear content on close. Round
> desktop cards to 28px and fit the popup to the effective viewport at zoom/DPI
> changes. Preserve the phone UI, list scroll/page, native phone reads and all
> server scoping. The existing index collector remains in the same document;
> do not synthesize detail page_view events or alter research sampling/session
> code. Static/memory-only browser review; no automated tests, database,
> migration, scheduler, Git mutation or release. Evidence: PROJECT_STATUS.md.

> Owner follow-up, 2026-10-16: adapt the phone Attendance summary to the owner's
> desktop reference. Show each status label/count above a prominent percentage
> and visible bar; keep the ring beside a two-column narrow-phone grid or a
> four-column wider-phone grid. Restrict layout rules to phone breakpoints and
> refresh the stylesheet version. Preserve existing filters, cards and desktop.
> Bounded static/manual review only; no automated tests, database, Git or release.

> Owner follow-up, 2026-10-16: implement Student Announcements as the approved
> Compact index with current platform typography/colors, responsive scope/title/
> context/publication rows and retained 20-row paging. Phones and no-JavaScript
> browsers use the ordinary authorized detail GET. Desktop index-origin detail
> GETs retain the collector lifecycle and render one bounded authorized background
> index plus a native side reader. Preserve all visibility/plain-text rules.
> Static and memory-only manual review; no automated tests, actual database,
> migration, scheduler, Git mutation or release. Evidence: PROJECT_STATUS.md.

> Owner follow-up, 2026-10-16: implement Focus for Student Discussions. Open the
> first authorized Group by default or validate the selected public Group ID.
> Reuse existing bounded query projections and reply transactions. Wide screens
> show topics beside the reader; phones use the topic list and ordinary detail
> links. Keep topic-list page context separate from reply paging and retain it
> through forms, reply redirects and All topics. Use shared platform tokens,
> role-scoped templates, native GET/POST fallbacks and owned Arabic labels.
> Static and bounded memory-only manual review; no automated tests, actual
> database, migration, scheduler, Git mutation or publishing. Evidence and
> limitations: PROJECT_STATUS.md.

> Owner follow-up, 2026-10-16: implement Flow Messages with compact spacing,
> a solid conversation sidebar, visible subject, incoming/outgoing bubbles and
> a compact composer. Keep the existing workspace shell, phone navigation,
> role-scoped native workflows, search/history, emoji and sender/member actions.
> Preserve POST/CSRF/context tokens, research identifiers and data. Static and
> bounded memory-only browser review; no automated tests, actual database,
> migration, scheduler, Git mutation or publishing. Evidence: PROJECT_STATUS.md.

> Owner follow-up, 2026-10-16: implement the approved refined Attendance ring
> and phone cards. Keep counts/percentages tied to the existing filtered finalized
> aggregate, show the total inside the ring, and replace the phone's wide table
> with native Group/date/time/mark cards and collapsible Location. Retain desktop
> table, 20-row pagination, GET fallback, filter count/context and platform tokens.
> Static/template and bounded memory-only browser review; no automated tests,
> real database, migration, Git mutation, scheduler or release. Evidence below
> remains in PROJECT_STATUS.md.

> Owner follow-up, 2026-10-15, continued 2026-10-16: implement Student Grades as
> the approved Course directory with SQL name search, Course/Term/Group filters,
> fixed 20-row paging and retained list context on detail/return links. Keep
> filters closed and show Group after selecting Course; offer local option search
> for long Course lists. Show unchanged overall/category/item calculations in a
> responsive result view with native category/comment disclosures. Preserve own
> released history, role/status SQL checks and local work. Static/MySQL-dialect
> compilation and memory-only manual browser review; no automated tests, actual
> database execution, migration, scheduler, Git mutation or publishing. Current
> evidence and limits: PROJECT_STATUS.md.

> Owner follow-up, 2026-10-15: implement Student Attendance filters using the
> closed My records disclosure. Replace Your recorded classes with all four
> status percentages and counts over the complete matching finalized history;
> use identical Course/Group/mark/inclusive date filters for list and aggregate.
> Group cells show names only. Preserve account scoping and withdrawn/archived
> history. Source/static and memory-only manual browser review; no automated
> tests, database/migration, Git mutation or delivery. Evidence: PROJECT_STATUS.md.

> Latest My records follow-up, 2026-10-15: compact physical-right Filters
> dropdown beside the title; remove introductory enrollment facts and the
> enrollment-history section. Default all three selectors to All and page the
> Student's own visible records across courses/episodes, with per-card provenance.
> Preserve legacy/audio routes and release/finality boundaries. Static verification
> and documentation remain required; no automated tests, database/Git mutation or
> deployment. Browser review of this follow-up was blocked by approval-review
> usage limits; do not treat the preceding screenshots as this version's evidence.

> Owner follow-up, 2026-10-15: implement My records as the approved timeline,
> adapting its scale and palette to shared platform tokens. Use closed native
> filter disclosures, Course -> Enrollment period -> activity/record type,
> factual scoped counts and achievement icons without encouraging prose.
> Preserve episode history and existing record/audio visibility. Source/static
> and bounded manual presentation review only; no automated tests, database,
> migration, commit/push or deployment. Current evidence: PROJECT_STATUS.md.

> Owner continuation, 2026-10-15: complete local and hosted release. The requested
> separate local migration account was created/configured privately; runtime
> privileges remain unchanged. Exact local upgrade and schema/count verification
> completed. Commit the reviewed allowlist, push both existing branches and deploy
> that SHA through the existing Railway web predeploy. Credentials and recovery
> artifacts stay ignored. Record final hosting/public evidence after execution.

> Latest execution/release authority, 2026-10-13: the owner now explicitly asks
> to apply the prepared migration, push and deploy the reviewed Login and Student
> changes. Verify 7d4e2a9c6013, snapshot privately, apply only 8e5f3b0d7124 and
> inspect actual local schema/data preservation. Create one bounded reviewed
> commit, push the existing branches and deploy its exact SHA on Railway with
> the expected-parent/target predeploy check. Preserve unrelated Help, data,
> files, variables, volumes and research policy. No automated tests or reset.
> Actual execution and release evidence: PROJECT_STATUS and the ignored release report.

> Latest Student follow-up, 2026-10-13: implement the requested header/dashboard
> presentation and Listening question/audio/timer/autosave/Next/Submit flow.
> Add teacher-authored playback choices with the separately approved prepared
> migration only. Preserve earlier Login work and all authorization, locking,
> deadline, finality, answer-key and research contracts. Do not apply migrations,
> mutate databases, run automated tests, commit/push or deploy. Static/offline SQL
> compilation and bounded manual fixture review are allowed; actual MySQL
> execution and runtime acceptance remain pending. Evidence: PROJECT_STATUS.md.

> Latest owner follow-up, 2026-10-13: implement the requested Login cleanup and
> two entry choices, with Sign in right and Find your level left. The placement
> choice replaces the credentials with Start test and opens an empty public
> interface. Retain native links, keyboard access, localization and the existing
> authentication contract. Verify source/templates and bounded manual browser
> presentation; no automated tests, database/migration or new delivery request.
> Evidence and limitations: PROJECT_STATUS.md.

> Latest release authority, 2026-10-13: the owner now explicitly requests GitHub
> upload and Railway website update of the reviewed activity/mobile/Messages
> changes. Create one reviewed allowlisted commit; push fast-forward-compatible
> updates to the existing follow-up and release branches, then deploy its exact
> SHA to the existing web service. Preserve unrelated Help, data, schema, volumes,
> variables and research policy. No new automated tests or migrations. Actual
> release evidence is recorded separately after execution.

> Latest owner follow-up, 2026-10-13: move all four Student activity overview
> details cards to the end, hide activity breadcrumbs on phones, add a shared
> mobile header that follows scroll direction without shifting content, and
> compact Messages list/chat presentation. Preserve native actions, forms,
> research contracts and existing local work. Local source/static/manual review
> only; no automated tests, database/migration, Git mutation or deployment.
> Implementation evidence and limits: PROJECT_STATUS.md.

> Latest verification request, 2026-10-12: complete available static and manual
> browser checks for the local activity presentation changes. Complete the three
> interrupted Quiz overview cases and record exact findings. Preserve the
> no-automated-tests/database-mutation boundary and paused upload/deployment.
> Completed evidence and remaining acceptance limits: PROJECT_STATUS.md.

> Latest owner follow-up, 2026-10-12: pause upload and review all four Student
> activity types. Align Quiz overview/results and Assignment/Speaking instructions
> and receipts with the latest Listening presentation where appropriate. Preserve
> activity-specific workflows and domain/research contracts. Local source/static
> and bounded manual presentation review only; no automated tests, database,
> migration, Git mutation or deployment. Actual evidence: PROJECT_STATUS.md.

> Latest local owner follow-up, 2026-10-12: compact Student Listening overview
> and results, with native Attempt/Question selectors and a bottom blue Result
> summary. Keep transcript visibility and all domain/research semantics. Verify
> templates, assets and bounded manual presentation behavior; no automated tests,
> database/migration, Git mutation or deployment. See PROJECT_STATUS.md.

> Latest owner follow-up, 2026-10-11: present Student Quiz attempt history as
> one visible summary with a native Attempt selector, newest first. Use existing
> authorized summaries and preserve no-JavaScript reading and result links.
> Verify source/templates/static assets and bounded manual presentation behavior,
> then deliver under the owner's existing GitHub/Railway authorization. No new
> automated test package, migration or domain/research policy change.

> Latest release authority, 2026-10-11: commit and push the reviewed platform
> navigation/Quiz/browser-draft/file-control follow-up to the existing GitHub
> branches, then deploy its exact SHA to the existing Railway web service.
> No login-session feature, schema/data change or unrelated local file is included.

> Latest owner follow-up, 2026-10-11: automatic-only Assignment drafts, compact
> Quiz overview/result cleanup and shared linked breadcrumbs beside Back.
> Review existing login expiry and recommend a policy; do not implement one
> before the owner chooses it. Continue local source/static/manual work only.

> Latest local scope, 2026-10-11: four-role Back navigation, Quiz disclosure
> removal, same-browser Assignment text drafts and localized file controls.
> The owner explicitly declines database changes for drafts. Keep final-submit
> contracts and existing data. Source/static/manual review only; no automated
> test package, migration, commit, push or deployment in this follow-up.

> Latest release authority, 2026-10-11: the owner now explicitly requests commit,
> push and deployment of the reviewed compatible UX implementation. Safely
> integrate the existing remote follow-up, deploy the exact verified GitHub SHA
> to the existing Railway web service and verify public readiness/assets. Preserve
> production data, volumes, variables and research boundaries. No schema change,
> Study, Version B or Baseline Freeze. See APPROVED_UX_RELEASE.md.

> Current authority, 2026-10-11: implement all remaining compatible approved
> A–M, N–U and corrected V–Z/W UX proposals in the current MAIN source. Preserve
> completed work and the selected dashboards/mobile navigation. Incremental
> actual-app verification uses only the existing owned disposable target.
> NO COMMIT, NO PUSH, NO DEPLOY, NO RAILWAY MODIFICATIONS, NO MIGRATION.
> This supersedes the historical release permissions below.

> Local compatible implementation and bounded actual-app review are complete;
> see [APPROVED_UX_PROPOSALS.md](APPROVED_UX_PROPOSALS.md) for original IDs,
> evidence and remaining PARTIAL acceptance boundaries. Source stays unstaged.

> Current release authority: final owner directive, 2026-10-10. Finish platform
> usage/context cleanup and exact Dark-logo recoloring, preserve prior density/A1
> changes, verify the actual four-role application using the owned local MySQL
> dataset, commit, safely fast-forward the existing release branch, push and
> deploy the verified revision to the existing Railway service. Preserve all
> production data/services/volumes/secrets. No Study, Version B, research policy
> change, new test package or new Baseline Freeze. Release evidence and limitations:
> [FINAL_UX_RELEASE.md](FINAL_UX_RELEASE.md).

> Current authority: final owner decision, 2026-10-10. Implement platform-wide
> content decluttering, removal of redundant introductory components and
> responsive density in the existing main project. Preserve Login, Messages,
> domain/security/research contracts and explicit Apply/Search/Save/Finalize.
> Implementation and bounded source-render evidence are recorded in
> [UX_DENSITY_IMPLEMENTATION.md](UX_DENSITY_IMPLEMENTATION.md). Authenticated
> runtime and expanded accessibility acceptance remain pending; no new freeze,
> Study activation, push or deployment is authorized.

> Earlier delivery authority: final owner approval, 2026-10-08. Execute W0 → W1 → W2 →
> W2-D02 → W3 → W4 → W5 → W6 → W7 → W8 → W9 under
> [VERSION_A_DELIVERY.md](VERSION_A_DELIVERY.md). The earlier W1 appearance and
> per-wave preview stops below are superseded. Preserve domain/research policy,
> the protected Login and the no-new-test-package verification boundary.

Owner-approved contract: [APPROVED_REPAIR_CONTRACT.md](APPROVED_REPAIR_CONTRACT.md).
Current checkpoint and evidence: [PROJECT_STATUS.md](PROJECT_STATUS.md).

This plan rehabilitates the existing Flask modular monolith in bounded Parts.
It does not authorize one bulk rewrite. Complete each Part's acceptance gate,
report the actual evidence and preserve unrelated work before proceeding.

## Current redesign waves - 2026-10-07

The owner approved the Design Proposal and Architecture Review, including
P01-P06, and authorized W0 followed by W1. Follow the dependency sequence and
exit gates in [VERSION_A_DESIGN_CONTRACT.md](VERSION_A_DESIGN_CONTRACT.md).
Do not treat the historical repair Parts below as permission to repeat database
or Git operations. W1 evidence and outstanding acceptance are recorded in
[VERSION_A_W1.md](VERSION_A_W1.md). Later waves remain dependent on foundation
stability; file-policy/migration and new-schema decisions gate only affected work.

## Latest owner close-out decision - 2026-10-06

The owner explicitly declined a new automated test package after requesting
deletion of the old tests. Do not recreate or run automated tests. Complete the
remaining legacy cleanup, bounded four-role manual browser review and static
source/template checks, and document their actual scope. This decision supersedes
the historical sequencing and replacement-coverage gates below. A missing full
regression run must not be described as a passed gate.

The development reset and fictional four-account seed were separately authorized
and completed after the in-place upgrade. The current schema remains `085b7a4e9012`;
all three demo Courses have prices. Historical migration files and compatibility
models remain to preserve the supported schema chain and scoped old bookmarks.
Retired runtime algorithms, forms and templates can be removed once their actual
consumers have been moved. No repeat reset, production deployment, scheduler
change or Git mutation is part of this follow-up.

## Historical owner sequencing change - 2026-10-06

The owner instructed all tests to stop and deferred further test execution until
all approved repair changes are implemented. The active P1F gate was interrupted
and its owned resources stopped. Continue P2-P8 and the remaining prepared P7A
source changes in bounded implementation steps, preserving the same domain and
security contract. Per-Part test gates, including P1F, are deferred to final
verification; implementation is not acceptance. Prepare/update meaningful tests
without executing them or invoking collection-only pytest. P9 live database,
scheduler, release and Git actions still require their concrete approved scope.

## Latest research resolution

## Earlier integration checkpoint - 2026-10-06

The owner additionally authorized finishing integration, remaining legacy entry
points and documentation, and applying the new database revisions. P2-P7 source
changes are present; P8 HTTP consumers now delegate to the new episode/general
account workflows. Private historical helpers and historical migrations are
retained until replacement acceptance, rather than deleted before coverage.
The database scope is an in-place upgrade of the inspected local development
database from `d574ab56594f` through `085b7a4e9012`, with a verified backup/restore
and isolated migration gates first. No reset, business seeding, scheduler change
or Git commit is included. Status and executed evidence are recorded separately
in `PROJECT_STATUS.md` and `REPAIR_INTEGRATION.md`.

On 2026-10-05 the owners chose password-only Researcher authentication and
15-day retention with daily expiry. Preserve the pre-existing shared login.
Earlier proposed MFA and pressure-only automatic purge work is removed from
this plan. Storage visibility, started-session range export, manual cleanup,
gap reporting and operator accountability remain in scope.

## P0 - Contract, recovery baseline and documentation

Scope: preserve a verified source baseline; record the final decisions;
update authority pointers; add README/Quick Start and a phase/status tracker.
No application/test/schema/env/scheduler changes, no installs or Git mutation.

Exit criteria:

- Approved/current/historical facts are clearly distinguished.
- MySQL-only target is documented without pretending SQLite is removed.
- Shared login, password-only and 15-day decisions agree across documents.
- Every accepted finance, enrollment, schedule, record and Activities rule
  appears in the contract, including transfer after destination study start.
- Source recovery archive is checked; local links and diff whitespace pass.
- Prior source/tests and deleted-file state are preserved byte-for-byte.

## P1 - Runtime entry and MySQL-only verification foundation

Scope: implement canonical `/` and unknown-environment rejection; create
an explicitly isolated MySQL test harness, then convert DB/migration/browser
fixtures and SQLite-specific runtime branches. Keep pure tests DB-free.
Split this phase into reviewed Parts; test-server provisioning/installations
need their concrete target and authorization before execution.

| Part | Bounded scope |
|---|---|
| P1A | Canonical root and fail-closed environment selection, with DB-free factory/identity checks |
| P1B | Owned disposable MySQL instance/lease infrastructure and pure target-guard checks |
| P1C | Config, central/local app fixtures and isolated per-app schema resources |
| P1D | Browser, committed snapshot/concurrency and CLI database resources |
| P1E | Migration probes through exact parent revisions, MySQL constraint assertions, verified historical downgrade corrections and a reviewed source revision for exact closed-code collations (synthetic execution only) |
| P1F | Fresh Alembic/model agreement, focused/full strict test and browser gates |

P1A through P1E are complete; P1F acceptance is deferred by owner instruction. See the status evidence
and the MySQL transition record before using any destructive test fixture.

Exit criteria:

- Anonymous and all-role root routing is correct; shared login stays intact.
- Misspelled environment fails closed; no debug fallback.
- DB tests refuse non-MySQL engines and non-isolated database targets.
- Fresh Alembic install and schema/model checks pass on isolated MySQL.
- Browser and snapshot/concurrency checks use MySQL; no silent DB-test skip.
- CSRF/rate limits/auth-version and workspace-isolation regressions pass.

## P2 - Accounts, eligibility, Rooms and schedules

Scope: stale-safe account/status writes; Student active-enrollment guard;
last-eligible-Teacher suspension guard; Room management and authoritative
Group/Teacher/Room schedule conflict checks.

Exit criteria:

- Competing edits cannot silently overwrite a later update.
- Account deactivation preserves history and respects active enrollments.
- Teacher suspension cannot bypass the replacement requirement.
- Room/Teacher effective-time overlap and concurrent writes are tested;
  adjacent slots remain valid. Historical schedules stay interpretable.

## P3 - Finance foundation and corrected/history views

Scope: Course price and invoice snapshots; LYD quantum/formatting/discount;
general collections, actual payouts, editable/deletable document revisions,
derived signed Student account and chronological correction reports.

Exit criteria:

- Discount amount/percentage, zero charge and rounding/display are correct.
- Collections are independent of invoice allocation; partial payments work.
- Pending/rejected money is excluded; credits and actual payouts balance.
- Editing obligations below money received creates valid credit.
- Document changes preserve evidence and do not erase older cash history.
- No mutable balance, ReceiptAllocation or installment table is introduced.
- Idempotency, numbering, stale forms and concurrent writes pass on MySQL.
- Existing payment provider security remains; real card processing is absent.

## P4 - Episode enrollment and atomic lifecycle operations

Scope: Enrollment episodes and Group membership history; atomic enrollment,
invoice/discount/optional collection; withdrawal/reversal; same-course transfer;
new episode on re-enrollment; actual wrong-Course/Group correction.

Exit criteria:

- Owner override, 2026-10-06: new/re-enrollment is permitted at/after Group
  study start with an informational notice. Wrong-course correction also
  allows a started destination; same-course transfer remains permitted.
- A repeated or failed operation creates no extra episode, liability or cash.
- Last-seat competition and transfer source/target locks are verified.
- Withdrawal releases a seat once; optional net-obligation reversal is audited;
  payout is a distinct real-money operation.
- Same-course transfer creates no charge; source history survives.
- Wrong-course correction changes real membership and financial effect while
  preserving original learning evidence and checking target eligibility.

## P5 - Academic episode ownership, corrections and fair deadlines

Scope: progress/attempt/submission/roster episode linkage; own historical
access; grade/attendance/feedback revisions; trusted request-arrival deadlines.

Exit criteria:

- Repeat enrollment cannot inherit prior attempts/submissions/completion.
- Transfer reports destination content and preserves source progress/history.
- Course Progress is never presented as mastery.
- Historical reads survive withdrawal/archive/account reactivation without
  granting write access or leaking another student's work/answer keys.
- Each correction has old/new/actor/server time and optional reason.
- Server processing/lock delay does not penalize on-time timed submissions;
  forged client clocks, replays and stale activity states remain protected.
- Include an on-time submission competing with a later observer that settles
  expiry. Capturing arrival time alone is insufficient if that observer can
  finalize the attempt first; preserve terminal-state and stale-state defenses
  while proving the fairness requirement with independent MySQL connections.

## P6 - Unified Activities and bounded Student reads

Scope: one Activities hub/navigation entry; type/state/Course filters;
action/deadline priority; all-four-type upcoming dashboard; episode history.
Keep type-specific execution pages, Course lessons and separate grade/attendance
records. Register route/element tracking deliberately under the safety contract.

Exit criteria:

- All four types appear only when authorized and available.
- Actions/states/deadlines and enrollment context are correct and bounded.
- Dashboard includes all types and navigation works on mobile/desktop.
- Existing activity rules, safe historical reads and research sampling/session
  meanings remain intact; coverage tests include the new hub.

## P7 - Research attribution, accountability and storage visibility

Prioritize F15 in a small independent Part after P1; it need not wait for
every finance/academic phase. The rest of this phase preserves Phase 6.

Historical P7A preparation boundary (now applied; current evidence in REPAIR_INTEGRATION.md):

- Issue an opaque purpose-specific delivery proof from the existing account
  public identity/authentication version and configuration public identity/
  immutable version number. Read these in the existing bounded preview query;
  do not store or export the proof or expose account identity to the collector.
- Require the proof in browser event batches and verify it against locked
  account/configuration rows before subject lookup/provisioning. Repeat that
  check on every provisioning retry. A stale/tampered scope is a permanent
  safe refusal, never a request to retry against the new scope.
- Preserve the original proof and event IDs through outbox retries. Discard
  other-scope pending data without crediting its dropped count to the current
  scope; late callbacks must not clear a newer scope's pending data.
- Keep the established session-reference resolution, clocks, event age,
  inactivity, replay rules, sampling, prompt display/answer and provenance
  behavior. Server-confirmed best-effort outcomes remain outside browser F15.
- Expected source boundary: collector hooks/routes, collection scope/ingestion,
  pure batch validation/top-level dictionary, a pure delivery-proof helper,
  collector JavaScript and their directly affected fixtures/regressions. F15
  uses existing identities and needs no schema change. Verify account/config
  switches, missing/forged proofs, lock-time changes, subject non-provisioning,
  unchanged replay IDs, privacy and real-browser delayed callbacks on MySQL.

Preparation evidence (not runtime acceptance): an ignored candidate preserves
all other top-level functions outside its declared changes in six existing Python
source files and adds one pure proof helper. Eight pure proof tests, thirteen pure
protocol/lock-order tests and ten JavaScript delivery simulations pass. Thirteen
owned MySQL checks also pass, including independent-connection account/configuration
changes while ingestion waits for locks. A real configuration race first exposed
opposed secondary-index/primary-key lock acquisition; the candidate now discovers
the current configuration ID without a lock, locks that primary row and rechecks
its liveness and proof before subject access. Four isolated actual-Chrome delivery
scenarios and 46 candidate-overlay regressions pass; the latter include six actual
LMS/Chrome checks and six export/snapshot checks. JavaScript syntax verification
also passes. No canonical runtime, test or migration source was changed for this
candidate during the immutable P1F full gate.

Two further integrated LMS/MySQL/Chrome candidate tests pass: actual browser
delivery is deferred until after an account-cookie or configuration switch;
the unchanged old batch is rejected and new-scope delivery is accepted with
correct subject/configuration rows. The browser harness's sign-in shortcut
disables CSRF; the separate owned transition gate verifies actual CSRF behavior.

Under the 2026-10-06 sequencing instruction, apply the bounded candidate, adapt valid test batches explicitly
to the new transport field and retain raw missing/forged-proof negative cases.
After all repair source changes are implemented, rerun the owned MySQL transition/locking and actual Chrome gates against the
applied source, including navigation/replay and delayed-callback scenarios.
Pure extracted-function checks prove control-flow order, not InnoDB blocking;
JavaScript simulations are not a substitute for the real browser gate.

Scope: original-scope browser delivery binding, safe configuration numbering,
trusted operator attribution, storage usage/gaps, session-start range export
and auditable manual raw-data cleanup. Preserve password-only/shared login,
15-day daily retention, population, sampling and all self-report semantics.

Exit criteria:

- Account/config switches cannot reattribute pending events; repeats keep
  IDs/idempotency. Browser and MySQL transition cases pass.
- No name/account/content/financial data leaks through research pages/exports.
- Action attribution covers workspace/operator/service paths.
- Usage and gap reporting are truthful; selected sessions keep full dependent
  windows. Cleanup leaves exclusion/link/audit metadata enforceable.
- Archive bytes/digest/snapshot/timezone and 410-after-removal remain correct.
- Daily 15-day expiry is tested in isolation; no pressure-only replacement
  or MFA requirement is introduced. Live task changes are separately scoped.
- No real collection period, model, binary labels or adaptation is invented.

## P8 - Legacy removal and service/query consolidation

Scope: convert remaining consumers then remove obsolete runtime for fee
plans/assignments, reactivated episodes and overwritten record history;
finish bounded reads and coherent operation services. SQLite removal belongs
to P1 and is audited here. Keep prototype isolated and migration history intact.

Exit criteria:

- No active route/template/import depends on an eliminated contract.
- KEEP/REWRITE/MERGE/DELETE tests follow approved behavior, not code mirroring.
- Old public links have intentional outcomes; no accidental dead navigation.
- Required focused/full strict gates pass without unexplained skips.
- Shared login, natural-use research and 15-day cleanup remain supported.

## P9 - Clean development database, recovery and release verification

Scope: provision the approved clean MySQL target; apply reviewed revisions
only to that target; optional fictional seed; database/private-file restore
test; all-role browser/security/operational verification. Live actions need
explicit target/revision approval; current test data being disposable does
not authorize indiscriminate deletion or copying credentials into reports.

Exit criteria:

- Fresh migration chain, enforced constraints/indexes and model agreement pass.
- No unexpected revision or manual data repair is silently accepted.
- Backup/restore is verified; source, DB and private-file state are compatible.
- Core operations, mobile/desktop flows and isolation pass against MySQL.
- Deployment settings, rate-limit/header topology and residual risks are
  documented with actual evidence. No unsupported production-readiness claim.
- Real research startup remains an explicit action outside synthetic checks.

## Common evidence and recovery gates

- Inspect pre-existing diff and exact expected files before each Part.
- Use focused tests first, then relevant regression/full strict gates where
  risk requires them. Report checks actually run separately from old results.
- Test MySQL locking with independent connections, not just asserted SQL.
- Review idempotency, consistent lock order, stale snapshots and rollback.
- Inspect migration DDL/constraints and data-loss effects before approval.
- Source archive is not a DB backup. Test restore before destructive DDL.
- Do not reset, stash, drop, install, register tasks, commit or push merely
  because it is the next item in a plan; obtain the action's concrete scope.
- If an acceptance gate fails, diagnose it without starting unrelated phases
  or reporting completion. Keep the tracker honest about remaining work.
