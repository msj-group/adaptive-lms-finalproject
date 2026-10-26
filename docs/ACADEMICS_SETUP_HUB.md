# Academics Setup Hub — 2026-10-23

Current authoring/status correction, 2026-10-26: New and Edit now use an in-page
dialog, Actions shows Edit directly, and Status uses a protected native selector.
See [ACADEMICS_INLINE_EDITOR.md](ACADEMICS_INLINE_EDITOR.md). The implementation
and evidence below describe the original selection before that correction.

## Authorization and checkpoint

The owner selected Setup Hub for desktop and phones, with a raised selected phone
card, lowered neighbours and horizontal swipe navigation. Checkpoint: `10e5003`
plus existing and concurrent local work. The selection authorizes source,
localization, documentation and static/fictional-memory presentation review.
It does not authorize automated tests, actual database execution, migrations,
resets/seeds, scheduler changes, installations, Git mutation or deployment.

## Implemented source

- `admin/academics/_base.html` and `_hub.html` supply the shared academic heading
  and the four native directory links: Courses, Academic terms, Levels and Rooms.
  The Admin shell suppresses its redundant section tabs only when this hub is
  present. Other destinations and the shared platform dock retain their owners.
- `admin/academics.css` implements four desktop cards and the phone deck, using
  semantic platform colors and exactly 20px feature corners. On phones, the
  centred card has full scale/opacity and a raised shadow; neighbours are scaled
  to 90% and translated down 12px. Native scroll snapping and small neighbouring
  previews make horizontal navigation discoverable.
- `admin_academics.js` centres the selected route, updates the front card and
  position, and follows the centred same-origin native GET link only after a
  deliberate horizontal gesture settles. Keyboard focus only centres a link;
  Enter still follows it. Resize, history restoration, vertical gestures and
  initial positioning do not trigger route selection. Reduced motion removes
  card transitions. Without JavaScript the cards form a two-column phone grid.
- `academic_setup_queries.py` supplies one lazy, request-cached scalar SELECT
  with four totals. Its caller is guarded by the four directory endpoints and
  a current active Administrator. Totals include every persisted status, with no
  active/current-term inference. Course level filters and Room paging do not
  alter these totals. Course result fragments do not render or call the hub.
- The four directory templates and Course results retain all existing academic
  fields and native operations. Code/order move into record identity metadata;
  native details disclosures collect management controls. No write is moved
  into JavaScript. Known simple tables render their phone record class on the
  server as well, preserving the no-script phone composition.
- Seven Arabic labels were added. Existing catalog values and the previously
  duplicated `Released grades` key are retained; no duplicate is introduced.

The native public object URLs, CSRF, signed Room snapshots, ordering, pricing,
activation/archive semantics and relevant write handlers are unchanged. Existing
authoring forms remain under the shared Admin treatment. Concurrent enrollment,
navigation and Daily Desk shell work was inspected and preserved in place.

## Executed verification

- All 268 HTML templates present at review time compile with the actual
  OwnedUIExtension. Both affected Python sources parse. `node --check
  app/static/js/admin_academics.js` passes.
- All nine original literal form blocks across the five changed list/result
  templates retain identical whitespace-normalized source, including method,
  action, hidden controls, snapshot expressions and native buttons.
- Hash comparison confirms the four domain route modules, their models, the
  four authoring templates and captured academic transaction/lifecycle services
  retain their captured bytes. Concurrent changes to unrelated enrollment and
  shared shell files are explicitly outside that unchanged claim.
- The four-count SELECT compiles against SQLAlchemy's MySQL dialect without an
  engine, connection or execution. This proves construction, not live results.
- Locale JSON parses, with no changed pre-existing values and no introduced
  duplicate. New labels render in Arabic/RTL in the real-browser rehearsal.
- Actual browser review renders production templates/assets from an ignored
  fictional-memory fixture. Every application view is intercepted, unlisted
  GETs are rejected and writes return 405. No application factory, SQLAlchemy
  engine or actual business data is used.
- Reviewed all four desktop directories at 1440px, the four-card 1024px layout,
  390px phone and 320px Arabic/RTL/dark. The selected Levels phone card has zero
  measured centre offset; other cards have the documented lower/smaller state.
  No horizontal page overflow was observed at the reviewed widths.
- Horizontal wheel selection reached Rooms. Real browser touch input returned
  from Rooms to Levels in LTR and reached Rooms from Levels in RTL. Keyboard
  focus stayed on the current route; Enter followed the focused Course link.
  Back restored Levels; resize and vertical touch scrolling did not navigate.
- Course filtering displayed one Elementary row while the hub retained four
  fictional total courses. An empty filter displayed zero rows and retained
  that same global count. Room page 2 displayed four rows while retaining a
  centre-wide total of 54. All are fixture facts, not application data counts.
- Native management disclosures expose the original operations. At 320px with
  JavaScript disabled, four hub links, four signed Room snapshot inputs and
  native management disclosures remain available, without page overflow.
- Reduced-motion transition duration is `0s`. Browser script errors: zero.
- Final CRLF-aware `git diff --check` passes, new owned files have no trailing
  whitespace, staging is empty and HEAD remains `10e5003`. The owned review
  browsers and fictional-memory server were stopped at close-out.

Evidence is ignored under `instance/academics_hub_20261023/`: captured source,
static/form receipts, the write-blocking manual fixture and browser screenshots.
This is not an automated test package or comprehensive regression acceptance.

## Boundary and stop point

Implementation is local and source-only. No automated tests, actual database
reads/writes, migrations, resets/seeds, scheduler, installation, staging, commit,
push or deployment was performed. Live-MySQL counts, authorization, writes and
transaction/concurrency behavior were not exercised. Required follow-up beyond
this selection must obtain its own authorization. Existing and concurrent work
remains in place.
