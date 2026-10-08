# Quiz answering and review — 2026-10-07

The owner requests direct answer saving, a question sidebar, review bookmarks
and direct question navigation for ordinary Student Quizzes. This supersedes
the earlier manual answer-save presentation for that activity. Listening remains
on its existing save controls; final submission remains an explicit decision.

## Student interaction

- Selecting a radio or checkbox saves immediately through the existing answer
  POST. There is no visible Save answer or Save and next button. Multiple-choice
  changes replace the complete selection set; clearing all selections returns
  that question to unanswered while retaining the answer history row.
- Successful saves and bookmark updates are quiet. The question sidebar follows
  confirmed saves. Only failures show a message, Retry and the explicit
  discard-and-reload recovery link; native answer-save success flashes are also
  suppressed on the question page.
- The sidebar links every question in authored order, shows answered/unanswered
  text, highlights the current question and shows its bookmark independently of
  answer status. It scrolls locally for long Quizzes. The final-submission card
  sits below the question list. On narrow layouts both follow the question;
  shared palette tokens support Arabic/RTL and light/dark.
- Bookmark toggles a review flag for the current question. Flags survive question
  navigation and reload during the browser session. They are not permanent or
  synchronized across devices. The icon-only toggle sits at the physical upper
  right of the question card in both languages, with an accessible action label,
  pressed state, focus ring and hover/press/marked effects respecting reduced
  motion. The explanatory and success text was removed at the owner's request.
- Same-tab links and form submissions wait for the latest answer and any pending
  bookmark response. Choosing again during a save sends the latest complete set
  after the previous write responds. Writes are never aborted to send a newer
  selection, and a missing response is treated as uncertain rather than saved.
- Previous/next and question-list links carry the current document/list/sidebar
  pixel positions into the next own-attempt question page. A single tab-scoped
  sessionStorage entry contains only coordinates, time and authorized public
  scope/target paths. It is consumed once, expires after five minutes and is
  validated for matching attempt/destination and bounded finite coordinates.
  No answer, prompt, option, bookmark, token or credential is stored. Restoration
  after layout/font loading stops if the user interacts. If storage is restricted,
  native protected navigation continues. Back/forward-cache reauthorization keeps
  the current position when reloading the authorized question.
- Answer counts and the unanswered-submission acknowledgement follow confirmed
  saves. A changed count clears the earlier acknowledgement. Final submission
  still rechecks unanswered state on the server, and saved results stay frozen.
- With JavaScript disabled, the explicit Continue fallback uses the original
  signed native answer POST. No browser storage holds prompts or answers.

## Protected server paths

`student.quiz_answer` negotiates JSON with `X-Quiz-Autosave: 1`. This header only
chooses response/empty-clear presentation. It bypasses no role, CSRF, visibility,
own-attempt, enrollment, hierarchy, locking, signed-state, active-option,
cardinality, deadline or finalization check. Native callers keep their nonempty
selection rule. Both initial answer insertion/flush and replacement commit are
inside the safe IntegrityError handler. JSON responses contain only the current
public question identifier and saved answered state, or a safe error/result URL.
Successful AJAX saves do not accumulate native success flashes.

The question index is one bounded read of internal/public identifiers, at most
100 questions plus an overflow sentinel; overflow fails closed. Internal ids
serve the batched progress lookup and never enter template DTOs. No other prompt,
option or answer key is loaded for navigation. Answered state now requires an
existing selection, so a retained answer row whose selections were cleared is
unanswered for both progress and final submission.

`student.quiz_bookmark` is a CSRF-protected, Student-only nested POST. It checks
the same published own Quiz/attempt, settles due attempts, verifies the existing
question-bound signed state and proves question membership. It writes only a
dedicated signed HttpOnly SameSite=Lax session cookie, scoped to `/student`,
using the configured secure-cookie setting. Actor public id/auth version bind
the flags to the account; a tampered, malformed or mismatched cookie yields no
flags. At most the latest 16 marked attempts carry a 100-bit question bitmap.
Positions are stable because authored question order is frozen once used.
No academic model, database migration, scoring policy or research dictionary,
sampling, session, provenance or retention rule changes. Existing server save
outcomes remain in use; no artificial manual-save interaction is emitted.

## Current verification boundary

The presentation follow-up compiled 197 Jinja sources and parsed 344 application/
migration Python sources, rendered 160 guarded offline Quiz states and preserved
all native POST protocols in every before/after render, including 320 answer/
final-submit input comparisons. Placement review confirms separate bookmark and
answer forms inside the question card, and final submission below the sidebar
list. Quiet success feedback, removed explanatory text, localized accessible
icon labels and bounded one-use position storage were reviewed in source. The
protected Quiz/query/bookmark/transaction/grading/expiry/Listening sources and
shared workspace guards are unchanged. All 3,228 prior Arabic entries remain
unchanged; one icon action label extends the catalog to 3,229. Node syntax and
Git diff whitespace checks passed; the index remains empty.

Original autosave implementation evidence: guarded offline review compiled 197
Jinja sources and parsed 344 application/
migration Python sources. Synthetic fixtures rendered 160 Quiz states covering
single/multiple selection, empty/partial/complete/cleared answers, bookmarks,
first/last questions, 1/5/100 questions and English/light versus Arabic/dark.
All 320 native answer/final-submit input comparisons preserved CSRF/state,
selection values and final-submission fields. Markup is balanced, has unique ids
and one workspace heading, keeps authored HTML escaped and exposes no answer-key
controls. Two bounded query statements compiled with MySQL syntax without SQL
execution. Pure selection/cookie helper review covered rejection/clear behavior,
refresh/unmark, tampering, account/auth-version isolation and cookie bounds.
Other Quiz functions, Listening route/template, shared replacement transactions
and grading/expiry sources are unchanged. All 3,207 earlier Arabic entries are
preserved; 21 owned phrases extend the catalog to 3,228. Node syntax checks and
Git diff whitespace review passed; the index remains empty.

No application factory/view dispatch, application database access, browser,
server, migration, installation, automated test suite or Git mutation. Actual
network retries, rapid input/navigation, cross-tab behavior, device layout and
transaction concurrency require future runtime verification; source review is
not comprehensive regression acceptance. Separate-tab concurrent edits retain
the existing server locking/last-accepted-write semantics.
