# Shadow Dispatch

## Overview

`shadow_dispatch` sends a sampled copy of an approved request to a group of candidate models and records their outcomes without changing or waiting on the primary response.

## Key Advantages

- Compare several candidates on the same sample of live traffic before gray release.
- Keep the primary response independent of candidate latency, failures, and output.
- Bound background work with call, time, byte, and concurrency limits.
- Join candidate outcomes to the primary request's replay record for later comparison.

## What Problem Does It Solve?

Offline evaluation can miss real prompt distributions. Shadow dispatch lets candidate models see production-shaped requests while the primary model continues to answer users. Each candidate's outcome is appended to the request's replay record for comparison later.

Each shadow copy starts from the approved neutral request after request plugins have run. The router renders it for the candidate through the usual encoding and provider adaptation, including reasoning controls, the provider's `chat_path`, and Azure OpenAI's `api_version`. Shadow calls are non-streaming.

Authentication comes from each candidate's configured provider credentials. HTTPS uses strict TLS certificate verification. Only W3C `traceparent` and `tracestate` headers propagate from the request; client credentials, `baggage`, and decision header mutations are not forwarded. Redirects are never followed, so prompts and credentials stay at the configured backend address.

## When to Use

- Evaluate candidates on live traffic before gray release.
- Keep candidate work separate from the live response.
- Collect replay observations for a comparison dataset or an external judge.

The plugin records outcomes; it does not grade answers or influence the live response.

## Configuration

Add the plugin to a decision alongside `router_replay` so outcomes have a record to attach to. Replace the candidate names with logical models configured in `providers.models`:

```yaml
plugins:
  - type: router_replay
    configuration:
      enabled: true
  - type: shadow_dispatch
    configuration:
      enabled: true
      models: [candidate-a, candidate-b]
      sample_rate: 0.05
      execution:
        max_concurrency: 2
        max_queue_size: 8
        timeout: 30s
      budget:
        max_calls: 2
        max_response_bytes: 2097152
      capture:
        enabled: false
        max_bytes: 8192
```

This is a breaking configuration update: replace old shadow configurations with this structure. Old configurations are rejected at load time.

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | required | Set to `true` to enable shadow dispatch for this decision. |
| `models` | required when enabled | List of configured logical models, at most 8. A single candidate also uses a list: `[candidate-a]`. |
| `sample_rate` | `1.0` | Fraction of eligible request groups to dispatch, in `[0, 1]`. One sampling decision covers the whole group. `0` disables dispatch. |
| `execution.max_concurrency` | `2` | Maximum in-flight candidate HTTP calls, shared across requests for the same recipe and decision. |
| `execution.max_queue_size` | `8` | Maximum pending request groups for the same recipe and decision, shared across requests. Counts groups, not candidate calls. |
| `execution.timeout` | `30s` | Per-group deadline, including queue wait and all candidate calls. Must be positive and at most `5m`. |
| `budget.max_calls` | Number of configured models | Actual HTTP call limit per group. Must cover all configured models. The first version makes no retries. |
| `budget.max_response_bytes` | Number of configured models × `1048576` | Aggregate response byte budget per group, divided up front among effective candidates. |
| `capture.enabled` | `false` | Store bounded excerpts of candidate text in replay outcomes. |
| `capture.max_bytes` | Number of configured models × `4096` | Aggregate capture byte limit per group, divided equally among effective candidates. |

The router samples once per request group and excludes the primary model from its effective candidates. A sampled-out request, or a group with no remaining candidates, produces metrics but no shadow outcome. Decisions that execute through the looper (ratings, confidence, fusion, ReMoM, workflows) reject the plugin at config load; the hook runs only on single-model provider dispatch.

Concurrency and queue capacity are shared across requests for each recipe and decision. The deadline and budgets apply to each request group. In the two-candidate example, the response budget gives each candidate 1 MiB and capture allows 4096 bytes each. Allocations are fixed before dispatch; errors remain charged conservatively rather than freeing budget for another candidate.

Admission limits cover calls, elapsed time, bytes, and concurrency. Reported token usage and cost estimates based on catalog pricing are observations, not token or cost admission limits. Missing usage is unknown, not zero.

### Fail-open behavior

