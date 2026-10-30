# Research v2 release preparation

Date: 2026-10-29. Source preparation only; no baseline approval, Git mutation,
database operation, hosting deployment or paid resource activation is recorded
by this document.

## Candidate boundary

The working tree contains unrelated Finance changes. Never run a release from
that whole tree or describe its inventory as a reviewed Research release. The
currently observed local HEAD and hosted source pin are
`4544d56915b7e321921f9ea652ba9b6b9c86e357`; recheck both before assembling a
candidate. Export that exact commit into a new ignored candidate directory,
then overlay only the explicit reviewed Research allowlist. Do not reset,
stash, clean or overwrite the original checkout. No `.git`, `.env`, instance
data, credentials, backups, exports, uploads or local operator receipts belong
in the candidate.

For mixed shared files, apply only the Research hunks to the exported commit.
In particular, do not copy the working-tree Arabic locale or authority ledgers
wholesale: they also contain unrelated Finance additions. Review the resulting
candidate against the pinned commit, including added files, rather than trusting
the allowlist alone. Preserve unrelated Finance work in the original checkout.

## Frozen inventory remains mandatory

`scripts/build_release.py` still requires an exact path-to-SHA256 runtime map
and its compact sorted-JSON aggregate SHA256 to match an existing baseline.
There is no bypass, baseline-writing or automatic approval option. Its default
is still `docs/VERSION_A_BASELINE_SOURCE.json`; the historical record is not
rewritten, and current source is expected to fail that old baseline.

An explicit `--baseline` may select another existing JSON file inside the
candidate's `docs/` directory. Relative baseline paths are resolved against the
directory containing that candidate's source, not the shell's current directory.
The record must contain:

- `baseline_identifier`: the distinct reviewed candidate identifier;
- `runtime_files`: every selected non-document path and its SHA256;
- `runtime_sha256`: SHA256 of UTF-8 `json.dumps(runtime_files, sort_keys=True,
  separators=(',', ':'))`;
- `migration_head`: the reviewed head of the candidate's migration graph;
- `design_system`, `dictionary_revision`, `tracking_scope_revision`: the reviewed
  version descriptions, including v1/v2 coexistence where appropriate.

Also record review authority, pinned parent commit, explicit Research allowlist,
acceptance evidence and limits in the candidate review record. An inventory
records bytes; it does not itself grant release or study approval. Generate and
inspect any proposed inventory only from the already filtered candidate. Do
not create an apparently accepted baseline from the main mixed worktree.
The read-only `source_inventory(include_docs=False)` helper returns the exact
runtime map for independent review. Baseline files are excluded from that map
to avoid self-reference. No builder command creates a replacement baseline.

The builder derives the migration head from literal revision metadata without
importing migrations, connecting to MySQL or invoking Alembic. It requires one
complete, connected, dependency-free revision chain and an exact baseline-head
match. A missing parent, duplicate revision, cycle, additional head or unsupported
dependency stops packaging. This check is source inspection, not evidence of a
successful MySQL upgrade or permission to apply one.

## Railway assets and final artifact

The allowlist now includes the Railway root configuration, Railpack build file,
exact Python version, hash-locked production dependency file, startup/migration/
Python-version assertion scripts, and both existing retention and quality
operator runners. The candidate still needs a separate Railway build-context
review: inclusion in the ZIP does not prove that Railpack copies each required
script or that cloud service configuration is correct.

After the independent candidate review and acceptance gates, invoke the builder
inside that candidate, for example:

```powershell
python <candidate>/scripts/build_release.py --baseline docs/RESEARCH_V2_BASELINE_SOURCE.json --output <new-artifact-path>.zip
```

Use actual absolute paths in place of the placeholders. The output ZIP and its
`.zip.sha256` sidecar must both be new. Validation completes before creating the
output directory. The v2 release manifest records the selected baseline path and
SHA256, runtime aggregate, derived migration head, reviewed metadata and all
packaged file hashes. ZIP integrity and every archived asset hash are verified
before success is reported. Inspect the ZIP/manifest and confirm the candidate
inventory has not changed after review.

This produces a source artifact, not a Git commit, hosted backup, deployed
service or collection activation. Hosted execution still requires a reviewed
source pin, exact target revision and fresh backup, a concrete deployment diff,
and separate approval for new paid monitoring resources. A physical-phone pilot
and collection/export/monitor acceptance remain separate launch gates.

The current ZIP document allowlist includes VERSION_A_*.md and the selected
baseline, not the new RESEARCH_V2_*.md operating records. Keep the reviewed v2
contracts and acceptance ledger with the source handoff; do not present this
ZIP alone as a complete v2 operations handoff. No document allowlist expansion
or replacement baseline approval is implied here.

## Verification boundary

This change uses Python AST parsing and scoped `git diff --check` only. No new
automated application tests, pytest, application/DB execution, dependency
installation, baseline freeze or artifact build is claimed. No candidate has
been approved merely by adding this packaging support.
