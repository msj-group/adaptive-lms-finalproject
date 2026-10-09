# Workspace search and notification follow-up — 2026-10-09

The owner approved whole-card notification activation, Mark all as read in the
bell preview, removal of the transient sidebar during navigation, live popup
search for every role, and moving the header's language/theme preferences to
More. This supersedes the historical Student-only/non-live global-search scope;
the existing Student search page and its filters remain available unchanged.

Implementation is in MAIN:
`C:\Users\abdul\Desktop\GraduationProject\AdaptiveEnglishLMS`, branch
`ui/platform-unification-20261009`. Starting HEAD for this follow-up was
`b6d10d3c3e064f0350054986dbf86d4d69880b28`. Remote ST2 and the retained Railway
worktree were rechecked at `113e124e2b9b06daf8162f8f6603b94a369156ad`; remote
master remained `99133899529376436f52c292df09f5894e9e8cc1`.

## Resulting behavior

- The existing notification Open submit button has a hit area covering the
  complete card. Clicking its title, message, icon, date or blank card area
  submits the same recipient-owned CSRF-protected POST. Its accessible name
  identifies the action and title, and keyboard focus outlines the card.
  Mark read retains its independent action above that hit area. Stored target
  paths remain absent from HTML; current target validation/authorization remains
  authoritative at the server.
- The bell preview contains Mark all as read using the existing protected
  read-all form. It is disabled when the header has zero unread updates. Native
  submission retains the existing read-all behavior and redirect to the inbox.
- Mobile rail hiding now belongs to CSS from the initial render, rather than
  waiting for deferred JavaScript and then sliding the rail away. Page-content
  entrance animation was removed. Explicit More, preview and search interaction
  motion remains subtle and respects reduced motion.
- The upper bar has a Search icon in the former preferences position. Language
  and theme remain in More. Desktop uses a More button in the existing rail's
  footer and the same compact panel, showing only shared utility destinations.
  Mobile retains its actual role-owned secondary destinations and 21px icons.
  Direct preferences controls were removed from the header and account menu;
  existing utility compatibility routes, Help and Login controls remain intact.
- Search opens a native modal dialog on Desktop and phones. Results update
  while typing, with a 160ms request-coalescing interval, abortable read requests,
  stale-response rejection, composition-input support, empty/error/retry states,
  internal scrolling, Escape/outside/close dismissal and focus restoration.
  Tab/Shift+Tab wrap within visible modal controls. Student bottom-bar and rail
  Search entries open this dialog; modified-click/native GET fallbacks remain.

## Role and privacy boundaries

One new authenticated GET, `/workspace/search`, serves the full native fallback
or an escaped preview fragment when `preview=1`. The server takes role, account
and research provenance from the authenticated context/configuration, never
from browser parameters. Responses are private/no-store and vary by Cookie.

Every role can find its existing workspace pages, matching English or localized
Arabic UI labels. Record search requires at least two characters, uses the
existing 100-character/eight-token normalization and literal SQL wildcard
escaping, and returns at most five records per category with explicit truncation
guidance. SQL query counts are bounded; no locks or writes are introduced.

| Role | Searchable records | Authorization |
| --- | --- | --- |
| Student | Courses, units, lessons, materials, announcements | Existing Student query service, effective enrolled-content visibility and announcement visibility |
| Teacher | Groups/course context, units, lessons, material titles/keywords, assignment/quiz/listening/speaking titles | Active own group assignments and active Teacher account in SQL; historical GET visibility retained; existing activity branches reused |
| Administrator | Terms, levels, courses, groups, rooms, Student/Teacher names or email matches | Administrator gate; public record URLs; only names/status displayed for people |
| Researcher | Configuration labels, session public IDs and pseudonymous subject codes | Researcher gate; only research tables, no account/identity-link joins; Sessions page's server-selected provenance scope |

Operational areas such as Finance, Attendance, Grades and Exports remain
searchable workspace destinations; this does not imply searching every field
in every operational record. Search does not index private message bodies,
answers, grade values, private file content or account-to-research mappings.

Typed queries are not persisted in browser storage or research metadata.
Opening/searching previews does not change history, emit a page view or define
a new research event. Existing Student result-click identifiers/ordinal
positions remain attached to content results. The collector, Event Dictionary,
sampling and original submitted-search behavior are unchanged.

## Verification actually performed

