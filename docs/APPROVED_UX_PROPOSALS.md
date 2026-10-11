# Approved UX proposals — local implementation, 2026-10-11

The implementation-stage evidence below predates the owner's subsequent explicit
commit/push/deploy request. That release is separately documented in
[APPROVED_UX_RELEASE.md](APPROVED_UX_RELEASE.md); its integration review also
preserves the already-pushed search/Messages follow-up f337f2d.

## Authority and checkpoint

The latest owner instruction supersedes earlier release permissions. MAIN began
clean on `ui/platform-unification-20261009` at
`f227629dcac8cc454c8df0861623c115215b0c66`. Prior density, context cleanup, ST2,
background, Dark logo and protected Login/Messages remain. Changes are local and
unstaged. No commit, push, deployment, Railway change, migration or production
data change occurred. No Study, Version B or Baseline Freeze is declared.

## Implemented decisions

- Student Home A: actual Continue/progress, next class, two activities, one
  announcement; learning details in Courses; one no-enrollment state.
- Teacher Home: current class when applicable, three oldest pending reviews,
  group operations and one announcement, without invented workload counters.
  Admin Home: setup exceptions, three upcoming classes, common operations,
  Finance access and compact People indicators.
- Researcher Home: development provenance, collection state and three
  selected-range indicators, with native detailed analytics/evidence disclosures.
  Visible delivery retains source, denominator and lifetime caveats.
- Exact bottom order: Student Home/Courses/Activities/Calendar/More; Teacher
  Home/Groups/Review/Attendance/More; Admin Home/Groups/Schedules/Finance/More;
  Researcher Home/Sessions/Exports/Config/More. Shared role lists populate rail
  and More, subtract primary destinations and use family-specific active states.
- A01–A04 / corrected W: opt-in native GET filtering/search; duplicate Activity
  footer Open removed; sole title target at least 44px. Shared query enhancement
  provides immediate stale invalidation, abort, IME guards, debounce, history,
  stable input focus and native no-JS/capability/failure recovery. An explicit
  template allowlist uses existing authorized views and escaped native fragments.
  Literal LIKE punctuation and query bounds are handled on the server.
- Compatible original GET candidates O-F010/016/022/026/028/030/031/034/047/051/
  056/069/088/090/093/151 are covered. POST, staged ranges, financial/research
  confirmations, Save, Finalize and Release were not automated.
- UX07/20/21/23/24/35: lesson map near title, one Group tools section, compact
  Assignment and Speaking review rows, visible primary filters with date-only
  disclosure and explicit Apply, source-qualified progress details.
- B2: original Quiz/Listening facts and policy open before Start. Quiz autosave
  and Listening manual Save remain distinct. Essential policy can increase height.
- TXT0297/0397/0401/0541/0572/0613: concise equivalents with operational rules
  retained. TXT0044/0534/0540/0546: justified KEEP, since generic replacement
  would lose actual state/rules. Prior B01–B09 cleanup is retained. New owned UI
  strings have Arabic translations.
- Proven duplicate empty-state actions are removed where a stable header/filter
  action survives. Conditional links, distinct operations and useful top Back/
  bottom Cancel remain. O-J01–04 are corrected, including a scoped Tab loop in
  the existing action-specific confirmation dialog.

## Actual verification

Current Flask source runs at `http://127.0.0.1:5006` with ordinary four-role
password authentication, CSRF enabled, TESTING disabled and the previously
authorized owned disposable loopback MySQL dataset. Its revision remains
`7d4e2a9c6013`; provenance is development and RAILWAY_ALLOW_STUDY is 0. The
pre-existing port 5000 process and Railway resources were left alone.

Bounded operator-invoked browser/static review was used, not a new automated test
package or pytest suite. Fifty-two authorized rail destinations returned 200.
The core matrix has 680 actual-page observations: 17 families × ten widths ×
English/Arabic × Light/Dark. Widths are 320/360/390/430/540/768/900/1024/1200/1440.
Additional affected-page, long-content, empty, validation, More, preference and
protected-page checks are recorded. No horizontal document overflow or uncaught
application JavaScript error was observed in the recorded captures.

Ordinary writes only affected fictional disposable data: a Quiz attempt and
autosaved answer, a Listening attempt and explicitly saved answer, one Teacher
draft Quiz and one Admin-created Student used for normal empty-Home login.
Invalid Teacher/Admin/Research forms were checked. Financial preview was checked;
invoices/payments remain zero. No final academic submission, result release,
financial commit, deletion or collection activation occurred.

More keyboard/dismissal/focus, RTL/LTR, comfortable/compact, System Dark and
reduced motion were checked. Calculable solid-background contrast pairs passed;
alpha/gradient composition remains partial. Physical microphone, mobile keyboard/
safe-area devices, native 200%/400% browser zoom, real assistive technology, all
final transactions and the Student multiple-enrollment runtime state remain
unverified. The inherited protected password visibility control is below the
preferred 44px target and was not redesigned.

Seventeen checked protected Login/Messages/assets/attempt-engine/collector files
remain byte-identical to HEAD. Student/Teacher Messages returned 200; Admin/
Researcher Messages correctly returned 403. Normal after-Login is captured at
390/768/1440. Only normal 390px before/after is parity-eligible: same geometry,
248 changed button-shadow pixels, not pixel-identical. Before-Login 768/1440
are actual 429 responses, excluded from normal parity. Controlled 503 images
are labelled simulated transport failures.

Automatic approval review rejected a proposed final Assignment submission
because the blank-state precondition had not been proven before confirmation.
That POST was not retried. Opening/cancelling confirmation, Escape, Tab/Shift+Tab
and focus return were safely checked; Student final-submit validation is unverified.

## Research interpretation and acceptance

Event Dictionary, collector hooks and usage collector are unchanged. Native IDs
and server participant/session isolation remain. Removed duplicate targets,
changed geometry/exposure and same-document GET results change observation
opportunities. Automatic GET updates do not invent document page views or submit
events; delegated actual result clicks retain IDs/ordinal. No dedicated
scroll-depth event was found. Preserved IDs do not prove baseline equivalence.
New baseline acceptance is required before study collection. Active-collection
end-to-end instrumentation was not rehearsed this round.

Compatible source implementation, safe GET behavior, four-role navigation and
bounded responsive checks: **PASS within recorded scope**. Comprehensive
workflow/device/accessibility acceptance and scientific baseline equivalence:
**PARTIAL**. This is not production-ready acceptance or a freeze.

## Evidence and separate release boundary

Evidence directory:
`C:/Users/abdul/.codex/visualizations/2026/10/10/01a12430-b1a2-7e62-a185-8944e4c04d3c/approved-proposals-local/`.
It contains the Arabic report, interactive gallery, original-ID decision registers,
measurements, browser/functional observations, protected/static/runtime evidence,
changed-file list and source-only patch. Each screenshot has a source/state label;
public Login is explicitly unauthenticated and simulated failures are separate.

A separately authorized release must review the patch, run permitted release
gates, assess research baseline changes and verify deployment/runtime. This
document authorizes none of those release operations.