The request path makes a non-blocking group admission check and returns. Everything else runs in a bounded worker after the primary dispatch response has been built. The primary path does not wait for candidate responses. Slow or failed candidates leave its response unchanged. Each candidate call ends in one result:

| Result | Reasons |
| --- | --- |
| `completed` | `completed` |
| `failed` | `backend_unresolved`, `credential_unresolved`, `encode_failed`, `timeout`, `queue_timeout`, `canceled`, `router_closing`, `transport_error`, `upstream_status`, `redirect_rejected`, `response_too_large`, `malformed_response` |
| `dropped` | `queue_full`, `same_as_primary`, `internal_request`, `request_unavailable` |
| `sampled_out` | `sampled_out` |

Results and reasons are exported as `sr_shadow_dispatch_total{decision,result,reason}`, with `sr_shadow_dispatch_latency_seconds`, `sr_shadow_dispatch_inflight`, and `sr_shadow_dispatch_queued`. Drops caused by resource bounds are reported through metrics and a structured `shadow_dispatch_dropped` event rather than a replay write, so an overloaded shadow lane cannot amplify load on the replay store.

### Interpreting failures

A `failed` outcome does not always say something about the candidate model. Read the reason together with `status_code`, `attempts`, and the truncated `error` in the outcome metadata:

| Meaning | Reasons | How to read it |
| --- | --- | --- |
| Candidate rejected the request | `upstream_status` with a 4xx `status_code` | The shadow model could not accept the approved request, for example an unsupported parameter or a context window that is too small. Count it against the candidate. |
| Candidate health or capacity | `upstream_status` with a 5xx `status_code`, `timeout`, `transport_error` | The backend was unreachable, overloaded, or too slow within the group’s `execution.timeout`. This measures the deployment, not answer quality. |
| Candidate tried to redirect | `redirect_rejected` with a 3xx `status_code` | The backend answered with a redirect. The router never follows it, so the prompt and the shadow credential only reached the configured backend. Point the shadow model at the backend's final address. |
| Candidate output problem | `malformed_response`, `response_too_large` | The backend answered but the body was not a valid response for its wire format or exceeded its share of `budget.max_response_bytes`. |
| Router-side, not about the candidate | `backend_unresolved`, `credential_unresolved`, `encode_failed` | The router could not build or address the shadow call. Fix the configuration and exclude these from any candidate comparison. |

`dropped` and `sampled_out` never reach the replay record. They mean the router chose not to send the shadow, so they carry no signal about the candidate model and are visible only in metrics and logs.

### Replay and audit capture

Each completed or failed candidate call appends one outcome to the primary request's replay record with `source: shadow_dispatch`, `target: model`, `target_ref: <shadow model>`, the verdict, and the reason. The outcome metadata carries primary and shadow request identity, primary and shadow model and backend, decision and recipe, sample rate, enqueue, start, and finish timestamps, queue wait and latency, attempts, status code, response size, stop reason, token counts, and a SHA-256 of the shadow text. Outcomes are append-only.

