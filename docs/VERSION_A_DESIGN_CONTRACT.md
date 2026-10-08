# Version A design and implementation contract

> Active authority, 2026-10-08: [VERSION_A_DELIVERY.md](VERSION_A_DELIVERY.md).
> The final owner approval supersedes the appearance and operational gates below:
> Direction D + D-MOTION, protected Login, evolved Messages, full W0–W9 delivery,
> authorized development migrations/disposable data, acceptance/pilot and a
> justified freeze. The text below records the earlier W1 target, whose visual
> appearance was rejected; it must not guide the canonical redesign.

Owner approval: 2026-10-07. Status: approved target, implementation in waves.
This contract supersedes the earlier visual proposal and text-only assignment
recommendation. It does not supersede existing domain, security or research
policies unless an explicit decision below says so. Evidence of completed work
belongs in PROJECT_STATUS.md and VERSION_A_W1.md, not in this target contract.

## Owner decisions

| Decision | Approved boundary | Delivery |
| --- | --- | --- |
| P01 | Modern Academic Workspace; tangible partial redesign; shared tokens/components; strong typography, spacing, controlled depth, functional motion, responsive, accessible, light/dark and RTL presentation | W1 onward |
| P02 | Teacher-configured Text or Single-File Assignment. Preserve current text assignments; one file, protected review, ownership/enrollment binding and existing grading workflow | W2-D02 |
| P03 | Private storage, server-side extension/MIME/signature validation and authorized download. Optional comment only within the existing lifecycle. Reuse a suitable central file policy after inspection; otherwise obtain approval for a minimum format/size rule | W2-D02 gate |
| P04 | Existing research event types only. Page view, navigation, form attempt, browser validation, visibility/heartbeat and available confirmed outcomes. No new account or general-error event types | W7 |
| P05 | Search position becomes the global ordinal across the actual rendered result list. Preserve algorithm, ranking and grouping; document the semantic boundary and update the compatible dictionary before freeze | W7 |
| P06 | Keep the Flask modular monolith. Make only demonstrated, local refinements of ownership, shared helpers, projections, hooks, presentation/survey boundaries or bounded analytics queries | Relevant wave only |

No multi-file, draft, resubmission, new late rule or expanded submission lifecycle
is authorized. An assignment migration must be limited, justified and reviewed
before creation/application. This approval does not authorize database migration,
reset, seed, deployment, dependency installation, Git mutation or real study collection.

## Architecture and research invariants

Version A = shared Core LMS + shared Research Instrumentation.
Version B = the same Core LMS + the same Research Instrumentation + an optional,
isolated, separately approved Adaptive Intervention Layer. Do not duplicate the
application or scatter A/B conditions through routes, templates or services.
Version A is the best reasonable non-adaptive baseline, not a deliberately weaker product.

Preserve authorization, transactions, validation, enrollment/ownership binding,
route and form contracts, CSRF, signed state, quiz/listening save distinctions,
quiet Quiz success feedback, question position and grading/financial/attendance
meanings. Do not change sampling, prompt windows, rating, retention, session
meaning, study provenance, labels or event semantics without explicit approval.
P05 is the single approved event-field semantic change and is still pending W7.

Passwords, field values, raw input, file names, image/file contents and sensitive
personal values must never enter research metadata. Account coverage is minimal
and privacy-safe. No invented ML, A/B, research or operational analytics results.
Every rate needs a denominator; missing is not zero. Charts require scope, source,
time range and an accessible alternative. Use bounded server-side queries when needed.

Research coverage additions and stable identities must ship together with their
collector allowlists and dictionary in W7. Do not add unsupported hooks in W1.
Do not rename events to match historical MASTER_PROMPT.md terminology.

## Design system ownership

| Layer | Owns | Does not own |
| --- | --- | --- |
| tokens.css | Canonical --ui-* theme/semantic tokens, brand primitives, scales, motion and compatibility aliases | Page layouts or behavioral state |
| base.css | Shared typography, buttons, fields, choices/uploads, cards, badges, alerts, loading/empty states and focus | Domain forms or portal composition |
| navigation.css | Shared rail/header/account menu, context navigation, tabs, pagination and record presentation | Research identity or navigation behavior |
| workspace.css | Shared workspace compositions and responsive layouts | Global palettes or base component variants |
| appearance.css | Preferences/utility panels, RTL, print and explicitly labelled temporary feature-theme compatibility | Canonical theme definitions |
| figma.css | Temporary legacy/page compositions pending their owning wave | New theme tokens, shared shell or base component design |
| Feature CSS | Feature-specific internal layout/state | Redefining shared buttons, fields or canonical tokens |
| _figma_ui.html and existing JS | Existing server-rendered shared macros and native behavior/hook contracts | Business rules or adaptive triggers |

