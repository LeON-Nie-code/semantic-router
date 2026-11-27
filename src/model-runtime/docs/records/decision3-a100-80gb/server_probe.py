"""Run the unmodified runtime and sample its CUDA allocator counters."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--memory-file', type=Path, required=True)
    args, runtime_args = parser.parse_known_args()
    from vllm_srun.cli import build_parser, choose_spin_count, config_from_args
    config = config_from_args(build_parser().parse_args(runtime_args))
    choose_spin_count(config)
    import torch
    from vllm_srun.api import server
    live = []
    class ObservedRuntime(server.Runtime):
        def __init__(self, *values, **keywords):
            super().__init__(*values, **keywords)
            live.append(self)
    # Keep the runtime's implementation and defaults; retain its instance only
    # to read the existing engine receipt (kernel sources and graph counters).
    server.Runtime = ObservedRuntime
    nvml = ctypes.CDLL('libnvidia-ml.so.1')
    if nvml.nvmlInit_v2() != 0:
        raise RuntimeError('NVML initialization failed')
    handle = ctypes.c_void_p()
    if nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
        raise RuntimeError('NVML GPU handle failed')
    class Memory(ctypes.Structure):
        _fields_ = [('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong),
                    ('used', ctypes.c_ulonglong)]
    peak_device, receipt, receipt_at = 0, {}, 0
    def sample():
        nonlocal peak_device, receipt, receipt_at
        while True:
            try:
                memory = Memory()
                if nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)) != 0:
                    raise RuntimeError('NVML memory query failed')
                peak_device = max(peak_device, memory.used)
                if time.monotonic() - receipt_at > 1 and live:
                    for served in live[0].served:
                        if served.health.ready and served.model:
                            receipt = served.model.engine_model.receipt()
                    receipt_at = time.monotonic()
                value = dict(time_ns=time.time_ns(),
                             device_used_bytes=memory.used,
                             sampled_peak_device_used_bytes=peak_device,
                             device_sampling_interval_seconds=0.1,
                             engine_receipt=receipt)
                if torch.cuda.is_initialized():
                    value.update(allocated_bytes=torch.cuda.memory_allocated(),
                                 reserved_bytes=torch.cuda.memory_reserved(),
                                 peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                                 peak_reserved_bytes=torch.cuda.max_memory_reserved())
                temp = args.memory_file.with_suffix('.tmp')
                temp.write_text(json.dumps(value))
                os.replace(temp, args.memory_file)
            except Exception as exc:
                print('memory sampler:', repr(exc), flush=True)
            time.sleep(0.1)
    threading.Thread(target=sample, daemon=True).start()
    from vllm_srun.cli import main as runtime_main
    raise SystemExit(runtime_main(runtime_args))


if __name__ == '__main__':
    main()
