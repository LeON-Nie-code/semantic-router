"""Download only registry-pinned package data and verify every file's SHA-256."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(model, name, expected, root, endpoint):
    target = root / model['repo_id'].split('/')[-1] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and digest(target) == expected:
        return {'file': name, 'bytes': target.stat().st_size, 'sha256': expected}
    if target.exists():
        target.rename(target.with_name(target.name + '.invalid-' + str(time.time_ns())))
    temporary = target.with_name(target.name + '.partial')
    if temporary.exists() and digest(temporary) == expected:
        os.replace(temporary, target)
        return {'file': name, 'bytes': target.stat().st_size, 'sha256': expected}
    url = f"{endpoint}/{model['repo_id']}/resolve/{model['revision']}/{name}"
    print(f"FETCH {model['repo_id']} {name}", flush=True)
    subprocess.run(['curl', '--silent', '--show-error', '--fail', '--location', '--retry', '5',
                    '--retry-all-errors', '--connect-timeout', '20',
                    '--speed-limit', '1024', '--speed-time', '90',
                    '--continue-at', '-', '--output', str(temporary), url], check=True)
    actual = digest(temporary)
    if actual != expected:
        raise ValueError(f'{target}: SHA256 {actual} != {expected}')
    os.replace(temporary, target)
    print(f"VERIFIED {model['repo_id']} {name} {target.stat().st_size}", flush=True)
    return {'file': name, 'bytes': target.stat().st_size, 'sha256': actual}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--lock', type=Path, required=True)
    p.add_argument('--endpoint', default='https://hf-mirror.com')
    p.add_argument('--only', nargs='*')
    p.add_argument('--workers', type=int, default=6)
    args = p.parse_args()
    models = json.loads(args.lock.read_text())
    order = ['d3-edge', 'd3-lite', 'd3-nano', 'd3-mini', 'd3-flash', 'd3']
    for short in order:
        if args.only and short not in args.only:
            continue
        model = next(m for m in models if m['repo_id'] == 'vllm-sr/' + short)
        print(f"START {short}@{model['revision']}", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(download, model, name, sha, args.root, args.endpoint)
                       for name, sha in model['files'].items()]
            files = [f.result() for f in futures]
        receipt = {'repo_id': model['repo_id'], 'revision': model['revision'],
                   'model_sha256': model['model_sha256'],
                   'manifest_sha256': model['manifest_sha256'], 'files': files,
                   'package_bytes': sum(f['bytes'] for f in files),
                   'download_endpoint': args.endpoint}
        (args.root / short / 'download-receipt.json').write_text(json.dumps(receipt, indent=2))
        print(f"COMPLETE {short} {receipt['package_bytes']}", flush=True)


if __name__ == '__main__':
    main()
