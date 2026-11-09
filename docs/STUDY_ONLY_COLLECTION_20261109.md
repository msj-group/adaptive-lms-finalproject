# Study-only collection - 2026-11-09

## Owner scope

The owner requests complete removal of Development and Demo data provenance,
not relabelling them. The owner separately confirms keeping the training/test
organization of real Study participants after the distinction was explained.
This supersedes the earlier operational-review collection/export scope.

## Runtime and storage

- Study is the sole provenance in settings, enums, subject/session validation,
  collection eligibility, storage selection, quality snapshots and exports.
- Invalid provenance refuses startup or collection; it never defaults or converts
  unknown/non-Study evidence into real Study data. Collection remains off by default.
- Removed the operational-review export branch, provenance labels, Demo audit action,
  demonstration-subject behavior and configurable non-Study startup path.
- Immutable source metadata stays in real Study exports and auditing. The existing
  internal evaluation partition `development` means model training on real data,
  not a source classification. Training/test/monitoring and final-test quarantine
  remain under the owner's explicit clarification. UI calls it Model training.
- Historical installed migrations remain reproducible. New revision e7f9012abc34
  follows d6e8f9012abc and narrows four existing provenance CHECKs plus the audit
  action CHECK. It refuses existing non-Study records/exports/Demo audits before
  any DDL. It performs no relabelling, deletion, seed, backfill or reset.
- Each CHECK replacement is an atomic MySQL ALTER. The additive narrowing stays
  compatible with the previous Study-configured app for source rollback. No
  automated downgrade is provided, and no database restore is needed for rollback.

## Hosted activation

The existing Study configuration, dates, 45-day retention, counters, observations,
answers, book content and student progress must be preserved. The owner later
requests including Test as well. Only its three prior external exclusions are
reinstated through the authenticated audited operator service, never direct SQL.
Activation requires the exact parent schema, source hashes, private backup, a
brief approved app/worker stop, guarded migration, source switch and health checks.
No Railway operation, dependency installation or local MySQL migration is included.
The local migration step is explicitly omitted for this hosted operation.

## Verification boundary

Use bounded static Python/Jinja/JSON validation, direct pure-policy checks, MySQL
DDL compilation and release/hash checks. No automated test package, synthetic
Study session or fabricated student event is created. Actual browser/device
delivery and export acceptance must be distinguished from these source checks.

## Simplified Researcher workflow - owner clarification

The owner additionally requests one cohesive UI, immediate execution without
Draft/Activate/Publish steps and no schedule form. The primary navigation is
Overview, Sessions, Exports and Management. Quality, participant assignment,
exclusions, audit and storage are management subsections using one canonical URL.
Old read URLs redirect; old manual activation/write endpoints and forms are removed.
Start/Stop is a signed CSRF-protected POST with an intended state, actor and
optimistic version. A replay cannot invert the state. A single locked transaction
creates/activates the approved fixed settings if needed, then starts immediately;
stopping closes open sessions and audits before one commit. The existing approved
end-of-February study limit is inherited, or supplied through
RESEARCH_COLLECTION_ENDS_AT for a first installation. No implicit new study length
or expiry extension is introduced. Saved versions remain internal accountability.
The release must never create synthetic Study events for validation. Deployment
pauses collection through the authenticated native service before changing source,
closes old-release sessions, and restores the original running state afterward.
This is required by the existing release-bound observation contract, not a UI test.

## Groups and live monitoring

Exports accept any selection of teaching groups; an empty selection means all
groups. Membership is resolved at the export's consistent read snapshot, and the
manifest records selected public group identifiers and this current-membership
basis. This does not claim historical enrollment membership. No identity map is
exported. Training/test quarantine is unchanged.

The overview polls a private read-only endpoint every three seconds while visible,
without overlapping requests. Student event transport flushes every five seconds;
heartbeat/sampling/retention and measurement meanings are unchanged. Presence
means server contact within 90 seconds and is deduplicated across browsers.
The bounded display returns up to 200 active research codes and 60 recent actions.
Group names are owner-approved cohort metadata, privately resolved without reading
names, addresses, messages or answers. Unassigned/reserved-test presence is visible,
but detailed contexts/actions remain protected until an allowed review assignment.
Action intentions are distinguished from server-confirmed results. Interrupted
connections show a reconnecting state with backoff; hidden pages stop polling and
navigation aborts pending requests. Every poll rechecks the Researcher role/session.
