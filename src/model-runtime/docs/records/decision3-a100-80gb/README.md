# Decision 3 on NVIDIA A100-SXM4-80GB

Source commit: `271f38dd46c6cf779ca0cce378d4a0904158f27e`.

One complete 80 GB GPU, MIG disabled. The instance has a 14-core CPU quota and approximately 248 GiB RAM.

Measurements use the unmodified native runtime HTTP `/v1/systemone` endpoint, with result caching disabled. Each of 24 deterministic text requests contains Choice, Noul and Score questions. Each concurrency uses 96 matching concurrent warmup requests and 96 timed requests. Warmup responses and failures remain in the evidence.

Latency is client-observed loopback HTTP time. Throughput counts successful requests over total measured wall time. The short run is a qualification sample, not a sustained-load or model-accuracy benchmark.

Allocator figures are PyTorch tensor allocated bytes, including model weights and runtime allocations. NVML device figures include CUDA context and allocations outside PyTorch; the device peak is sampled every 100 ms and may miss shorter transients. Load-to-ready timing includes verification, loading, calibration and warmup; filesystem and compilation caches may already be populated.

Repeated answers, post-long-input answers and process restart answers use `atol=1e-5, rtol=1e-5` on numeric fields and exact matching on categorical fields.

| Model | Status | Concurrency | p50 ms | p95 ms | req/s | Loaded allocated GiB | Peak allocated GiB | Loaded device GiB | Sampled peak device GiB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d3-edge | Passed service checks | 1 | 13.68 | 16.61 | 69.46 | 1.17 | 2.25 | 4.08 | 4.08 |
| d3-edge | Passed service checks | 4 | 47.93 | 73.19 | 87.52 | 1.17 | 2.25 | 4.08 | 4.08 |
| d3-edge | Passed service checks | 16 | 140.82 | 169.20 | 108.94 | 1.17 | 2.25 | 4.08 | 4.08 |
| d3-lite | Passed service checks | 1 | 18.14 | 23.72 | 52.20 | 1.67 | 2.74 | 4.60 | 4.60 |
| d3-lite | Passed service checks | 4 | 44.90 | 87.68 | 68.03 | 1.67 | 2.74 | 4.60 | 4.60 |
| d3-lite | Passed service checks | 16 | 270.35 | 346.25 | 57.86 | 1.67 | 2.74 | 4.60 | 4.60 |
| d3-nano | Passed service checks | 1 | 21.56 | 29.91 | 42.68 | 4.18 | 5.35 | 7.37 | 7.43 |
| d3-nano | Passed service checks | 4 | 58.48 | 89.10 | 61.19 | 4.18 | 5.35 | 7.37 | 7.43 |
| d3-nano | Passed service checks | 16 | 223.97 | 273.30 | 68.77 | 4.18 | 5.35 | 7.37 | 7.43 |
| d3-mini | Passed service checks | 1 | 41.54 | 52.88 | 24.28 | 8.61 | 9.77 | 12.35 | 12.95 |
| d3-mini | Passed service checks | 4 | 127.06 | 183.01 | 30.42 | 8.61 | 9.77 | 12.35 | 12.95 |
| d3-mini | Passed service checks | 16 | 503.03 | 576.01 | 31.30 | 8.61 | 9.77 | 12.35 | 12.95 |
| d3-flash | Passed service checks | 1 | 54.22 | 72.97 | 17.40 | 15.73 | 17.02 | 18.04 | 20.14 |
| d3-flash | Passed service checks | 4 | 177.87 | 269.24 | 20.72 | 15.73 | 17.02 | 18.04 | 20.14 |
| d3-flash | Passed service checks | 16 | 792.70 | 930.95 | 19.54 | 15.73 | 17.02 | 18.04 | 20.14 |
| d3 | Passed service checks | 1 | 150.87 | 233.04 | 6.06 | 48.66 | 50.48 | 50.74 | 54.59 |
| d3 | Passed service checks | 4 | 577.07 | 927.74 | 5.97 | 48.66 | 50.48 | 50.74 | 54.59 |
| d3 | Passed service checks | 16 | 2546.81 | 2977.93 | 6.11 | 48.66 | 50.48 | 50.74 | 54.59 |

## Load and stability

| Model | Load to ready s | All requests | Failed requests | Repeated | After long input | Restart |
| --- | --- | --- | --- | --- | --- | --- |
| d3-edge | 9.92 | 614 | 0 | True | True | True |
| d3-lite | 10.58 | 614 | 0 | True | True | True |
| d3-nano | 16.87 | 614 | 0 | True | True | True |
| d3-mini | 18.33 | 614 | 0 | True | True | True |
| d3-flash | 18.73 | 614 | 0 | True | True | True |
| d3 | 57.04 | 614 | 0 | True | True | True |

## Evidence and image provenance

The provider reported base-image digest
`sha256:44b85d2f47f7905f2d429a529fa45ee940f5d5353dbf89fb6bfe7a50513231b5`
for its managed container. The display tag was
`cuda13.2.1-py3.12-torch2.12.1.1`. The provider withheld its registry
reference. The digest was not independently verified against the registry.
The tested runtime was installed into a virtual environment on this base.
The repository-built CUDA image was not tested.

- GPU: one NVIDIA A100-SXM4-80GB, MIG disabled.
- Software: NVIDIA driver 595.91.07, Python 3.12.3, PyTorch 2.12.1+cu132,
  Triton 3.7.1 and FLA core 0.5.2. See [environment-lock.txt](environment-lock.txt)
  for the observed package versions.
- [results.json](results.json) lists each model revision, package and manifest
  hashes, package size, kernel and graph receipts, and result metrics.
- [inputs.json](inputs.json) contains the shared panel and long-text case.
  The runner adds the recipe ID as the model field before hashing the request.
- [responses.jsonl](responses.jsonl) concatenates the six original response
  files in results order. Each result specifies its one-based starting line,
  line count and SHA-256 over that exact slice, including newlines.

The model cards reported approximate numerics with batching enabled.
The stability checks compare outputs within each recipe and do not establish
reference-implementation parity. These tests cover the fixed text panel on
the named GPU and source revision. Official CUDA-image qualification remains
open.

## Reproduce

Check out runtime commit `271f38dd46c6cf779ca0cce378d4a0904158f27e`.
Use a Linux container with the CUDA software versions listed above, the
Python development headers, and an A100 80 GB. From that checkout, with the
CUDA build of PyTorch installed, install the runtime and FLA:

```sh
python -m pip install -c src/model-runtime/requirements-lock.txt ./src/model-runtime fla-core==0.5.2
```

From this record directory, use a fresh experiment path with sufficient
space for the model files:

```sh
python -m unittest -v test_qualify
python download_models.py --root /path/to/experiment/models --lock models-lock.json --endpoint https://huggingface.co
python qualify.py --root /path/to/experiment
python report.py /path/to/experiment
```

The service binds to loopback and serves one recipe at a time. Keep other GPU
workloads stopped for comparable measurements. The sampler reads allocator and
NVML counters without changing the model execution path.
