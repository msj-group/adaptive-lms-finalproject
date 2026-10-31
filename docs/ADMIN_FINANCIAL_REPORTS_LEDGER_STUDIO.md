# Administrator Financial reports - Ledger Studio

## Authority and scope

The owner selected Ledger Studio on 2026-10-26 and explicitly requested correct
subpage transitions after earlier presentation omissions. This Part implements
the three existing Financial reports pages, their read-only nested readers and
real export interaction locally from `4544d56`. Source, localization,
documentation and bounded static/manual fictional-memory review are in scope.
No automated tests, actual database/business write, migration, reset/seed,
scheduler, dependency, Git mutation or deployment belongs to this Part.

Concurrent Finance Account focus work, shared catalog/documentation additions
and pre-existing untracked files were preserved. The two workspaces keep their
own presentation clients. Ledger readers request an optional read-only response
from the original authorized GET routes; ordinary Finance requests retain their
native templates and signed writers.

## Implemented behavior

- Keep the current Atlas shell, navigation and phone dock. Ledger Studio supplies
  the selected heading, page-scoped summary cards, three local report tabs,
  search/date controls, account/operation rows and export action. Feature panels
  have 20px corners, semantic theme colors and English/Arabic/RTL support.
- Account metrics sum only the already-returned current page, with an explicit
  scope label. History and exception metrics describe their own returned rows;
  they are not centre-wide totals or reconstructed historical balances.
- Tabs, GET filters and report pagination update within the current page and
  maintain native canonical URLs. Browser Back/Forward restore the selected
  report. The original list DOM and its filters stay intact while a reader is
  open. Search input focus and selection survive its returned GET results.
- Desktop readers use a side dialog; phone readers use a full-width bottom
  sheet. The native modal handles focus containment. Close, Escape, local Back
  and browser Back return to the report without losing its committed filters.
  Nested navigation retains reader content and scroll positions in a bounded
  in-memory trail. Forward re-authorizes a native reader before reopening it.
- Revision and exception readers use the exact returned report row. Revision
  before/after amounts, states, operator, time and reason remain escaped text.
  The document link opens its current state, separately from the recorded change.
- Account, document, correction-history and receipt readers use the original
  Student-scoped GET routes and their existing role/nested-ownership checks.
  Their invoice/movement/history paging and related links stay in the dialog.
  The report reader exposes read-only review; operational writers remain in
  Finance. Historical provider exceptions link to invoice context and do not
  introduce any simulated or new payment workflow.
- Export opens in the same dialog. PDF/CSV requests use the actual existing
  export routes and all committed report filters. HTML/error/redirect responses
  never count as a successful download. An explicit failure, Retry and native
  recovery remain in place. The 5,000-row refusal, full matching export scope,
  CSV safeguards, PDF renderer and original export columns remain unchanged.
- Without the optional client, native filters, paging, account/document links
  and CSV/PDF links still exist. A `noscript` disclosure retains all revision and
  exception facts. This fallback was inspected statically, not executed with
  browser JavaScript disabled. Print controls and print-only reader styling
  were inspected statically; a physical print job was not exercised.

## Integrity and file boundary

`general_financial_reports.py` adds public link metadata to returned records;
it keeps the filters, joins, ordering, page/export limits, formulas, notes and
declared export columns. `financial_report_presentation.py` validates public
identifiers, resolves current Student public identities for the bounded history
page and derives only presentation summaries/links. No numeric object URLs are
introduced. Additional exception SELECT projections have matching UNION shapes.

`general_reports.py` supplies this presentation context only for HTML. Four
read-only branches in `general_finance.py` recognize `X-Ledger-Reader: 1` after
the existing guards and return `_report_reader.html`. Report/financial response
privacy headers and native movement/correction/preview handlers remain.

Owned presentation files:

- `app/templates/admin/general_finance/report.html`
- `app/templates/admin/general_finance/_report_row.html`
- `app/templates/admin/general_finance/_report_reader.html`
- `app/static/css/admin/ledger_studio.css`
- `app/static/js/admin_ledger_studio.js`
- Missing owned labels in `app/locales/ar.json` and the authority/plan/status/
  decision records.

