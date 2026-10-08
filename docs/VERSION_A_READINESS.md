# Version A delivery and readiness — 2026-10-08

The approved Version A implementation is delivered in source. The protected
Login is preserved; Messages is evolved; Direction D + D-MOTION, single-file
assignments, current instrumentation and bounded real-data Researcher charts are
implemented. A data-free neutral artifact and operator/centre/research runbooks
are prepared. Actual hosting and real Study collection are not verified.

PASS means the stated local/prepared scope passed; it is not a universal claim.
PARTIAL identifies remaining coverage or actual-host acceptance. BLOCKED means
the intended live outcome cannot be verified without the external prerequisites.
Evidence and exact limitations are in `VERSION_A_ACCEPTANCE.md`.

| Required status | Result | Evidence / remaining boundary |
|---|---|---|
| IMPLEMENTATION | PASS | W0–W7 source and current role/browser/workflow evidence; D + D-MOTION, Login/Messages exceptions; no B runtime |
| ACCESSIBILITY | PARTIAL | Native keyboard/focus, visible labels, landmark/IDs, reduced motion and sampled contrast pass; native screen readers, full WCAG/cross-browser certification not performed |
| RESPONSIVE ACCEPTANCE | PASS | Sampled 320/390/1440, Light/Dark/System/RTL and equivalent 200%/400% reflow pass; no universal device/browser claim |
| BUSINESS WORKFLOWS | PASS | Native learning, communication, media, grading/release/correction, attendance/finality, finance/history and Account checks pass; bounded, not exhaustive regression |
| FILE ASSIGNMENT | PASS | Text/single final private file, teacher choice/freeze, authorized access, validation/CSRF/stale/deadline/duplicate/ownership and cleanup checks pass |
| DATABASE | PASS | Actual final head, no model delta, empty 75-table MySQL migration and independent dump/restore pass |
| INSTRUMENTATION | PASS | Same wire/schema/types, classified Student pages, privacy Account, stable hooks, global ordinal, native autosave/focus/burst/heartbeat evidence; documented delivery limits |
| RESEARCH EXPORT | PASS | Native immutable operational-review archive, hashes/counts/CSV compatibility/position/rating/privacy pass; resource caps refuse whole oversized exports |
| DEVELOPMENT PILOT | PASS | Separate v4, unchanged policy, ordinary sampled Survey, 86 stored events/one answer; historical demo provenance preserved; never Study |
| BASELINE FREEZE | PASS | Local source/UI baseline YC-VA-D1-20261008, exact runtime inventory and dictionary/hooks/configuration/evidence record; production/collection gates separate |
| PRODUCTION PACKAGE | PASS | Allowlisted data-free ZIP, internal file hashes/runtime freeze match/ZIP check and extracted source/templates; integrity evidence adjacent to artifact |
| DEPLOYMENT READINESS | PARTIAL | Production settings/entrypoint/runtime pins/proxy/health/private storage/bootstrap/backup/retention templates/runbooks prepared; actual Linux build/host/TLS/scheduler/smoke unverified |
| CENTRE HANDOVER READINESS | PARTIAL | Practical centre/operator/research guides delivered; actual address/operator provisioning, host acceptance and centre walkthrough require selected hosting |
| REAL COLLECTION READINESS | BLOCKED | No accepted actual production host/smoke or documented operational participation/eligibility setup. Default Development; Pilot paused; Study never activated automatically |

## Hosting gates to close

1. Select provider/domain and verify Python/MySQL/storage/process/network support.
   Build the actual Linux runtime with pinned dependencies. Verify trusted host,
   TLS, secure session/CSRF, protected WSGI listener, private persistent storage
   permissions and backups. Never ship/import the developer DB/files/secrets.
2. Follow the empty migrations → deliberate Administrator/Researcher bootstrap
   → centre setup path. Record host startup/restart/health/logs and a bounded
   four-role production smoke with fictional Development provenance.
3. Schedule/observe daily 15-day retention; verify backup and DB+file restore on
   the actual infrastructure. A scheduler example is not an installed job.
4. Verify approved external participation/consent/exclusions and smoke-account
   exclusion. Existing non-study subjects stay non-study. Deliberately set Study
   provenance and use a new post-freeze configuration under the approved policy.
5. Record baseline/configuration/period/operator activation, first eligible
   accepted batch, classified export and stop check. This is the first permitted
   real collection boundary, not local completion or an app startup side effect.

Normal operation follows `VERSION_A_CENTRE_GUIDE.md`; deployment/recovery follows
`VERSION_A_PRODUCTION_RUNBOOK.md`; collection/stop/export/exclusions follows
`VERSION_A_RESEARCH_OPERATIONS.md`. A centre hosting operator is required for
infrastructure work. No Codex/developer workflow is required for normal LMS use.
