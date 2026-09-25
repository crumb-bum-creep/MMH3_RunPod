# Known-Good Pod and Memory Findings

Captured 2026-08-27 from a functioning MMH3 RunPod.

## Known-good runtime

- GPU: NVIDIA RTX PRO 6000 Blackwell Server Edition
- VRAM reported by NVIDIA: 97,887 MiB
- Driver: 595.91.07
- Driver CUDA compatibility: 13.2
- CUDA toolkit: 13.0 (13.0.88)
- Python: 3.12.3
- PyTorch: 2.11.0+cu130
- torchvision: 0.26.0+cu130
- torchaudio: 2.11.0+cu130
- Triton: 3.6.0
- SageAttention: 2.2.0
- ComfyUI: 0.32.0
- ComfyUI commit: c2bcbecd82ec...
- Container cgroup memory limit: 187,999,997,952 bytes (~175.09 GiB)
- Swap: none

Observed Comfy launch flags:

```text
--listen
--enable-cors-header *
--use-sage-attention
--extra-model-paths-config /ComfyUI/extra_model_paths.yaml
--disable-dynamic-vram
```

> This is a **historical capture**, not the vNext default. Later real-pod sessions showed inconsistent host-RAM retention with this launch mode. vNext defaults to normal dynamic-VRAM behavior and exposes `Disable dynamic VRAM` as an explicit System setting for controlled A/B testing.

## Telemetry workloads

| Run | Workload | Cold/Warm | Peak VRAM | Start RAM | Peak RAM | End RAM |
|---|---|---|---:|---:|---:|---:|
| 1 | New R2V, 15 s, 0.7 MP | cold | 66,986 MiB | 0.99 GiB | 60.01 GiB | 59.65 GiB |
| 2 | Original R2V, 15 s, 0.8 MP | warm | 67,251 MiB | 63.48 GiB | 67.70 GiB | 67.30 GiB |
| 3 | New R2V, 15 s, 0.7 MP | warm | 66,442 MiB | 67.14 GiB | 70.65 GiB | 70.29 GiB |
| 4 | T2V, 10 s, 0.5 MP | warm / first FL2V family load | 80,117 MiB | 70.29 GiB | 93.93 GiB | 93.70 GiB |
| 5 | Old I2V, 15 s, 0.7 MP | warm | 76,347 MiB | 93.72 GiB | 116.77 GiB | 103.35 GiB |
| 6 | Old T2V, 15 s, 0.7 MP | warm | 75,197 MiB | 105.70 GiB | 107.30 GiB | 106.93 GiB |

## Key observation

Memory usage is not simply a transient generation spike.

The first cold R2V run leaves roughly 60 GiB of container memory resident. Repeated R2V runs increase the steady-state baseline only moderately. When a T2V/FL2V workload is introduced, the steady-state container memory jumps by roughly another 23 GiB, and subsequent workflow-family changes can push the peak substantially higher.

This strongly suggests that model/cache residency across workflow families is a major contributor to the user's historical RAM pressure. The adaptive MMH3 runtime should therefore reason about both:

1. transient peak requirements for the next job, and
2. accumulated resident state from previously loaded model families.

## Design implications

- Never derive usable RAM from host `free -h`; use the cgroup limit.
- Treat R2V and FL2V/I2V model-family residency separately.
- Track steady-state RAM and VRAM before accepting another high-memory job.
- Use warning/critical thresholds relative to the detected cgroup limit.
- Provide an intentional model/cache release operation between families when needed.
- The watchdog should avoid killing the whole Pod when ComfyUI alone can be recovered.
- Hardware profiles should be capability-based, not tied to exact system-RAM sizes.
