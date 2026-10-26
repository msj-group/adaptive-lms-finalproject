# People Focus Desk — 2026-10-23

## Authorization and checkpoint

The owner selected Focus Desk for the Administrator People workspace on desktop
and phones. This selection supersedes the Atlas table presentation for the
Student and Teacher directories and profiles only. Checkpoint: `10e5003`, with
pre-existing and concurrent work preserved in place.

The authorized boundary remains source, localization, documentation and bounded
static/fictional-memory browser review. No automated tests, real database reads
or writes, migration, reset/seed, scheduler, dependencies, Git mutation or
deployment is included.

## Implemented presentation

- Owner follow-up, 2026-10-25: on phones, place the Filters disclosure next to
  New Student/New Teacher. It reveals the same native search/status form; no
  filter or query is duplicated. Without enhancement the native form is visible.
  Desktop has a 20px gap between directory/profile cards and 10px between person
  cards, retaining platform colors and 20px corners.
- Edit profile and Message now open centered dialogs inside the current People
  directory at desktop and phone widths, matching the selected concept. This
  supersedes the previous enhanced links to standalone edit/correspondence pages.
  Existing native routes remain recovery links and retain all POST authority.
- Owner creation-dialog correction: New Student and New Teacher now open the
  original form in a centered native dialog over the current People directory.
  Validation remains in the dialog. This supersedes the earlier full-page
  creation presentation; native links remain the no-script recovery path.
- Owner sidebar correction, 2026-10-23: show one People destination in the
  Administrator sidebar, with Students and Teachers as tabs inside People.
  People remains active for both directories and their profile/account child
  pages. This corrects the separate Student/Teacher sidebar shortcuts; it does
  not change the native routes or the Focus Desk composition.
- Both existing directories share the People heading, native Student/Teacher
  links, name/email search and account-status filtering. The result count is
  exactly the current filtered collection length; no overall or other-role
  count is fabricated or queried.
- Desktop displays the person list beside the selected profile and loads the
  first current result. Selecting another row requests that person's existing
  authorized detail URL. On phones, selection opens the profile in the same
  document; People returns to the retained list, filters and scroll position.
- The shared profile shows identity, status, created date, account type and
  existing record links. Current Student learning context/balance, Teacher
  assignments and complete profile information retain their existing facts.
  Disclosures keep detailed records and account management accessible.
- Edit, creation and Message reuse their original native forms inside dialogs;
  financial/enrollment/history links and password reset continue to their native
  pages. Former per-row Edit/status
  shortcuts are consolidated in the selected person's profile. Status writes
  use the original detail-form action, CSRF, account snapshot and confirmation,
  and retain the existing detail redirect policy. A suspended account has the
  native Reactivate control and no Message link.
- Without the enhancement, ordinary person links open the full native profile;
  GET filters and all native profile actions/disclosures remain available.
  Shared navigation, appearance, density and the sole platform phone dock are
  retained. Feature surfaces use platform semantic colors and 20px corners,
  logical RTL properties and reduced-motion handling.

## Read and write boundaries

`admin_people.js` performs GETs only. It does not post, synthesize account
success, change the list address or replace creation/edit/password workflows.
No Python view, query, model or schema change is needed. Each fetched document
must match the exact same-origin native detail URL, current public person ID,
Student/Teacher kind and Administrator identity/authentication-version/role
binding. Redirects, login pages and mismatched content are rejected; scripts and
embedded documents are not imported. A changed binding disables enhancement.

Requests use `cache: no-store`, cancellation and revision checks. Filtering,
replacement results and navigation invalidate pending reads; a late response
cannot replace a newer selection. Failures leave native links available and
offer Retry/Open full profile. A new selection removes the preceding profile
before requesting its replacement. The shared native confirmation handler owns
status submission, including dynamically inserted hidden-only forms.

Native server role/object authorization, conditional integrity, transactions,
signed stale-form protections and safe rollback remain unchanged. Research
classification, sampling, provenance, retention and financial/academic meanings
receive no new policy.

## Source ownership

- `app/templates/admin/people/`: shared base, directory, results, role links,
  full profile page and reusable profile.
