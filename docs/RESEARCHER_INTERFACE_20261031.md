# Researcher interface implementation — 2026-10-31

## Authority and boundary

The owner requested implementation of the approved Figma Researcher concept,
with the platform's light palette and actual Youth Centre logo, no AURA branding
inside the interface and no motivational copy. The owner explicitly retained
the existing Figma filename. No Figma file rename was performed.

This is a local presentation change. Existing uncommitted work was preserved.
It does not authorize deployment, migrations, database access/reset/seeding,
scheduler changes, new automated tests, staging, commits or pushes.

## Design source and implementation

- Figma file: `bdCGkAftbo8eTQ5QAVzv4o`; desktop overview `2:39`, phone `2:50`.
  Design contexts were read for the overview, sessions/detail, quality,
  configuration, export preparation/review/archive, storage, audit and groups.
- Research-only shell, navigation and primitives:
  `app/templates/research/_shell.html`, `_navigation.html`, `_ui.html` and
  `app/templates/layouts/research_base.html`.
- Scoped presentation: `app/static/css/research_interface.css` and
  `app/static/js/research_interface.js`. Other role shells/styles were not edited.
- Updated the existing Researcher dashboard, sessions/detail, quality/groups,
  configuration list/detail/form, exports/list/create/detail/unavailable,
  storage, audit and exclusions templates. Existing detailed analytics remain.
- Reused `app/static/images/figma_logo.png`, the existing local font files and
  exact shared SVG icon geometry. Downloaded the four supplied Figma ambient
  SVGs into `app/static/images/research/`; no expiring asset URL is shipped.
- Added 203 Arabic catalog phrases without replacing existing translations.

The workspace remains light even when the device/account preference is dark.
Scoped root paint, native control color scheme and shared surface tokens prevent
dark colors leaking into headers, menus or notification controls. Preferences
and other roles are not rewritten. Arabic direction and typography are supported.

Desktop uses the 220px rail and translucent pale cards. Phone uses the bottom
navigation, a native More panel, session cards, collapsible filters and visible
participant assignment controls. Account, language and sign-out remain reachable
on phone. Native navigation is also available without scripting.

## Workflow and data integrity

No Figma sample counts, observations or participants were inserted into runtime
templates or application data. Overview counters and the daily chart use the
existing v1 query/analytics results, with explicit v1/source labels and empty
states. Actual v2 technical/development reports remain separate. Report scope
and cutoff are visible; raw responses are not relabelled as eligible training
examples. Missing ratings remain unlabeled, and reserved-final-test answers stay
hidden.

The v2 GET view uses `?view=groups` for participant assignments. Its two existing
POST redirects now return to that view. No authorization, validation, locking,
signed state, CSRF or assignment logic was changed. Assignment control IDs are
unique while their native field names are preserved.
`v2.py` was already untracked before this request; its pre-existing business
implementation is preserved. The export source was also already modified; this
follow-up only adds preservation of the archive presentation parameter.

Export preparation now proceeds through selection, server preview and creation.
Editing a reviewed scope hides the previous preview/create control until another
review. This is a client convenience guard: authoritative creation still validates
and takes a fresh server snapshot. Manifest details/copying and the original
download links remain. Archive pagination preserves `view=archive`; the archive
and export center intentionally use the same existing server-scoped records.

Storage retains its existing signed cleanup preview, explicit checkbox, native
POST actions and shared destructive-action confirmation. Its counts still cover
all retained sources. No cleanup, archive removal or real download was executed.

## Motion and subordinate navigation

Figma supplied native opacity/translation keyframes for the desktop and phone
overview groups: a 14px entrance, approximately 550ms per group, staggered by
80ms on the supplied 6-second timeline. These values are factored into one CSS
animation. The operational UI plays the entrance once; it does not repeatedly
hide data on the prototype's looping timeline. System reduced motion and the
existing device motion preference disable it.

The ten other queried desktop frames returned no native keyframe nodes; no
additional motion was fabricated for them. Subpages use native authorized URLs.
Feedback-window and manifest dialogs use real recorded values, native modal
focus behavior, Escape/backdrop dismissal and focus restoration. More/search,
notifications, dirty-form guards and destructive confirmations reuse the existing
platform helpers.

Figma MCP's prototype-transition limitation applies:

> Prototype interactions aren't currently supported in MCP. Keyframes on the canvas are visible, but not the smart animate interactions between frames or their transition settings. Code generated from this selection will represent the start and end states, not the transition between them.

Accordingly, functional subordinate navigation is implemented, but exact Smart
Animate parity between prototype frames is not claimed.

## Verification actually performed

- Parsed all 20 Researcher/layout templates through the actual localization
  extension, and parsed the two touched Python sources with `ast.parse`.
- `node --check app/static/js/research_interface.js` completed successfully.
- Parsed the four supplied SVGs as XML; no scripts, embedded raster images or
  foreign objects. Runtime source scan found no AURA, fictional fixture strings
  or expiring Figma asset URLs in the Researcher templates/CSS/JS.
- Verified the additive catalog against the pre-task working copy: no existing
  translation changed. No CSS parser/dependency was installed; browser CSSOM and
  computed style inspection were used.
- Manual browser review used the actual templates/forms in an ignored, owned
  loopback presentation renderer at `instance/research_interface_20261031/`.
  It does not call `create_app` or `db.init_app`, and replaces every business view before serving.
  All displayed records are explicitly fictional. This adds no demonstration
  option or fixture to the application runtime.
- Reviewed desktop 1440×1000 and phone 390×844, overview populated/empty states,
  sessions/cards/detail, feedback dialog, quality/groups, configuration views,
  exports/review/manifest, storage preview/cancel and audit. Checked focus return,
  Escape dismissal, native scope-review POST presentation and hiding the stale
  create button after a scope edit. No create/delete/assign/download business
  operation was performed.
- Also reviewed the single-day event-count presentation, the closed v2 release
  gate, configuration field-error focus, and the existing search/notification
  panels, including Ctrl+K search and Escape dismissal. A 320px viewport check
  also had no horizontal page overflow.
- Arabic RTL, a nonpersistent dark-preference fixture and reduced-motion fixture
  were reviewed. The canvas/hero/header remained light; the root control color
  scheme was light; the overview animation was disabled. Phone page width had
  no horizontal overflow; wide evidence tables retain bounded internal scrolling.
- Repository whitespace review uses the established CRLF-aware invocation:
  `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check`.
  It returned exit 0; Git emitted only its existing LF/CRLF conversion notices.
  The staged file list was empty.

No automated tests, pytest collection, real authentication/role checks, MySQL
queries, real export generation, retention execution or hosted browser review
were run. Manual presentation review is not automated regression or end-to-end
database acceptance. No migration, real-database, scheduler, deployment or Git
mutation was performed. The local implementation is the stop point.
