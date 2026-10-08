# Student Template ST2 — candidate delivery and baseline change record

Candidate: `YC-VA-AT2-20261008`. Status: **IMPLEMENTED / BOUNDED LOCAL VERIFICATION COMPLETE / NOT FROZEN**.
Original approved source baseline: `YC-VA-D1-20261008`. Owner visual acceptance and the remaining acceptance gates are required before a new freeze. This document does not supersede the historical freeze record or enable Study collection.

## Authorization and safe recovery

The current owner request authorizes a high-fidelity visual port of the active AdaptiveTemplates Student runtime, with native LMS functionality, fixed research semantics, the existing canvas, protected Login and unchanged Messages. It explicitly forbids preserving the rejected ST1 state. No rejected-state backup/archive/snapshot was created.

The original source release was independently verified: ZIP SHA256 `c1aa1c588697589c2cdebd48a7b22ddab86dfbfc98fe7af0bf5e96c0a5a687fb`; all original release files matched after selective recovery. The restored runtime inventory contains **619 files**, aggregate SHA256 `77f2ff626775cf6443a53ec37d95dce385476c9d6e87dbd611314a15f52c70b3`. Git HEAD alone was not treated as recovery authority.

Only five files were restored: `student/dashboard.html`, `layouts/portal_base.html`, `docs/VERSION_A_BASELINE_FREEZE.md`, `docs/VERSION_A_READINESS.md`, `docs/VERSION_A_DELIVERY.md`. Six proven ST1-added files were removed: `student_template.css`, Inter/Outfit font+license pairs, and `docs/VERSION_A_STUDENT_TEMPLATE.md`. The fonts used now were acquired afresh after baseline preservation. No whole-repository restore/reset/clean or Git history mutation occurred.

Before ST2 edits, the restored complete authored source was archived independently as `YC-VA-D1-PRE-STUDENT-TEMPLATE.zip`: **673 files**, SHA256 `6951c61a41ca8471d55538cd90f9063ec4f4a2da53c0e3311d909cde24102c4f`. Its manifest records Git revision, MySQL revision, dictionary/scope/configuration identity, exclusions and recovery procedure. Secrets, dependencies, databases, private uploads, caches and Git internals are excluded; database content is not a source snapshot. All ZIP entries/checksums were verified. The original release, restored snapshot and historical freeze remain separate and immutable.

Local evidence location: `C:/Users/abdul/.codex/visualizations/2026/10/07/01a11772-b277-7c42-b248-f13e62d51e53/student-template-st2`. Recovery evidence: `baseline-restoration.json`, `restored-source-manifest.json`, ZIP checksum and `RESTORE-PROCEDURE.md`.

## Active reference and visual ownership

Router authority was inspected through `src/app/App.tsx`, `src/pages/StudentDashboard.tsx`, `src/pages/student/routes.tsx`, `LearningViews.tsx`, `CommunityViews.tsx` and `studentUi.tsx`. The active component graph, motion hook and actual styles/assets were reviewed. Legacy `src/imports` components were not taken as runtime authority.

