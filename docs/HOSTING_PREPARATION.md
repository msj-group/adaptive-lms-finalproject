# Hosting preparation and empty installation

Current ST2 Railway preparation uses [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md).
Its migration head is `7d4e2a9c6013`; the earlier repair commands below are
historical and must not be used for this Railway candidate. Do not deploy a
historical D1 release ZIP in place of current ST2.

Earlier D1 deployment authority (2026-10-08) referenced
`VERSION_A_PRODUCTION_RUNBOOK.md`, `VERSION_A_RESEARCH_OPERATIONS.md` and the
D1 release manifest. For this ST2 Railway candidate, the guide above supersedes
that release selection and the historical migration/preparation statements below.

Latest owner decision, 2026-10-06: the future hosting database starts with
no business data or example accounts. The existing local database is retained.
Evaluation uses ordinary workflows with manually entered fictional input;
its data is not an actual study. No hosting plan has been purchased or selected.

## LibyanSpider suitability

LibyanSpider documents Python and custom Docker support in
[JPaaS](https://help.libyanspider.com/ar/kb-article/getting-started-with-jpaas/).
Its published [software stack catalogue](https://help.libyanspider.com/kb-article/software-stack-versions/)
also lists MySQL and Redis. A [Linux VPS](https://help.libyanspider.com/kb-article/how-to-order-vps-in-client-area/)
provides another deployment route with control over the operating system.

These services are suitable candidates, not verification that this release
has been deployed there. Confirm the intended Python/runtime version, MySQL
version, private persistent storage, HTTPS and daily scheduler on the exact
plan before purchasing. Shared hosting cannot be assumed to support a
persistent Flask process, the pinned dependencies and all required services.
Use MySQL/InnoDB; the project does not substitute MariaDB or SQLite.

## Installation starts empty

1. Create a new, explicitly selected MySQL database and dedicated deployment
   credentials. Do not import the local database, SQL recovery archives,
   uploaded files, audio recordings or research exports.
2. Deploy the source and install compatible runtime dependencies on the
   chosen host. Supply secrets privately through its environment configuration.
   Keep the application stopped during schema preparation.
3. Set the production HTTP environment and `APP_TIMEZONE=Africa/Tripoli`.
   Set `PAYMENT_PROVIDER_MODE=disabled`, `RESEARCH_RETENTION_DAYS=15` and
   `RESEARCH_DATA_PROVENANCE=development` for operational evaluation.
   Preserve CSRF and secure cookies; use HTTPS and a private persistent
   `MATERIAL_STORAGE_ROOT` outside `app/static`.
4. On the new database only, apply the complete Alembic history using
   `flask --app run:app db upgrade`. Verify head `085b7a4e9012` and no
   business rows before provisioning accounts. Do not use `create_all`,
   revision stamping or a development backup as an installation shortcut.
   Migration metadata and internal control/sequence rows are necessary
   schema infrastructure, not example business content.
5. Provision the first Administrator deliberately in a private interactive
   terminal: `python scripts/create_admin.py`. Its environment comes from
   `FLASK_ENV`; its password is prompted privately. Nothing creates default
   users or copies the older four local accounts. Provision an approved
   Researcher with `scripts/create_researcher.py` and a privately configured
   `RESEARCHER_EMAIL_ALLOWLIST`. Other accounts and content are created through
   the appropriate ordinary workflows.
6. Review Course prices, Rooms, schedules, accounts and permissions with
   manually entered fictional input. Dashboards and registers show real
   stored counts, with empty states until input is entered. No simulated
   checkout, per-account demonstration action or data-mode selector exists.

The commands above describe deliberate future operations. Writing this guide
does not execute them, provision a server or change either database.

## Research integrity during operational evaluation

All environments default to `development` provenance, including production
HTTP settings. Research pages automatically select the deployment's scope;
URL parameters cannot select a different classification. The review scope
includes retained older non-study history without reclassifying its rows.
Only genuine study sessions are eligible for study exports. Evaluation
sessions, older non-study subjects and their sessions remain excluded.
The same export workflow can produce a separately labelled operational-review
archive on development deployments. Its server-selected scope is recorded in
the manifest, session rows and creation audit; it never becomes a study export.

Configure the appropriate collection period and sampling settings, then
deliberately start collection in the Researcher workspace if reviewing that
workflow. Ordinary startup does not create a configuration or start collection.
Schedule `scripts/run_research_retention.py` daily with the same target,
environment and approved 15-day retention. A later actual study needs its
own explicit operational decision and `RESEARCH_DATA_PROVENANCE=study`;
historical session classifications do not change when a setting changes.

## Remaining production preparation

- Select and verify a production WSGI server and process restart policy.
  `python run.py` is a local development command.
- Set trusted reverse-proxy handling and use a shared persistent login
  rate-limit backend for multiple processes. Current source still uses
  `memory://`; those host-specific production settings are not completed here.
- Configure HTTPS, private storage permissions, database/network access,
  request limits, backups with recovery procedures and service/job monitoring.
- Review the installed application manually across all four roles, including
  uploads/audio, financial/enrollment operations and scheduled retention.
  No new automated tests are requested or authorized.

No real online payment adapter is available. Cash and verified bank-transfer
collection use normal financial workflows. Historical provider evidence and
the complete migration chain remain; removing simulation is not a table drop.
