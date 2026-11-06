# Raco ModelOpt Cloud Run History

All timestamps below are UTC from GitHub Actions metadata.

## Source model

- Filename: raco_aliked_lightglue_pipeline_k3072_folded_noshapes.onnx
- Drive file ID: 1bQvRw5k9-6um6ByCv7NKHKosmnl2KuVQ
- Size: 50,039,973 bytes
- SHA256: 5ac2e8f7b8de8bfd894dd258add9ba885492f34cc255dfa0eeb5026541171d26
- ONNX graph nodes reported by cloud run: 3,051
- Source ONNX opset: 20

## Main conversion

Run: https://github.com/GitrnHub/File-Transfer/actions/runs/42000257687
Run ID: 42000257687
Conclusion: success
Created: 2026-11-05 09:10:52 UTC
Updated: 2026-11-05 10:43:39 UTC
Artifact ID: 13407118719
Artifact name: raco-modelopt-onnx-42000257687
Artifact size: 138,916,742 bytes
Artifact digest: sha256:b00c5ea519f05a94169eb4f48cd341a97e5773703ce29a5ba2679aff4d7b27b5
Artifact expiry at creation: 2026-11-12 10:43 UTC

Environment reported in logs:
- Python 3.12
- PyTorch 2.8.0+cpu
- NVIDIA ModelOpt 0.48.0
- ONNX Runtime 1.30.0
- CPUExecutionProvider explicitly selected
- CUDA unavailable in PyTorch

Variant outcomes in the main run:

| Variant | Result | Build time |
|---|---|---:|
| fp16_autocast_balanced | SUCCESS | 78.0 s |
| fp16_autocast_conservative | SUCCESS | 72.5 s |
| fp16_autocast_aggressive | SUCCESS | 80.6 s |
| fp8_fp16_all_entropy_mha16_512 | SUCCESS | 862.8 s |
| fp8_fp16_all_max_mha16_512 | FAILED: AssertionError | 11.1 s |
| fp8_fp16_matmul_gemm_entropy_mha32_512 | SUCCESS | 872.5 s |
| fp8_fp16_conv_entropy_mha16_512 | SUCCESS | 843.0 s |

The main workflow uploaded the six successful ONNX variants and their manifest/logs.

## FP8 Max retry

Run: https://github.com/GitrnHub/File-Transfer/actions/runs/42176268060
Run ID: 42176268060
Conclusion: success
Created: 2026-11-06 06:43:39 UTC
Updated: 2026-11-06 06:47:19 UTC
Artifact ID: 13483676226
Artifact name: raco-fp8-max-retry
Artifact size: 69,421,595 bytes
Artifact digest: sha256:a5045d9a20de9ae2b8d45b8471a4fdbac49f79069a9629b9e8e18379dd2db334
Artifact expiry at creation: 2026-11-13 06:47 UTC

Successful retry settings:
- One medium-difficulty synthetic pair
- 512x512
- CPUExecutionProvider
- calibrate_per_node=False
- FP8 max calibration
- Output: fp8_fp16_all_max_mha16_512.onnx
- QuantizeLinear nodes: 273
- DequantizeLinear nodes: 273

## Final inventory

Total successful variants: 7
- 3 FP16
- 4 FP8 + FP16

No accuracy or TensorRT engine performance result is claimed by these conversion runs.