- Bounded rendered-browser review: 37 search layout observations across all
  four roles at 1440/1024/768/430/390/320px, plus Dark, Arabic RTL, System Dark and
  a reduced-motion 390x500px viewport. Zero JavaScript errors, horizontal
  overflow or dialog overlap with the bottom navigation after corrections.
- Twenty-four Escape/focus-restoration checks, More utility access and
  preferences popover checks passed. Native modal Tab/Shift+Tab wrapping and
  composition-input handling passed. Search opens without changing the route,
  including Student bottom Search and Student/Teacher Messages headers.
- All 49 inspected actual search result destinations returned HTTP 200:
  Student 18, Teacher 19, Administrator 10, Researcher 2. New native GET
  fallbacks worked without JavaScript for every role; anonymous requests
  redirected to the existing login. Caller role/provenance parameters did not
  expose Administrator records to other roles. Fragments contain no script or
  collector configuration.
- Initial-render review blocked navigation initialization scripts on five
  Teacher destinations; the sidebar remained hidden and content animation was
  `none`. The canonical review server at port 5002 was refreshed and all four
  Arabic dashboards and live search fragments returned HTTP 200.
- Mouse clicks on notification title/message/icon/date and mobile touch on
  blank area/title, plus keyboard Enter, activated the existing Open form.
  Independent Mark read and preview Mark all activated their correct forms,
  with CSRF present. Submissions were prevented at the boundary; no business
  POST reached Flask. Notification read-state fingerprints stayed unchanged.
- Literal `%%`, `__` and backslash queries yielded no broad wildcard results.
  Teacher result URLs were all inside the inspected Teacher's assigned groups.
  Research session search found retained development history with no account
  join. All reviewed query statements were bounded SELECTs.
- Empty/short-query guidance, stale-response rejection, simulated HTTP 503
  feedback and successful Retry were inspected. The simulated failure is
  explicitly labelled in the gallery.
- Existing notification forms/fields/actions and target/query/route services
  remain unchanged. Login source, Messages component files, background tokens,
  research collector/dictionary and existing Student search logic are unchanged.
  Login screenshots were pixel-identical to the original at matching 1440x960
  and 390x960 settings. Python/JSON/Jinja parsing, JavaScript syntax and Git
  whitespace checks passed.

[Actual rendered screenshot gallery](http://127.0.0.1:8766/search-st2.html)
contains 35 images. Evidence and bounded review scripts are outside the repository
in the existing `platform-ui` QA directory; no session cookie, database or QA
artifact is committed.

These are bounded browser/static/read-only SQL checks, not an automated
regression test suite. Full write-transaction acceptance, physical device
keyboards and an actual screen-reader session remain untested. No migration,
schema/reset, grading/attendance publication, Study activation, push, deployment
or baseline freeze occurred. The local review process explicitly retains
`RESEARCH_DATA_PROVENANCE=development` and `RAILWAY_ALLOW_STUDY=0`.

## Changed application files and local commits

- `app/templates/_notification_macros.html`
- `app/templates/notifications/_bell.html`
- `app/static/css/product/notifications.css`
- `app/blueprints/workspace.py`
- `app/services/workspace_search.py` (new)
- `app/locales/ar.json`
- `app/static/css/product/search.css` (new)
- `app/static/css/product/mobile-navigation.css`
- `app/static/css/product/shell.css`
- `app/static/js/workspace_search.js` (new)
- `app/static/js/mobile_navigation.js`
- `app/templates/_figma_ui.html`
- `app/templates/layouts/_mobile_navigation.html`
- `app/templates/layouts/_product_shell.html`
- `app/templates/layouts/_workspace_search.html` (new)
- `app/templates/layouts/admin_base.html`
- `app/templates/layouts/portal_base.html`
- `app/templates/layouts/research_base.html`
- `app/templates/workspace/_search_results.html` (new)
- `app/templates/workspace/search.html` (new)

Source commits:

- `5e4a3bca2ec0b0ae039d05c33c4a44c2f2333c97`: notification hit areas and preview
  read-all action.
- `f3dcc3318b7fe36b60cd9cd6d424afde94c2eb2e`: all-role live search, More utility
  placement and initial sidebar-flash removal.

MAIN is ready for owner review. Railway requires separate acceptance of the
rendered changes and controlled workflow checks before any push/release.
