# Raco ALIKED + LightGlue ModelOpt Cloud Conversion Runbook

Version: 2026-11-06
Repository: https://github.com/GitrnHub/File-Transfer
Scope: ONNX precision conversion only. All conversion steps run on GitHub-hosted Actions; no local runner or local conversion command is required.

## 1. Verified result

Seven ONNX variants were successfully produced on GitHub-hosted Ubuntu CPU runners:
- 3 FP16 AutoCast variants
- 4 FP8 + FP16 Q/DQ variants

Main conversion: https://github.com/GitrnHub/File-Transfer/actions/runs/42000257687
FP8 Max retry: https://github.com/GitrnHub/File-Transfer/actions/runs/42176268060

The FP8 Max retry reported 273 QuantizeLinear and 273 DequantizeLinear nodes.

These are conversion successes, not accuracy certification. All seven outputs still require the original 24-pair Raco matching benchmark and TensorRT 11.4 validation before deployment.

## 2. Source model identity and cloud download

Source filename: raco_aliked_lightglue_pipeline_k3072_folded_noshapes.onnx

Google Drive file ID: 1bQvRw5k9-6um6ByCv7NKHKosmnl2KuVQ

Verified size: 50,039,973 bytes

Verified SHA256:
5ac2e8f7b8de8bfd894dd258add9ba885492f34cc255dfa0eeb5026541171d26

The workflow uses a stable Drive file-ID download endpoint:
https://drive.usercontent.google.com/download?id=FILE_ID&export=download&confirm=t

The Drive file must be shared as Anyone with the link / Viewer for anonymous GitHub-hosted runners. A browser-local blob URL, a ChatGPT Library URL, or a temporary browser URL containing session-only parameters is not a cloud-download URL.

Do not trust HTTP 200 or a filename alone. Verify exact byte size and SHA256 before loading ONNX.

The original ONNX is downloaded into the ephemeral runner filesystem. It is not committed to Git and is not included in the generated model package.

## 3. Cloud-only runner design

The successful jobs used:
- runs-on: ubuntu-latest
- Python 3.12
- CPU-only PyTorch
- ONNX Runtime CPUExecutionProvider
- NVIDIA ModelOpt 0.48.0

No NVIDIA GPU, CUDA toolkit, local conda environment, Visual Studio, or TensorRT installation is required for this ONNX conversion stage.

Important GitHub runner limit:
- Standard GitHub-hosted ubuntu-latest for a public repository: 4 vCPU, 16 GB RAM, 14 GB SSD.
- Standard GitHub-hosted ubuntu-latest for a private repository: 2 vCPU, 8 GB RAM, 14 GB SSD.
- ubuntu-slim is much smaller (1 CPU, 5 GB RAM) and must not be used for this workload.

Keep the repository public if relying on the 16 GB public standard runner specification. Re-check GitHub's current runner table before reproducing.

The workflow limits CPU thread pools to one thread (OMP_NUM_THREADS, MKL_NUM_THREADS, OPENBLAS_NUM_THREADS, ORT_NUM_THREADS) and sets MALLOC_ARENA_MAX=1 to reduce memory fragmentation.

## 4. Pinned Python environment

The successful run used:
- Python 3.12
- torch 2.8.0+cpu
- nvidia-modelopt[onnx]==0.48.0
- ONNX Runtime 1.30.0
- opencv-python-headless
- ONNX and ModelOpt dependencies installed by the pinned ModelOpt extra

Install commands used by Actions:

python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.8.0

python -m pip install nvidia-modelopt[onnx]==0.48.0 opencv-python-headless

Then run python -m pip check and import-test ModelOpt AutoCast, ModelOpt quantization, ONNX, ONNX Runtime, and OpenCV.

The CPU runtime was explicitly checked with torch.cuda.is_available() == False and CPUExecutionProvider available. The run's ONNX Runtime build listed TensorRT, CUDA, and CPU providers, but the calibration calls explicitly selected cpu. The presence of CUDA/TensorRT provider names does not mean a GPU was used.

A ModelOpt log line "Failed to get GPU count: NVML Shared Library Not Found" was non-fatal in the CPU FP8 retry; quantization continued and succeeded.

## 5. Calibration data

No real image dataset was available in the cloud job, so scripts/generate_raco_synthetic_pairs.py creates deterministic synthetic image pairs.

The generator creates textured scenes, geometric lines and shapes, text, and known perspective warps. It writes images, homographies, and pairs.csv.

The workflow generated three 512x512 pairs with easy, medium, and hard difficulty labels. It reordered them so the medium pair was first. The converter intentionally used only one pair to keep the CPU job within memory limits.

The effective calibration tensor shape was:
images:2x3x512x512

This is a smoke/representative calibration set, not a substitute for real Raco application images. Treat FP8 scales as provisional until accuracy is measured on real matching pairs.

## 6. FP16 AutoCast: memory workaround

### What failed

The first CPU attempts used ModelOpt AutoCast reference calibration. ModelOpt's ReferenceRunner marks many/all graph intermediates as outputs to collect activation magnitudes. For this graph, even a single 512x512 image pair pushed the process to approximately 15.3 GB RSS on the 16 GB runner. The runner then received a shutdown signal.

Reducing the number of image pairs did not solve the peak memory problem. The expensive object was the retained intermediate graph output set, not the image tensor itself.

### What worked

The successful converter skipped ReferenceRunner activation capture:
- data_max=float("inf")
- calibration_data=None
- keep_io_types=True
- init_max=65504.0
- source opset preserved
- explicit operator exclusions used for balanced/conservative variants

This allowed the three FP16 variants to build in CPU memory.

