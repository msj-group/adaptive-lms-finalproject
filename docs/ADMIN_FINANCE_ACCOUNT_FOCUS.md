# Administrator Finance — Account focus

## Authority and scope

The owner selected **Account focus** on 2026-10-26 and explicitly requested
correct subpage transitions and verification. Implemented locally from
`4544d56`, preserving the existing untracked work and concurrent Ledger Studio
changes. Scope: presentation, a Finance-specific native-route client, Arabic
labels, documentation and bounded static/manual fictional-memory review.
No deployment, staging/commit/push, dependency, scheduler, automated tests,
actual database access, financial operation, migration, reset or seed.

## Result and navigation

- Student accounts retains its native name/email GET, fragment owner, paging,
  total count and server-provided balances. A directory sits beside the chosen
  account on desktop and above it on phones. Account selection requests the
  existing authorized public Student ledger. No aggregate or new SQL is added.
- The account shows the existing signed balance semantics, obligations,
  confirmed collections and payouts. Local Movements/Invoices controls retain
  every native row, state, action and independent paging parameter. Both
  sections remain available without JavaScript.
- Invoice/movement reading, Record movement, Record collection with its invoice
  context, correction, correction-history paging, receipt and read-only
  enrollment history open within the current workspace. Back/Escape restore
  the prior reader; returning to the directory/register retains its filters,
  URL and selection context. A bounded eight-entry memory trail is not stored.
- Financial registers retain their native filters and rows; Details, History,
  student accounts and receipts use the same reader. Main Finance tabs remain
  native GET navigation. Student profile and actual course/group corrections
  intentionally retain their People/Groups workflow owners and native links.
- Print controls target the current imported document/receipt. Original URLs,
  read retries and full-page recovery remain available. No printer was invoked.

## Integrity decisions

The client requires exact same-origin native paths, allowed query parameters,
actor/public ID/auth-version/role, content kind and nested Student/document
identity before importing an explicit content region. Redirected/foreign reads,
unexpected forms and active markup are refused. Obsolete reads are aborted on
new selection, filters, result replacement, return and exit; restored pages
reload to re-authorize their private content. The Reports reader's separate
header is never sent by this client.

Native forms retain CSRF, operation key, snapshot, field names, choices and
values; only presentation IDs and the explicit original POST action are set
after import. Attribute access avoids native fields named `action`/`method`
shadowing HTMLFormElement properties. Existing form/draft/preview helpers are
reused. Validation remains inside the reader. A stale-form redirect keeps the
returned fields and matching server snapshot; attempted values are not merged.
Back after that response, or after an uncertain save, fetches a fresh account.

Only the native success flash rendered on the matching redirected Student
account proves a completed save. Any other result retains the draft, blocks
repeat submission and offers account review. Backend locking, amount rules,
integrity handling, audit history and authorization are unchanged. The shared
query client gains one opt-in `data-query-refresh-current` flag so Finance's
post-save refresh preserves the committed URL including its page; other
consumers retain their original behavior.

## Executed evidence

- Twelve scoped templates compile with the localized StrictUndefined renderer.
  Seven rendered native-control comparisons match the before-state: movement,
  invoice correction, payment correction, accounts and all three register
  filter modes. New movement also renders when native context omits `kind`.
- Both affected JavaScript sources pass syntax checks. The three native
  movement/correction/preview handlers match the baseline by AST. Of 173
  protected source hashes, 169 remain equal; three read/report files changed
  concurrently outside this task and the fourth is the explicitly scoped
  query-refresh client change. No model/migration/financial writer is changed
  by this task. Existing Arabic values and duplicate-key counts are preserved;
  this task added 20 labels. Other new labels belong to concurrent work.
- Manual browser review uses actual production templates/assets over an owned
  Flask memory renderer that intercepts every application endpoint. It creates
  no engine and executes no actual application view or business write. POST
  response fixtures rehearse presentation only; unchanged monetary facts are
  never evidence of real persistence or financial acceptance.
- Reviewed account/invoice/movement selection, both forms and validation,
  draft stay/discard, bank fields and read-only preview, native completion
  markers, account refresh on page two, both ledger pagers, receipt, correction
  history/paging, escaped reason text, enrollment history and nested Back.
  Also reviewed register Details/Back, retained Pending filter, deleted empty
  state, credit/settled/due accounts, search/empty results and rapid selection.
- Rehearsed stale form return, unmarked redirect, another Student's return,
  read failure/retry/Back and actor mismatch. Drafts/repeat blocking and
  current-view retention were observed. Hidden signed values are not exposed
  through the browser review; their pass-through is covered by static control
  comparison and source inspection.
- Actual frames at 1440/768/390/320px include English/light and Arabic/RTL/dark,
  phone invoice/correction/Back and form control geometry. Measured document
  client/scroll widths match (1425/753/375/305px including frame scrollbars).
  The phone controls stay within the document width. Feature panels are 20px
  and use the existing semantic theme and shared shell/dock.
- Final fresh-browser account/document/Back/form/Back review has an empty
  warning/error log and matching 1265px client/scroll widths. Screenshot and
  browser receipt are saved with the ignored evidence. CRLF-aware diff checking
  exits 0 and task-file trailing-whitespace review passes. Ordinary diff checking
  exits 2 on this mixed-ending checkout, including concurrent report-source
  CR-at-EOL lines. HEAD stays `4544d56`; the index is empty.

Ignored evidence and the temporary renderer are under
`instance/finance_account_focus_20261026/`. Static and manual review are bounded
evidence, not comprehensive automated regression, real-MySQL concurrency,
live authorization/persistence, printer or deployment acceptance. Stop point:
local source implementation and documented review; no release action. Owned
temporary browser tabs and the fictional renderer are closed after review.