| Active React source | Flask/Jinja destination | Port / adaptation |
| --- | --- | --- |
| App.tsx -> StudentDashboard.tsx | layouts/portal_base.html + student/template/_shell.html | Actual Router verified. Floating 272px rail at 24px, 32px radius; main inset320; native destinations instead of demo routes. |
| StudentDashboard navigation/header/profile | student/template/_navigation.html + _shell.html | Bare aligned SVG icons; native IA/order; branded mark, Display/Bell/Account, native bottom nav/More. Messages excluded. |
| LearningViews.StudentOverview | student/dashboard.html | Continuation/next-class 1.55:1 composition, group context, server progress and multi-course studio; no mocked completion. |
| LearningViews.CourseCard | student/template/_ui.html course_card | Navy information/photo 1.15:1 region, 44px title, integrated footer/action/progress. Existing study photo matches reference SHA256. |
| LearningViews.LessonsPage / LessonPage | student/learning/outline.html + lesson.html | Numbered rows, 250px map and reading studio; native material content and completion footer. No new focus-mode/tabs feature. |
| LearningViews.ActivityHub / ActivityCard | student/template/_activities.html | Visual glass activity cards/pills; original native filters, page links, counts and ordering. |
| LearningViews.QuizPage / QuizResultPage | student/quizzes/detail.html + result.html | Tinted instructions, navy completed attempts, glass availability, native result review; multiple attempts retained. |
| LearningViews.SpeakingDetailPage | student/speaking/detail.html + record.html + receipt.html | Speaking visual family; native recorder module, real upload/validation and confirmed receipt. No simulated microphone business state. |
| CommunityViews.SearchPage | student/search.html | Navy search surface, glass filters/results; original server GET search and global ordinal retained. |
| CommunityViews.Calendar / Announcements / Discussions / Notifications | Student feature templates / owned macros / features.css | Feature-specific layouts and native page model; calendar entries, permissions and all real actions preserved. |
| studentUi.WorkspaceTitle / Panel / SectionHeading / Badge / EmptyCanvas / fields | student/template/_ui.html + CSS/student/foundation.css | Outfit/Inter/Arabic hierarchy, translucent panels, semantic statuses/forms/empty states; native form semantics and focus. |
| student/useStudentMotion.ts | Existing unchanged D-MOTION JS + Student-scoped CSS | Fixed short page arrival/hover/state feedback. No React runtime, timed stagger chain, ambient loop, fake progress or adaptive effect. |
| theme.css / portal.css / index.css / fonts.css | CSS/student/foundation.css / shell.css / features.css | Port actual geometry and surfaces, not the prototype page-gradient backdrop. Canonical canvas/tokens remain authoritative. |
| Prototype functions without full LMS equivalents | Native Assignment/Quiz-taking/Listening-taking/Records/Account templates | Documented extensions of the same visual family. Prototype has no active Assignment submit or Quiz-taking equivalent; never imply exact parity there. |

The canonical Student canvas remains `var(--ui-page)` (native light render `rgb(234,240,250)`). No prototype full-screen gradient, color blobs, ambient motion or React runtime was introduced. Component-only navy gradients, tinted covers, highlights and rings are allowed. Multiple real courses/groups, attempts and records are rendered from existing server view models.

CSS ownership:

- Canonical shared tokens/base/appearance remain unchanged.
- `student/foundation.css`: Student font namespace, semantic visual primitives, light/dark glass variables, focus/forms/status/empty/table/dialog variants, transparency/contrast fallbacks.
- `student/shell.css`: actual reference shell/header/nav geometry and native mobile nav/More variants.
- `student/features.css`: native Student feature compositions, course studio, learning/activity/search/community/account layouts.
- Every new selector is Student-scoped; font faces use `ST Outfit`, `ST Inter`, `ST Arabic` names.
- `template_student = current_user.role == 'student' and request.blueprint != 'messages'` gates both CSS loading and the Student shell. Messages retains the original shell. Login does not load the new Student styles. Other roles retain original branches; shared Teacher macros are not overwritten.
- Student skips obsolete D Student layout layers instead of stacking ST2 overrides over them; shared/base behavior and unchanged native scripts are retained.
- Functional and research `id`, `name`, `action`, context, `data-*` hooks remain independent of decorative class names. No Python, JavaScript, form, model, service, collector, migration or schema source changes.

Fonts: locally served variable Outfit, Inter and Noto Sans Arabic, each with its copied SIL Open Font License1.1. Sources are the official `google/fonts` OFL directories `outfit`, `inter`, `notosansarabic`. No Google Fonts runtime fetch is used. The existing study image is byte-identical to the active reference asset: SHA256 `d03af3be4ce5014f0272c31beb7519b7597127acc2c7454cf9109a977fb8674c`.

## Complete Student surface inventory

All **31 requested areas** were visually/source reviewed and integrated. `page-coverage.json` records native observed paths/widths and qualification. Some items are state variants inside one native route, not new pages. Help/Display retain their actual Dashboard/popover entry behavior.