Replay redaction applies to shadow outcomes the same way it applies to the rest of the record: viewers without content rights see the routing and timing fields but not `target_ref`, `reason`, or `metadata`. Enable `capture.enabled` only when you need to store response text. See the fragment:
[`config/fragments/plugin/shadow-dispatch/sampled.yaml`](https://github.com/vllm-project/semantic-router/blob/main/config/fragments/plugin/shadow-dispatch/sampled.yaml).

### Export a comparison dataset

Captured outcomes become a comparison dataset through `GET /api/v1/observability/replays/dataset`. The endpoint reads the records the usual replay filters select and answers with a versioned manifest of the primary and candidate outputs for each input.

A manifest is identified by its own digest, so the export takes the split plan as part of the request: `seed` fixes split assignment and each repeated `split` is written as `name:weight`, for example `?seed=2026-q3&split=train:8&split=eval:2`. The same records under the same seed and splits rebuild the same manifest, including the split every example lands in, so adding later observations never moves an example that was already placed.

```bash
curl -H "Authorization: Bearer $ROUTER_MANAGEMENT_TOKEN" \
  "$ROUTER_MANAGEMENT_URL/api/v1/observability/replays/dataset?recipe=vault&seed=2026-q3&split=train:8&split=eval:2"
```

The manifest carries identity, output digests, and lineage, never prompt or response text, so it can be published beside the numbers it supports. An observation enters whole or not at all: a request that failed, is unfinished, lost its input to truncation, or never recorded a digest is left out and counted under an exclusion reason, and `counts` reports what was kept and what was dropped. Because the manifest describes the whole selection it was built from, a selection larger than 5000 records is refused rather than exported as a page. Narrow the filters and export again.

One recipe or one decision usually dominates live traffic, and a dataset built from it reads as a statement about the whole router when it is a statement about that decision. `balance_by` and `balance_max` cap how much one group may contribute, grouping by `recipe`, `decision` or `primary_model`. The rows a cap keeps follow the seed rather than the clock, so a balanced dataset is a sample of the traffic and not of when it arrived, and rows dropped for balance are counted under `balance_cap` like any other exclusion. Both parameters are given together; one alone is refused.

The export needs `replay.read` and reads the same records the list API does. Body capture must be on for the decisions being compared, since an observation with no captured request is excluded as `request_body_missing`.

### Hand the comparison to a judge

`GET /api/v1/observability/replays/dataset/judge-tasks` takes the same selection and split plan as the export, plus a `blinding_key`, and answers with pairwise tasks: one primary answer against one shadow answer, with the request each was given. Every pair appears twice with the sides swapped, so a judge that prefers whichever answer comes first shows up as disagreement between the two orders. Each side carries an opaque arm label that names neither the model nor which side is the primary, and the labels differ in every pair. Only the key maps a label back to a model, so keep it away from the judge. It may not equal the manifest seed, which is published.

```bash
curl -H "Authorization: Bearer $ROUTER_MANAGEMENT_TOKEN" \
  "$ROUTER_MANAGEMENT_URL/api/v1/observability/replays/dataset/judge-tasks?recipe=vault&seed=2026-q3&split=train:8&split=eval:2&blinding_key=$JUDGE_KEY"
```

A pair is built only when the request and both answers hash back to the digests in the manifest. A stored request names the model the router sent it to, so the input digest covers the request body without its top-level model field (`Model` in the recorded request, `model` in a client body), which is also the `input` a judge reads, and each task carries that `input_digest`. This changed input identity, deduplication and splits, so the manifest is `shadow-dataset.v2` and the tasks are `shadow-judge-tasks.v2`. A `shadow-dataset.v1` manifest is refused. The shadow text comes from the captured excerpt, so the shadow decision needs `capture.enabled` on and a `capture.max_bytes` large enough for every candidate’s share to hold a whole answer. An excerpt that was cut, or a primary answer a response-stage plugin rewrote before it was stored, is left out and counted as `arm_text_digest_mismatch`, and a pair with no text at all as `arm_text_missing`. A request that does not match its digest is counted as `input_text_digest_mismatch`, and a missing one as `input_text_missing`. An answer that names its own model is marked `names_own_model`, since no label can blind a judge to that.

The tasks carry prompt and answer text, so the route needs `replay.detail` and refuses a caller without it rather than redacting the tasks. The judge runs outside the router.

### Publish the dataset and its judgments

`tools/calibration/shadow-dataset` publishes a manifest, and optionally the judgments a judge returned for it, into a storage directory. Any object store mounted as a filesystem works as that directory. The tool validates every input before it writes anything: the manifest has to match its own digest, the tasks have to be the ones the manifest and the blinding key produce from the text they carry, and the judgments have to answer those tasks with no field the judgment record does not define. The key is read for that check and never published.

```bash
make run-shadow-dataset GO_TOOL_ARGS="--dest /mnt/shadow-datasets \
  --manifest $PWD/manifest.json --tasks $PWD/tasks.json --judgments $PWD/judgments.json \
  --blinding-key $JUDGE_KEY"
```

The manifest lands at `manifests/<digest>/<digest of the file>.json`, because the manifest digest leaves out the counts and two exports of the same examples can differ in what they excluded, and the judgment set lands at `judgments/<manifest digest>/<digest of the set>.json` beside a `.report.json` that counts outcomes, slot picks and how many pairs followed the slot rather than the answer. Every name comes from content, so publishing the same files twice changes nothing and a published name is never replaced with different bytes. The judge tasks are read to check the judgments against but never published, because they carry the prompt and both answers. Keep datasets out of this repository.
