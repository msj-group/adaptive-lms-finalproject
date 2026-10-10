# Final platform UX release candidate — 2026-10-10

The owner explicitly authorized implementation, actual-app verification, commits,
safe integration, official GitHub push and deployment to the existing Railway
web service. Earlier no-release instructions are historical for this scope.

## Source and changes

Initial main branch: `ui/platform-unification-20261009`; HEAD
`d9c7e1bef63800d8500d4bdd865fe4c93a0fcbff`. The existing density implementation,
Arabic additions and eight A1 presentation corrections were preserved.

Shared `_workspace_context.html` and `activities/_context.html` now retain only
legitimate filtered return controls. Academic breadcrumb/caption/intro panels are
removed. `_workspace_navigation.html` preserves actual Teacher/Admin object tools
after content. `_product_shell.html` renders one owned heading and removes the
duplicated desktop title outside protected Messages. Page-specific obsolete
guidance, upper context and empty wrappers were removed throughout four roles.
Minimum group/term/student/account identity stays next to real operations.
Authored educational content, availability/finality, draft/release distinctions,
financial consequences and research provenance/bounds remain.

`figma_logo.png` is byte-identical. The new `figma_logo_dark.png` changes only
220,136 visible blue pixels to RGB `#FFFFFF`, retaining every alpha and every
non-blue RGBA value at the original 1254×1254 geometry. Existing theme state
selects the asset in shared sidebar/mobile logo placements; Login is unchanged.

Confirmed acceptance issues received small corrections: the logo wrapper uses
block flex layout to preserve the original mobile header line box; workspace context
supplies the academic-term name for Quiz/Listening display; More focus handling
exempts its dismissal control from premature focus-leave closure so outside-click
dismissal can return focus. Routes, business rules, models, migrations, Event
Dictionary, collector and Quiz autosave implementation are unchanged.

## Actual application evidence

Current real Flask Development factory, `TESTING=False`, CSRF enabled, Python
3.14.6 and MySQL 8.0.46 run against the previously authorized owned synthetic
schema, verified by loopback port, datadir and server UUID. Four roles authenticate
through the ordinary Login form using private newly generated credentials.
No pre-existing local or Railway database is used for these QA writes.

Seventeen page families cover 320, 360, 390, 430, 540, 768, 900, 1024, 1200,
1440 CSS pixels in English LTR/Arabic RTL and Light/Dark (680 core captures).
Affected families receive fresh-load regression captures after later corrections.
The route review includes normal, empty and long authored-content states.
Quiz Start/autosave/reload, Listening explicit Save/reload, Teacher Quiz draft
validation/Save, Admin financial-impact preview and Research draft validation/Save
are exercised in this round. Previous A1 lesson, assignment/file, speaking-upload,
feedback, attendance/gradebook drafts and operational export evidence remains
historical evidence for the prior source, with presentation contracts preserved.

More keyboard Tab/Shift+Tab, Escape, outside dismissal, focus return, role links,
Compact density, reduced motion and browser accessibility-tree names are checked.
Explicit Light/Dark and System Light/Dark with preference reload are observed for
four roles at mobile/desktop. Protected Login/Messages comparisons are captured
separately; empty Messages is the actual dataset state, not a fabricated thread.

## Limits and research boundary

Overall broad A1 acceptance remains **PARTIAL**, not production readiness or WCAG
certification. Physical microphone/device touch/safe areas, real screen readers,
native 200%/400% zoom, populated Messages and final academic or populated financial
transactions remain unverified. No new automated test package or pytest run.
Sensitive production paths are read-only; production credentials are unavailable
for postdeploy four-role smoke and no historical shared passwords are reused.

No research target IDs are removed/added by this final cleanup. Stable IDs do not
make exposure/scroll measurements equivalent: content removal and changed geometry
require a new separately approved baseline before any scientific study. Event
dictionary, sampling, timing, provenance and session separation remain unchanged.
`RESEARCH_DATA_PROVENANCE=development`, `RAILWAY_ALLOW_STUDY=0`; the owned A1
Development rehearsal remains paused. No Study/Version B/Baseline Freeze.

## Release gate and external evidence

The clean existing release worktree can fast-forward to the reviewed development
branch without dropping earlier deployment fixes; no divergent source was found.
No new migration is included; existing predeploy checks use the same schema head
`7d4e2a9c6013`. Preserve MySQL, both active volumes, secrets, domain, Railpack and
Python/Gunicorn startup. Verify exact remote/deployed commit, build/predeploy/startup
and `/health/ready` plus public static assets before reporting deployment success.

Detailed revision/deployment IDs and final status belong to the external owner
report because they can only be known after this candidate is committed/deployed.
Evidence directory:
`C:\Users\abdul\.codex\visualizations\2026\10\10\01a12430-b1a2-7e62-a185-8944e4c04d3c\final-cleanup-release`.
Every browser capture is explicitly actual authenticated local application,
public local Login, or a labelled earlier reference. No mock is live-app proof.
