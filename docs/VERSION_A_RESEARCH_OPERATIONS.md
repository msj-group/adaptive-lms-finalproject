# Version A research operations and Study transition

The current methodology is natural-use, schema `natural-use-events.v1`, raw
optional 1–5 self-report, current sampling/windows and daily 15-day retention.
The W7 generator dictionary is `va-w7-r1`, scope `va-scope-r1`, export CSV
`natural-use-csv.v2`. See `VERSION_A_RESEARCH_CONTRACT.md` for the exact allowlisted
pages/controls, native autosave meaning, Account privacy and Search global ordinal.

## Status, stop and export

Sign in as the approved Researcher at the shared Login. Inspect current
configuration state/period and recent sessions/events. A paused configuration,
outside-period request, excluded or inactive/non-Student account is ineligible.
An eligible page renders the collector; accepted batches and the session's
delivery counters distinguish observed activity from invalid/replayed/late
requests. Zero events during no eligible use is not automatically delivery failure.
Unreported browser/tab losses remain unknown; replay responses are transport
idempotency, not repeated-click behavior.

Pause/stop through the existing configuration action. Verify collection stopped
and a subsequent eligible Student page has no collector. Stop during incidents
or a material experimental UI change; leave Core LMS available if safe.
Do not edit sampling/windows/rating to troubleshoot delivery.

Create an export through Researcher Exports, selecting the intended configuration
and local session-start period. The archive is an immutable read snapshot with
its cutoff/time contract, dictionary, counts and internal hashes. Rates use
declared denominators. Each answered prompt labels only its own recorded lookback
window; unprompted/dismissed/unanswered intervals are unlabeled. Never fill missing
ratings with zero. No account mapping or content belongs in the archive.
Inspect the manifest's dataset_kind/provenance/configuration and source revision
notice before analysis. Large exports may be refused by existing resource caps;
use smaller periods/configuration scopes. Server resource caps refuse (never
truncate) above 50,000 sessions, 2,000,000 events or 200,000 prompts, and the
existing archive byte limit still applies. These are export resource boundaries,
not collection/sampling/label semantics. Download before expiry and keep copies
under the approved governance/retention policy. Historical pre-W7 Search positions
may restart per group; use a post-freeze configuration for homogeneous global-ordinal
analysis. Generator revisions do not relabel earlier observations.

## Daily retention and exclusions

The hosting operator schedules `scripts/run_research_retention.py` daily with
the production environment. Inspect count-only success logs and scheduler status.
It deletes research data older than the approved 15-day boundary and affected
export archives; it does not delete LMS academic/financial records or uploads.

Participation/consent/exclusions remain governed by the centre's external approved
arrangements. The system automatically covers eligible active Students unless
excluded; it has no per-page opt-in or automatic consent assertion. A private
operator terminal uses Researcher password authentication for these commands:

```sh
python scripts/research_operator.py --researcher APPROVED_EMAIL status STUDENT_EMAIL
python scripts/research_operator.py --researcher APPROVED_EMAIL exclude STUDENT_EMAIL
python scripts/research_operator.py --researcher APPROVED_EMAIL reinstate STUDENT_EMAIL
python scripts/research_operator.py --researcher APPROVED_EMAIL purge-expired
```

Emails are identity-sensitive; keep shell/output private. Passwords are prompted,
never command arguments. The legacy-exclusion override requires the existing
explicit flag only when the external process changed. The last command previews
expired counts; `--execute` deletes them only under the approved retention
operation. Neither the UI nor these tools create an ethics/consent approval.

## Deliberate transition to Study

Actual hosting/provider and production smoke have not yet been supplied or verified.
Keep `RESEARCH_DATA_PROVENANCE=development` throughout setup and smoke. A production
HTTP environment alone never implies Study. Do not import Developer/Pilot/demo
users, subjects, sessions, exports, private files or credentials into the clean
production database. Once real centre records exist they are persistent; do not
reset them to simplify research setup.

After documented baseline freeze, complete actual-host acceptance, TLS/private
storage/retention/backup/log checks and approved external participation arrangements.
Verify and deliberately exclude smoke/operator Students before Study. Pause
collection, back up, preserve immutable provenance, supply `study` privately,
restart and verify diagnostic scope. Existing non-study subjects remain non-study
and fail closed in Study; do not relabel them. Use genuine approved participant
accounts without historical smoke subjects and a NEW post-freeze configuration
with the approved exact sampling/window/rating policy and intended period.
Record baseline ID, configuration/version, dates and operator decision before
starting. Check eligibility, first accepted delivery, export classification,
missingness and stop control. Study activation must be a deliberate centre/research
operation, not an automated consequence of startup or this implementation.

Do not implement A/B assignment or Adaptive B here. A future B uses the same
Core LMS and dictionary with a separately approved isolated intervention layer.
Material Student UI changes after freeze require a version/change record and
research-impact evaluation; do not silently compare across different baselines.
