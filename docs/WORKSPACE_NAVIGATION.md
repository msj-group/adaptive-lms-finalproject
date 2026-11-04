# Workspace navigation

Authenticated workspaces use `workspace_navigation.js` to render authorized Flask
responses in the current document. Routes, role checks, CSRF, validation and POST
redirects remain server-owned. HTML responses are fetched afresh on each visit.
The browser's Navigation API owns URLs, redirects, Back and Forward.

`page_scope.js` and `workspace_navigation.js` are persistent scripts marked with
`data-app-runtime` in the base layout. Other executable page scripts run in server
order after the incoming styles are ready. A module's initializer is cached by its
script URL and rerun for each view, rather than downloading the script on every
visit. Increment a script's URL version when changing deployed code.

## Page resources

Page scripts register an initializer using this pattern:

```js
window.aelmsModules.register(document.currentScript, function (pageScope, pageScript) {
  const root = document.querySelector('[data-feature]');
  if (!root) return;
  pageScope.listen(window, 'resize', () => update(root));
});
```

Use the scope's `listen`, `fetch`, timer, animation-frame and `createObserver`
methods for resources that can outlive the page DOM. `pageScript` provides the
current script element's configuration attributes on both initial load and
subsequent visits. Keep ordinary local feature state inside the initializer.
Avoid unowned window/document listeners and global declarations in inline code.

After a successful response is prepared, navigation dispatches `pagehide` with
the outgoing URL still current. Existing recorder cleanup and final collector
delivery run before the old page scope is disposed. The new view receives
`DOMContentLoaded`, `load`, `pageshow` and then `aelms:page-ready`. Resource scopes
cancel requests, listeners, timers and observers from the outgoing view. Final
collector requests with `keepalive` are allowed to finish.

Scripts that update native history during initialization must defer that update
with `pageScope.setTimeout(..., 0)` until the containing navigation has finished.
For scroll/back metadata, `navigation.updateCurrentEntry` avoids starting a
competing navigation. Existing feature-specific asynchronous clients keep control
of clicks and submissions that they already handle.

## Page headings and filters

The Student, Teacher and Administrator header contains one compact page title
beside Back. `workspace_header.js` runs after feature initialization and uses the
template's title block as the page name. The first content heading and its
introductory copy no longer reserve a separate row. Secondary section headings,
activity content, dialog headings, notices and action controls retain their owners.
Focused curriculum editors and the Researcher workspace keep their own layouts.

The shared header's Filters button opens the existing filter panel as a native
popover. The panel stays in its original DOM tree, including inside any enclosing
GET form. This preserves form association, dependent selects, delegated events,
live query clients and collection hooks. Original disclosure/control state stays
synchronized with outside dismissal and Escape. Existing clients can still find
their original controls. Browsers without popover support retain those controls.
The panel is positioned below the header button and constrained to the viewport.

Search and notification controls in the top bar are rendered only for the exact
main dashboard endpoint of each role, including Researcher. Account controls
remain available on every page. Search routes and section-specific search fields
remain available independently of the dashboard opener.

Keep a page's `title` block descriptive. Page chrome should mark its primary
heading with `data-workspace-page-heading`; avoid using that marker for prose or
student-authored material. The shared header observes partial view changes without
reinitializing feature clients. Its listeners and observer use the page scope.

Bounded Chrome checks covered 26 sections across the three roles at desktop and
390/320-pixel phone widths, with real filter opening/dismissal. Focused checks
also covered GET submission, live filter counts, dependent form ownership,
English and RTL positioning, dark appearance and an unaffected creation dialog.

## Phone composition

`mobile_workspace.css` loads after the shared and feature styles. Its rules apply
only below 768 pixels: consistent touch targets, single-column forms, intentional
primary/secondary action groups, horizontally scrollable section tabs, readable
course covers and a two-column More menu. Teacher group cards place their primary
workspace action above three equal destination tiles. Desktop compositions retain
their existing feature layouts.

The Finance directory starts with account selection on phones instead of opening
the first reader automatically. Selecting an account displays its existing scoped
reader; the reader's Back button restores the directory, focus and scroll. Above
900 pixels the existing automatic first-account reader is retained. Financial
calculations, form validation and write policies are unchanged.

Phone review used the existing isolated fictional accounts in Chrome 157. It
covered 49 main sections across all four roles at 390 pixels, with additional
320/430-pixel touch, dashboard-only tool, More menu, RTL/dark and desktop header
checks. Focused read-only visits covered representative group/course details and
creation forms, plus Finance account selection and return. No business records
were submitted. The fixture has no rendered PDF images; those checks establish
editor layout and controls, not PDF rendering or every business workflow.

## Footer and timezone presentation

Role-attribution footers such as Youth Centre / Student portal and their Admin,
Teacher and Researcher equivalents are omitted. The sign-in copyright caption
is also omitted. Help/support links and Researcher data-scope notices retain
their existing functions and meaning. Empty attribution-only footers are removed.

Named timezone badges and center-time explanatory captions are omitted from
dashboards, calendars, schedules, activity/discussion views and date-entry forms,
including hover text and localized variants. Dates, time controls and calendar
range summaries remain. Server timezone configuration, conversions, scheduling,
publication and stored/exported research protocol values are unchanged.

Bounded Chrome review covered 49 sections plus the four role dashboards in
English, followed by affected Arabic dashboard/calendar/form views for every
role. Template syntax and new translation entries were checked separately.
This presentation follow-up applies no migration or real business-data change.

## Boundaries

The enhanced path requires Navigation API precommit support. Browsers without it
retain native server navigation. Explicit reload, authentication boundaries,
external links and file/media downloads keep browser navigation. Authentication
scope changes cause a full visit so another account's interface is never reused.
Unsaved form/recording guards remain active. A failed precommit request keeps the
current view and URL and offers a centered error message without automatic retries.

## Verification on 2026-11-04

Bounded browser checks used Chrome 157 and the existing isolated MySQL review
fixture with fictional accounts. Checked admin, teacher, student and researcher
section navigation within one document, Back, full-page form validation and save
redirects, unsaved-form cancellation, repeated initialization, interrupted script
loading, offline navigation/recovery, PDF preview/edit page retention and the
academic navigation layout at phone width. JavaScript syntax and template
compilation were checked separately. No replacement automated test package was
created. Real study collection and every business workflow were not rehearsed.