The client checks origin, exact allowlisted paths, expected public Student and
document, reader kind, actor public ID/auth version/role, response URL, status
and HTML type. It rejects executable/foreign reader content, aborts obsolete
reads and clears private content on an actor mismatch. Closing still works
after that refusal. Downloads check the expected MIME type and attachment
header before offering bytes; no report data is stored in localStorage or
history state. The history state contains navigation references only.

## Executed evidence

Ignored one-off receipts, the manual renderer, fictional export and captures
are retained in `instance/ledger_studio_20261026/`. They are presentation review
artifacts, not a replacement automated test package.

- Four changed/new Python sources parse. Three owned templates compile with
  the real localization extension and StrictUndefined. The owned JavaScript
  syntax check exits 0. CSS braces balance and owned-source trailing-whitespace
  review is empty.
- Seven protected source hashes match the before-state: finance calculations,
  balance reads, financial history, export renderer, report/account route
  guards and the Admin base layout. All three native write/preview handler ASTs,
  both report calculation helper ASTs and all export column definitions match.
  Existing Arabic values and duplicate-key counts are retained alongside
  concurrent additions.
- Actual production templates/assets were reviewed manually in a real browser
  through a separate loopback renderer. It uses fictional memory, rejects
  every non-GET, intercepts application views and initializes no factory,
  engine or database. Existing routes are registered only for native URL
  construction; this does not execute live authorization or ORM queries.
- Manual paths covered account -> invoice -> Back -> invoice paging ->
  collection -> receipt -> correction history -> document; report history ->
  revision -> current document; exception -> correct pending transfer;
  report tab changes, student search, report pagination and date filtering.
  The 53-row fictional account listing showed three remaining rows on page 2.
  Browser Back closed the reader with the exact search retained; Forward
  reloaded the reader. Phone Close retained both committed date values.
- CSV and PDF requests used the production export renderers over fictional
  report objects. The PDF reached browser Downloads, with `%PDF-` signature
  and 23,363 bytes. A deliberately wrong HTML export response produced the
  inline failure/retry state without a download-success message.
- Deliberately refused reads retained the prior record and Retry loaded the
  requested record. A foreign Student response was rejected. A foreign actor
  response cleared private content and required reload; Close was rechecked
  after correcting its invalid-session edge case. Escaped authored markup
  remained text in both revision/history readers.
- Reviewed desktop content at 1,265 usable pixels and phone frames at 390/320
  pixels, including Arabic/RTL/dark. Measured document client/scroll widths
  matched (1,265/1,265, 375/375 and 305/305); the corrected 320px dialog measured
  320/320 and occupied the full phone width. Captures include desktop revision,
  phone account and narrow Arabic revision readers.

### Final local close-out, 2026-10-27

The final JavaScript syntax command exited 0. The four owned Python sources
parsed and the Arabic JSON catalog parsed successfully. HEAD remained
`4544d56` and the staging area was empty. Concurrent working-tree changes
remain present and were not staged, discarded or included in a commit.

Plain `git diff --check` exited 1 because it treated CR-at-EOL in changed
CRLF source/documentation lines as trailing whitespace. The explicit
`git -c core.whitespace=cr-at-eol diff --check` exited 0; the owned-source
static receipt also lists no trailing spaces. No Git configuration was changed.

The temporary review renderer's owned loopback port 62601 had no listener
at close-out. The final invalid-session popstate and download-URL cleanup
refinements received static syntax review after the relevant manual browser
checks; those refinements were not separately exercised in the browser.

## Limits and stop point

This is local source completion with bounded static and manual presentation
evidence. No automated tests or pytest collection were created or executed.
Actual MySQL queries, live role rejection, persistent financial changes,
concurrency, a 5,001-row live export and comprehensive regression acceptance
were not exercised. Existing financial guards are preserved by source review,
not claimed as newly executed database-backed tests.

No actual data, classifications, research sampling/retention, migration,
dependency, scheduler, staging/commit/push, Git configuration or deployment was
changed by this Part. The owned temporary renderer is no longer listening
at close-out. Stop point: Ledger Studio and its requested in-page read/export
transitions are implemented locally; publication is outside this request.
