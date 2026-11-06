# Raco ModelOpt Cloud Conversion: Failure Matrix

This file records failures observed during the actual GitHub Actions runs and the fixes that worked.

| Symptom | Root cause / interpretation | Working response |
|---|---|---|
| blob:https://chatgpt.com/... | Browser-local Blob URL; not an internet-downloadable URL | Use a stable HTTPS file endpoint accessible to the runner |
| Drive download HTTP 403 | File still restricted or the link depends on a logged-in browser session | Set Drive General access to Anyone with the link / Viewer; use file-ID endpoint |
| curl URL rejected / invalid port | Pasted URL had escaping/markup characters and was parsed as malformed authority/port | Do not paste browser-formatted temporary URLs; construct URL from stable Drive file ID in workflow |
| HTTP 200 but wrong bytes | Google Drive may return an HTML confirmation/error page or wrong file | Verify exact file size and SHA256 before ONNX load |
| Runner shutdown during AutoCast | ReferenceRunner retains a large set of graph intermediate outputs; RSS reached about 15.3 GB | Skip AutoCast activation capture with data_max=inf and calibration_data=None; retain FP32 I/O and explicit op exclusions |
| ONNX Runtime FP8 calibration rejects Resize tensor(double) | Resize optional input dtype invalid for ORT | Build an FP8-only copy; Cast roi/scales to FLOAT and sizes to INT64; run ORT preflight |
| FP8 entropy calibration works but FP8 Max fails at Range | calibrate_per_node=True path hit ONNX Runtime symbolic shape inference AssertionError | Retry the max variant with calibrate_per_node=False; this produced Q=273 and DQ=273 |
| ModelOpt log says NVML Shared Library Not Found | CPU runner has no NVIDIA NVML library; ModelOpt attempted to query GPU count | Non-fatal in the successful CPU retry; confirm the later quantization log and output status |
| Upload step looked green but artifact list was empty | Workflow YAML path contained a literal backslash before the GitHub runner.temp expression | Ensure the YAML contains the actual expression with no leading backslash; confirm the artifact appears through the Actions API |
| Workflow appears queued for a long time | Check run metadata, jobs, and steps; do not assume global GitHub outage | Check stale active runs, concurrency group, job conditions, and whether the job has actually started. Avoid creating many duplicate issue events |
| Many unrelated workflows run on issue creation | Other workflows in the same repo also listen to issues events, then skip at job-level if | Prefer workflow_dispatch or a dedicated event; if using issues, guard each job by a strict title prefix |

## Important distinctions

- A workflow conclusion of success can mean the script and package steps succeeded, but the per-variant manifest must still be inspected.
- The main conversion run had six successful variants and one failed FP8 Max variant. The seventh variant was completed by the isolated retry.
- FP16 AutoCast without activation capture is not equivalent to calibrated FP16 node selection.
- FP8 Q/DQ insertion is not an accuracy guarantee; use the original Raco benchmark.
- Standard ubuntu-latest runner RAM differs by repository visibility: public standard runner 16 GB; private standard runner 8 GB according to GitHub's current runner table. Re-check before reproducing.