| # | Student surface | Jinja owner | Visual composition | Evidence file | Verified scope / limits |
| --- | --- | --- | --- | --- | --- |
| 01 | Dashboard | student/dashboard.html | Dashboard | native-page-coverage.json; comparison pair 1 / 9 | Real multi-group counts, native next lesson and next class; server-supplied progress. Empty state also rendered offline. |
| 02 | Courses list | workspace/groups.html | Course cards | native-learning.json | All real groups / courses; operational and unavailable states retained. Native course-card composition replaces the old D list. |
| 03 | Course details | workspace/course.html | Course hero / progress / sections | native-learning.json | Native group/course context and all real destination links preserved. |
| 04 | Units and lesson outline | student/learning/outline.html | Numbered glass lesson rows | native-learning.json; comparison pair 2 | Native ordering, published visibility, multiple units and group scope. |
| 05 | Lesson details | student/learning/lesson.html | 250px course map + reading studio | native-learning.json; native-final-student.json; comparison pair 3 / 10 | Native completion/undo, signed progress state, previous/next and complete-and-next controls retained. |
| 06 | Materials | student/learning/lesson.html | Integrated material blocks / media | source-compatibility.json; native-learning.json | All rich-text/link/image/audio/video/PDF/file branches source-reviewed. Sanitization, private downloads and media hooks unchanged; not every material format physically played. |
| 07 | Activities hub | student/activities.html + student/template/_activities.html | Glass activity grid / native GET filters | native-page-coverage.json; comparison pair 4 | Original types/state/group/course filters, result order, identities and pagination. |
| 08 | Text Assignment | student/assignments/detail.html | Writing panel / availability context | native-learning.json; comparison pair 7 | Actual Teacher-authored Text task, native validated final submission and receipt. |
| 09 | File Assignment | student/assignments/detail.html | Single-file native form variant | native-learning.json | Actual Teacher-authored Single File, invalid-signature rejection, PDF final receipt; anonymous access blocked. |
| 10 | Assignment receipt / feedback | student/assignments/detail.html | Server-confirmed receipt / feedback section | native-learning.json; source-compatibility.json | Receipt exercised for Text/File. All released/pending feedback and finality branches source-reviewed; not every feedback/deadline state separately rehearsed. |
| 11 | Quiz intro | student/quizzes/detail.html | Tinted instructions + navy attempts + glass availability | native-media.json; native-final-get.json; comparison pair 5 / 11 | Native start/continue CSRF form unchanged. Instructions remain visible; all attempts and authoritative state retained. |
| 12 | Quiz question | student/quizzes/question.html | Question paper + native question-map panel | native-media.json; native-final-student.json; comparison pair 6 / 12 | Actual autosave failure/retry acknowledgement, bookmarks, navigation and final submit; no auto-next. |
| 13 | Quiz result | student/quizzes/result.html | Score hero / question outcome review | native-media.json; native-final-student.json | Actual server result, native unanswered handling and answer-key boundaries. |
| 14 | Listening details | student/listening/detail.html | Activity brief / audio context | native-media.json | Native lifecycle and private audio authorization. |
| 15 | Listening question | student/listening/question.html + student/template/_audio_player.html | Listening paper / manual-save controls | native-media.json | Actual audio response and explicit native answer save; not Quiz autosave. |
| 16 | Listening result | student/listening/result.html | Result summary / question review | native-media.json | Native final submission and result rendered. |
| 17 | Speaking details | student/speaking/detail.html | Speaking brief / readiness | native-speaking.json | Teacher publication and existing authoritative availability. |
| 18 | Recorder | student/speaking/record.html | Recording studio / safe upload fallback | native-speaking.json; native-speaking-playback.json; native-speaking-validation.json | Permission denied path + actual MediaRecorder using a synthetic browser microphone; real multipart upload. Physical microphone not reviewed. |
| 19 | Speaking receipt | student/speaking/receipt.html | Confirmed receipt / private playback | native-speaking-playback.json; native-speaking-fallback.json | Confirmed WebM recorder and WAV file-fallback receipts; private playback and anonymous protection. |
| 20 | Search | student/search.html | Navy search band / native filters / result surfaces | native-research-pilot.json; research-export-verification.json; comparison pair 8 | Server search, filters, original rank/grouping and approved global ordinal. No-results branch reviewed; ordinal32 exported. |
| 21 | Records / historical episodes | student/records/index.html + detail.html | Enrollment journal / record sections | native-page-coverage.json; native-final-get.json | All available real enrollment-section destinations reviewed; native bounded history and released-only rules unchanged. |
| 22 | Attendance | student/attendance/list.html | Native filters / table / status treatments | native-page-coverage.json | Native final attendance meaning, denominator and filter scope unchanged. |
| 23 | Grades | student/grades/list.html + detail.html | Released-grade table / detail panel | native-page-coverage.json | Existing released-only results and detail permissions; no invented analytics. |
| 24 | Announcements | student/announcements/list.html + detail.html | Feature-specific announcement surfaces | native-page-coverage.json | Real list/detail/acknowledgement contracts unchanged; no new announcement feature. |
| 25 | Calendar | student/calendar/index.html + student/template/_calendar_macros.html | Glass range controls / list-week-day | native-page-coverage.json; native-final-get.json | Native bounded ranges and entries. Week internal horizontal region is keyboard focusable; day/list behavior remains native. |
| 26 | Discussions | student/discussions/overview.html + list.html + detail.html | Group index / discussion reading / native composer | native-page-coverage.json | Native forms, routing, order and participant permissions retained; not a new messaging product. |
| 27 | Notifications | notifications/inbox.html (source unchanged), Student-scoped CSS | Refined inbox / desktop Bell preview | native-modes-isolation.json; native-final-student.json | Desktop preview bounded/scrollable; mobile Bell retains direct Inbox navigation. Native read operation exercised. |
| 28 | Account | workspace/account.html | Student-only account sections / native forms | native-quality.json; native-final-student.json; research-export-verification.json | Native prefixed WTForms CSRF, invalid/valid photo, password change/session invalidation. Passwords/photo/file names/typed values absent from research metadata. |
| 29 | Help | workspace/help.html + native Help popover | Student-scoped guidance variant | native-quality.json | Current Help entry redirects to Dashboard/popover; keep that real workflow. Legacy template still integrated/source-reviewed. |
| 30 | Display preferences | workspace/preferences.html + native Display popover | Student-scoped local preferences | native-final-student.json; native-quality.json | Current entry uses native popover. Light/Dark/System, comfortable/compact, reduced motion and native save retained. |
| 31 | Error / empty / unavailable | errors/workspace.html + feature branches | Student-only safe status / recovery surfaces | native-error-isolation.json; native-final-get.json; student-template-source-review.json | Native unavailable404, CSRF400 and form errors reviewed; final standalone Student variant excludes auth/Messages paths and other roles. Offline empty Dashboard/Courses/Search and source branches; not every rare server failure induced. |

