# Version A — W0 / W1 delivery and acceptance

> Historical 2026-10-07 checkpoint. Its visual appearance was rejected and
> replaced by Direction D + D-MOTION under the final 2026-10-08 owner authority.
> Current delivery, acceptance and freeze: VERSION_A_DELIVERY.md,
> VERSION_A_ACCEPTANCE.md and VERSION_A_BASELINE_FREEZE.md. Do not deploy or
> assess the current source against this earlier appearance/status.

Date: 2026-10-07. Approved target:
[VERSION_A_DESIGN_CONTRACT.md](VERSION_A_DESIGN_CONTRACT.md).

W0 is complete in documentation. W1 is implemented in source with bounded
static and isolated browser evidence. Full application/workflow acceptance
remains pending; the W1 exit gate is not declared complete. W2 has not started.
This is not a Baseline Freeze or authorization for real study collection.

## Delivered

- Canonical light/dark/system semantic roles in tokens.css; existing --color-*,
  --figma-* and navigation/message aliases preserve compatibility. Contrast-aware
  control borders/focus, consistent type/spacing/radius/elevation/motion scales.
- Base components: 44px buttons/fields, native choices/select/upload controls,
  themed status/alert/empty/loading surfaces, visible focus and disabled/error
  styling. Cards no longer gain elevation merely because they are hovered.
- Shared four-role shell: stronger page title, 16px body, 14px actions/records,
  264/248px rail, Student max 1200 and operational/research max 1440, mobile
  drawer with existing keyboard/inert behavior and no-JS navigation fallback.
- Shared account/context/tabs/records use semantic theme tokens. Logical layout
  and existing RTL mirroring remain. Fixed opacity feedback for account/utility
  panels; native controls remain immediately available. Removed decorative
  viewport reveals/stagger from shared content; no timing gates were added.
- Removed superseded shell/base declarations from figma.css and workspace.css
  and proven shared/theme duplicates from appearance.css. Feature-specific and
  legacy theme compatibility remains for the owning page waves. This is a bounded
  consolidation, not a claim that all inline/local/legacy CSS has been removed.
- Corrected both existing shared-help descriptions and the Arabic translation:
  Quiz autosaves; Listening retains explicit Save answer / Save and next behavior.

## Exact implementation scope

Six CSS files: tokens.css, base.css, navigation.css, workspace.css, figma.css,
appearance.css. One shared presentation client: figma_ui.js (only decorative
viewport reveal removal). Two help templates: appearance/_panels.html and
workspace/help.html (copy only), plus the matching key in locales/ar.json.

Contract/plan/decision/status documentation was updated. No role layout, macro,
route, form/model/service, database, dependency, research collector/dictionary,
Quiz client or activity workflow was changed by this delivery. The pre-existing
dirty working tree was preserved; the changed-source inventory was compared to
the in-memory start-of-wave snapshot rather than attributed from Git HEAD.

## Evidence actually obtained

| Review | Evidence | Limit |
| --- | --- | --- |
| Source syntax | 197 Jinja templates parsed; 297 Python files AST-parsed without app import; Arabic JSON parsed; node --check on figma_ui.js | Syntax review, not automated regression tests |
| Role shell rendering | Actual Student, Teacher, Admin and Researcher layouts/macros rendered with an isolated Jinja context | Placeholder component content; no application/database or operational metrics |
| Desktop presentation | 1440px previews: body 16px, H1 32px, actions 14px, rail 264px; no document horizontal overflow across four roles | Specific previews, not every page or breakpoint |
| Narrow presentation | 390px Student light and dark/RTL; no document horizontal overflow | RTL geometry reviewed with English sample copy; full Arabic runtime translation not accepted here |
| Mobile drawer | Closed rail inert; open dialog/modal semantics, content inert, focus on Close navigation; Escape restores Open navigation focus | Existing client exercised only in isolated shell |
| Shared menus | Account Escape restores summary focus; Display opens native popover with language focus | No preferences POST, sign-out or authentication execution |
| No JavaScript | 390px navigation remains visible above content, relative rail, column shell, no overflow | Isolated shell only |
| Focus/contrast | Dark input focus 3px #91b6ff; text/surface contrast 5.43 light muted / 7.89 dark muted; control border/surface 3.16 / 4.18; link/focus surface 8.03 / 7.83 | Mathematical token checks, not a complete accessibility acceptance |
| Browser diagnostics | Final isolated review: zero JavaScript errors; research collector absent | Does not verify real research event delivery |
| Repository hygiene | Bundled Git diff --check completed successfully; no staging/commit/reset | Existing CRLF notices and unrelated dirty changes remain |

The normal computer-use browser entry point was unavailable. Sandboxed Edge
could not launch reliably, and the first separate browser could not reach the
sandboxed preview listener. The successful review used self-contained temporary
HTML with embedded existing assets, original deferred script ordering, and a
fresh headless Edge profile. No app server, credentials or database was loaded.
Browser review artifacts are outside the repository, in the task's visualization
workspace under w1-foundation; they are interface samples, not research data.

No automated test package, pytest run, migration creation/application, install,
database reset/seed, Git mutation, scheduler change, Version B implementation
or real collection was performed.

## W1 exit still requires

- Bounded manual review of actual four-role pages, including Login/errors,
  activity forms, Quiz/Listening/Speaking, records, Messages/Notifications and
  Researcher screens against the shared cascade. Verify actual content lengths,
  validation, native POST ownership and signed/CSRF state.
- Intermediate widths, 320px, zoom, keyboard order, screen-reader landmarks,
  native control/error announcements, forced colors and preference transitions.
  Check both translated Arabic and English, explicit/system themes and reduced
  motion. Isolated preview success is not equivalent to these acceptance gates.
- Confirm page-specific styles do not reintroduce shared component overrides;
  any required correction stays in W1 scope. Do not begin dependent W2 changes
  until the foundation gate is stable.

W2-D02 will inspect file policy and prepare its minimum product/migration review
if needed. W7 will implement the approved tracking scope and P05 position meaning;
neither is silently included in this foundation delivery. W8 retains all pilot,
delivery/export and owner freeze gates.
