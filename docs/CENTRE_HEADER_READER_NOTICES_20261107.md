# Centre header, reader continuity and necessary notices

## Approved owner scope

Remove Back from main section entry pages, put the centre logo physically left
of the section title for every role, and show the notification bell everywhere
except Notifications. Keep the focus reader immersive. Complete and next lesson
must retain fullscreen. Shrink audio/video markers beside printed book numbers,
remove waveforms and media filename suffixes from display titles, and make player
controls responsive with an X close control. The later owner clarification removes
redundant notices and redesigns necessary ones instead of suppressing errors.
Continue on master and the current VPS only. No database change is required.

## Presentation and state boundaries

- The main navigation destinations suppress their server-owned Back link; detail
  and edit pages keep their established parent/history behavior. The shared
  identity partial handles Administrator, Teacher, Student and Researcher.
  Header layout stays physically left-to-right while title text uses dir=auto;
  Filters retain the right header slot. Existing notification recipient guards,
  counts and inbox writes remain unchanged. The reader bell is hidden in its
  immersive fullscreen view, where only content and the exit control remain.
- Fullscreen targets the stable html element. The existing authorized-page loader
  retains that element through a same-Group reader transition, disposes old page
  resources, and mounts the new server-rendered Lesson. A scoped fullscreen reader
  bridge supports the same POST/redirect/render flow without Navigation API
  interception. It carries the real submitter, CSRF and signed progress token;
  no automatic completion, fake attempt, retry queue or data cache is introduced.
  Leaving the reader or crossing a Group/authentication boundary ends retention.
  Unsupported-browser history traversal reloads the authoritative server page.
- Markers are visually smaller and offset from their saved anchor; stored page
  coordinates and material identities are unchanged. Only terminal audio/video
  suffixes are removed from labels. URLs, original filenames and stored files
  remain unchanged. The separate Course content section below book pages is the
  existing collection of Lesson materials without a page pin. Teacher additions
  remain separately labelled; neither collection is deleted by this change.
- The media player removes the canvas, audio graph and waveform animation, uses
  ordinary HTML media events directly, and keeps play/seek/volume/speed/PiP/video
  fullscreen. A container-responsive second controls row prevents phone overlap.
  The close control is an accessible X. No autoplay permission is added.
- Shared flash presentation consumes routine success and redundant informational
  acknowledgements. Final-submission notices, warnings, errors and contextual
  information remain visible. All shared alert uses receive a neutral surface,
  severity icon and restrained entry motion, including persistent explanatory
  notices. Critical notices do not auto-dismiss. Administrator messages stay
  inline instead of opening automatic acknowledgement dialogs. Keyboard focus,
  native alert/status semantics, reduced motion, forced colors and RTL remain.

## Verification boundary

Use Python/JavaScript syntax, production-factory/mapper and Jinja compilation with
database connections blocked, source review and deployed source/HTTPS checks.
No automated test package, migration, business-data write, student completion,
content reimport, SSH/firewall change or VPS reboot is part of the verification.
Browser use remains disabled; the owner chose physical device review. Actual
fullscreen continuity, phone layout and native media playback are not reported
as browser-verified. The existing authorized readiness 503 remains separate.
Deployment hashes, status, retained previous source and execution evidence live
in the private operator workspace.

Primary API references:
- https://developer.mozilla.org/en-US/docs/Web/API/Element/requestFullscreen
- https://developer.mozilla.org/en-US/docs/Web/API/FormData/FormData
