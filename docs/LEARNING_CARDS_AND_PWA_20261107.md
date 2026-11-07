# Learning cards and installable application

## Approved owner scope

Apply the proposed Student course cards and shared activity cards, retain the
current workspace title and Filters positions, and omit the displayed percentage
and “Keep going”. The owner separately approved PWA installation after reviewing
the proposed behavior and online-only treatment of private content. Keep the
single master branch and deploy only to the current Libyan Spider VPS.

## Presentation

One shared Course card renders the actual Course title, Group, native published
Lesson progress and book actions. Home uses the same card with its authorized
continue destination. Only the bar animates, from zero to its genuine server
value, once on entering the viewport over 1.2 seconds. The completed/total count
is stable; no percentage, encouraging slogan or artificial progress is rendered.
Reduced-motion, forced-color and no-JavaScript fallback retain the real value.

Activities and Home use the same native item view and card macro. Remove the
course navigation tabs, repeated course/group metadata, hero copy, duplicate
headings and table columns. Keep activity title/type, actual deadline, progress,
closed/upcoming availability when relevant, correct read/continue/result links,
paging and existing GET filters. The shared header markup/CSS/JavaScript remains
unchanged. Workbook availability is queried once in the existing own-enrollment
dashboard service, feeding both Home and Courses with the same data.

## Installation technique and platform behavior

This is a Progressive Web App (PWA), using the existing Flask application and
authentication. It adds no native executable, new backend service or dependency.
The public Web App Manifest defines a stable origin-relative identity, root
start URL and scope, centre icons and education metadata. Requested display is
fullscreen with standalone fallback. Supporting platforms open an installed
application without the browser URL bar; OS window/status controls and actual
fullscreen support depend on the browser/platform.

An Install app entry inside the existing account menu uses the browser's native
beforeinstallprompt when available. Otherwise it shows platform-specific manual
instructions for Safari/iOS, Chrome/Android or desktop Chrome/Edge. Actual
installation remains the device user's choice. The entry is hidden while the
page runs in standalone/fullscreen application mode and after an appinstalled
event for the current browser page lifetime. It does not change account settings
or request notification/device permissions.

The same server sessions and Student/Teacher guards apply. Changing to another
origin/domain later creates a different installation identity; an existing
IP-origin installation does not silently migrate to the new domain.

Primary references:
- https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable
- https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/display
- https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/How_to/Trigger_install_prompt

## Service Worker privacy and update policy

The root-scoped Service Worker stores only same-origin public /static/ UI
assets, three small installation resources and an identity-free offline page.
The runtime static cache is bounded to 80 entries, network-first, and refuses
private/no-store responses, redirects and Range requests. HTML navigations are
network-only with a generic offline fallback; they are never written to cache.
All private books/pages/media, account data, APIs and POST writes remain outside
the cache. There is no background sync, queued completion, offline submission,
automatic media download or copied authentication information.

New workers do not force skipWaiting, client claims or page reloads during open
work/Quiz attempts. Activation cleans only this application's old cache prefix.
Worker JavaScript is revalidated with no-cache headers; icons/manifest are public
and contain no private identifiers. All installation remains same-origin HTTPS
on hosting; localhost is used only for development.

## Verification boundary

Static Python/Jinja/mapper checks, JavaScript syntax, actual unauthenticated local
HTTP inspection of manifest/worker/offline/icon responses and read-only native
data inspection are allowed. No automated test package, pytest, migration, data
reset or business write is part of this change. The schema stays a4b9d0c21e76.
Browser use is disabled in Work settings; the owner explicitly chose to continue
with available checks and review the actual device experience themselves.
Device installation, Safari/Android-specific prompts and visual layout are not
reported as verified until they are actually inspected on those devices.

The frozen archive, source hashes, VPS status and rollback release are recorded
in the private operator workspace. Preserve the existing readiness exception;
this change does not repair or conceal that separate health contract.
