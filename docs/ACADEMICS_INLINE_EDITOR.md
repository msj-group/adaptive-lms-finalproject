# Academics inline editor correction — 2026-10-26

The owner requested three corrections to Setup Hub: New and Edit must stay
inside the current directory, Status must be selectable, and Actions must show
Edit directly instead of Manage. This supersedes the previous management
disclosure and full-page authoring presentation for JavaScript-enabled use.

## Behavior

- All four New actions and row Edit links now load their original authorized
  form into a centered native dialog above the existing Academics page. Submit
  uses the original POST route and payload, including CSRF, Course edit snapshot
  and Room snapshot. Server field errors remain inside the dialog. Successful
  saves refresh the current directory and hub totals without navigating the
  browser or discarding its Course filter or Room page.
- Actions contains a direct Edit link. Course/Level order controls remain with
  the record identity; the Level Courses link and Room History link remain.
- Each Status cell owns a native Active/Archived select and protected POST form.
  Selection submits within the current page. Without JavaScript an Apply button
  submits the same form normally. Existing lifecycle restrictions and Room's
  signed snapshot still decide whether a change is allowed.
- A small server helper validates explicit target/current status after each
  route's existing locks. Invalid, duplicated, stale and already-applied
  selections cannot accidentally reverse a completed transition. Original
  callers without a status field retain the original guarded toggle behavior.
- Responses must match the current account/authentication version, directory,
  record and expected same-origin destination before being installed. Closing
  a changed draft asks for confirmation. An uncertain POST retains its draft,
  disables further saves and requests an explicit reload; it is never retried
  automatically. Keyboard, Escape, native fallback, Arabic/RTL and 20px corners
  remain supported. The phone section deck and platform dock are unchanged.

## Verification executed

- All 284 current templates compiled through the actual OwnedUIExtension.
  Five affected Python sources parsed; the new JavaScript passed `node --check`.
- Eight missing Arabic labels were added without changing existing translations
  or introducing duplicate keys. The pre-existing `Released grades` duplicate
  remains. CRLF-aware `git diff --check` passes; staging remains empty and HEAD
  remains `10e5003`.
- Seven isolated checks exercised the real status-selection helper: legacy
  toggle, archive, reactivate, repeated/already-applied selection, invalid value,
  stale current state and missing expected state. These checks used an in-memory
  rollback stand-in and executed no SQL.
- Browser review used real production templates, fields and CSRF in an isolated
  fictional-memory server. Uniqueness/existence lookups were substituted; no
  application factory, database engine or business database was used. Fictional
  creates/edits/status changes exercise presentation, not live-MySQL transactions.
- Each of the four resources was opened with New, submitted with missing fields,
  corrected and saved, then edited and saved. The modal stayed open for validation
  and the directory URL stayed unchanged for each operation. Every Actions cell
  exposed Edit and no Manage disclosure remained.
- Status changes refreshed in place. A fictional blocked Level archive showed
  its server rejection and restored the authoritative Active selection. Course
  filtering and Room page 2 remained while opening/saving dialogs.
- Draft cancellation could be rejected without losing entered values. Confirmed
  discard stayed on the directory. The original draft baseline also protected
  browser unload. A blocked network POST was attempted once,
  retained its draft and disabled resubmission. A response for a different actor
  installed no form and disabled status writes. Edit still opened after Course
  query results were dynamically replaced.
- Reviewed desktop and 320px Arabic/RTL/dark phone. The 296px dialog fit within
  the 320px viewport without page or dialog horizontal overflow. Native no-script
  URLs and the Status Apply control remained. Browser script errors: zero.

Review sources, before-copies, receipts and screenshots are in the separate task
workspace `C:/Users/ma999/Documents/Codex/2026-10-26/new-room-level-academic-term-course/`.
The new helper checks do not constitute database or concurrency acceptance.
No real database reads/writes, migrations, dependency installation, Git staging,
commit, push or deployment was performed.