- Student/Teacher `list.html`, `_results.html` and `detail.html`: small adapters
  preserving the existing route/template entry points.
- `app/static/css/admin_people.css`, `app/static/js/admin_people.js`: scoped
  layout and optional read-only profile enhancement.
- `app/static/js/admin_people_create.js`, `admin/people/_create_dialog.html`:
  centered creation, native form extraction/submission and validation feedback.
- `app/static/js/admin_people_actions.js`, `admin/people/_action_dialog.html`:
  centered signed edit and correspondence forms. Student/Teacher form templates
  add actor/kind/public-person markers; Messages compose/thread templates mark
  their existing composer panels and native sent/duplicate flash outcome.
- `app/locales/ar.json`: owned People labels added without rewriting unrelated
  translations. The creation correction adds creation-only metadata to the
  existing create/edit templates; password-reset templates remain unchanged.

## Profile actions within People

The action client imports only the exact actor/person/kind-bound native edit
form or actor-bound correspondence composer. Compose additionally matches the
requested recipient; an existing-conversation redirect from that exact native
recipient GET pins the returned thread URL. Reply validation and completion
must retain that thread. No inbox, contact list, other participant's unrelated
thread, page scripts or management actions are imported. Presentation IDs are
prefixed without altering field names, signed values or native destinations.
The compact composer retains native text/send controls and character counting;
the full Messages page continues to own its optional emoji UI.

Original CSRF, account snapshots, message-state nonces, server validation,
role/object authorization and transaction/idempotency owners remain unchanged.
Form errors stay inside the dialog. Shared draft protection covers ordinary
changes and returned validation drafts; Close/Cancel/Escape/backdrop restore
focus, and a pending write cannot be cancelled or retried automatically.
Unconfirmed results preserve the draft and block repeat submission.

An edit completes only after the exact native detail redirect with matching
actor/person/kind. It refreshes current filters through the existing explicit
query-owner hook and restores the edited profile when still in the result set.
A filtered-out profile returns to the list. Messages complete only after the
native thread redirect and an actual native sent/already-sent flash outcome,
with no warning/error flash in the returned response;
an unrelated redirect or read-only rejection is not reported as sent. The page
stays on People and displays the confirmed native outcome.

The follow-up does not change any Python view, query, validator, transaction,
notification, schema or domain/research policy. Ignored manual/static evidence
is under `instance/people_refinements/`; its account and correspondence objects
exist only in memory, with database email uniqueness deliberately substituted.
No real account modification, message delivery or notification is exercised.

Executed follow-up review compiled eight localized templates and preserved the
before/after named native controls for Student/Teacher create (six each), edit
(five each), message compose (five) and reply (three). Ten protected backend/
shared-client hashes match the captured before-state, as does the creation
client. Prior Arabic values and duplicate-key counts are preserved; this change
adds six owned labels while retaining concurrent catalog additions.

Actual manual browser review covered desktop edit open/validation, discard/stay,
fictional-memory save and filtered directory refresh. It covered message compose
and existing-thread reply, inline empty-body validation and synthetic completion
without leaving People. Measured desktop gaps are 20px between the two main
cards and 10px between person cards. Arabic/RTL/dark phone review at 320px
confirmed aligned 44px New/Filters buttons, search/status disclosure, active
filter preservation and restored selected profile after a memory-only edit.
Draft protection, character counting, unchanged-form close and message completion
were reviewed. A direct 320x740 viewport kept the edit dialog within y=12..728,
with a scrollable body and no horizontal overflow (286px client/scroll width).
The compact message textarea is 160px high. Final desktop/phone browser warning
and error logs were empty. Captured screenshots are in the ignored evidence
directory. The temporary viewport was reset and current review tabs closed.

Final follow-up checks: both changed JavaScript clients pass Node syntax checks;
CRLF-aware `git diff --check` exits 0, staging is empty and HEAD is `10e5003`.
These are bounded static/manual receipts, not automated regression or live
authorization/transaction/delivery acceptance.

## Centered creation: integrity and verification

