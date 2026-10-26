# Admin Announcements scope board — 2026-10-23

## Authority and checkpoint

The owner requested new desktop/phone concepts for Admin Communication's
Announcements tab, alternatively authorizing the existing Teacher page's
presentation. Implement the Teacher-style option, adapted to Administrator
scope selection. Inspected HEAD: `10e5003`, with substantial existing local
work preserved. This is a presentation correction, not a new business Part.

## Implemented locally

- `app/templates/admin/announcements/overview.html` and `_query_results.html`
  now present a collapsible native Scope/Status filter, selected-first audience
  cards and a scrolling announcement board. Desktop uses a side rail; compact
  widths use horizontally snapping cards with the current scope raised.
- Existing Center/Course/Group filters retain their meaning. An All announcements
  card removes only the scope filter. Scope cards retain the current status,
  and pagination retains both filters. No search/date feature, aggregate count,
  individual recipient data, new endpoint or query is introduced.
- Each row shows existing status/scope, creation time, escaped title/body preview,
  context and author. Review draft/View announcement opens the existing native
  detail GET. The footer count describes this returned page only.
- `_base.html` and `app/static/css/admin/announcements.css` scope the visual
  treatment to this feature, including the existing detail and draft forms.
  Preserve semantic themes, 20px feature corners, native keyboard interaction,
  English/Arabic/RTL, existing section links and the platform's sole phone dock.
- `app/locales/ar.json` adds 12 labels. Existing catalog values and duplicate-key
  counts remain unchanged. No new feature JavaScript is needed; the existing
  query-filter enhancer and native GET fallback retain their owners.

## Executed verification

- Five localized Jinja templates compile successfully. Static comparison finds
  the detail/editor forms unchanged after excluding the added presentation class.
  This preserves the original methods, action URLs, hidden CSRF/state fields,
  target inputs, publish availability and permanent-withdrawal confirmation.
- SHA-256 comparison confirms no change during this task to Admin announcement
  routes/forms or shared announcement queries, transactions and signed tokens.
- JSON parses; all previous locale values remain; 12 additions introduce no new
  duplicate keys. Static comparison uses installed tools and standard Python.
- A real browser rendered the actual templates/assets on a temporary loopback
  server with fictional memory data and all application writes intercepted.
  No application factory, application database engine or actual database is used.
  Reviewed 1440px desktop, 768px intermediate, 390px Arabic/dark and 320px narrow
  layouts, with no horizontal document overflow in measured views. The 20px board
  corner is confirmed in the browser.
- Reviewed live Scope/Status fragment replacement, selected-first updates,
  escaped script-like title/body text, an empty filtered state, native GET filter
  submission from a script-free page, and native draft detail/editor navigation.
  Published detail retains its native POST/CSRF/state and required withdrawal
  checkbox; withdrawn detail offers no mutation form. Native POST forms were
  inspected without submitting business changes.
- Presentation evidence and before-source copies are ignored under
  `instance/admin_announcements_20261023/`, including `static-receipt.json`,
  `desktop.png` and `phone-ar-dark.png`.
- Ordinary `git diff --check` exits 2 with CR-at-EOL findings in the existing
  mixed-line-ending checkout. The read-only CRLF-aware invocation
  `git -c core.whitespace=cr-at-eol diff --check` exits 0 with no findings.
  New source/doc files have no trailing whitespace. HEAD remains `10e5003`,
  and staging is empty; no Git configuration was changed.

## Boundaries and stop point

No automated tests were created or executed. No actual MySQL access, business
writes, migration, reset/seed, scheduler, dependency installation, Git mutation
or deployment is included. This review does not establish database-backed
persistence, notifications, concurrency or comprehensive regression acceptance.
The temporary review server/tab are closed after inspection. Final Git and
whitespace receipts are recorded in PROJECT_STATUS.md. The selected interface
is implemented locally and remains available through the existing route.
