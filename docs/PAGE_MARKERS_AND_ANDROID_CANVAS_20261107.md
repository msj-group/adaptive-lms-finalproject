# Page-relative markers, section bells and Android canvas

## Approved owner correction

The owner reported that media markers cover book text and drift instead of
growing during page zoom. Enlarge the centre header logo to account-icon scale.
Address the black top strip in the installed application, confirmed as Android.
The latest correction restricts notification bells to section entry pages only,
excluding Notifications, details, forms and the reader. Continue on master and
the existing VPS; no course-data change or new deployment target is involved.

## Implementation

The reader page sheet is an inline-size query container. Marker hit targets,
outline and glyph use container-width units, so their visible geometry scales
with the same sheet as the PDF image. Saved percentage anchors retain a centered
translate(-50%,-50%); remove the fixed 5px lateral displacement and phone-only
fixed-pixel overrides. A small transparent outline replaces the opaque disc,
allowing printed content underneath to remain visible. The visible outline is
2% of sheet width; the glyph is 1.25%. A transparent minimum 24px hit target helps
touch selection without painting over surrounding text. Stored coordinates,
media identities and files are unchanged. This applies to Student/Workbook
reading and shared reader previews, not Teacher placement controls.

The owner clarified that the title-side header logo is for phones only. It uses
a 44px slot with a 40 by 44px contained image up to the existing 639px phone
breakpoint and is hidden on larger screens. Remove the 24 by 30px phone override;
title truncation and the right
Filters position remain native. A shared server section-root predicate now owns
both main-page Back suppression and bell visibility. Notification APIs, records
and recipient authorization are unchanged; the reader no longer renders a bell.

Android installed fullscreen remains requested with standalone fallback. Add
viewport-fit=cover so Chromium may draw into the display cutout. Paint the HTML
canvas with the existing page color, set manifest startup/theme colors to the
light app canvas, and synchronize the page theme-color with the actual current
body background after page/theme changes. Header and reader controls account for
safe-area insets; measured fixed-header height includes the top inset. No fake
top overlay, clipped content, forced orientation or zoom restriction is added.

The site cannot override device/manufacturer restrictions on drawing into the
camera region. Existing WebAPK manifest updates are controlled by Chrome, not an
immediate server push. Device confirmation is required before claiming that an
OS-owned black strip disappeared. No reinstall or user-data clearing is executed.

## Verification and operational boundary

Compile all Jinja templates and changed Python with database connections blocked,
check changed JavaScript syntax and source diff, then verify the frozen archive
and public HTTPS assets on the active VPS. No automated test package, migration,
business-data write, course import, student completion, reboot or SSH/firewall
change is part of the task. Work browser access remains disabled; the owner will
review actual Android/cutout and touch/zoom behavior. Existing readiness 503 stays
separate. Source hashes, deployment receipt and retained previous release are
recorded in the private operator workspace.

Primary references:
- https://web.dev/learn/pwa/app-design/
- https://chromium.googlesource.com/chromium/src/+/HEAD/components/webapps/docs/android_architecture.md
- https://www.w3.org/TR/css-contain-3/