## Motion and accessibility contract

Fixed page arrival:180ms,4px; hover/press and navigation feedback:140ms; existing D-MOTION native dialog/sheet/focus behavior is unchanged. Effects do not delay interaction, sampling, navigation, autosave or submission. No auto-next, fake loading/success/progress, recurring ambient animation, long stagger chain, adaptive intervention or VersionB condition was added.

Native mobile bottom navigation and More remain the single mobile navigation system. No prototype Drawer runtime was imported. Responsive reviews cover1440,1024,768,390,320px. Controls retain native semantics, labels/CSRF, focus feedback and touch targets. The calendar week grid scrolls within a named keyboard-focusable region. Reduced-motion preference/device settings were exercised; backdrop/reduced-transparency/forced-color CSS fallbacks are implemented but not all physically exercised on all browsers/assistive technology.

## Visual parity evidence

`comparison-gallery.html` contains **12 matched-viewport pairs**, plus course-card and navigation crops, captured from the actual active React reference on8443 and actual isolated Jinja templates/local assets. Reference and Jinja demo fixtures use matching labels only outside the application database. Native workflows use real development server data separately.

At1440px, both sidebar bounding boxes are x24,y24,width272,height952. Outfit heading geometry, main alignment, glass borders/shadows/radii, navy/image course-card proportions, studio and activity/search panels were inspected and refined. The final Quiz refinement moves existing visible save guidance into its instruction panel and separates native attempts from availability; it changes no action/form semantics.

Intentional exceptions: page backdrop; native IA/order; existing mobile bottom navigation/More; native multiple groups/attempts; real availability/rules/CSRF/materials/feedback; absent prototype submission/taking routes. Login/Messages are excluded. No pixel-perfect claim is made for these necessary differences.

## Actual local runtime evidence

This is bounded native browser/MySQL and static review, **not exhaustive automated regression acceptance**. No new automated test package, pytest run, dependencies, migrations or schema reset was created.

