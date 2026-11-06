# Platform scrolling and Lesson-end reveal - 2026-11-07

The owner requested fixing phone People scrolling, replacing default scrollbar
appearance across the platform with restrained branded feedback, removing the
book-page description box and revealing the Lesson completion action only at
the end. This is a presentation correction on the already deployed course.

## Implementation

On phones, People directory/profile panes use normal document overflow and
allow scroll chaining. Previously, the unbounded phone directory retained
desktop `overscroll-behavior:contain` together with `overflow:hidden`, creating
a non-scrolling pane that could trap gestures before they reached the page.
The desktop independently scrolling panels remain unchanged.

The common base layout hides native document/element scrollbars and loads a
page-scoped, pointer-inert progress cue. The cue follows the actual active
scroller and fades after scrolling stops, including horizontal/RTL and modal
contexts. CSS supplies smooth programmatic scrolling. No wheel/touch handler
is intercepted, prevented, replaced or emulated. Keyboard scrolling, pinch
zoom, existing scroll restoration and native device inertia are preserved.
Reduced motion removes animation; forced-colors mode restores native bars.
No telemetry, persistence, network request or dependency is added.

The Student reader no longer renders the Lesson description metadata box;
stored descriptions are unchanged. Completion remains a native CSRF/signed
form. A real end-of-content sentinel reveals the compact action only after
the end enters the reader viewport and its book-image loads settle. Reveal
does not mark the Lesson complete or infer that media were consumed. It gently
brings a partially visible revealed card into view unless the learner has
scrolled away. There is one primary action: Complete and next lesson when
an authorized next Lesson exists, otherwise Complete lesson. Completed Lessons
retain Undo completion. Without JavaScript, the native end-of-Lesson form is
still available. No completion was submitted during review.

## Verification and boundary

Node syntax checks passed for both changed JavaScript modules. DB-blocked
static review parsed 19 Python files, compiled 18 changed templates, configured
ORM mappers and confirmed the unchanged single migration head 8e74c029ab61.
No automated tests were created or run, following the repository instruction.

Authenticated local Administrator review at 390x420 verified that scrolling
over the People list moved the document from 0 to 322.4px, with the branded
cue present and no horizontal overflow. The directory computed overflow
visible and overscroll auto. Profile scrolling reached 737.6px; returning to
the list preserved its position. The local directory contains two fictional
Student records; a larger hosted directory remains a distinct browser check.

Local Student review at a verified 390px layout confirmed the removed metadata
box, ten unchanged media markers, exactly one completion button and an initially
inert/hidden completion area. At the end, the card revealed and became fully
visible without submitting completion. Reduced-motion emulation computed
scroll-behavior auto and transition duration 0s. Device/media overrides were
cleared after review. Native screenshots were inspected; no screenshot export
or physical touch-device acceptance is claimed.

## Hosted rollout

The owner explicitly approved the brief service restart after reviewing the
prepared package. Release `20261106T221556Z-3067b33` succeeded at 22:28:51 UTC on
2026-11-06 (00:28:51 Libya time on 2026-11-07). Six existing presentation files
changed and two platform scroll assets were added. All 896 deployed source files
matched the frozen package hashes. The schema remains 8e74c029ab61/95 tables;
the previous immutable release and prior protected data/file backups remain.
The existing bounded release helper performed no schema migration or content
import. Its historical `source_overlay_applied:false` receipt field is not the
source authority: the reviewed worktree manifest and independently matched
896 file hashes prove the actual overlay that was deployed.

Authenticated hosted Student review opened the course directly and inspected
Grammar Focus Unit 1 (PDF pages 150-151). Both images loaded. The book metadata
box was absent; native scrollbar width computed none. The one completion
control was initially inert and became fully visible after scrolling to the
end. No completion was submitted by the agent; the owner's existing 3/88
completion count was retained through this read-only review. The owner elected
to test People on their physical phone rather than switch the hosted browser
to an Administrator session. Hosted Administrator/physical-phone acceptance
therefore remains owner validation, distinct from the passed local review.

Health and login return 200. The previously owner-accepted readiness 503 remains
unchanged. No account, enrollment, course file, finance, research-label, Git
staging/commit/push, SSH/firewall or VPS reboot change was performed. Runtime
source checks/receipts are in
`C:\Users\abdul\AdaptiveEnglishLMS-VPS\2026-11-07-scroll-polish`.