Remove only proven shared duplicates during W1. Retain legacy/page compatibility
until the affected page is migrated and checked. No bulk automatic CSS rewrite.
Preserve functional and research hooks; style classes are not research identities.

## Canonical visual and motion specification

Brand artwork and the centre's blue/red palette remain. Blue is the shared primary
action; red is a restrained brand detail and danger uses accessible semantic tokens.
Light: page #f5f7fb, surface #ffffff, ink #092969, text #31445f, muted #596b85.
Dark: page #0c1422, surface #162238, ink #e9f0ff, text #d3deef, muted #a8b8ce.
System theme works without JavaScript. Fonts are local system fonts; no external
font service or new dependency. H1 30-32 desktop/24-26 mobile; body 16; tables and
labels 14; metadata 12-13. Spacing 4/8/12/16/24/32/48, page 32/24/16.
Buttons/fields radius 10, cards 16, overlays 20; a 264 rail narrows to 248.
Student content max 1200; operational/research content max 1440. Reading width
remains a page-level concern. Aim for 44px controls, visible 3px contrasting focus,
native keyboard interactions and text/icon state cues. Decorative cards do not float.

Motion is fixed, non-adaptive and non-blocking: hover 120ms, press 80ms,
popover 140ms, modal 160ms, drawer 200ms; opacity/color/transform only where useful.
Respect system and explicit reduced motion. No fake loading, success confetti,
autonext, forced rating, behavioral spotlight or frustration-triggered assistance.
Login readiness and survey focus repairs belong to their owning waves.

## Wave sequence and exit gates

| Wave | Scope / owning areas | Main risk and verification | Exit |
| --- | --- | --- | --- |
| W0 | Approved contract, roadmap, status and decision authority | Historical policies mistaken for current approval; document cross-check | Decisions, scope and gates recorded |
| W1 | Shared tokens, base components, shell/navigation and bounded CSS ownership consolidation | Cascade, native forms, focus, RTL/dark and mobile navigation; static checks and bounded four-role manual review | Foundation source verified; visual/manual gates recorded before dependent work |
| W2 | Student critical journeys: Login, Dashboard, Courses/Lesson, Assignment, Quiz, Search; W2-D02 file-policy and migration review gate | Preserve save/order/ownership and search behavior; focused manual workflow and privacy checks | Critical baseline accepted; gated file work remains isolated if pending |
| W3 | Remaining Student surfaces including Account, Materials, Speaking, Listening, Messages, Notifications, Calendar, Grades and help | Natural-use coverage, recorder/uploads, keyboard and mobile; complete page inventory | Every meaningful surface reviewed; coverage proposals ready for W7 |
| W4 | Teacher portal and useful progress/work/attendance/grade visuals | Permissions, grading and released-state meanings; verified data projections and manual role flows | Teacher acceptance; no invented analytics |
| W5 | Admin operations and justified attendance/schedule/enrollment/finance visuals | Financial and enrollment semantics; bounded queries and cross-role operations review | Admin acceptance and sourced/defined metrics |
| W6 | Researcher analytics, coverage, missing-data and quality views | Missing vs zero, denominators, source/time scope, bounded queries and exports | Researcher acceptance with truthful data provenance |
| W7 | Approved tracking scope/dictionary, Courses/Account, Activities/search mapping, global search position, Quiz IDs/ordering, autosave, final burst, survey focus | Stable IDs and event meaning/delivery; manual instrumentation and export verification | Same sufficient instrumentation for A/B; unresolved schema decisions approved first |
| W8 | Responsive/a11y/cross-role acceptance, separate pilot, delivery/export verification and baseline identifier | Confusing pilot with study; documented evidence and owner freeze decision | Documented Baseline Freeze; no automatic real collection |

Do not advance a dependent wave before its prerequisite gate is stable. A new
research/schema, sampling/label, A/B, workflow, database lifecycle, security,
finance/grading or file-lifecycle decision stops only the dependent portion.
Continue authorized independent work. No new automated test package or test
execution: the owner's 2026-10-06 instruction remains in force. Report static
and manual evidence honestly, with outstanding runtime checks explicit.

Freeze additionally requires final approved UI/tracking scope, stable event
dictionary/IDs, responsive/accessibility/cross-role acceptance, instrumentation,
separate pilot, delivery and export checks, UI baseline version and documented
owner decision. Material experimental-UI changes after freeze require a change
record, version and research-impact evaluation. Completion of a visual wave
does not authorize study collection.