- `student-template-source-review.json`:208 Jinja sources compiled;32 light/dark isolated fixtures, including empty and protected surfaces.
- `native-page-coverage.json`:450 native GET renders across five widths; no horizontal overflow or page errors. Some request URLs are aliases to the same final route.
- `native-final-get.json`:198 further native renders after an owned server restart with final Jinja sources; Quiz, all available record sections, Calendar week/day and unavailable404; no overflow/page errors. Final Quiz Light/Dark/Arabic mode rows use verified native language/theme values.
- `native-learning.json`:real Student/Teacher login, course/outline/lesson, completion+undo restoration, search; native Text/File authoring/publication and final submissions; invalid file signature; owner/Teacher file download200, anonymous302, no-store/nosniff/VaryCookie and task-type freeze.
- `native-media.json`:real Quiz start/autosave503/Retry200/bookmark/manual question navigation/final result; Listening audio200/manual-save/final result. An external Speaking locator stopped this driver; the separate recorder review completed that flow.
- `native-final-student.json`:native Quiz second attempt/autosave/final result, notification read, invalid and valid photo with original bytes restored, prefixed Account CSRF, compact20px mode, reduced motion, System follows device Dark, native password change forces Login and relogin works.
- `native-speaking.json`, `native-speaking-playback.json`, `native-speaking-fallback.json`, `native-speaking-validation.json`:actual synthetic-device getUserMedia/MediaRecorder, WebM final receipt and private playback; denied permission fallback; valid WAV file receipt; invalid signature has field error and no receipt. Physical microphone not tested.
- `native-communication.json`, `native-modes-isolation.json`:Inbox/New/Reply/Edit/Hide/Clear, mobile actions/focus, no-results and retained other-participant history; Login/Messages Light/Dark desktop/mobile do not load Student CSS. Teacher/Admin/Researcher native shells were inspected without Student CSS.
- `native-quality.json`:prefixed WTForms CSRF, native Help/Display popovers, More focus/body-inert/Escape, visible keyboard focus and CSRF rejection/logout.
- `native-modes-isolation.json`:66 selected native Student renders across Dark-English1440, Dark-Arabic390 and Light-Arabic320; local Arabic font/RTL, no overflow/page errors. Native desktop notification preview stays bounded; mobile Bell remains direct Inbox navigation.

Review-driver corrections are explicit, not hidden: recorder input is populated by the original submit handler (not during preview); playback needed a readiness wait; invalid Speaking HTML returns200 with field errors; reused fictional message text can match two retained rows; Account CSRF field names are prefixed; mobile Bell navigates to Inbox rather than opening desktop preview; final mode checks use `aelms.language`. Resolution evidence is recorded in the companion reports. No business/JS fix was made to force these assumptions.

## Research compatibility and current collection state

Unchanged dictionary/schema:`natural-use-events.v1`; dictionary revision`va-w7-r1`; scope`va-scope-r1`; export`natural-use-csv.v2`. All stable field/control hooks in the bounded fixture comparison match the restored baseline. **496 protected source files** hash-match, including all existing JS, auth, Messages, Teacher/Admin/Researcher, business/security/services/migrations, survey and historical freeze files. No missing baseline files or unexplained source changes were found.

The preserved original configurationv4 was paused at capture. A **separate native Development operational-review v5**, public ID`6e2277a9-e71e-407f-954a-a45746ba72d0`, was created with all nine original policy values unchanged. Native activation retiredv4; no historical source/freeze/snapshot was overwritten. v5 is now paused (`is_collecting=false`); every configuration is non-collecting. Retention remains15days. No Study collection or VersionB operation.

`st2-development-pilot-final.zip`:SHA256 `a7e0d6c3cc21c1d8d2eb0b3c42a9cd2ff827c33a4a8e8d75087a98c8912da195`. Verified161 events,5sessions,1fictional subject,1answered prompt; event IDs unique, invalid events0; original schemas and all client page identities preserved; search ordinal32; final navigation click burst3; native Account has no field-value events; private answer/message/password/file/recipient markers absent; raw Rating3 and actual120s lookback window preserved. All nine sampling parameters match baseline. Original server outcomes that lack page context remain unchanged; they are distinguished from client page events. The answered-only archive does not independently exercise every missing-rating lifecycle branch.

Survey appeared through ordinary visible use under unchanged sampling. Heading focus, no preselected rating, five options, nonmodal semantics, actual server rating acknowledgement and logical focus after dismissal were reviewed. No sampling probability/window/budget change was used to force a prompt.

This material UI change affects layout, visual density and affordance presentation even though event meaning is unchanged. Old D1 and new AT2 usage cannot be treated silently as one fixed UI baseline. A new owner-reviewed freeze/version record is required; subsequent meaningful changes require a change record and research-impact review.

## Exact changed source boundary

**34 existing Jinja files modified:**