The dialog imports only the expected creation form from its exact existing
same-origin GET. Its kind and Administrator public-id/auth-version/role binding
must match. Editing forms have no creation marker and cannot be imported.
The omitted native action is resolved explicitly to the fetched creation URL.
Original fields, CSRF, password rules, validation and server handlers are kept.
The form's helper heading is Account role, including the corresponding Arabic
label, rather than the unrelated shared sign-in/placement wording.

The original POST payload is captured before disabling submit controls. There
is one pending submission, no automatic POST retry and no cancellation of a
pending write. Authoritative form errors replace only the dialog form; the
shared draft guard registers each returned form. Escape/Close/Cancel/backdrop
restore focus and require discard confirmation when entered details remain.
Native modal focus management, logical layout, 20px corners and a bounded scroll
area support desktop and phones.

Successful creation requires the native redirect to an authorized matching-kind
profile, current actor binding and the exact public-id URL. Only then is the
dialog released and the same People directory address reloaded. Existing search
and status filters remain in effect, including when they exclude the new row.
An unconfirmed response retains entered details, disables another submission
and offers Check directory; no successful account is fabricated.

Executed bounded review compiled the five affected localized templates and
passed the new script's Node syntax check. Before/after rendered control
sequences match for both roles in creation (six controls) and editing (five
controls). Student/Teacher handlers, form/validator definitions and shared
workspace code have matching captured source hashes. Eight dialog/role labels
were added without replacing prior catalog values.

Real-browser manual review covered Student and Teacher opening, empty-form
validation within the dialog, draft discard/stay, unconfirmed-result retention,
and a fictional-memory successful Teacher submission returning to the same
directory. A 320px Arabic/dark phone frame contained the dialog and content
without horizontal overflow, and Escape closed the empty form. The final
desktop dialog capture had no browser console errors.

Ignored evidence: `instance/people_create_dialog/`. The presentation fixture
uses a unique session cookie, native field/CSRF validation and a deliberately
substituted in-memory email-uniqueness check. Its synthetic account exists only
in memory. No production application factory, engine, SQL, password hashing,
commit, real account creation, automated tests, migration or deployment ran.
These receipts do not establish live-MySQL uniqueness or persistence acceptance.

## Verification and limits

Sidebar correction review: all four Student/Teacher directory/profile renders
contain one active People sidebar link, no separate Students/Teachers sidebar
links and the correct active internal tab. Actual browser review confirmed
People stays active while the Teachers tab is selected. CRLF-aware diff check
returned exit 0. Ignored correction evidence is in
`instance/people_sidebar_20261023/`; no automated tests or database access ran.

Executed bounded static review compiled all 267 templates present at that
checkpoint with the real OwnedUIExtension and StrictUndefined. The new script
passes `node --check`. A rendered before/after inspection found identical native
status-form actions, confirmations and hidden/control sequences for active and
suspended Students and Teachers (four combinations). Hashes confirm unchanged
Student/Teacher views, account write handlers and the shared query-filter client
relative to this selection's captured before-state. Shared workspace code and
other Admin areas were also changing concurrently; those changes were preserved
and are not attributed to People.

Actual browser review used production templates/assets in an ignored local
fictional-memory fixture. It creates no application factory or database engine,
intercepts every application view, rejects unlisted requests and blocks writes.
Reviewed desktop list/automatic selection, switching profiles, suspended-account
presentation, Teacher assignments, search/status updates and empty results.
Reviewed 375px phone list/profile/back and Arabic/RTL dark presentation at 320px
for both list and selected profile, with no horizontal document overflow. A
script-free native profile retained its accessible status form and both hidden
signed controls. These are presentation/static receipts, not transaction tests.

Final `node --check` and CRLF-aware `git diff --check` both returned exit 0;
staging remained empty and HEAD remained `10e5003`. Owned visible People labels
have Arabic entries. `pf-content` is a structural class block, not a translation
key. The final desktop capture reported no browser console errors.

Local ignored evidence is under `instance/people_focus_20261023/`. No automated
tests were created or run. No actual MySQL operation, migration, business-data
change, scheduler, installation, staging, commit, push or deployment occurred.
Live authorization/session changes, write delivery, contention and comprehensive
end-to-end regression acceptance remain unexercised. Stop point: the selected
People presentation is implemented locally within the approved boundary.
