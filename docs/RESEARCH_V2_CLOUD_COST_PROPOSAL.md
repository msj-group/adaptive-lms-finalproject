# Research v2 monitoring cost proposal

Prepared 2026-10-29. Proposal only: no service, schedule, account, secret,
database permission, notification or paid resource has been activated.

Owner decision, 2026-10-29: prepare monitoring only; do not activate paid
resources. The proposed $1-5/month estimate is NOT spending authorization.

## Proposed operation

| Component | Proposed configuration | Purpose |
| --- | --- | --- |
| Quality worker | One Railway scheduled instance, hourly; 120-second process ceiling; target limit 0.5 GB RAM and 1 vCPU | Inspect aggregate delivery/coverage and report actionable incidents |
| Quality state | One private 0.5 GB volume; 45-day local operational retention | Preserve notification deduplication and report progress across runs |
| Retention worker | One daily scheduled instance, same target resources, separate success heartbeat | Enforce each configuration's recorded expiry policy |
| Independent monitor | Two Healthchecks.io checks on the free Hobbyist plan, private Telegram integration | Alert if either scheduled job stops completing |

The workers run on hosting and continue while the owners' computer is off.
The quality worker uses a separate read-only database principal; publishing
aggregate dashboard reports requires a separately limited publisher principal.
The retention worker has only its reviewed retention permissions. None of
these new identities/permissions is installed by this proposal. Never reuse
the migration administrator for a runtime worker.

No model API or paid AI review is required for this first operational monitor.
It detects collection problems; it cannot certify model accuracy or participant
frustration. Human pilot review remains required.

## Estimated additional cost

Railway's published rates are $0.00000386 per GB-second of RAM,
$0.00000772 per vCPU-second and $0.00000006 per GB-second of volume storage.
For a 30-day month, 720 hourly runs each consuming the full 120 seconds at
0.5 GB RAM and one full vCPU cost about $0.834 in compute. A 0.5 GB volume
for that month is about $0.078. Thirty daily 120-second runs add about $0.035
in compute. Combined nominal resources are approximately $0.95/month.

**Proposed initial operating budget: $1-5/month additional to the existing
web/database bill. This is an estimate, not a guaranteed billing cap.**
Boot/build overhead, database activity, network traffic, account plan and real
resource consumption can change the total. Storage is billed throughout the
month; CPU/RAM estimates assume the scheduled processes exit promptly.
The retention runner has no proven 120-second execution bound yet; measure
it before accepting this estimate for production and keep its heartbeat grace
separate from the quality worker deadline. Resource limits must be verified in
the actual service configuration before enabling either schedule.

Railway Hobby has a $5 monthly minimum including $5 usage credits; Pro has a
$20 minimum including $20 usage. Remaining credits may cover the workers.
The current workspace plan, remaining credits and actual bill have not been
verified. The Free plan does not provide ongoing cron jobs after trial, so a
plan change could cost more than the incremental worker estimate above.
No plan upgrade is authorized by accepting this proposal alone.

Healthchecks.io currently offers 20 monitored jobs and 100 log entries per
job for $0/month. Its Telegram integration supports private messages.
Heartbeat requests will carry no participant IDs, ratings, answers or event
payloads. Healthchecks will still receive network metadata and job timings.
Telegram quality alerts will contain generic operational incident categories;
participant data remains in the role-gated platform.

## Before activation

1. Confirm the owners' budget decision and inspect the actual Railway plan.
2. Review the research-only release candidate; verify the hosted DB's exact
   parent revision and recoverable backup before the approved upgrade.
3. Configure worker identities and secrets using private deployment settings,
   never chat, source, logs or browser local storage.
4. Connect two distinct success checks and a private Telegram destination.
   Review and authorize the specific notification test before sending it.
5. Demonstrate successful runs, missed-run alerts, notification failure and
   recovery with fictional data. Check resource consumption and expiry scope.
6. Enable collection only after the phone/pilot gates are accepted. Do not
   certify delivery from a quiet period with no eligible Student traffic.

## Official sources checked 2026-10-29

- [Railway pricing](https://railway.com/pricing)
- [Railway cron lifecycle](https://docs.railway.com/cron-jobs)
- [Healthchecks.io pricing](https://healthchecks.io/pricing/)
- [Healthchecks.io Telegram integration](https://healthchecks.io/integrations/telegram/)
