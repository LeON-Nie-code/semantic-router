#!/usr/bin/env python3
"""Fine-tune only the released vela3-fast FactCheck LoRA factors."""

import argparse
import hashlib
import json
import random
import shutil
import time
from pathlib import Path

import torch
from factcheck_data import NEGATIVE, POSITIVE, RELEASE, RELEASE_REVISION
from safetensors import safe_open
from safetensors.torch import save_file
from torch.nn.functional import cross_entropy
from vllm_srun.config import ModelConfig, ServeConfig
from vllm_srun.engines.native.adapters import load_adapter_network, module_name
from vllm_srun.families.vela2.encoder_layout import batch_indices
from vllm_srun.families.vela2.members import INDEX_INPUTS
from vllm_srun.registry.artifacts import sha256_file
from vllm_srun.runtime import Runtime

from router_signal_direct import ask, bundle, has_scan_budget, scores


FACTCHECK = "router:factcheck"
SHARED = "router"


def train(args):
    """Train the task adapter with the stock or shared LoRA tensors as the starting point."""
    torch.manual_seed(42)
    randomizer = random.Random(42)
    training_text = args.train.read_bytes()
    data = [json.loads(line) for line in training_text.splitlines() if line.strip()]
    randomizer.shuffle(data)
    runtime = Runtime(
        ServeConfig(
            models=(
                ModelConfig(model=RELEASE, revision=RELEASE_REVISION, device="cuda:0"),
            )
        )
    )
    runtime.load()
    served = runtime.lookup(None)
    model = served.model
    device = model.engine_model.device
    try:
        questions = bundle(
            "fact_check", own=True, truncate=has_scan_budget(model.info.limits)
        )
        examples = []
        labels = (NEGATIVE, POSITIVE)
        for row in data:
            items = model.plan(row["text"], questions).items
            assert len(items) == 1 and items[0].call.network == FACTCHECK
            examples.append((items[0].sequence, labels.index(row["label"])))
        engine = model.engine_model
        backbone = engine.spec.backbone
        branch = (
            load_adapter_network(engine.backbone, backbone, FACTCHECK).to(device).eval()
        )
        branch.kernels = engine.kernels
        head = model.networks.heads[FACTCHECK].eval()
        branch.requires_grad_(False)
        source = backbone.adapters[FACTCHECK].weight_files[0]
        with safe_open(str(source), framework="pt", device="cpu") as file:
            metadata = file.metadata()
            names = [
                name for name in file.keys() if name.endswith((".lora_A", ".lora_B"))
            ]
        factors = {
            name: branch.get_parameter(
                module_name(name.removeprefix("encoder."))
            ).requires_grad_(True)
            for name in names
        }

        def forward(batch):
            sequences, targets = zip(*batch, strict=True)
            width = max(len(sequence.ids) for sequence in sequences)
            ids = torch.full(
                (len(batch), width), model.networks.pad, device=device, dtype=torch.long
            )
            mask = torch.zeros_like(ids)
            for i, sequence in enumerate(sequences):
                ids[i, : len(sequence.ids)] = torch.tensor(sequence.ids, device=device)
                mask[i, : len(sequence.ids)] = 1
            indices = batch_indices(list(sequences))
            logits, _ = head(
                branch(ids, mask),
                *(torch.from_numpy(indices[k]).to(device) for k in INDEX_INPUTS),
            )
            return logits.reshape(len(batch), 3)[:, :2], torch.tensor(
                targets, device=device
            )

        with torch.no_grad():
            ref = scores(
                "fact_check",
                served.placement.accelerator.execute(
                    device, lambda: ask(model, data[0]["text"], questions)
                ),
            )
            logits, _ = forward(examples[:1])
            local = logits.softmax(-1)[0].tolist()
        error = max(
            abs(ref[label] - value) for label, value in zip(labels, local, strict=True)
        )
        if error >= 2e-4:
            raise ValueError(
                f"training forward differs from the released runtime: {error:.6g}"
            )
        if args.init == "shared":
            shared = backbone.adapters[SHARED].weight_files[0]
            with torch.no_grad(), safe_open(
                str(shared), framework="pt", device="cpu"
            ) as file:
                for name, factor in factors.items():
                    factor.copy_(file.get_tensor(name))
        initial = {name: factor.detach().cpu() for name, factor in factors.items()}
        args.out.mkdir(parents=True, exist_ok=False)
        batches = []
        for _ in range(args.epochs):
            order = list(examples)
            randomizer.shuffle(order)
            blocks = []
            for offset in range(0, len(order), 64):
                block = sorted(
                    order[offset : offset + 64], key=lambda example: len(example[0].ids)
                )
                while block:
                    width = len(block[-1][0].ids)
                    size = 2 if width > 256 else 4 if width > 128 else 8
                    blocks.append(block[-size:])
                    del block[-size:]
            randomizer.shuffle(blocks)
            batches.extend(blocks)
        optimizer = torch.optim.AdamW(factors.values(), lr=2e-5)
        started = time.perf_counter()
        torch.cuda.reset_peak_memory_stats(device)
        for step, batch in enumerate(batches):
            optimizer.zero_grad(set_to_none=True)
            logits, targets = forward(batch)
            loss = cross_entropy(logits, targets)
            if not torch.isfinite(loss):
                raise ValueError(f"nonfinite loss at step {step + 1}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                tuple(factors.values()), 1.0, error_if_nonfinite=True
            )
            optimizer.step()
            if step == 0 or (step + 1) % 100 == 0 or step + 1 == len(batches):
                print(f"step={step + 1} loss={loss.item():.5f}", flush=True)
        torch.cuda.synchronize(device)
        metrics = {
            "steps": len(batches),
            "training_rows": len(examples),
            "epochs": args.epochs,
            "examples_seen": sum(len(batch) for batch in batches),
            "training_seconds": time.perf_counter() - started,
            "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(device),
            "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(device),
            "trainable_parameters": sum(p.numel() for p in factors.values()),
            "initial_probability_max_error": error,
            "source_model": RELEASE,
            "source_revision": model.info.revision,
            "torch": torch.__version__,
            "learning_rate": 2e-5,
            "seed": 42,
            "initialization": args.init,
            "training_sha256": hashlib.sha256(training_text).hexdigest(),
        }
        print(json.dumps(metrics), flush=True)
        candidate = args.out / "candidate"
        candidate.mkdir()
        for name in model.package.files:
            if name != source.name:
                shutil.copyfile(model.package.root / name, candidate / name)
        with safe_open(str(source), framework="pt", device="cpu") as file:
            tensors = {
                name: (
                    factors[name]
                    .detach()
                    .cpu()
                    .to(file.get_tensor(name).dtype)
                    .contiguous()
                    if name in factors
                    else file.get_tensor(name)
                )
                for name in file.keys()
            }
            changed = sum(
                not torch.equal(tensors[name], initial[name].to(tensors[name].dtype))
                for name in factors
            )
        assert changed, "no exported LoRA factor changed"
        save_file(tensors, str(candidate / source.name), metadata=metadata)
        metrics["adapter_sha256"] = sha256_file(candidate / source.name)
        metrics["changed_lora_tensors"] = changed
        (args.out / "training.json").write_text(json.dumps(metrics, indent=2) + "\n")
    finally:
        runtime.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--train", type=Path, required=True, help="FactCheck training rows as JSONL"
    )
    parser.add_argument("--init", choices=("stock", "shared"), default="stock")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=2)
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("--epochs must be positive")
    train(args)
