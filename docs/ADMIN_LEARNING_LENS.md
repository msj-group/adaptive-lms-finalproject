# Administrator Learning lens — 2026-10-26

## Phone configured-weight summary correction — 2026-10-26

The owner supplied a phone capture where 20/20 crossed the small summary ring.
The prior phone dimensions had only been reviewed with short values such as
2/3; that did not cover the existing maximum page of twenty Groups.

- Changed only mobile Gradebook summary CSS: the ring is now 104px, or 96px
  below 360px, with a 24px/22px single-line ratio. Labels use 12px text, counts
  retain a separate column with a 10px gap, and rows have a 36px minimum height.
  Attendance's four-column phone metrics and desktop declarations are untouched.
- Updated the feature stylesheet version in the shared Learning base so browsers
  request the correction. Calculations, returned counts and all query/client
  behavior are unchanged by this task.
- Manually reviewed real production templates/assets with twenty fictional
  memory-only gradebooks: 20/20 at 320/464px, 0/20 at 390px, and 9/20 in
  Arabic/RTL/dark at 320px. No horizontal overflow was measured. At 320px,
  20/20 has approximately 16.5px inset on both sides of the 96px ring; metric
  labels and counts retain a measured 10px gap. Browser warning/error logs are
  empty. CSS braces balance, no trailing whitespace, and CRLF-aware diff check
  passes. No automated tests or actual database was used.
- Review artifacts are ignored under `instance/learning_lens_summary_20261026/`;
  `grade-summary-phone.jpg` shows the corrected twenty-Group card in context.
  Twenty-two of twenty-three protected hashes match the immediate baseline;
  the shared query client changed externally in its optional motion refresh
  branch and was inspected/preserved without edits. Learning forms do not opt
  into that branch. No staging, commit, push, migration or deployment occurred.

## Owner reader correction — 2026-10-26

The owner's follow-up requires Review, This group's attendance and Gradebook
Open inside the current tab, removal of session All attendance and Group
attendance Group details, and better phone/desktop typography. This correction
supersedes the initial native-only navigation and no-new-script statements
recorded below. HEAD remains `10e5003`; staging remains empty.

Implemented in the six reader/shared templates, the feature stylesheet, new
`app/static/js/admin_learning_lens.js` and four missing Arabic labels. The two
overview template sources, native filter controls and server/query owners are
unchanged by this task. Existing work and data remain preserved.

- Fetch only the same native read-only URLs. Match the exact same-origin URL,
  actor/auth version/role, view kind and public Group/session identifiers before
  importing one explicit content region. Reject redirects, active/form content
  and unexpected responses. Server authorization remains authoritative.
- Keep the current tab/URL and original list DOM. Back/Escape restore the prior
  reader or list, filter selections, disclosure state, focus and scroll. Retain
  a bounded in-memory trail without persistent storage. Group attendance paging
  uses the same guarded reader; the original Gradebook fragment owner remains.
- Cancel obsolete reads on return, filtering, fragment replacement and exit;
  do not open old Gradebook rows during a pending filter update. Browser-restored
  pages reload for authorization. Failures offer Retry/native full-page recovery;
  actor mismatch clears the reader/trail and requires reload.
- Remove All attendance from session details and Group details from Group
  attendance at their template source. Inline content omits direct-page Group
  tabs/return navigation, leaving one Back control. Existing correction/history
  links remain native, and the signed correction screen is untouched.
- Use shared fonts with clearer list/item headings, larger body/metadata text,
  balanced category/weight sizing and tabular numerals. Retain RTL, themes,
  20px panels and the four-status horizontal phone row. Offset reader scrolling
  so the phone Back control clears the existing header.

Executed evidence is in ignored `instance/learning_lens_reader_20261026/`:

- Eight production templates compile with OwnedUIExtension and StrictUndefined.
  At final review 22 of 23 protected source hashes match the before-correction
  baseline; four native GET filter comparisons match. The shared query client
  changed concurrently after the earlier 23/23 receipt. Its optional motion and
  workspace-link additions are absent from these Learning lens forms. They
  received source review and were preserved without edits; filtering to the
  incomplete Group, Open and Back were rechecked with the current script,
  retaining one result, the selected filter/URL and empty browser error logs.
  Prior Arabic values/duplicate counts
  match, with exactly four missing labels appended. CSS braces balance and
  owned templates have no trailing whitespace. `node --check` and the
  CRLF-aware `git diff --check` exit 0.
- The existing GET-only loopback renderer served real production templates and
  assets from fictional memory, without the factory, engine or database. Every
  write is rejected before production views. This is manual presentation review,
  not an application/authorization test suite.
- Review -> session -> This group's attendance -> Group reader leaves
  `/admin/attendance` unchanged. Both Back steps restore the prior view. The
  inline reader has neither removed label. Final-session details retain six
  correction links; the fictional HTML note remains text with zero injected
  images. Group/State Attendance selections and two filtered rows survive Escape.
- Open -> Gradebook reader leaves `/admin/grades` and its existing filter query
  unchanged. Returning from the incomplete Group restores its filter and single
  result. Category expansion works. A script-free overview has zero scripts
  and its native Open link reaches the existing Group gradebook route.
- Manually reviewed desktop 1440px, intermediate 768px and phones 390/320px,
  including both readers in Arabic/RTL/dark. Measured views do not overflow
  horizontally. At 320px all four English summary metrics share one Y coordinate;
  Arabic metrics and the 390px session counts also share one coordinate. All
  three English category titles occupy one line at 320px. The phone Back control
  is visible at approximately Y=100px. Final browser warning/error logs are empty.
