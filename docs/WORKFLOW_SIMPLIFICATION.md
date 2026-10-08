# Fewer clicks across the platform

Owner request: 2026-10-07. Review the whole platform for unnecessary steps,
then remove, combine or replace them. Implementation remains source-only.

The review covers the shared navigation/account/display/help/messages and
notifications, Student learning and activities, Teacher content/assessment/
feedback/attendance/grades, Administrator academic/account/enrollment/finance
tools, and Researcher configuration/session/export work. It uses source paths
and existing route contracts. It does not claim observed browser timings.

## Implemented shortcuts

Counts below are estimated **button/link activations from the stated starting
surface**, excluding typing, opening a select, entering credentials and network
time. They describe the old source path versus the new available path; they
are not measured user-study results. Ordinary actions remain available.

| Starting surface and task | Before | Available now | Decision |
| --- | --- | --- | --- |
| Teacher dashboard or review queue: read work and write feedback | Open submission, open feedback: 2 | Review feedback: 1 | Open the existing editor directly; it already includes the answer or recording. |
| Assignment/recording submissions: review feedback | Read/listen, open editor: 2 | Review feedback: 1 | Keep read-only detail as a secondary link; invalid reviewer records retain their review state. |
| Feedback editor: return to this group's pending work | Global queue, select group, apply: 3 | Group review queue: 1 | Carry the current authorized Group into the existing GET filter. |
| Gradebook: enter or correct an item's scores | Open item, open score sheet: 2 | Enter/Correct scores: 1 | Show the direct action only while the Group is operational; keep item detail. |
| Attendance list: mark an existing draft | Open session, open marking: 2 | Mark attendance: 1 | Show only for an operational draft; finalized sessions keep their detail link. |
| Attendance draft: mark 30 students present and save | 30 individual marks + save: 31 | Mark all present + save: 2 | Explicit form-only bulk selection; review exceptions before saving, with an immediate undo. |
| Administrator Group list: manage members or schedule | Open Group, choose management tool: 2 | Members/Schedule: 1 | Link to the established role-gated tools using the Group public ID. |
| Research export list: download an existing archive | Open detail, download: 2 | Download ZIP: 1 | Show only when the archive row exists; retain details and the protected download checks/audit. |
| New Quiz/Listening question: save and enter the next | Save, add question: 2 | Save and add another question: 1 | Redirect to a fresh blank form after successful commit. |
| Ten questions, starting at the first blank form | 10 saves + 9 add links: 19 | 9 save-and-add actions + final save: 10 | Reuse the same validated write path; no duplicate or automatic question creation. |
| New Lesson form: save and begin adding materials | Save, open materials: 2 | Save and add materials: 1 | Open the existing material desk after successful draft creation. |
| Teacher course content tree: add a Lesson to a Unit | Manage lessons, new lesson: 2 | Add lesson: 1 | Use the exact nested Unit/Group path; show for the active hierarchy only. |
| Student Lesson: complete and continue | Mark complete, next: 2 | Complete and next lesson: 1 | Resolve the next published own-course Lesson on the server; failure stays on the current Lesson. |
| Activity library: choose a type | Type cards and a duplicate dropdown | Type cards only | Carry the chosen type in the GET form's hidden value; reduce the remaining filter grid to three fields. |

## Brainstorm decisions by workflow

