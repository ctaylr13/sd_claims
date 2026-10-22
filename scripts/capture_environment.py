"""Capture the local runtime before a frozen run; never read credentials."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def capture(output, root):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    commands = {
        'python-freeze.txt': [sys.executable, '-m', 'pip', 'freeze', '--all'],
        'python-inspect.json': [sys.executable, '-m', 'pip', 'inspect', '--local'],
        'python-check.txt': [sys.executable, '-m', 'pip', 'check'],
        'node-version.txt': ['node', '--version'],
        'yarn-version.txt': ['yarn', '--version'],
        'node-installed.json': ['npm', 'ls', '--all', '--json'],
        'tesseract-version.txt': ['tesseract', '--version'],
        'tesseract-languages.txt': ['tesseract', '--list-langs'],
        'git-head.txt': ['git', 'rev-parse', 'HEAD'],
        'git-status.txt': ['git', 'status', '--short'],
        'working-tree.patch': ['git', 'diff', '--binary', 'HEAD'],
    }
    results = {}
    for name, command in commands.items():
        try:
            result = subprocess.run(command, cwd=root, capture_output=True, timeout=120)
            (output / name).write_bytes(result.stdout)
            (output / (name + '.stderr')).write_bytes(result.stderr)
            results[name] = {'command': command, 'returncode': result.returncode}
        except (OSError, subprocess.TimeoutExpired) as error:
            results[name] = {'command': command, 'error': str(error)}

    languages = output / 'tesseract-languages.txt'
    tessdata = None
    if languages.exists():
        match = re.search(r'List of available languages in "(.+?)"', languages.read_text())
        if match:
            tessdata = Path(match[1])
    binaries = {}
    for executable in (sys.executable, 'node', 'yarn', 'tesseract'):
        resolved = shutil.which(executable)
        if resolved:
            path = Path(resolved).resolve()
            binaries[executable] = {'path': str(path), 'sha256': file_hash(path)}
    sources = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root,
    ).decode().split('\0')
    metadata = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'python': sys.version, 'platform': platform.platform(),
        'machine': platform.machine(), 'libc': platform.libc_ver(),
        'commands': results, 'executables': binaries,
        'thread_settings': {key: os.environ.get(key) for key in (
            'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')},
        'tessdata': ({'path': str(tessdata), 'hashes': {
            str(p.relative_to(tessdata)): file_hash(p)
            for p in sorted(tessdata.rglob('*')) if p.is_file()}}
            if tessdata and tessdata.is_dir() else None),
        'source_hashes': {name: file_hash(root / name) for name in sorted(set(sources))
                          if name and (root / name).is_file()},
        'limits': 'Installed-environment capture, not an offline wheel/binary archive. '
                  'Includes historical packages still installed; does not expand application requirements.',
    }
    (output / 'environment.json').write_text(json.dumps(metadata, indent=2) + '\n')
    inventory = {p.name: file_hash(p) for p in sorted(output.iterdir()) if p.is_file()}
    (output / 'SHA256.json').write_text(json.dumps(inventory, indent=2) + '\n')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.umask(0o077)
    metadata = capture(args.output.resolve(), root)
    failed = [name for name, result in metadata['commands'].items()
              if result.get('returncode') != 0]
    print(json.dumps({'output': str(args.output), 'failed_checks': failed}))
    return bool(failed)


if __name__ == '__main__':
    raise SystemExit(main())
