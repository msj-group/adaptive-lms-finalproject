# D1 and ST2 source history preservation — 2026-10-08

The owner explicitly approved two local preservation commits and annotated
tags after the initial read-only audit. No push, history rewrite, deployment
or ST2 research freeze was authorized or performed.

| Source state | Commit | Annotated tag |
|---|---|---|
| Previous verified Version A D1 | `9ab3e4c3ba89d6eae581bce63951674b14c4e41b` | `YC-VA-D1-20261008` |
| Actual latest ST2 candidate | `c64429855252df8177d9a455f9bd25d39cc32753` | `YC-VA-AT2-20261008` |

Ancestry is `99133899529376436f52c292df09f5894e9e8cc1 -> D1 -> ST2`.
Both new commits are on `preserve/st2-20261008` in the isolated worktree
`C:/Users/abdul/Desktop/GraduationProject/AdaptiveEnglishLMS-preserve-ST2`.
The original dirty `AdaptiveEnglishLMS` working tree remains on `master` at
`9913389`; its source files and index were not overwritten or discarded.
The owner subsequently authorized a separate 22-file Railway deployment
commit whose direct parent is ST2. This does not change either preservation
commit or annotated tag. Resolve its exact ID with `git log -1 --format=%H`
on the prepared branch; it is not a new source freeze or a push/deployment.

All 65 reachable historical commits were inspected. None contained the exact
D1 source. The independently preserved D1 ZIP passed CRC, adjacent SHA256,
all 673 authored file hashes, and the original frozen 619-file runtime digest:
`77f2ff626775cf6443a53ec37d95dce385476c9d6e87dbd611314a15f52c70b3`.
The D1 archive hash is
`6951c61a41ca8471d55538cd90f9063ec4f4a2da53c0e3311d909cde24102c4f`.
Its commit was verified blob by blob against all 673 hashes. Nine superseded
HEAD source files already absent from the restored baseline were removed only
from the isolated worktree. Archive metadata was excluded from the commit.

All 691 current ST2 files matched the delivery manifest before preservation.
They were copied from the actual current primary source, not an older ZIP.
The ST2 commit was verified blob by blob against that manifest. It changes
34 Jinja files and adds 18 files: 17 runtime/assets and one ST2 document.
There are no D1-to-ST2 Python, JavaScript or migration changes or deletions.
Its 636-file runtime digest is
`0a3ad21a3f3c6c5dc079b047b5b9408d22d1aef1376f899d85ca5f3ead6485fa`.
Both commits preserve exact source bytes, including original CRLF/license
whitespace. They contain no private data directories, populated environment
files, dependencies or QA archives. A heuristic secret-pattern review found
only documented placeholder/sentinel MySQL URLs, not real credentials.

Source evidence and initial preflight audit remain independently preserved in
the owner's visualization evidence directories. Frozen D1 source/inventory
and the ST2 delivery report were not overwritten.

The owner confirmed `https://github.com/msj-group/adaptive-lms-finalproject.git`
as the official repository. Existing origin is correct and remains unchanged.
The later `git fetch --no-tags origin` succeeded on 2026-10-08; remote master
is `99133899529376436f52c292df09f5894e9e8cc1`. The prepared branch and both
annotated tags were absent remotely at verification time. No existing remote
history is replaced by creating those refs. Refresh the check before the
owner's later normal push; do not force-push or overwrite an existing tag.
Exact proposed push steps are in
[RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md).

ST2 remains **CANDIDATE NOT FROZEN**. A Git commit/tag or successful test
deployment does not approve final visuals, physical microphone, screen reader,
non-Blink browser, production acceptance or real Study collection.
