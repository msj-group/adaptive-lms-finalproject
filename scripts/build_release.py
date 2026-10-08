"""Build a data-free, allowlisted hosting artifact; never reads .env or storage."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
TOP_FILES = ('wsgi.py', 'requirements-production.txt', '.env.production.example', '.dockerignore')
OPERATOR_FILES = ('create_admin.py', 'create_researcher.py', 'research_operator.py',
                  'run_research_retention.py', 'deployment_diagnostics.py', 'build_release.py')
SUFFIXES = {'.py', '.html', '.css', '.js', '.json', '.png', '.jpg', '.jpeg', '.gif',
            '.webp', '.svg', '.ico', '.ttf', '.woff', '.woff2', '.txt', '.ini', '.mako'}


def release_files(include_docs=True):
    selected = [ROOT / name for name in TOP_FILES]
    selected += [ROOT / 'scripts' / name for name in OPERATOR_FILES]
    for directory in ('app', 'migrations', 'deploy'):
        for item in (ROOT / directory).rglob('*'):
            if not item.is_file() or '__pycache__' in item.parts:
                continue
            if item.suffix in SUFFIXES or directory == 'deploy' or item.name in ('README', 'Dockerfile'):
                selected.append(item)
    if include_docs:
        selected += list((ROOT / 'docs').glob('VERSION_A_*.md'))
        selected.append(ROOT / 'docs' / 'VERSION_A_BASELINE_SOURCE.json')
    # Reject links, unexpected extensions, and accidental absent operator assets.
    for item in selected:
        if not item.is_file() or item.is_symlink() or ROOT not in item.resolve().parents:
            raise ValueError('Release asset is absent or outside the source root')
    return sorted(set(selected), key=lambda p: p.relative_to(ROOT).as_posix())


def source_inventory(include_docs=True):
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in release_files(include_docs=include_docs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.suffix != '.zip' or output.exists():
        raise ValueError('Use a new explicit .zip output path; existing artifacts are never overwritten')
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory = source_inventory()
    baseline = json.loads((ROOT / 'docs' / 'VERSION_A_BASELINE_SOURCE.json').read_text())
    runtime = {name: digest for name, digest in inventory.items() if not name.startswith('docs/')}
    runtime_digest = hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if runtime != baseline['runtime_files'] or runtime_digest != baseline['runtime_sha256']:
        raise ValueError('Runtime source differs from the frozen baseline; record and review the change first')
    manifest = {'format': 'youth-centre-release.v1', 'migration_head': '7d4e2a9c6013',
                'baseline_identifier': baseline['baseline_identifier'],
                'runtime_sha256': runtime_digest,
                'design_system': 'Direction D.1 / D-MOTION.1',
                'dictionary_revision': 'va-w7-r1', 'tracking_scope': 'va-scope-r1',
                'contains_business_or_research_data': False, 'files': inventory}
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
    output.with_suffix('.zip.sha256').write_text(digest + '  ' + output.name + '\n')
    print(json.dumps({'artifact': str(output), 'files': len(inventory), 'bytes': output.stat().st_size,
                      'sha256': digest, 'integrity_verified': True}, indent=2))


if __name__ == '__main__':
    main()
