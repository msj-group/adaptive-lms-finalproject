# Reader page zoom and fullscreen completion

## Owner scope

Remove Undo completion and the X control from the Lesson completion card. Reveal
the same end-only card in fullscreen. Zoom only book pages, keeping surrounding
reader controls at their normal size, including fullscreen. Remove native-looking
pointer/touch feedback across the application. Continue on master and the current
VPS; do not alter curriculum, progress records or the database schema.

## Implementation

The completion dialog remains a child of the fullscreen reader. Remove the
explicit fullscreen exclusion, and reevaluate end visibility when fullscreen
changes. Keep the existing deliberate downward-input/end/image-loading guards,
native modal top layer and animation. The completed-Lesson branch contains only
Next lesson and Exit to courses links. The uncompleted branch keeps the signed,
CSRF-protected Complete and next lesson / Complete and exit form. Removing Undo
from this card does not modify the server's existing progress history or APIs.
Escape/backdrop dismissal remains available without a visible X.

The active reader owns Ctrl+wheel and two-finger pinch gestures for its PDF page
container only. It adjusts page layout width from fit size up to four times fit,
keeps the location under the gesture midpoint anchored, and retains native
single-finger/wheel panning. Media pin positions follow the resized pages; player
dialogs, completion cards, header and navigation are not resized. Ctrl/Cmd plus,
minus and zero work inside the reader as accessible zoom/reset shortcuts.
Resize/fullscreen changes recompute fit width while retaining the zoom factor.
No extra zoom button is introduced. Browser magnification outside the reader and
viewport accessibility remain available; no maximum-scale/user-scalable limit is
added. The new book-only gesture handling supersedes the prior native-browser
zoom decision only for this reader. A zoom gesture disarms completion so reflow
cannot be mistaken for a deliberate end scroll. Scope disposal removes handlers.

The shared foundation removes WebKit's tap flash and native button appearance,
and suppresses pointer-only focus outlines. Existing platform button feedback,
text selection, form affordances, keyboard focus-visible and forced-color focus
indicators remain intact.

## Verification boundary

Use static JavaScript syntax, production-factory/Jinja compilation with database
connections blocked, source diff review and deployed source/HTTP verification.
No automated test package, migration, fabricated completion or other business
write belongs to this change. Actual touch gestures, fullscreen presentation and
visual feedback require device review; Work browser access is disabled and the
owner chose to review on their device. The existing readiness exception remains
separate. Frozen source hashes, deployment receipt and previous release are
recorded in the private operator workspace.

Primary API references:
- https://developer.mozilla.org/en-US/docs/Web/API/HTMLDialogElement/showModal
- https://developer.mozilla.org/en-US/docs/Web/API/Touch_events/Multi-touch_interaction
- https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/touch-action
