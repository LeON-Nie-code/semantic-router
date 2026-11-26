# Evaluating the vela3-fast FactCheck adapter

The built-in FactCheck signal asks whether answering a request requires
verifying facts about the world. A request can look like a factual question
even when the facts needed to answer it are already in the prompt. This
experiment checks that boundary on the released `vela3-fast` checkpoint and
three LoRA variants. It is one step in the qualification work tracked in
[#3200](https://github.com/vllm-project/semantic-router/issues/3200).

## Data and label audit

The [Router Signal Suite](https://huggingface.co/datasets/vllm-sr/router-signal-suite)
revision `fa08b2a642df30955ad2ad2206d74050c7f12b5c` gives 5,252 training
rows, 198 development rows and 800 same-source test rows. Each of the five
training sources has equal numbers of both labels. The two classes each have
1,364 requests containing a question mark. Their median lengths are 62 and 63
characters. Shorter text alone gets an AUC of 0.512.

A blinded, assistant-assisted audit sampled ten rows per source and label
(seed 3200). The categories below describe what the auditor understood the
request to require. The sample weights all five sources equally and its labels
have one judgment per row.

| Audit category | Published need | Published no need |
| --- | ---: | ---: |
| External lookup | 20 | 9 |
| Stable factual question | 18 | 4 |
| Provided information suffices | 2 | 2 |
| Needs clarification | 3 | 7 |
| Creative, subjective or other nonfactual task | 4 | 26 |
| Ambiguous | 3 | 2 |

The [dataset builder](https://huggingface.co/datasets/vllm-sr/router-signal-suite/blob/fa08b2a642df30955ad2ad2206d74050c7f12b5c/builder/src_fact.py)
maps some source categories directly to the two labels. The audit surfaced
requests that only ask for arithmetic or a rewrite of supplied material in
the positive class, and requests to summarize a linked page in the negative
class. Stable factual questions still fit the public signal's verification
policy and are kept as a distinct audit category.

The revised training split fixes six concrete cases and replaces 96 negatives
with self-contained English requests from six templates, matching the replaced
rows by character length. The row and class counts stay fixed. The replacements
remove 58 Dolly, 19 OpenQuestionType, nine NF-CATS, nine Search Arena and one
URS row. Of the removed rows, 51 are tagged as non-English and 28 have no
language tag. The data comparison measures the combined effect of the label
fixes, examples and source mix. The training comparison
keeps the cross-entropy objective, seed 42, two epochs and AdamW at 2e-5. It
varies the data, then varies the LoRA initialization with the revised data
held fixed. Shared initialization copies the shared LoRA tensors into the
FactCheck branch. The unchanged shared adapter is a separate diagnostic
comparison with its own readout.

## Evaluation

The release used for all comparisons is
[`vela3-fast@7cbefd4b`](https://huggingface.co/vllm-sr/vela3-fast/tree/7cbefd4b92b57234bd0ffb9872451e81abcdac79).
Scoring uses the built-in question and the native runtime. Scores use a
per-model threshold selected on the 198 development rows at a 20% false
positive cap. No Robots recall at 5% false positives is an additional ranking
diagnostic. No Robots has
[known label shortcuts](https://huggingface.co/datasets/vllm-sr/router-signal-suite#known-limits).

The evaluation also includes 160 fresh MGSM negatives, 160 SimpleQA positives,
16 matched pairs that
replace a supplied fact with irrelevant context, 12 math paraphrase pairs and
eight requests that need clarification. The authored probes were frozen
before scoring and are separate from the training additions. In the pairs,
the supplied facts and surrounding text are fictional test inputs. The screen for
further traffic validation is at least 80% recall on the external-evidence
cases and at most 20% false positives on both MGSM and the evidence-supplied
cases. These are experiment cutoffs. The report also gives Wilson intervals
to show the uncertainty of the small authored slices.

## Results

| Adapter and training data | Same-source AUC | No Robots recall at 5% FPR | MGSM false positives | SimpleQA recall | Native p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Released FactCheck | 0.673 | 79.1% | 136/160 | 114/160 | 24.53 ms |
| Released shared adapter | 0.794 | 85.6% | 34/160 | 160/160 | 25.75 ms |
| FactCheck initialization, original data | 0.922 | 58.1% | 146/160 | 159/160 | 25.08 ms |
| FactCheck initialization, revised data | 0.925 | 37.3% | 136/160 | 160/160 | 24.75 ms |
| Shared initialization, revised data | 0.935 | 33.0% | 84/160 | 160/160 | 25.14 ms |

| Adapter and training data | Recall without evidence | False positives with evidence | Correctly ranked evidence pairs | Both labels correct | Math style flips |
| --- | ---: | ---: | ---: | ---: | ---: |
| Released FactCheck | 0/16 | 0/16 | 0/16 | 0/16 | 10/12 |
| Released shared adapter | 0/16 | 0/16 | 2/16 | 0/16 | 10/12 |
| FactCheck initialization, original data | 11/16 | 16/16 | 2/16 | 0/16 | 8/12 |
| FactCheck initialization, revised data | 11/16 | 16/16 | 3/16 | 0/16 | 7/12 |
| Shared initialization, revised data | 14/16 | 12/16 | 15/16 | 3/16 | 6/12 |

Shared initialization reduced math false positives from 136 to 84 on the
same revised training set and ranked 15 of 16 evidence pairs correctly.
At the development-selected threshold it still flagged 12 of 16 requests
that supplied the necessary evidence (95% Wilson interval 50.5% to 89.8%),
and No Robots recall dropped to 33%.
All three candidates missed the traffic-validation screen. Retain the
released checkpoint. The released shared adapter's zero recall on the
authored evidence-absent requests also weighs against switching to it.

Repeating shared initialization with the same data and seed produced a
different checkpoint. The repeat kept the direction of the result on these
probes. It ranked 15/16 evidence pairs correctly and reached same-source
AUC 0.934. It still exceeded the screen's false-positive limits on MGSM
(66/160) and on prompts with supplied evidence (10/16).

Each exported package differs from the release in
`factcheck_adapter.safetensors`. All three candidates served three typed
`/v1/systemone` requests over the model runtime's local HTTP interface on
CPU. After restoring the released package, all three reference scores matched
their pre-swap values exactly. This was an offline comparison on public and
authored requests. Live Router traffic validation remains a separate step.

| Artifact | FactCheck adapter SHA-256 |
| --- | --- |
| FactCheck initialization, original data | `17c5225b89db384157f5ca4d4bf36d1438b7dcc4746ae185020cc72d75c4fc5e` |
| FactCheck initialization, revised data | `27579034c957ef725859a2dbb79f1e070574d7577897c956687d6ab4ddf974aa` |
| Shared initialization, revised data | `b55bb2db8d3a93e61c7c3be328218486df046372f250e45fa5c7b59809850202` |
| Shared initialization, repeated run | `ac6f13448d24ef46bf77ab8e5bc85464b2a0f8c8f1954489f0e323e2f9d0cb1d` |

## Reproduce

From the repository root, use the model runtime's Python environment with
CUDA PyTorch. The run used PyTorch 2.10.0+cu128 on an RTX 4090 D and the
runtime at `5dad59bda6752a3aeaddb1260f463c0a1e168336`. The tools pin the
released revision above.

```bash
work=$(mktemp -d)
hf download vllm-sr/router-signal-suite --repo-type dataset \
  --revision fa08b2a642df30955ad2ad2206d74050c7f12b5c \
  --include 'text/*/fact_check/*.jsonl' --local-dir "$work/suite"

tools=src/model-runtime/tools
suite="$work/suite/text"
out="$work/run"
python "$tools/factcheck_data.py" --suite "$suite" --out "$out"
python "$tools/factcheck_train.py" --train "$out/stock.jsonl" --out "$out/stock-stock"
python "$tools/factcheck_train.py" --train "$out/revised.jsonl" --out "$out/stock-revised"
python "$tools/factcheck_train.py" --train "$out/revised.jsonl" --init shared --out "$out/shared-revised"

python "$tools/factcheck_eval.py" --suite "$suite" --out "$out/released.metrics.json"
python "$tools/factcheck_eval.py" --suite "$suite" --shared --out "$out/shared.metrics.json"
for arm in stock-stock stock-revised shared-revised; do
  python "$tools/factcheck_eval.py" --suite "$suite" \
    --model "$out/$arm/candidate" --out "$out/$arm.metrics.json"
done
```

Each training receipt records the dataset SHA-256 and exported adapter SHA-256.
The evaluator records the input and adapter digests, development-selected
threshold, counts, AUC, Brier score, pairwise outcomes and single-request
latency for the 800 same-source test inputs. The latency times native model
calls without a Router or network hop.
