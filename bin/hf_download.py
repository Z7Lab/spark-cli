#!/usr/bin/env python3
"""
Direct HuggingFace downloader — no hf CLI, no token required for public models.
Resume-safe via HTTP Range headers. Atomic writes via .tmp rename. Shows live speed.

Usage: python3 hf-download.py <repo_id> <local_dir> <glob_pattern>
"""

import fnmatch
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


def get_token():
    for env in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
        if os.environ.get(env):
            return os.environ[env]
    p = Path.home() / '.cache' / 'huggingface' / 'token'
    return p.read_text().strip() if p.exists() else None


def make_request(url, token=None, extra_headers=None):
    headers = {'User-Agent': 'hf-download/1.0'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if extra_headers:
        headers.update(extra_headers)
    return urllib.request.Request(url, headers=headers)


def list_files(repo_id, pattern, token):
    url = f'https://huggingface.co/api/models/{repo_id}'
    with urllib.request.urlopen(make_request(url, token), timeout=30) as resp:
        data = json.loads(resp.read())
    files = [s['rfilename'] for s in data.get('siblings', [])]
    return [f for f in files if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(Path(f).name, pattern)]


def get_file_size(url, token):
    req = make_request(url, token)
    req.get_method = lambda: 'HEAD'
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return int(resp.headers.get('Content-Length', 0))
    except Exception:
        return 0


def download_file(repo_id, filename, local_dir, token, flat=False):
    url = f'https://huggingface.co/{repo_id}/resolve/main/{filename}'
    # Default preserves the repo's path under local_dir. --flat drops the repo
    # subdirs and writes the file directly as local_dir/<basename> — needed when
    # a repo nests files (e.g. split_files/<type>/<file>) but the target layout
    # is flat (e.g. ComfyUI's models/<type>/<file>).
    dest = Path(local_dir) / (Path(filename).name if flat else filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')

    total = get_file_size(url, token)
    existing = tmp.stat().st_size if tmp.exists() else (dest.stat().st_size if dest.exists() else 0)

    # Already complete
    if dest.exists() and total and dest.stat().st_size == total:
        print(f'  ✓ Already complete: {dest.name}')
        return

    if existing:
        print(f'  Resuming {dest.name} from {existing/1024**3:.2f} GB of {total/1024**3:.2f} GB')
        extra = {'Range': f'bytes={existing}-'}
        mode = 'ab'
    else:
        size_str = f'{total/1024**3:.2f} GB' if total else 'unknown size'
        print(f'  Downloading {dest.name} ({size_str})')
        extra = None
        mode = 'wb'

    for attempt in range(8):
        try:
            req = make_request(url, token, extra)
            with urllib.request.urlopen(req, timeout=60) as resp:
                downloaded = existing
                start = time.time()
                with open(tmp, mode) as fh:
                    while True:
                        chunk = resp.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        elapsed = time.time() - start or 0.001
                        speed = (downloaded - existing) / elapsed / 1024**2
                        pct = downloaded / total * 100 if total else 0
                        print(f'\r  {downloaded/1024**3:.2f}/{total/1024**3:.2f} GB  {pct:.1f}%  {speed:.1f} MB/s    ', end='', flush=True)
            print()
            # The server can close the connection early (CDN hiccup) — read() then
            # returns empty without raising. Verify the full length before
            # accepting the file; otherwise resume and retry rather than renaming a
            # truncated partial as if it were complete.
            if total and downloaded < total:
                raise IOError(f'incomplete download: {downloaded}/{total} bytes')
            tmp.rename(dest)
            return
        except Exception as e:
            print(f'\n  Attempt {attempt + 1} failed: {e}. Retrying in 5s...')
            time.sleep(5)
            existing = tmp.stat().st_size if tmp.exists() else 0
            extra = {'Range': f'bytes={existing}-'}
            mode = 'ab'

    print(f'  ERROR: Failed after 8 attempts: {filename}')
    sys.exit(1)


def usage():
    print('Usage: python3 hf-download.py <repo_id> <local_dir> <pattern> [--flat]')
    print()
    print('Arguments:')
    print('  repo_id     HuggingFace repo  (e.g. <org>/<model>-GGUF)')
    print('  local_dir   Local destination directory')
    print('  pattern     Glob pattern to match filenames  (e.g. "*Q4_K_XL*")')
    print('  --flat      Write matched files directly as local_dir/<basename>,')
    print('              dropping repo subdirs (e.g. split_files/<type>/).')
    print()
    print('Examples:')
    print('  python3 hf-download.py <org>/<model>-GGUF ~/models/<name> "*Q4_K_XL*"')
    print()
    print('Auth: token is optional for public models. If needed, reads from')
    print('      $HF_TOKEN or ~/.cache/huggingface/token (written by hf auth login).')


def main():
    argv = sys.argv[1:]
    flat = False
    if '--flat' in argv:
        flat = True
        argv = [a for a in argv if a != '--flat']
    if len(argv) < 3 or argv[0] in ('-h', '--help'):
        usage()
        sys.exit(0 if '--help' in sys.argv else 1)

    repo_id, local_dir, pattern = argv[0], argv[1], argv[2]
    token = get_token()

    print(f'Fetching file list: {repo_id}  (pattern: {pattern})')
    files = list_files(repo_id, pattern, token)

    if not files:
        print(f'No files matched pattern "{pattern}" in {repo_id}')
        sys.exit(1)

    print(f'Found {len(files)} file(s):')
    for f in files:
        print(f'  {f}')
    print()

    for f in files:
        download_file(repo_id, f, local_dir, token, flat=flat)

    print('\nDone.')


if __name__ == '__main__':
    main()