Important accuracy caveat: these FP16 files are not activation-calibrated to representative real Raco inputs. The conversion used initializer-range / explicit operator-rule selection without collecting real intermediate activations. Do not describe them as accuracy-preserving until the 24-pair benchmark confirms it.

### FP16 operator policies

Balanced excludes:
ReduceL2, Softmax, LayerNormalization, Div, Sqrt

Conservative excludes the balanced list plus:
Exp, Log, Sigmoid, Tanh, Pow, Reciprocal, Gather

Aggressive excludes no operator types in this custom policy.

All three keep model I/O types unchanged (keep_io_types=True).

## 7. FP8 + FP16 Q/DQ conversion

The FP8 path uses ModelOpt ONNX quantization with:
- quantize_mode="fp8"
- calibration_method="entropy" or "max"
- calibration_data_reader=CalibrationDataProvider(...)
- calibration_shapes="images:2x3x512x512"
- calibration_eps=["cpu"]
- op_types_to_quantize chosen per variant
- high_precision_dtype="fp16"
- direct_io_types=False
- source opset preserved (source opset was 20)
- use_external_data_format=False

### Resize type compatibility fix

The first FP8 attempts failed because ONNX Runtime rejected a Resize input typed tensor(double).

The converter now makes a separate FP8-only copy of the source model and inserts explicit Cast nodes for Resize optional inputs:
- roi -> FLOAT
- scales -> FLOAT
- sizes -> INT64

The original source model is left unchanged. The FP8-only copy is loaded in an ONNX Runtime CPU preflight session before quantization.

### Three per-node FP8 variants that succeeded

1. All supported operators, entropy, FP16 MHA accumulation:
   Conv, Gemm, MatMul
   Output: fp8_fp16_all_entropy_mha16_512.onnx

2. MatMul/Gemm only, entropy, FP32 MHA accumulation:
   MatMul, Gemm
   Output: fp8_fp16_matmul_gemm_entropy_mha32_512.onnx

3. Conv only, entropy, FP16 MHA accumulation:
   Conv
   Output: fp8_fp16_conv_entropy_mha16_512.onnx

These ran with calibrate_per_node=True, which ModelOpt documents as a lower-memory, longer-running calibration option.

### FP8 Max retry

The first all_max run with calibrate_per_node=True failed inside ONNX Runtime symbolic shape inference at a Range node with an AssertionError.

The isolated retry changed the calibration mode to calibrate_per_node=False, retained one 512x512 medium pair and CPUExecutionProvider, and succeeded:
- output: fp8_fp16_all_max_mha16_512.onnx
- QuantizeLinear nodes: 273
- DequantizeLinear nodes: 273
- run: https://github.com/GitrnHub/File-Transfer/actions/runs/42176268060

Do not assume calibrate_per_node=False is always lower-memory. It worked for this one-pair max retry; measure memory if changing calibration size or graph.

## 8. Seven verified output variants

FP16:
- fp16_autocast_balanced.onnx
- fp16_autocast_conservative.onnx
- fp16_autocast_aggressive.onnx

FP8 + FP16:
- fp8_fp16_all_entropy_mha16_512.onnx
- fp8_fp16_matmul_gemm_entropy_mha32_512.onnx
- fp8_fp16_conv_entropy_mha16_512.onnx
- fp8_fp16_all_max_mha16_512.onnx

The first six were packaged by run 42000257687; the FP8 Max model was produced by run 42176268060.

## 9. Repeat procedure

1. Put the source ONNX in a Drive location accessible by link (Viewer). Record its file ID, exact size, and SHA256.
2. Update MODEL_ID, EXPECTED_MODEL_SIZE, and EXPECTED_MODEL_SHA256 in the workflow. Do not put the ONNX itself in Git.
3. Check that the repository is public if the workflow expects the 16 GB standard public runner.
4. Keep runs-on: ubuntu-latest, CPU-only PyTorch, Python 3.12, and pinned ModelOpt 0.48.0 for a like-for-like reproduction.
5. Keep the one-pair 512x512 calibration cap for the large ALIKED+LightGlue graph. Do not restore 1024 or multiple reference batches without a memory test.
6. Run the main workflow. The current repository uses an issue-title prefix [run-modelopt] as an issue-event trigger; workflow_dispatch is also enabled. Prefer a dedicated workflow dispatch in future automation if available.
7. Run the isolated FP8 Max retry using [run-modelopt-max] if needed.
8. Check the job steps and manifest; do not treat a green workflow alone as proof of all variants. Check each variant status and Q/DQ counts.
9. Download the Actions artifacts before their retention period expires. The original output artifacts used 7-day retention.
10. Run the 24-pair Raco matching benchmark and TensorRT 11.4 parser/build tests before using any variant.

## 10. What this workflow does not do

- It does not compile TensorRT .engine files.
- It does not use an NVIDIA GPU on the standard GitHub-hosted CPU runner.
- It does not prove FP16/FP8 accuracy.
- It does not benchmark latency or GPU memory.
- It does not replace the user's original 24-pair accuracy baseline.

TensorRT engine compilation and RTX 5070 performance measurement are a separate validation stage and require an environment with the appropriate TensorRT runtime and GPU access.

## 11. Official references

- GitHub-hosted runner specifications: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- Choosing a runner: https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job
- GitHub workflow syntax: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- NVIDIA ModelOpt 0.48.0 quantization source: https://github.com/NVIDIA/Model-Optimizer/blob/0.48.0/modelopt/onnx/quantization/quantize.py
- NVIDIA ModelOpt AutoCast guide: https://github.com/NVIDIA/Model-Optimizer/blob/0.48.0/docs/source/guides/8_autocast.rst
- ModelOpt ONNX quantization guide: https://github.com/NVIDIA/Model-Optimizer/tree/0.48.0/examples/onnx_ptq
