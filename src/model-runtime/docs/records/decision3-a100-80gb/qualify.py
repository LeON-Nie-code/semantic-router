"""Reproducible single-GPU HTTP qualification for six official D3 packages.

All requests and responses are retained, including warmup and failures.
Performance uses 96 measured requests per concurrency and a fixed request panel.
This is hardware qualification, not a model quality/accuracy benchmark.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

SOURCE = '271f38dd46c6cf779ca0cce378d4a0904158f27e'
ORDER = ['d3-edge', 'd3-lite', 'd3-nano', 'd3-mini', 'd3-flash', 'd3']


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_bytes(encode(value) + b'\n')


def questions():
    return {
        'domain': {'type': 'choice', 'instructions': 'Which domain is this request about?',
                   'criteria': {'code': 'Programming', 'math': 'Mathematics', 'other': 'Anything else'}},
        'reasoning': {'type': 'noul', 'instructions': 'Does this need multi-step reasoning?'},
        'difficulty': {'type': 'score', 'instructions': 'How difficult is this task?',
                       'criteria': ['Trivial', 'Moderate', 'Hard', 'Expert']},
    }


def panel():
    texts = [
        'Write a Python function that merges two sorted lists.',
        'Prove that the square root of two is irrational.',
        'Hello! How are you today?',
        '请解释数据库索引，并举一个 Python 查询的例子。',
        'Compare the time complexity of quicksort and mergesort.',
        'Explain how photosynthesis converts sunlight into chemical energy.',
        'What is 17 multiplied by 23? Show your reasoning.',
        'Summarize this document and identify its main assumptions. ',
    ]
    return [{'id': f'text-{i:02d}', 'body': {'state': text * repeat, 'questions': questions()}}
            for i, (text, repeat) in enumerate((t, n) for n in (1, 4, 12) for t in texts)]


def http(url, body=None, timeout=180):
    request = urllib.request.Request(url, data=None if body is None else encode(body),
                                     headers={'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors='replace')
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {'unparsed_body': raw}


def finite(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite(v) for v in value.values())
    if isinstance(value, list):
        return all(finite(v) for v in value)
    return True


def validate(status, body, expected):
    failures = []
    if status != 200:
        failures.append('http_error')
    if not finite(body):
        failures.append('nonfinite')
    answers = body.get('answers', {}) if isinstance(body, dict) else {}
    for key, q in expected.items():
        answer = answers.get(key)
        if not isinstance(answer, dict):
            failures.append('missing:' + key)
            continue
        if answer.get('error'):
            failures.append('question_error:' + key)
        if answer.get('type') != q['type']:
            failures.append('wrong_type:' + key)
        field = {'choice': 'choice', 'noul': 'noul', 'score': 'score'}[q['type']]
        if field not in answer or answer[field] is None:
            failures.append('missing_value:' + key)
        if q['type'] == 'choice' and answer.get(field) not in q['criteria']:
            failures.append('invalid_choice:' + key)
        if q['type'] == 'noul' and isinstance(answer.get(field), (int, float)):
            if not 0 <= answer[field] <= 1:
                failures.append('invalid_noul:' + key)
        if q['type'] in ('noul', 'score') and not isinstance(answer.get(field), (int, float)):
            failures.append('nonnumeric:' + key)
        if q['type'] in ('choice', 'score'):
            probs = answer.get('probabilities')
            if not isinstance(probs, dict) or not probs:
                failures.append('missing_probabilities:' + key)
            elif (not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
                          for v in probs.values()) or abs(sum(probs.values()) - 1) > 1e-4):
                failures.append('invalid_probabilities:' + key)
    return failures


def close(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(close(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(close(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right, abs_tol=1e-5, rel_tol=1e-5)
    return left == right


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(fraction * len(values)) - 1)] if values else None


def environment():
    versions = {}
    for name in ('torch', 'triton', 'fla-core', 'numpy', 'safetensors', 'tokenizers',
                 'huggingface-hub', 'uvicorn', 'starlette'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    def command(args):
        p = subprocess.run(args, capture_output=True, text=True)
        return {'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    return {'source_commit': SOURCE, 'versions': versions, 'python': sys.version,
            'platform': platform.platform(), 'cpu_quota': Path('/sys/fs/cgroup/cpu.max').read_text(),
            'memory_limit': Path('/sys/fs/cgroup/memory.max').read_text(),
            'gpu': command(['nvidia-smi', '-q']),
            'dependencies': command([sys.executable, '-m', 'pip', 'freeze']),
            'runtime_image_digest': None, 'runtime_image_status': 'platform metadata unavailable'}


def await_ready(base, process):
    limit = time.monotonic() + 1800
    while time.monotonic() < limit:
        if process.poll() is not None:
            raise RuntimeError(f'server exited {process.returncode}')
        try:
            status, card = http(base + '/v1/models', timeout=3)
            if status == 200 and card.get('data'):
                if 'failed' in [m.get('status') for m in card['data']]:
                    raise RuntimeError('model load failed: ' + json.dumps(card))
                if all(m.get('ready') for m in card['data']):
                    return card
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)
    raise TimeoutError('readiness exceeded 1800s')


def run_one(args, short, cases):
    destination = args.root / 'results' / (short + '-' + str(time.time_ns()))
    destination.mkdir(parents=True)
    model = args.root / 'models' / short
    receipt = json.loads((model / 'download-receipt.json').read_text())
    save(destination / 'package.json', receipt)
    save(destination / 'environment.json', environment())
    save(destination / 'inputs.json', cases)
    for name in ('qualify.py', 'server_probe.py', 'test_qualify.py'):
        shutil.copyfile(Path(__file__).with_name(name), destination / name)
    memory_file = destination / 'allocator.json'
    command = [sys.executable, str(Path(__file__).with_name('server_probe.py')),
               '--memory-file', str(memory_file), 'serve', str(model),
               '--served-model-name', 'vllm-sr/' + short, '--device', 'cuda:0',
               '--host', '127.0.0.1', '--port', str(args.port), '--threads', '14',
               '--offline', '--result-cache-entries', '0', '--load-attempts', '1']
    save(destination / 'command.json', command)
    records, lock = [], threading.Lock()
    raw = (destination / 'requests.jsonl').open('w')
    def request(case, phase, index):
        body = dict(case['body'], model='vllm-sr/' + short)
        started = time.perf_counter()
        item = {'phase': phase, 'index': index, 'id': case['id'],
                'request_sha256': hashlib.sha256(encode(body)).hexdigest()}
        try:
            status, response = http(base + '/v1/systemone', body, timeout=600)
            item.update(status=status, response=response,
                        failures=validate(status, response, body['questions']))
        except Exception as exc:
            item.update(status=None, error=repr(exc), failures=['transport_error'])
        item['latency_ms'] = (time.perf_counter() - started) * 1000
        with lock:
            raw.write(json.dumps(item, ensure_ascii=False) + '\n')
            raw.flush()
            records.append(item)
        return item
    base = f'http://127.0.0.1:{args.port}'
    summary = {'schema': 'decision3-a100-qualification/1',
               'model': receipt['repo_id'], 'verdict': 'FAIL', 'source_commit': SOURCE,
               'input_sha256': sha(destination / 'inputs.json'), 'concurrency': {},
               'stability_tolerance': {'atol': 1e-5, 'rtol': 1e-5}}
    process = None
    try:
        with (destination / 'server.log').open('w') as log:
            started = time.perf_counter()
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            card = await_ready(base, process)
            summary['load_to_ready_seconds'] = time.perf_counter() - started
            save(destination / 'model-card.json', card)
            time.sleep(0.5)
            if memory_file.exists():
                summary['loaded_allocator'] = json.loads(memory_file.read_text())
            baseline = [request(case, 'smoke', i) for i, case in enumerate(cases)]
            for concurrency in (1, 4, 16):
                phase = f'concurrency-{concurrency}'
                # Warm the same panel at the tested concurrency: sequential warmup
                # cannot prepare the graph layouts used by concurrent batches.
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    list(pool.map(lambda i: request(cases[i % len(cases)], 'warmup-' + phase, i),
                                  range(args.count)))
                started = time.perf_counter()
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    results = list(pool.map(lambda i: request(cases[i % len(cases)], phase, i),
                                            range(args.count)))
                elapsed = time.perf_counter() - started
                successful = [r for r in results if not r['failures']]
                latencies = [r['latency_ms'] for r in results]
                summary['concurrency'][str(concurrency)] = {
                    'attempted': len(results), 'succeeded': len(successful),
                    'failed': len(results) - len(successful), 'wall_seconds': elapsed,
                    'successful_requests_per_second': len(successful) / elapsed,
                    'latency_p50_ms': statistics.median(latencies),
                    'latency_p95_ms': percentile(latencies, .95)}
                print(short, phase, summary['concurrency'][str(concurrency)], flush=True)
            reference = baseline[0].get('response', {}).get('answers')
            repeated = [request(cases[0], 'repeat', i) for i in range(5)]
            long_case = {'id': 'long-8192-target', 'body': {
                'state': 'Analyze this document. ' +
                         'This paragraph describes software design, mathematics, and experimental evidence. ' * 700,
                'questions': questions()}}
            save(destination / 'long-input.json', long_case)
            request(long_case, 'long-input', 0)
            recovered = [request(cases[0], 'after-long', i) for i in range(5)]
            summary['repeat_stable'] = reference is not None and all(
                close(reference, r.get('response', {}).get('answers')) for r in repeated)
            summary['after_long_stable'] = reference is not None and all(
                close(reference, r.get('response', {}).get('answers')) for r in recovered)
            time.sleep(1.1)
            if memory_file.exists():
                summary['final_allocator'] = json.loads(memory_file.read_text())
            status, metrics = None, None
            with urllib.request.urlopen(base + '/metrics', timeout=10) as response:
                (destination / 'metrics.txt').write_bytes(response.read())
            process.terminate()
            process.wait(timeout=60)
            started = time.perf_counter()
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            save(destination / 'restart-model-card.json', await_ready(base, process))
            summary['restart_load_seconds'] = time.perf_counter() - started
            restarted = [request(cases[i], 'restart', i) for i in (0, 1, 3)]
            summary['restart_stable'] = all(
                close(baseline[i].get('response', {}).get('answers'),
                      r.get('response', {}).get('answers')) for i, r in zip((0, 1, 3), restarted))
            failures = [r for r in records if r['failures']]
            summary['failed_requests'] = len(failures)
            summary['total_requests'] = len(records)
            summary['execution_passed'] = (not failures and summary['repeat_stable'] and
                                           summary['after_long_stable'] and summary['restart_stable'])
            # Full qualification also requires an immutable runtime image identity.
            summary['verdict'] = 'PENDING_IMAGE_IDENTITY' if summary['execution_passed'] else 'FAIL'
    except Exception as exc:
        summary['exception'] = repr(exc)
        print(short, 'FAIL', repr(exc), flush=True)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raw.close()
        save(destination / 'summary.json', summary)
        checksums = {p.name: sha(p) for p in destination.iterdir() if p.is_file()}
        save(destination / 'SHA256SUMS.json', checksums)
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--only', nargs='*', default=ORDER)
    p.add_argument('--count', type=int, default=96)
    p.add_argument('--port', type=int, default=18143)
    p.add_argument('--skip-complete', action='store_true')
    args = p.parse_args()
    cases = panel()
    for short in args.only:
        if args.skip_complete and any(
            json.loads(p.read_text()).get('execution_passed') and
            json.loads(p.read_text()).get('final_allocator', {}).get('engine_receipt') and
            (p.parent / 'qualify.py').exists()
            for p in (args.root / 'results').glob(short + '-[0-9]*/summary.json')
        ):
            print('already completed', short, flush=True)
            continue
        receipt = args.root / 'models' / short / 'download-receipt.json'
        while not receipt.exists():
            print('waiting for verified package', short, flush=True)
            time.sleep(15)
        print('QUALIFY', short, flush=True)
        result = run_one(args, short, cases)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
