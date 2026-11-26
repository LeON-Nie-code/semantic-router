#!/usr/bin/env python3
"""Evaluate a Vela FactCheck checkpoint with the question the Router sends."""

import argparse
import hashlib
import json
import math
import time
from collections import defaultdict
from itertools import groupby
from pathlib import Path

import numpy as np
from factcheck_data import (
    NEGATIVE,
    POSITIVE,
    RELEASE,
    RELEASE_REVISION,
    controlled_rows,
    suite_rows,
)
from router_signal_ab import auc
from router_signal_direct import ask, bundle, has_scan_budget, scores

from vllm_srun.config import ModelConfig, ServeConfig
from vllm_srun.registry.artifacts import sha256_file
from vllm_srun.runtime import Runtime

SPLITS = ("dev", "test", "hold-no_robots", "hold-simpleqa", "fresh-mgsm")


def threshold_at_fpr(values, max_fpr):
    """Most permissive threshold under the cap, keeping tied scores together."""
    allowed = math.floor(max_fpr * sum(label == NEGATIVE for _, label in values))
    threshold = math.nextafter(max(score for score, _ in values), math.inf)
    used = 0
    for score, tied in groupby(sorted(values, reverse=True), key=lambda row: row[0]):
        used += sum(label == NEGATIVE for _, label in tied)
        if used > allowed:
            break
        threshold = score
    return threshold


def fraction(hits, total):
    """Report the numerator, denominator and Wilson 95% interval."""
    if not total:
        return None
    p, z = hits / total, 1.96
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = (
        z
        * math.sqrt(p * (1 - p) / total + z * z / (4 * total**2))
        / (1 + z * z / total)
    )
    return {
        "hits": hits,
        "total": total,
        "rate": p,
        "ci95": [max(0, center - half), min(1, center + half)],
    }


def rates(values, threshold):
    return {
        name: fraction(
            sum(score >= threshold for score, label in values if label == which),
            sum(label == which for _, label in values),
        )
        for name, which in (("fpr", NEGATIVE), ("recall", POSITIVE))
    }


def summarize(values, threshold):
    predictions = np.array([score for score, _ in values])
    truth = np.array([label == POSITIVE for _, label in values])
    return {
        "auc": auc(predictions, truth) if truth.any() and not truth.all() else None,
        "brier": float(np.mean((predictions - truth) ** 2)),
        **rates(values, threshold),
    }


def paired_metrics(groups, threshold):
    evidence = groups["evidence"]
    arithmetic = groups["arithmetic"]
    return {
        "evidence_ranking": fraction(
            sum(pos > neg for neg, pos in evidence.values()), len(evidence)
        ),
        "evidence_both_correct": fraction(
            sum(neg < threshold <= pos for neg, pos in evidence.values()), len(evidence)
        ),
        "arithmetic_style_flip": fraction(
            sum(
                (story >= threshold) != (formula >= threshold)
                for story, formula in arithmetic.values()
            ),
            len(arithmetic),
        ),
    }


def evaluate_probes(predict, threshold):
    rows = list(controlled_rows())
    controlled = defaultdict(list)
    pairs = defaultdict(lambda: defaultdict(list))
    for kind, pair, label, text in rows:
        score = predict(text)
        controlled[kind].append((score, label))
        pairs[kind][pair].append(score)
    return {
        "controlled": {
            kind: summarize(values, threshold) for kind, values in controlled.items()
        },
        "pairs": paired_metrics(pairs, threshold),
        "controlled_sha256": row_digest(rows),
    }


def row_digest(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def evaluate(args):
    revision = RELEASE_REVISION if args.model == RELEASE else None
    runtime = Runtime(
        ServeConfig(
            models=(
                ModelConfig(model=args.model, revision=revision, device=args.device),
            )
        )
    )
    runtime.load()
    try:
        served = runtime.lookup(None)
        model, placement = served.model, served.placement
        questions = bundle(
            "fact_check", own=True, truncate=has_scan_budget(model.info.limits)
        )
        if args.shared:
            model.router.roster.router_tasks.pop("factcheck")

        def predict(text):
            response = placement.accelerator.execute(
                placement.device, lambda: ask(model, text, questions)
            )
            probability = scores("fact_check", response)[POSITIVE]
            if not 0 <= probability <= 1:
                raise ValueError(f"invalid FactCheck probability: {probability}")
            return probability

        by_split = {}
        inputs = {}
        latencies = []
        for split in SPLITS:
            rows = suite_rows(args.suite, split)
            if not rows:
                raise ValueError(f"no rows for {split}")
            if split in ("fresh-mgsm", "hold-simpleqa"):
                rows = sorted(
                    rows, key=lambda row: hashlib.sha256(row["id"].encode()).digest()
                )[:160]
            by_split[split] = []
            inputs[split] = row_digest(rows)
            for row in rows:
                start = time.perf_counter()
                score = predict(row["text"])
                if split == "test":
                    latencies.append((time.perf_counter() - start) * 1000)
                by_split[split].append((score, row["label"]))
            print(f"{split}: {len(rows)}", flush=True)

        threshold = threshold_at_fpr(by_split["dev"], 0.20)
        public = {
            name: summarize(values, threshold) for name, values in by_split.items()
        }
        low_fpr = threshold_at_fpr(by_split["hold-no_robots"], 0.05)
        report = {
            "model_revision": model.info.revision,
            "adapter_sha256": sha256_file(
                model.package.root
                / (
                    "router_adapter.safetensors"
                    if args.shared
                    else "factcheck_adapter.safetensors"
                )
            ),
            "shared_diagnostic": args.shared,
            "evaluation_inputs_sha256": inputs,
            "dev_threshold": threshold,
            "published_threshold": {
                split: rates(by_split[split], 0.9999)
                for split in ("test", "fresh-mgsm")
            },
            "public": public,
            "no_robots_at_5pct_fpr": rates(by_split["hold-no_robots"], low_fpr),
            **evaluate_probes(predict, threshold),
            "test_latency_ms": {
                "p50": float(np.percentile(latencies, 50)),
                "p95": float(np.percentile(latencies, 95)),
            },
        }
        args.out.write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    finally:
        runtime.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--model", default=RELEASE)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--shared",
        action="store_true",
        help="use the released shared adapter for diagnosis",
    )
    parser.add_argument("--out", type=Path, required=True)
    evaluate(parser.parse_args())


if __name__ == "__main__":
    main()