- `app/templates/errors/workspace.html`
- `app/templates/layouts/activity_base.html`
- `app/templates/layouts/portal_base.html`
- `app/templates/student/activities.html`
- `app/templates/student/announcements/detail.html`
- `app/templates/student/announcements/list.html`
- `app/templates/student/assignments/detail.html`
- `app/templates/student/attendance/list.html`
- `app/templates/student/calendar/index.html`
- `app/templates/student/dashboard.html`
- `app/templates/student/discussions/detail.html`
- `app/templates/student/discussions/list.html`
- `app/templates/student/discussions/overview.html`
- `app/templates/student/grades/detail.html`
- `app/templates/student/grades/list.html`
- `app/templates/student/learning/lesson.html`
- `app/templates/student/learning/outline.html`
- `app/templates/student/listening/detail.html`
- `app/templates/student/listening/question.html`
- `app/templates/student/listening/result.html`
- `app/templates/student/quizzes/detail.html`
- `app/templates/student/quizzes/question.html`
- `app/templates/student/quizzes/result.html`
- `app/templates/student/records/detail.html`
- `app/templates/student/records/index.html`
- `app/templates/student/search.html`
- `app/templates/student/speaking/detail.html`
- `app/templates/student/speaking/receipt.html`
- `app/templates/student/speaking/record.html`
- `app/templates/workspace/account.html`
- `app/templates/workspace/course.html`
- `app/templates/workspace/groups.html`
- `app/templates/workspace/help.html`
- `app/templates/workspace/preferences.html`

**17 new runtime/asset files:**

- `app/static/css/student/features.css`
- `app/static/css/student/foundation.css`
- `app/static/css/student/shell.css`
- `app/static/fonts/Inter-OFL.txt`
- `app/static/fonts/Inter-variable.ttf`
- `app/static/fonts/NotoSansArabic-OFL.txt`
- `app/static/fonts/NotoSansArabic-variable.ttf`
- `app/static/fonts/Outfit-OFL.txt`
- `app/static/fonts/Outfit-variable.ttf`
- `app/templates/student/template/_activities.html`
- `app/templates/student/template/_announcement_macros.html`
- `app/templates/student/template/_audio_player.html`
- `app/templates/student/template/_calendar_macros.html`
- `app/templates/student/template/_discussion_macros.html`
- `app/templates/student/template/_navigation.html`
- `app/templates/student/template/_shell.html`
- `app/templates/student/template/_ui.html`

This documentation file is the only additional repository document. Exact digests and the new complete candidate source ZIP are recorded externally in `candidate-source-manifest.json`. No staging/commit/tag/branch/reset/clean/stash/push or source history modification.

## Cleanup, acceptance and remaining gates

The four temporary fictional-account hashes were restored to their saved originals, auth versions incremented to revoke review sessions, recovery verified, and the temporary credential file deleted. Owned native5084 and isolated8792 review servers were stopped; the pre-existing owner reference8443 was not stopped. Native review business rows and isolated operational-review data remain fictional/development; they are not Study data. MySQL revision remains`7d4e2a9c6013`; no schema change or production deployment.

Candidate `YC-VA-AT2-20261008` is implemented and locally reviewed. **Not frozen; real Study collection remains off.** Required remaining acceptance: owner final visual review; physical microphone/device recording; NVDA/VoiceOver or equivalent physical screen-reader review; a non-Blink browser/target production-device review, including transparency/forced-color fallbacks; target-host delivery/production smoke/pilot/export gates and documented owner freeze. Existing local production-readiness source is preserved; no new production-host acceptance is claimed. Chrome and Edge reviews both use Blink.

No exhaustive automated suite or every role action/deadline/material format/rare network failure was executed. Existing English-first application translations remain authoritative; this task verifies Arabic/RTL layout/font behavior, not a complete translation project. These are explicit acceptance limits, not inferred passes.

Final standalone error review: `native-error-isolation.json` records Student error glass/font/canvas variants at five widths and Dark/Arabic, with authenticated Messages/auth error routes and Teacher/Admin/Researcher error surfaces excluded. The earlier GET report retains its original pre-correction error render; the final companion report is authoritative for that surface.

The unchanged production release builder deliberately refuses to package runtime that differs from the historical D1 freeze record. The external AT2 candidate source snapshot is labelled unfrozen; it is not a bypassed production release. Production packaging must wait for the approved new freeze record.
