# Student and Teacher Messages — 2026-10-26

## Authority and checkpoint

The owner requested applying the Admin Communication Messages tab to the
existing Teacher and Student Messages pages. Work starts from `10e5003` with
extensive pre-existing local edits preserved. This extends presentation and
progressive navigation to the two requested roles; it grants no additional
contacts, conversation access or delivery permissions. Existing source/manual
verification boundaries remain: no automated tests, actual database access,
real delivery, migration, scheduler, dependency, Git mutation or deployment.

## Implementation

- `messages/base.html` supplies one shared actor/auth-version/role-bound
  workspace and content boundary. Inbox, picker, compose and thread templates
  provide their original content through `message_content`. The Admin heading
  stays in its current shell; Student/Teacher keep their current role shell,
  single Messages heading and mobile navigation. Researcher retains native
  navigation without the progressive Communication client.
- Student and Teacher load the existing `admin_communication.js` client after
  `messages.js`. New message, Start a conversation, contact selection, inbox
  search, thread navigation and browser Back use the same native HTML routes
  inside the page. Its existing actor/object checks, stale-read cancellation,
  draft confirmation, validation and uncertain-send protection are reused.
  Message edit/hide/clear retain their native workflow owners.
- `messages.css` applies the Admin tab's 260px desktop rail, separated rows,
  selected marker, circular avatars, 20px panels, semantic message bubbles and
  composer treatment to Student/Teacher. Responsive flex sizing retains an
  internally scrolling list/timeline and a composer above the existing mobile
  dock. Typing has no blue textarea/composer outline; controls keep keyboard
  focus indicators. Theme tokens, RTL and reduced-motion handling remain.
- `layouts/portal_base.html` updates only the Messages stylesheet version for
  this work. Other source changes there predate this request. The progressive
  client changes only its two comments; its navigation and POST logic are
  unchanged. No endpoint, query, service, model, locale or migration is edited.

## Executed verification

- Nine localized Jinja templates compile in the existing StrictUndefined
  manual-render environment, without creating an application factory or DB
  engine. Node syntax checks pass for the Communication and message clients.
- All seventeen protected SHA-256 comparisons match the captured before-state,
  including routes, authorization/query/transaction/token/delivery services,
  models, message macros, existing message tools, shared draft guards, locale,
  Admin styling and Admin/Researcher layouts. All three child templates differ
  only in their block name; their native forms are byte-equivalent after
  whitespace normalization. CSS braces balance at 300 pairs; this is bounded
  static inspection, not a complete CSS validator or automated regression gate.
- Manual real-browser review uses production templates/assets with fictional
  memory. Student English/light desktop and 390px phone, plus Teacher
  Arabic/RTL/dark desktop and 320px phone were inspected. Desktop viewport was
  1280px; phone samples use isolated 900px-high frames. No horizontal overflow:
  measured client/scroll widths match at 1280, 390 and 320px. Phone composer
  bottom is 799px and the reviewed Student dock starts at 822px.
- Reviewed New message/Start a conversation and recipient/thread transitions,
  Student blank-body validation, Stay-on-page draft retention, fictional send
  completion, Teacher inbox search and browser Back restoring its `q` URL.
  Message colors and metadata contrast use semantic tokens. Emoji and the
  native phone message-action sheet remain available. No edit/delete action
  was submitted. Fictional send rehearsal delivers nothing outside memory.
- Evidence, before-state copies, temporary renderer and static receipt are
  ignored under `instance/role_messages_20261026/`. Screenshots include
  `student-desktop.png`, `student-phone.png`, `teacher-desktop-rtl.png` and
  `teacher-phone-rtl.png`. Final whitespace/Git checks and renderer cleanup
  are recorded in PROJECT_STATUS.md.

## Limits and stop point

This is local presentation and source verification. It does not establish live
MySQL persistence, executed authorization/locking behavior, notification
delivery, no-script browser acceptance or comprehensive regression coverage.
No automated tests or pytest collection were created/executed. No actual
database, real message delivery, business write, migration, reset/seed,
scheduler, dependency installation, staging, commit, push or deployment is
included. Stop after the requested shared presentation and bounded review.
