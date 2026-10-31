# Shared workspace navigation and Theme - 2026-10-30

## Authority and checkpoint

The owner's current request authorizes local interface corrections for Admin,
Student, Teacher and Researcher, on desktop and phone. It supersedes the earlier
instruction to keep the Atlas navigation shell unchanged. Preserve existing
Finance/Research work and all application data. The inspected HEAD is 4544d56;
the worktree already contains unrelated tracked and untracked changes. No
deployment, Git mutation, migration or automated application test is included.

## Source changes

### Owner clarification: main Admin destinations only

The follow-up request keeps only Overview, People, Groups, Calendar, Academic
setup, Attendance & grades, Communication, Finance and Financial reports in
the Admin rail. The shared navigation partial now renders the existing Atlas
main-destination macro using Student-style links. Subpages such as Students,
Teachers, Rooms, Invoices and Notifications stay inside their sections, with
the existing top-bar notification access preserved. The phone More panel uses
the same main destinations, omitting sections already in the bottom bar.
Active selection uses the containing section, so Teachers highlights People.

This follow-up changes only `layouts/_product_navigation.html` and
`admin/_atlas_navigation.html`, plus this receipt. Both templates passed Jinja
syntax review. Manual fictional/no-DB browser review confirmed the nine desktop
destinations, the Students/Teachers inner tabs, People selected on Teachers,
and the five remaining main sections in phone More at 390 x 844. No automated
test, business view, database, migration, deployment or Git mutation ran.
The owned preview renderer was stopped again after this review. Screenshots:
`admin-main-sections-desktop.jpg` and `admin-main-sections-phone.jpg` in the
ignored evidence directory described below.

### Initial shared shell work

- All role shells include the same header and sidebar composition. Admin uses
  the Student rail geometry and the existing server-owned Admin destinations,
  including the same destinations in phone More. Remove the former Admin
  Help, Settings and My account rail shortcuts and the separate theme button.
  Keep search, its keyboard shortcut, notifications and account functions.
- Remove header hierarchy text and the shared page breadcrumb trails. Page
  titles, object tools, functional section tabs and learning timers remain.
- The account menu contains Account settings, Language, an inline Theme
  dropdown and Sign out. Remove Help from that menu. Theme offers device,
  light and dark choices; its icon uses the resolved theme, including device
  changes. Existing preference cookies and the native CSRF-protected POST
  fallback remain. The enhanced Theme choices apply without page navigation.
- Account menus scroll within the available phone height above the bottom
  navigation. The same markup supports English and Arabic/RTL.
- Shared Back remains an ordinary link so existing asynchronous save and
  unsaved-work guards own navigation. The server supplies a registered parent
  fallback and validates any origin as a same-origin, same-role GET page.
  Notifications are included among the shared authorized page destinations.
- The enhancement keeps at most 24 visited page paths, including list queries,
  in browser history state. A one-use sessionStorage bridge is accepted for
  two minutes, only with a matching server-validated origin and opaque
  account/auth-version/role scope. Returning to an ancestor consumes that
  ancestor and its descendants, preventing repeated Back loops. Reload and
  browser Back/Forward preserve the entry's trail. Sign out clears the current
  trail/bridge. Direct entries, unavailable storage and unverified transitions
  use the native parent fallback. No form values or page content are retained;
  no history is sent to the server or collector.

Primary files: `app/blueprints/workspace.py`,
`app/static/js/workspace_back.js`, `app/static/js/appearance.js`,
`app/static/js/figma_ui.js`, `app/templates/_figma_ui.html`,
`app/templates/layouts/_workspace_{header,sidebar,back}.html`, the two shell
templates, their layout asset references, `appearance/_panels.html`, the
navigation partials, the shell/appearance/Admin styles and `app/locales/ar.json`.

## Verification actually performed

Static review: Python AST and Arabic JSON parsing; changed Jinja syntax;
`node --check` for the three changed/new JavaScript files. All passed.
The repository contains CRLF blobs that plain `git diff --check` reports as
trailing whitespace. Preserving those existing file endings,
`git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check`
passed for both the scoped changes and the full tracked worktree. No Git
configuration was changed. The staged file list is empty.

Manual browser review used the actual templates, localization extension,
registered route map and static assets through an owned loopback renderer.
It uses fictional in-memory display context and does not initialize the
database extension, authenticate real accounts or run actual business views.
This is presentation evidence, not database-backed application acceptance.

Observed in the browser:

- Admin desktop at 1440 x 1000: Student-style scrollable rail, shared header,
  missing old rail shortcuts/top theme button, no breadcrumbs, and Theme
  dropdown with working light/dark choices and matching sun/moon icons.
- Phone at 390 x 844: all four role shells, account Theme choices, Admin More
  destinations and the existing bottom navigation. Device mode resolved to
  the current light device theme. No operating-system theme was changed.
- Arabic/RTL: translated Theme choices, correct direction and Admin rail.
  At 320 x 568, the menu has automatic internal scrolling; its measured bottom
  was 487px and the bottom dock began at 496px. No horizontal page overflow.
- Admin: dashboard -> filtered/page-2 list -> detail -> nested page -> Back
  detail -> Back original filtered/page-2 list -> dashboard destination.
  Reloading the detail retained the correct origin. No Back loop occurred.
- Researcher: filtered list/detail followed by browser Back and Forward;
  the workspace Back destination remained correct for each restored page.
- An edited fictional form triggered the ordinary unsaved-work confirmation.
  Cancelling kept the page and entered text. Escape closed Theme first, then
  the account menu. The Language preferences POST returned the Arabic view.

The temporary renderer and evidence are ignored under
`instance/ui_shell_review_20261030/`. Desktop and phone screenshots are saved
there. The owned renderer is stopped at close-out.

## Boundaries and stop point

No automated tests, pytest collection or test supervisor were created/run.
No actual MySQL read/write, business action, migration, reset, seed, scheduler,
dependency installation, secret/configuration change, Git staging/commit/push
or deployment was performed. Research collection/sampling/retention, domain
authorization and Finance reader navigation remain with their existing owners.

These source changes are complete locally. Actual authenticated workflows,
autosave/recording with real data, physical Android/iPhone devices and hosted
acceptance were not exercised. Publishing the changes is a separate operation.