- Captures: `gradebook-desktop.jpg`, `gradebook-phone.jpg`,
  `attendance-phone.jpg` and `attendance-phone-ar-dark.jpg`.

Limits: real MySQL, actual authorization rejection, correction writes, concurrent
requests, live failure/actor-change responses and Group paging beyond the memory
fixture were not exercised. Those client guards received source review only.
No automated tests, database/reset/seed, migration, scheduler, dependency,
deployment, staging, commit, push or history change occurred. Work stops at this
local presentation correction.

## Authorization and checkpoint

The owner selected Learning lens for both existing Attendance & grades tabs,
then requested Present, Absent, Late and Excused beside each other in one
horizontal phone row. The continuation on 2026-10-26 completed this local
presentation from HEAD `10e5003`, retaining all pre-existing work.

Scope is source, localization, documentation and bounded static/manual
fictional-memory presentation review. No actual database, automated tests,
migration, scheduler, dependency, deployment or Git mutation is included.

## Implemented presentation

- The native `/admin/attendance` and `/admin/grades` routes share the selected
  heading and native tab links within the current Admin shell and phone dock.
  No synthetic tab totals or additional server reads are introduced.
- Attendance uses the selected ring and a four-status count summary above
  dated class records. Only finalized sessions already on the current bounded
  page contribute. Drafts remain listed and are explicitly excluded from the
  summary; a drafts-only page shows four zeros. This is not a center-wide total
  or an attendance grade/rate. Ratios only size SVG segments and visual bars;
  no numerical attendance percentages are shown.
- On phones the ring sits above the four metrics. All four labels/counts/bars
  remain in one horizontal row, including 320px and Arabic/RTL. Session and
  captured-roster counts retain all four statuses and zero values.
- Native class details retain original class metadata, captured students,
  escaped private notes, final/draft notices and the existing finalized-record
  Correct / history links. The signed correction screen itself is unchanged.
- Gradebook setup uses the selected course/group directory, configured-weight
  summary and expandable category reader. Configured weights, completeness,
  availability messages, source context, maximum points and scored/captured
  counts retain their existing server authority and formatted values. Individual
  Student results/comments are neither added nor fetched. Archived labels remain.
- The original GET filter values, choices, validation owners, paging parameters,
  Group context links and public object URLs remain. The existing Gradebook
  result-fragment owner updates the summary and directory together. Filter and
  category disclosures use native HTML; no new JavaScript is required.
- Feature panels retain 20px corners and shared semantic colors. English,
  Arabic/RTL, light/dark, wrapped authored text and forced-color text alternatives
  remain. Narrow category headers give titles their own line instead of splitting
  a final letter beside the existing two-decimal weight.

## Files

- `app/templates/admin/learning/{_base,_ui}.html`: scoped shared shell and pure
  presentation macros over already-returned values.
- `app/templates/admin/attendance/{overview,group,detail}.html`: existing
  Attendance consumers and native links/forms.
- `app/templates/admin/grades/{overview,_query_results,detail}.html`: existing
  Gradebook consumers and fragment boundary.
- `app/static/css/admin/learning_lens.css`: feature-only responsive styles.
- `app/locales/ar.json`: 25 missing owned labels appended. Existing values and
  duplicate-key counts are preserved.
- Authority, plan, status and decisions document this selected presentation.

## Executed evidence

Ignored one-off source receipts, the manual renderer and screenshots are in
`instance/learning_lens_20261025/`. They are review artifacts, not a replacement
automated test package or application fixture.

- All eight owned localized templates compile with the real OwnedUIExtension
  and `StrictUndefined`.
- All 23 protected source hashes match the before-task baseline, including
  Attendance/Grade routes and services, calculations, models, shared macros,
  the original correction form, shell/navigation, query client and Student pages.
- Four rendered native-filter comparisons match before/after: Attendance default
  and unknown-group/date/draft selections, Gradebook default and combined
  Group/Term/configuration selections. GET methods, control names/values/options
  and existing query attributes are retained.
- Prior Arabic translation values and duplicate counts match; 25 owned labels
  were added. CSS braces balance, owned templates have no trailing whitespace,
  and the CRLF-aware `git diff --check` exits 0.
- Actual production templates/assets were manually inspected in a real browser
  through a separate loopback renderer using fictional in-memory objects. This
  renderer does not invoke the application factory or initialize a database
  engine; it intercepts reads and rejects every write before production views.
- Reviewed desktop 1440px, intermediate 768px and phones 390/320px. Measured
  pages have no horizontal overflow. At 320px all four English Attendance
  metrics shared one vertical coordinate; Arabic/RTL/dark metrics also shared
  one coordinate in reverse reading order. The 390px four-column row is saved
  in `attendance-phone.jpg`; RTL/dark and desktop Gradebook captures also remain.
- Manual interactions covered native Attendance filters and Clear, drafts-only
  zero totals, final-session reading with six preserved correction links, escaped
  note text with no injected image, Gradebook fragment filtering to the incomplete
  group, group reading and expandable categories. Archived Group labeling and
  private-results wording remain visible. Refined 320px category titles each
  occupy one line. Final browser warning/error logs are empty.

## Limits and stop point

This is bounded source and presentation evidence. Actual MySQL queries,
authorization rejection, signed correction execution, concurrent writes and
comprehensive regression acceptance were not exercised. Existing server guards
are preserved by unchanged source, not claimed as newly executed tests.

No actual data, classification, research sampling/retention, migration,
scheduler, dependency, deployment, staging, commit, push or history changed.
The temporary renderer/tab are closed at close-out. Stop point: Learning lens
is implemented locally for the two Admin tabs and their read-only readers,
including the requested horizontal phone Attendance metrics.