| Area | Pattern considered | Result |
| --- | --- | --- |
| Shared navigation | Repeated sidebar scrolling, separate Help/Display pages | Earlier changes already preserve sidebar position and open utility panels in place. Retain them. |
| Shared account | Logout/profile scattered between menus | Earlier account card already consolidates these actions. Keep one account entry point. |
| Sign-in | An extra Continue click after authentication | The branded transition already advances automatically; preserve the requested visual and native fallback. |
| Student course content | Returning through Unit lists between Lessons | Existing previous/next links and inline outline already bypass the lists; complete-and-next now combines the final two actions. |
| Student Quiz answers | Save, then navigate separately | The owner's subsequent Quiz request replaces manual save with option-change saving and direct all-question shortcuts/bookmarks. Retain final submission and unanswered-question acknowledgement; see QUIZ_INTERACTION.md. |
| Student Listening answers | Save, then navigate separately | Existing Save and next combines these actions. Retain the separate final submission and unanswered-question acknowledgement. |
| Timed activities | Start an attempt automatically when opening a card | Keep an explicit start after the brief: starting consumes an attempt and starts its deadline. A navigation link must not start it. |
| Written/recorded submission | Remove the final submission decision | Keep the final decision because the saved answer/recording cannot be replaced. Existing execution pages already combine the brief, work and submission. |
| Teacher feedback | Separate content reading from the comment editor | Use the direct editor shortcuts above; comments remain separate from grades. |
| Teacher authoring | Return to the overview after every repeated creation | Add save-and-continue where the next context is already known: questions and Lesson materials. |
| Teacher course structure | Open an intermediate Lesson list merely to add a Lesson | Add the exact nested creation shortcut to each eligible Unit. |
| Teacher attendance | One identical selection per Student | Add explicit bulk present selection with undo; preserve notes, manual exceptions, draft save and finalization. |
| Teacher attendance creation | Skip choosing/reviewing the class and captured roster | Retain the schedule/date preview and signed creation confirmation. These establish the real meeting and frozen roster. |
| Teacher grading | Open detail before entering scores | Link directly to the score sheet; retain separate release review and correction rules. Never fill grades automatically. |
| Administrator account lists | Open detail merely to edit/search an account | Existing direct Edit/status actions and live name/status search already avoid that detour. Retain them. |
| Administrator academic setup | Require detail before every course/group edit | Existing list-level Edit links already cover this; Group member/schedule shortcuts close the remaining useful gap. |
| Administrator enrollment | Hide the source/destination episode review | Retain eligibility and correction context. The earlier started-group acknowledgement was already removed by the owner. |
| Administrator finance | Remove previews/reasons/confirmation to save clicks | Retain the existing inline preview, authoritative save, reasons and document/history rules. Student financial records already collect related operations in one context. |
| Messages | A subject-entry step before sending, or a separate Send click | Existing first-message flow supplies a default subject; Enter sends, Shift+Enter inserts a line. Retain direct conversation links and emoji search/categories. |
| Messages | Silently delete/clear without deciding the scope | Retain explicit own-message and personal-conversation scope decisions and stale-form protection. |
| Notifications | Open the whole inbox just to inspect one item | Existing bell preview and protected direct opening already cover this. GET previews still leave unread state intact. |
| Research sessions | Reload after each of several filters | Keep one Apply action so the researcher can set dates/version/state together. Existing timeline type filtering operates on the loaded events in place. |
| Research configuration | Activate/pause collection through generic shortcuts | Retain the version-specific review, signed state and explicit collection decisions. |
| Research exports | Download through an intermediate detail page | Add list-level download. Selection review remains optional and available before archive creation; provenance and immutable snapshot rules remain server-owned. |

## Implementation and integrity

- Direct actions use existing nested endpoints and public identifiers. Their
  destination views still recheck current role, assignment/enrollment,
  operational state and object ownership. Opening an editor saves nothing.
- New `after_save` submitters choose between fixed server-built navigation
  destinations. No URL, Student identity, question placement or next-Lesson
  identity is accepted from the browser. Unknown values use ordinary behavior.
- Save-and-add redirects only after the original successful commit. The new
  GET reauthorizes and issues a token for the current parent version. Validation,
  stale state, locking, publication freeze and error branches are unchanged.
- Complete-and-next uses the same signed completion action/version and
  transaction. Only changed/already-completed outcomes may continue; an
  unsuccessful save cannot navigate away and suggest completion succeeded.
- Bulk attendance changes existing radio controls in memory only. It makes no
  request, stores nothing, changes no roster/note/state field and never saves or
  finalizes. A later manual mark invalidates bulk undo to preserve that edit.
  Existing unsaved-form protection sees the changes. Controls stay hidden
  without JavaScript; ordinary individual marking works as before.
- Export availability is one correlated EXISTS in the paginated, provenance-
  scoped list query. It reads no archive bytes or account mapping and adds no
  per-row query. Download still rechecks retention, role, provenance, bytes and
  SHA-256 through the established audited endpoint.
- All new owned text is in the Arabic dictionary. Native controls, existing
  theme styles, RTL, sidebar offsets, one page title and unsaved-work protection
  continue to apply. No additional confirmation popup was introduced.

## Verification

Current source review results are recorded in PROJECT_STATUS.md. Verification
is bounded AST/Jinja/JavaScript and offline synthetic markup review with network
and database operations forbidden. Actual browser usability, responsive layout,
form transactions and concurrency acceptance remain unexecuted under the
owner's source-only instruction. No automated test suite, server, migration,
application database operation, dependency install, staging or commit is part
of this work.
