# Android reader zoom, installation and desktop language direction

The owner reported unstable two-finger zoom on Android, supplied a screenshot
of Chrome's native fullscreen exit notice, requested installation across device
types and asked the desktop header to follow the language/sidebar direction
while preserving the phone layout. Continue on master and the new VPS only.

## Reader correction

The platform smooth-scroll rule also applied to the reader's scroll container.
The old pinch handler repeatedly changed page width and assigned horizontal and
vertical scroll offsets separately. Those corrections could animate behind the
touch updates. The reader now opts out of programmatic smooth scrolling and
browser scroll anchoring locally; focal corrections commit both axes with
`behavior: instant`. Explicit animated completion transitions remain available.
Coalesce zoom updates into animation frames, identify the two book touches,
rebase when the participating fingers change and discard cancelled updates.
Keep completion disarmed throughout a held pinch. Do not resize in response to
an uncancellable gesture already owned by the browser. Retain native single
finger/wheel panning, keyboard zoom/reset, page-relative media geometry and
book-only scaling; no synthetic momentum or whole-interface zoom is introduced.

## Fullscreen boundary

The screenshot's IP-address/exit-instructions overlay is Chrome UI. A website
cannot style it away. When the window already reports fullscreen display mode
without a DOM fullscreen element, focus the reader within that existing app
window instead of making a redundant Fullscreen API request. Preserve the exit
button, Escape, safe areas, explicit Unit completion and same-group native
reader transitions. Normal browser tabs still use native fullscreen and retain
browser-controlled exit guidance. Never hide browser security UI or change
browser settings. Do not promise that an OS-owned notice is removed everywhere.

## Installation

Keep the single user-click install entry on secure pages. Use a retained native
install prompt where available; prevent duplicate clicks and consume each event
once. A failed prompt opens usable instructions instead of silently doing
nothing. Keep rejection/dismissal under user control and confirm installation
only through the existing appinstalled signal. Restore the entry on subsequent
page/navigation refreshes when it remains applicable.

Manual guides cover iPhone/iPad Safari (including Open as Web App where present),
Android browser installation, Safari Add to Dock on supported macOS, Firefox web
apps on supported Windows and Chrome/Edge desktop installation. In-app browsers
receive instructions to open a suitable external browser. A small non-modal
dialog fallback covers unavailable native dialog methods. Preserve manifest
identity, transparent icons, scope, HTTPS and online-only private content.
Installation support and exact browser prompts are not under site control.

## Desktop header

The shared header CSS forced LTR at all widths. From the existing 1024px desktop
breakpoint it now inherits language direction for the header/context/title row,
so Arabic mirrors consistently with the sidebar. The existing phone rules,
physical title/filter positions, logo and navigation remain unchanged.

## Verification and operational boundary

Use JavaScript syntax checks, all-Jinja compilation with database connections
blocked, dependency/mapper checks, encoding/placeholder preservation, source
review and immutable-source/public-HTTPS readback. Do not create or run an
automated application test package, fake sign-ins, study events, completions or
notifications. Physical Android gestures, installed-app behavior and the visual
desktop/mobile layouts require owner-device confirmation. No schema, course
content or progress mutation is part of this change. Source publication follows
the owner's standing approval requirement before restarting services, with a
protected backup and retained previous release.

Primary references reviewed on 2026-11-07:

- https://developer.mozilla.org/en-US/docs/Web/API/Element/scrollTo
- https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/overflow-anchor
- https://www.w3.org/TR/pointerevents4/#the-touch-action-css-property
- https://fullscreen.spec.whatwg.org/#security-and-privacy-considerations
- https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeinstallprompt_event
- https://support.apple.com/guide/iphone/iphea86e5236/ios
- https://support.apple.com/en-us/104996
- https://support.mozilla.org/en-US/kb/web-apps-firefox-windows
