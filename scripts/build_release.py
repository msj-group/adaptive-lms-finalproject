"""Build a data-free, allowlisted hosting artifact; never reads .env or storage."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / 'docs' / 'VERSION_A_BASELINE_SOURCE.json'
TOP_FILES = ('wsgi.py', 'requirements-production.txt', 'requirements-production.lock',
             '.env.production.example', '.dockerignore', '.python-version',
             'railpack.json', 'railway.toml')
OPERATOR_FILES = ('create_admin.py', 'create_researcher.py', 'research_operator.py',
                  'run_research_retention.py', 'run_research_quality.py',
                  'deployment_diagnostics.py', 'build_release.py', 'railway_start.py',
                  'railway_migrate.py', 'verify_python_version.py')
SUFFIXES = {'.py', '.html', '.css', '.js', '.json', '.png', '.jpg', '.jpeg', '.gif',
            '.webp', '.svg', '.ico', '.ttf', '.woff', '.woff2', '.txt', '.ini', '.mako'}


def reviewed_baseline_path(path):
    """Select a source-owned record; selection never creates or approves it."""
    candidate = path if path.is_absolute() else ROOT / path
    resolved = candidate.resolve()
    if (not candidate.is_file() or candidate.is_symlink()
            or (ROOT / 'docs').resolve() not in resolved.parents
            or resolved.suffix != '.json'):
        raise ValueError('Use an existing reviewed baseline JSON inside the source docs directory')
    return resolved


def release_files(include_docs=True, baseline_path=None):
    selected = [ROOT / name for name in TOP_FILES]
    selected += [ROOT / 'scripts' / name for name in OPERATOR_FILES]
    for directory in ('app', 'migrations', 'deploy'):
        for item in (ROOT / directory).rglob('*'):
            if not item.is_file() or '__pycache__' in item.parts:
                continue
            if directory == 'migrations' and 'proposals' in item.relative_to(ROOT / directory).parts:
                continue
            if item.suffix in SUFFIXES or directory == 'deploy' or item.name in ('README', 'Dockerfile'):
                selected.append(item)
    if include_docs:
        selected += list((ROOT / 'docs').glob('VERSION_A_*.md'))
        selected.append(reviewed_baseline_path(baseline_path or DEFAULT_BASELINE))
    # Reject links, unexpected extensions, and accidental absent operator assets.
    for item in selected:
        if not item.is_file() or item.is_symlink() or ROOT not in item.resolve().parents:
            raise ValueError('Release asset is absent or outside the source root')
    return sorted(set(selected), key=lambda p: p.relative_to(ROOT).as_posix())


def source_inventory(include_docs=True, baseline_path=None):
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in release_files(include_docs=include_docs, baseline_path=baseline_path)}


def migration_head():
    """Read a single complete revision chain without importing or executing it."""
    revisions = {}
    for path in sorted((ROOT / 'migrations' / 'versions').glob('*.py')):
        if path.name == '__init__.py':
            continue
        values = {}
        for statement in ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path)).body:
            if isinstance(statement, ast.Assign):
                names = [target.id for target in statement.targets if isinstance(target, ast.Name)]
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                names = [statement.target.id]
            else:
                continue
            for name in names:
                if name in ('revision', 'down_revision', 'depends_on'):
                    if name in values:
                        raise ValueError('Repeated migration metadata is not supported')
                    values[name] = ast.literal_eval(statement.value)
        revision, parent = values.get('revision'), values.get('down_revision')
        if (not isinstance(revision, str) or not revision or revision in revisions
                or 'down_revision' not in values
                or (parent is not None and (not isinstance(parent, str) or not parent))
                or values.get('depends_on') is not None):
            raise ValueError('Release requires unique literal revisions in one dependency-free chain')
        revisions[revision] = parent
    parents = {parent for parent in revisions.values() if parent is not None}
    heads = set(revisions) - parents
    if not revisions or not parents.issubset(revisions) or len(heads) != 1:
        raise ValueError('Release migration graph is incomplete or has multiple heads')
    head = next(iter(heads))
    visited = set()
    current = head
    while current is not None:
        if current in visited:
            raise ValueError('Release migration graph contains a cycle')
        visited.add(current)
        current = revisions[current]
    if visited != set(revisions):
        raise ValueError('Release migration graph is disconnected')
    return head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--baseline', type=Path, default=DEFAULT_BASELINE,
                        help='Existing reviewed baseline JSON under source docs; relative to source root')
    args = parser.parse_args()
    output = args.output.resolve()
    checksum_output = output.with_suffix('.zip.sha256')
    if output.suffix != '.zip' or output.exists() or checksum_output.exists():
        raise ValueError('Use new explicit .zip and checksum paths; existing artifacts are never overwritten')
    baseline_path = reviewed_baseline_path(args.baseline)
    inventory = source_inventory(baseline_path=baseline_path)
    baseline = json.loads(baseline_path.read_text(encoding='utf-8-sig'))
    for key in ('baseline_identifier', 'migration_head', 'design_system',
                'dictionary_revision', 'tracking_scope_revision'):
        if not isinstance(baseline.get(key), str) or not baseline[key].strip():
            raise ValueError('Reviewed baseline is missing release metadata')
    runtime = {name: digest for name, digest in inventory.items() if not name.startswith('docs/')}
    runtime_digest = hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if runtime != baseline.get('runtime_files') or runtime_digest != baseline.get('runtime_sha256'):
        raise ValueError('Runtime source differs from the frozen baseline; record and review the change first')
    head = migration_head()
    if head != baseline['migration_head']:
        raise ValueError('Migration graph head differs from the reviewed baseline')
    manifest = {'format': 'youth-centre-release.v2', 'migration_head': head,
                'baseline_identifier': baseline['baseline_identifier'],
                'baseline_path': baseline_path.relative_to(ROOT).as_posix(),
                'baseline_sha256': inventory[baseline_path.relative_to(ROOT).as_posix()],
                'runtime_sha256': runtime_digest,
                'design_system': baseline['design_system'],
                'dictionary_revision': baseline['dictionary_revision'],
                'tracking_scope': baseline['tracking_scope_revision'],
                'contains_business_or_research_data': False, 'files': inventory}
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'x', compression=ZIP_DEFLATED) as archive:
        for relative in inventory:
            archive.write(ROOT / relative, relative)
        archive.writestr('release-manifest.json', json.dumps(manifest, indent=2, sort_keys=True))
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Release ZIP integrity failure')
        for relative, digest in inventory.items():
            if hashlib.sha256(archive.read(relative)).hexdigest() != digest:
                raise ValueError('Release asset digest mismatch')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with checksum_output.open('x', encoding='utf-8', newline='\n') as checksum:
        checksum.write(digest + '  ' + output.name + '\n')
    print(json.dumps({'artifact': str(output), 'files': len(inventory), 'bytes': output.stat().st_size,
                      'sha256': digest, 'integrity_verified': True}, indent=2))


if __name__ == '__main__':
    main()
