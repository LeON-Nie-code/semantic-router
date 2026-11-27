"""Verify evidence checksums and produce the six-recipe qualification table."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

ORDER = ['d3-edge', 'd3-lite', 'd3-nano', 'd3-mini', 'd3-flash', 'd3']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    summaries, rows, verification, lifecycle = [], [], [], []
    for model in ORDER:
        candidates = sorted((args.root / 'results').glob(model + '-[0-9]*/summary.json'))
        # Runs with a frozen harness include full concurrent warmup and restart checks.
        final = [p for p in candidates if (p.parent / 'qualify.py').exists()]
        if not final:
            rows.append([model, 'PENDING', '-', '-', '-', '-', '-', '-', '-', '-'])
            continue
        path = final[-1]
        data = json.loads(path.read_text())
        sums = json.loads((path.parent / 'SHA256SUMS.json').read_text())
        for name, expected in sums.items():
            actual = hashlib.sha256((path.parent / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f'{path.parent / name}: checksum mismatch')
        records = [json.loads(line) for line in (path.parent / 'requests.jsonl').read_text().splitlines()]
        if data.get('total_requests') is not None:
            assert data['total_requests'] == len(records)
            assert data['failed_requests'] == sum(bool(r['failures']) for r in records)
        for concurrency, point in data['concurrency'].items():
            selected = [r for r in records if r['phase'] == f'concurrency-{concurrency}']
            assert point['attempted'] == len(selected)
            assert point['succeeded'] == sum(not r['failures'] for r in selected)
        verification.append({'model': model, 'directory': path.parent.name,
                             'checked_files': len(sums), 'checked_requests': len(records)})
        data['failure_counts'] = {
            'http_or_transport': sum(r.get('status') != 200 for r in records),
            'nonfinite': sum('nonfinite' in r['failures'] for r in records),
            'missing_results': sum(any(f.startswith(('missing:', 'missing_value:'))
                                       for f in r['failures']) for r in records),
            'oom_responses': sum('out of memory' in json.dumps(r.get('response', {})).lower()
                                 or 'out_of_memory' in json.dumps(r.get('response', {})).lower()
                                 for r in records),
        }
        lifecycle.append([model, f"{data.get('load_to_ready_seconds', 0):.2f}",
                          str(data.get('total_requests', len(records))),
                          str(data.get('failed_requests', '?')),
                          str(data.get('repeat_stable', False)),
                          str(data.get('after_long_stable', False)),
                          str(data.get('restart_stable', False))])
        summaries.append(data)
        loaded = data.get('loaded_allocator', {}).get('allocated_bytes', 0) / 2**30
        peak = data.get('final_allocator', {}).get('peak_allocated_bytes', 0) / 2**30
        device_loaded = data.get('loaded_allocator', {}).get('device_used_bytes', 0) / 2**30
        device_peak = data.get('final_allocator', {}).get('sampled_peak_device_used_bytes', 0) / 2**30
        if not data['concurrency']:
            rows.append([model, data['verdict'], '-', '-', '-', '-', '-', '-', '-', '-'])
        for concurrency in sorted(data['concurrency'], key=int):
            point = data['concurrency'][concurrency]
            rows.append([model, data['verdict'], concurrency,
                         f"{point['latency_p50_ms']:.2f}", f"{point['latency_p95_ms']:.2f}",
                         f"{point['successful_requests_per_second']:.2f}",
                         f'{loaded:.2f}', f'{peak:.2f}',
                         f'{device_loaded:.2f}', f'{device_peak:.2f}'])
    output = args.root / 'REPORT.md'
    headers = ['Model', 'Status', 'Concurrency', 'p50 ms', 'p95 ms', 'req/s',
               'Loaded allocated GiB', 'Peak allocated GiB',
               'Loaded device GiB', 'Sampled peak device GiB']
    lines = [
        '# Decision 3 on NVIDIA A100-SXM4-80GB', '',
        'Source commit: `271f38dd46c6cf779ca0cce378d4a0904158f27e`.', '',
        'One complete 80 GB GPU, MIG disabled. The instance has a 14-core CPU quota and approximately 248 GiB RAM.', '',
        'Measurements use the unmodified native runtime HTTP `/v1/systemone` endpoint, '
        'with result caching disabled. Each of 24 deterministic text requests contains '
        'Choice, Noul and Score questions. Each concurrency uses 96 matching concurrent '
        'warmup requests and 96 timed requests. Warmup responses and failures remain in the evidence.', '',
        'Latency is client-observed loopback HTTP time. Throughput counts successful requests '
        'over total measured wall time. The short run is a qualification sample, not a sustained-load or model-accuracy benchmark.', '',
        'Allocator figures are PyTorch tensor allocated bytes, including model weights and '
        'runtime allocations. NVML device figures include CUDA context and allocations outside '
        'PyTorch; the device peak is sampled every 100 ms and may miss shorter transients. '
        'Load-to-ready timing includes verification, loading, calibration and warmup; '
        'filesystem and compilation caches may already be populated.', '',
        'Repeated answers, post-long-input answers and process restart answers use '
        '`atol=1e-5, rtol=1e-5` on numeric fields and exact matching on categorical fields.', '',
        '| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |',
    ]
    lines.extend('| ' + ' | '.join(map(str, row)) + ' |' for row in rows)
    lines += ['', '## Load and stability', '',
              '| Model | Load to ready s | All requests | Failed requests | Repeated | After long input | Restart |',
              '| --- | --- | --- | --- | --- | --- | --- |']
    lines.extend('| ' + ' | '.join(row) + ' |' for row in lifecycle)
    lines += ['', '## Evidence status', '',
              'The platform container image digest is not exposed in the current environment. '
              'Each run records the exact dependency versions, source revision, model revision '
              'and package hashes. Full qualification remains pending this image identity.', '',
              'Model SHA-256, manifest SHA-256, package byte counts, model cards, graph/kernel '
              'receipts, allocator/NVML counters, commands, raw responses and per-file SHA-256 values '
              'are retained in each run directory. Earlier exploratory runs are retained separately.', '']
    output.write_text('\n'.join(lines))
    with (args.root / 'performance.csv').open('w') as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
    (args.root / 'verification.json').write_text(json.dumps(verification, indent=2))
    (args.root / 'summary.json').write_text(json.dumps(summaries, indent=2))
    print(output)
    for row in rows:
        print(' | '.join(map(str, row)))


if __name__ == '__main__':
    main()
