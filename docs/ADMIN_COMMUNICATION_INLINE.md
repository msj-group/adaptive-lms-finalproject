# Admin Communication interaction correction — 2026-10-26

## Authority and checkpoint

The owner requested animated Audience selection without a document reload;
New announcement, Review draft and announcement titles must open within the
current page. Messages must similarly keep New message, Start a conversation,
the plus control and recipient/conversation selection within the workspace.
Remove the blue chat-input outline. This supersedes the earlier native-only
navigation presentation while retaining those routes as recovery paths.
Inspected HEAD is `10e5003`; extensive pre-existing local work is preserved.

## Local implementation

- `admin/announcements/overview.html` and `_query_results.html` opt the native
  Scope links, paging and clear controls into their existing GET-fragment owner.
  `query_filters.js` supplies optional keyed card motion, selected-first focus
  restoration, native filter synchronization and filtered refresh. Other pages
  keep their existing behavior. Reduced-motion preferences skip animation.
- `admin_communication.js` opens the original announcement reader/editor in a
  centered native dialog. The existing form fields, CSRF, signed state and POST
  URLs remain authoritative. Editor DOM IDs are prefixed to avoid collisions
  with the board's Scope filter. Validation remains in the dialog, and native
  redirected responses retain their actual notices. Closing after a returned
  detail refreshes the current board through its existing query owner, retaining
  its committed URL, filters and page. It never claims that a redirect alone
  proves publication or another business outcome.
- `messages/base.html` supplies an Admin-only content boundary. Native picker,
  compose, thread, history-page and inbox GETs replace this content with a short
  transition. Existing-conversation redirects remain server-owned. Browser
  Back/Forward reconcile the view, and Admin inbox search keeps its native GET.
  Normal send/reply uses the original signed FormData and POST; only the native
  sent/idempotent marker with no blocking notice confirms completion. Validation
  keeps entered text. Edit/hide/clear remain their existing native workflows.
- Fetched content is checked against the current actor/auth version/role and
  exact endpoint, recipient or public thread/announcement identity as applicable.
  Stale reads are cancelled. Draft discard uses the shared confirmation. An
  uncertain POST retains the draft, disables repeat sending and offers native
  review; a read failure offers Retry without replacing current content.
- `messages.js` exposes explicit enhancement/disposal so contact search,
  textarea sizing, keyboard sending, emoji, thread tools and action sheets work
  after replacement. Disposal aborts contact requests and releases observers and
  document/window listeners. Initial rendering for other roles is preserved.
- Scoped CSS in `admin/announcements.css` and `messages.css` preserves themes,
  RTL, 20px dialog corners and the existing shell/dock. Admin chat typing has no
  blue textarea or composer outline; action controls retain keyboard focus.
  The Admin base changes only the query-client asset version for this correction.
  Existing Arabic catalog bytes remain unchanged; all added UI uses known labels.

## Executed verification

- Ten localized Jinja templates compile with the existing StrictUndefined
  manual-render environment. Node syntax checks pass for all three changed
  feature/shared clients. No application factory or database engine is created.
- Fourteen protected route/service/shared-client SHA-256 hashes match the
  captured before-state. Both announcement POST form source comparisons match
  exactly after whitespace normalization; existing Arabic catalog bytes match.
- Manual real-browser inspection used actual templates/assets and fictional
  memory only. Covered English/light desktop, Arabic/RTL/dark phones, Audience
  selection with preserved Status, title/Review draft/New announcement entries,
  same-dialog edit/validation, Stay/Discard, and fictional draft completion with
  board refresh. Desktop was reviewed at 1440px and phones at 390/320px; measured
  document/dialog widths did not overflow horizontally.
- Messages review covered all requested entry controls, new/existing contacts,
  browser Back, compose/reply validation, fictional send/reply completion,
  reinitialized emoji and draft guards. Computed textarea/composer outlines are
  `none` while typing on desktop and phone. A 503 read preserved the workspace
  with Retry; a 503 POST retained text and disabled Send. Expected 503 network
  errors belong to this explicit failure rehearsal, not successful flows.
- Evidence, source copies, the one-off static receipt and temporary renderer
  are ignored under `instance/admin_communication_20261026/`. Screenshots include
  `desktop-announcement.jpg`, `phone-announcement.jpg` and `phone-composer.jpg`.

## Boundaries and stop point

No automated tests were created/executed, and no pytest collection occurred.
No actual MySQL access, real message delivery, business write, migration,
reset/seed, scheduler, dependency installation, Git mutation or deployment is
included. Fictional completion responses are presentation evidence only; they
do not establish real persistence, authorization/locking execution, notification
delivery or comprehensive regression acceptance. Native recovery is retained
structurally; no-script execution and other-role browser regression are not
claimed. Final whitespace/Git and renderer cleanup evidence is recorded in
PROJECT_STATUS.md.
