# Media placement, Arabic and installed icon repair

The owner approved adding the six missing printed play controls and ten Workbook
replay controls identified by the read-only media audit, then reported corrupted
Arabic UI text and requested transparent installed-app icons.

## Content scope

The additive operator plan uses the existing verified recordings. Main-book
printed pages 16, 47, 74, 86, 98 and 117 receive their missing first-occurrence
controls. Workbook review pages 9, 15, 21, 27, 33, 39, 45, 51, 57 and 63 receive
independent replay controls, in the margin beside the exercise label. Repetitions
remain distinct placements. The Course and every active independent Group copy
are resolved through existing same-Course material lineage, never guessed titles.

Each new main-book Material retains its own UploadedFile identity, as required by
the existing unique constraint, while an app-owned hard link reuses the already
validated immutable bytes. No new media payload is uploaded or copied. Workbook
controls reuse existing TeachingAssets. The reviewed job hashes source bytes
before write locks, revalidates the complete scoped content snapshot under the
native hierarchy locks, commits atomically and refuses conflicting placements.
Exact existing placements are idempotent. It preserves publication, Workbook
exercise/answer boundaries, Lesson progress, attempts and account records.
No notification producer is invoked for this placement correction.

Protected full backups and new-row receipts are operator artifacts outside Git.
Source rollback retains appended content. A data rollback must use the bounded
receipt and ordinary archival/editor controls after review; never restore an old
database over subsequent user activity or reset course contents automatically.

## Arabic

5,065 owned translation values had UTF-8 bytes previously decoded as Windows-1256.
Recover them with the exact reversible inverse, leaving 67 sound values intact.
All 5,132 keys, ordering and formatting placeholders remain. No business text,
names or database data is transcoded. The existing dictionary hash changes its
public JavaScript URL so updated pages load the corrected dictionary.

## Installed icons and worker lifecycle

Export transparent 180/192/512 PNGs from the existing centre SVG mark. New icon
URLs are used by the manifest, favicon, touch icon and notification worker.
Transparent icons declare `purpose=any`; they are not incorrectly labelled
maskable. Preserve manifest identity, display mode and scope. Public-shell cache
v3 retains the existing private-content exclusion and normal worker activation;
do not force an active lesson or assessment to reload. Operating systems still
control launcher masks, selection highlights and installed identity refresh.

The push service is PartOf the app service and wanted by it, so subsequent app
restarts/source deployments restart the worker on the same release, and app starts
bring it back. A worker failure does not make the app depend on its availability.

Primary references: [Manifest icons](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/How_to/Define_app_icons),
[Chrome installed-app identity updates](https://developer.chrome.com/blog/improvements-to-web-app-updates).

## Verification boundary

Use source/template/JavaScript/mapper checks, exhaustive reversible-catalog and
placeholder checks, alpha-channel inspection, source-page placement review and
scoped real MySQL readback. No automated application test suite, fabricated login,
learner progress or notification is created. Native phone/desktop installation
refresh and authenticated browser acceptance require the owner's device.
