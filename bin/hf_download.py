#!/usr/bin/env python3
"""
Direct HuggingFace downloader — no hf CLI, no token required for public models.
Resume-safe via HTTP Range headers. Atomic writes via .tmp rename. Shows live speed.

Usage: python3 hf-download.py <repo_id> <local_dir> <glob_pattern> [--flat | --hf-cache]
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


def get_repo_info(repo_id, token):
    """Return (commit_sha, [rfilename, ...]) for a model repo's main revision."""
    url = f'https://huggingface.co/api/models/{repo_id}'
    with urllib.request.urlopen(make_request(url, token), timeout=30) as resp:
        data = json.loads(resp.read())
    sha = data.get('sha')
    files = [s['rfilename'] for s in data.get('siblings', [])]
    return sha, files


def list_files(repo_id, pattern, token):
    _, files = get_repo_info(repo_id, token)
    return [f for f in files if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(Path(f).name, pattern)]


def get_file_size(url, token):
    req = make_request(url, token)
    req.get_method = lambda: 'HEAD'
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return int(resp.headers.get('Content-Length', 0))
    except Exception:
        return 0


def _fetch(url, dest, token):
    """Robustly download `url` → `dest` (a Path): resume-safe, atomic .tmp rename.

    Survives a flaky link: each of 8 attempts resumes from the bytes already on
    disk via an HTTP Range header, and a short read (CDN closing the stream early)
    is treated as incomplete and retried rather than renamed as if complete.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.tmp')

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

    # A flaky link drops the stream mid-shard repeatedly; that's fine as long as
    # each resume inches forward. So cap on *consecutive no-progress* attempts, not
    # total attempts — a download that keeps advancing never exhausts its budget,
    # while a genuinely dead link still bails after MAX_STALLS idle rounds.
    MAX_STALLS = 12
    stalls = 0
    last_size = existing
    while True:
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
            cur = tmp.stat().st_size if tmp.exists() else 0
            stalls = 0 if cur > last_size else stalls + 1   # progress resets the budget
            last_size = cur
            if stalls >= MAX_STALLS:
                print(f'\n  ERROR: stalled {MAX_STALLS}x with no progress at '
                      f'{cur/1024**3:.2f}/{total/1024**3:.2f} GB: {url}')
                sys.exit(1)
            print(f'\n  Interrupted at {cur/1024**3:.2f} GB ({e}); resuming '
                  f'(stall {stalls}/{MAX_STALLS}) in 5s...')
            time.sleep(5)
            existing = cur
            extra = {'Range': f'bytes={existing}-'}
            mode = 'ab'


def download_file(repo_id, filename, local_dir, token, flat=False):
    url = f'https://huggingface.co/{repo_id}/resolve/main/{filename}'
    # Default preserves the repo's path under local_dir. --flat drops the repo
    # subdirs and writes the file directly as local_dir/<basename> — needed when
    # a repo nests files (e.g. split_files/<type>/<file>) but the target layout
    # is flat (e.g. ComfyUI's models/<type>/<file>).
    dest = Path(local_dir) / (Path(filename).name if flat else filename)
    _fetch(url, dest, token)


def _repo_dirname(repo_id):
    """huggingface_hub's on-disk repo dir, e.g. Qwen/Qwen3-8B → models--Qwen--Qwen3-8B."""
    return 'models--' + repo_id.replace('/', '--')


def download_to_cache(repo_id, pattern, cache_root, token):
    """Seed an HF hub cache so from_pretrained / hf_hub_download resolve OFFLINE.

    Lays matched files into the canonical layout transformers / huggingface_hub
    read when HF_HUB_OFFLINE=1:

        <cache_root>/hub/models--<org>--<name>/
            refs/main                       # the commit sha
            snapshots/<sha>/<rfilename>     # the file (a real file, not a blob symlink)

    Real files in snapshots/<sha>/ satisfy the offline cache lookup (it only checks
    the snapshot path exists), so we skip huggingface_hub's blobs/ + symlink dance —
    which keeps this a plain, resume-safe HTTP downloader for a flaky link. Content
    is pinned to <sha> so the snapshot is internally consistent.
    """
    sha, all_files = get_repo_info(repo_id, token)
    if not sha:
        print(f'  ERROR: could not resolve a commit sha for {repo_id} '
              f'(gated repo without a token?)')
        sys.exit(1)
    files = [f for f in all_files
             if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(Path(f).name, pattern)]
    if not files:
        print(f'No files matched pattern "{pattern}" in {repo_id}')
        sys.exit(1)

    repo_dir = Path(cache_root) / 'hub' / _repo_dirname(repo_id)
    snap = repo_dir / 'snapshots' / sha
    print(f'Seeding HF cache: {repo_id}@{sha[:12]} → {repo_dir}')
    print(f'Found {len(files)} file(s):')
    for f in files:
        print(f'  {f}')
    print()

    for f in files:
        url = f'https://huggingface.co/{repo_id}/resolve/{sha}/{f}'
        _fetch(url, snap / f, token)

    # refs/main lets the offline lookup resolve revision "main" → this snapshot.
    refs = repo_dir / 'refs'
    refs.mkdir(parents=True, exist_ok=True)
    (refs / 'main').write_text(sha)
    print(f'  ✓ refs/main → {sha[:12]}')


def usage():
    print('Usage: python3 hf-download.py <repo_id> <local_dir> <pattern> [--flat | --hf-cache]')
    print()
    print('Arguments:')
    print('  repo_id     HuggingFace repo  (e.g. <org>/<model>-GGUF)')
    print('  local_dir   Local destination directory')
    print('  pattern     Glob pattern to match filenames  (e.g. "*Q4_K_XL*", or "*" for all)')
    print('  --flat      Write matched files directly as local_dir/<basename>,')
    print('              dropping repo subdirs (e.g. split_files/<type>/).')
    print('  --hf-cache  Treat local_dir as an HF cache root (HF_HOME) and seed the')
    print('              canonical refs/ + snapshots/<sha>/ layout, so a later')
    print('              from_pretrained / hf_hub_download resolves it with')
    print('              HF_HUB_OFFLINE=1 (no network). Use "*" to mirror a whole repo.')
    print()
    print('Examples:')
    print('  python3 hf-download.py <org>/<model>-GGUF ~/models/<name> "*Q4_K_XL*"')
    print('  python3 hf-download.py Qwen/Qwen3-8B ~/spark-train/cache/huggingface "*" --hf-cache')
    print()
    print('Auth: token is optional for public models. If needed, reads from')
    print('      $HF_TOKEN or ~/.cache/huggingface/token (written by hf auth login).')


def main():
    argv = sys.argv[1:]
    flat = '--flat' in argv
    hf_cache = '--hf-cache' in argv
    argv = [a for a in argv if a not in ('--flat', '--hf-cache')]
    if len(argv) < 3 or argv[0] in ('-h', '--help'):
        usage()
        sys.exit(0 if '--help' in sys.argv else 1)
    if flat and hf_cache:
        print('--flat and --hf-cache are mutually exclusive.')
        sys.exit(1)

    repo_id, local_dir, pattern = argv[0], argv[1], argv[2]
    token = get_token()

    if hf_cache:
        download_to_cache(repo_id, pattern, local_dir, token)
        print('\nDone.')
        return

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
