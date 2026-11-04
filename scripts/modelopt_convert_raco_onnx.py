# -*- coding: utf-8 -*-
"""Create FP16 mixed-precision and FP8+FP16 ONNX variants for Raco ALIKED+LightGlue.

Runs on a standard GitHub-hosted Linux CPU runner. CPUExecutionProvider performs
FP16 analysis and FP8 calibration; no NVIDIA GPU is required. The source ONNX and
calibration images are never copied into Git or the output ZIP.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
import time
import traceback
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnx
import onnxruntime as ort
import torch
from modelopt.onnx.autocast import convert_to_mixed_precision
from modelopt.onnx.quantization import quantize as modelopt_quantize
from modelopt.onnx.quantization.calib_utils import CalibrationDataProvider

SUPPORTED_FP8_OPS = ["Conv", "Gemm", "MatMul"]
SENSITIVE_FP16_OPS = ["ReduceL2", "Softmax", "LayerNormalization", "Div", "Sqrt"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_float_triplet(value: str, name: str) -> tuple[float, float, float] | None:
    value = (value or "").strip()
    if not value:
        return None
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 3:
        raise ValueError(f"{name} must contain three comma-separated numbers")
    return tuple(float(p) for p in parts)


def read_pairs(csv_path: Path, max_pairs: int) -> list[dict[str, Any]]:
    if not csv_path.is_file():
        raise FileNotFoundError(f"Calibration pairs CSV not found: {csv_path}")
    rows: list[dict[str, Any]] = []
    base = csv_path.parent
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"image0", "image1"}.issubset(reader.fieldnames):
            raise ValueError("pairs.csv must contain image0 and image1 columns")
        for index, row in enumerate(reader):
            p0 = Path((row.get("image0") or "").strip())
            p1 = Path((row.get("image1") or "").strip())
            if not p0.is_absolute():
                p0 = (base / p0).resolve()
            if not p1.is_absolute():
                p1 = (base / p1).resolve()
            if not p0.is_file() or not p1.is_file():
                raise FileNotFoundError(
                    f"Pair row {index + 2} has a missing image: image0={p0}; image1={p1}"
                )
            rows.append({
                "pair_id": (row.get("pair_id") or f"pair_{index + 1:04d}").strip(),
                "image0": p0,
                "image1": p1,
            })
            if max_pairs > 0 and len(rows) >= max_pairs:
                break
    if not rows:
        raise ValueError("pairs.csv has no usable image pairs")
    return rows


def preprocess_image(
    path: Path,
    size: int,
    color_order: str,
    pixel_divisor: float,
    mean: tuple[float, float, float] | None,
    std: tuple[float, float, float] | None,
) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"OpenCV could not read image: {path}")
    if color_order == "RGB":
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = cv2.resize(image, (size, size), interpolation=cv2.INTER_LINEAR)
    x = image.astype(np.float32) / float(pixel_divisor)
    if mean is not None:
        x -= np.asarray(mean, dtype=np.float32).reshape(1, 1, 3)
    if std is not None:
        std_array = np.asarray(std, dtype=np.float32).reshape(1, 1, 3)
        if np.any(std_array == 0):
            raise ValueError("std values must be non-zero")
        x /= std_array
    return np.ascontiguousarray(np.transpose(x, (2, 0, 1)), dtype=np.float32)


def make_calibration_array(
    pairs: list[dict[str, Any]],
    size: int,
    color_order: str,
    pixel_divisor: float,
    mean: tuple[float, float, float] | None,
    std: tuple[float, float, float] | None,
) -> np.ndarray:
    batches = []
    for pair in pairs:
        image0 = preprocess_image(pair["image0"], size, color_order, pixel_divisor, mean, std)
        image1 = preprocess_image(pair["image1"], size, color_order, pixel_divisor, mean, std)
        # One ONNX inference is one pair: [2, 3, H, W].
        batches.append(np.stack([image0, image1], axis=0))
    return np.ascontiguousarray(np.concatenate(batches, axis=0), dtype=np.float32)


def write_autocast_calibration_dir(
    pairs: list[dict[str, Any]],
    sizes: list[int],
    destination: Path,
    color_order: str,
    pixel_divisor: float,
    mean: tuple[float, float, float] | None,
    std: tuple[float, float, float] | None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for size in sizes:
        for pair in pairs:
            image0 = preprocess_image(pair["image0"], size, color_order, pixel_divisor, mean, std)
            image1 = preprocess_image(pair["image1"], size, color_order, pixel_divisor, mean, std)
            batch = np.stack([image0, image1], axis=0).astype(np.float32, copy=False)
            safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in pair["pair_id"])
            np.savez_compressed(destination / f"{safe_id}_{size}.npz", images=batch)


def inspect_model(path: Path) -> dict[str, Any]:
    model = onnx.load(str(path), load_external_data=True)
    onnx.checker.check_model(model)
    input_info = []
    for value in model.graph.input:
        shape = []
        for dim in value.type.tensor_type.shape.dim:
            shape.append(dim.dim_param if dim.dim_param else dim.dim_value)
        input_info.append({"name": value.name, "shape": shape, "elem_type": value.type.tensor_type.elem_type})
    outputs = []
    for value in model.graph.output:
        shape = []
        for dim in value.type.tensor_type.shape.dim:
            shape.append(dim.dim_param if dim.dim_param else dim.dim_value)
        outputs.append({"name": value.name, "shape": shape, "elem_type": value.type.tensor_type.elem_type})
    opset = max((x.version for x in model.opset_import if x.domain in ("", "ai.onnx")), default=0)
    return {
        "opset": opset,
        "node_count": len(model.graph.node),
        "initializer_count": len(model.graph.initializer),
        "input": input_info,
        "outputs": outputs,
    }


def inspect_variant(path: Path) -> dict[str, Any]:
    model = onnx.load(str(path), load_external_data=True)
    onnx.checker.check_model(model)
    fp16_initializers = sum(1 for x in model.graph.initializer if x.data_type == onnx.TensorProto.FLOAT16)
    fp8_types = {
        getattr(onnx.TensorProto, "FLOAT8E4M3FN", -100),
        getattr(onnx.TensorProto, "FLOAT8E4M3FNUZ", -101),
        getattr(onnx.TensorProto, "FLOAT8E5M2", -102),
        getattr(onnx.TensorProto, "FLOAT8E5M2FNUZ", -103),
    }
    fp8_initializers = sum(1 for x in model.graph.initializer if x.data_type in fp8_types)
    q_nodes = sum(1 for n in model.graph.node if n.op_type == "QuantizeLinear")
    dq_nodes = sum(1 for n in model.graph.node if n.op_type == "DequantizeLinear")
    opset = max((x.version for x in model.opset_import if x.domain in ("", "ai.onnx")), default=0)
    return {
        "file_size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "opset": opset,
        "node_count": len(model.graph.node),
        "initializer_count": len(model.graph.initializer),
        "fp16_initializer_count": fp16_initializers,
        "fp8_initializer_count": fp8_initializers,
        "quantize_linear_nodes": q_nodes,
        "dequantize_linear_nodes": dq_nodes,
    }


def run_variant(
    name: str,
    kind: str,
    output_path: Path,
    log_path: Path,
    convert_fn,
    manifest_rows: list[dict[str, Any]],
) -> None:
    started = time.perf_counter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    status = "FAILED"
    detail: dict[str, Any] = {}
    error = ""
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        log_file.write(f"Variant: {name}\nKind: {kind}\nOutput: {output_path}\n\n")
        log_file.flush()
        try:
            with redirect_stdout(log_file), redirect_stderr(log_file):
                convert_fn()
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise RuntimeError("Conversion returned without producing a non-empty ONNX file")
            detail = inspect_variant(output_path)
            if kind == "fp8" and (detail["quantize_linear_nodes"] == 0 or detail["dequantize_linear_nodes"] == 0):
                raise RuntimeError("FP8 output contains no Q/DQ nodes; no FP8 quantization was applied")
            status = "SUCCESS"
        except Exception as exc:
            error = repr(exc)
            traceback.print_exc(file=log_file)
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
    elapsed = round(time.perf_counter() - started, 2)
    row = {
        "name": name,
        "kind": kind,
        "status": status,
        "build_seconds": elapsed,
        "output_file": output_path.name if status == "SUCCESS" else "",
        "error": error,
        **detail,
    }
    manifest_rows.append(row)
    print(f"[{status}] {name} ({elapsed:.1f}s)")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--calibration-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-pairs", type=int, default=24)
    parser.add_argument("--resolutions", default="512,1024")
    parser.add_argument("--calibration-ep", choices=["cpu"], default="cpu")
    parser.add_argument("--color-order", choices=["RGB", "BGR"], default="RGB")
    parser.add_argument("--pixel-divisor", type=float, default=255.0)
    parser.add_argument("--mean", default="")
    parser.add_argument("--std", default="")
    args = parser.parse_args()

    model_path = args.model.resolve()
    calibration_root = args.calibration_root.resolve()
    output_root = args.output_root.resolve()
    pairs_csv = calibration_root / "pairs.csv"
    if not model_path.is_file():
        raise FileNotFoundError(f"Source ONNX not found: {model_path}")
    if args.pixel_divisor <= 0:
        raise ValueError("pixel-divisor must be > 0")
    sizes = sorted({int(x.strip()) for x in args.resolutions.split(",") if x.strip()})
    if not sizes or any(x < 32 or x % 32 != 0 for x in sizes):
        raise ValueError("resolutions must be comma-separated positive multiples of 32, e.g. 512,1024")
    mean = parse_float_triplet(args.mean, "mean")
    std = parse_float_triplet(args.std, "std")
    if std is not None and any(x == 0 for x in std):
        raise ValueError("std values must be non-zero")

    if "CPUExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("ONNX Runtime CPUExecutionProvider is unavailable.")

    model_meta = inspect_model(model_path)
    if len(model_meta["input"]) != 1 or model_meta["input"][0]["name"] != "images":
        raise RuntimeError(f"Expected one ONNX input named 'images'; got {model_meta['input']}")
    pairs = read_pairs(pairs_csv, args.max_pairs)
    if len(pairs) < 1:
        raise ValueError("At least one calibration image pair is required")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"run_{timestamp}"
    onnx_dir = run_dir / "onnx"
    log_dir = run_dir / "logs"
    onnx_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        modelopt_version = importlib.metadata.version("nvidia-modelopt")
    except importlib.metadata.PackageNotFoundError:
        modelopt_version = "unknown"

    print("Raco ModelOpt ONNX conversion")
    print("Source:", model_path)
    print("Source SHA256:", sha256_file(model_path))
    print("ModelOpt:", modelopt_version)
    print("Python:", sys.version.split()[0])
    print("PyTorch:", torch.__version__, "CUDA:", torch.version.cuda)
    print("Execution device: GitHub-hosted CPU runner")
    print("ONNX Runtime:", ort.__version__, "providers:", ort.get_available_providers())
    print("Calibration pairs:", len(pairs), "sizes:", sizes, "EP:", args.calibration_ep)
    print("Preprocessing:", args.color_order, "pixel_divisor:", args.pixel_divisor, "mean:", mean, "std:", std)

    manifest_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="raco_modelopt_calib_") as temp_name:
        temp_dir = Path(temp_name)
        autocast_calib_dir = temp_dir / "autocast_calibration"
        write_autocast_calibration_dir(
            pairs, sizes, autocast_calib_dir, args.color_order, args.pixel_divisor, mean, std
        )

        # FP16 AutoCast variants: calibrated node classification, all keep FP32 model I/O.
        fp16_variants = [
            ("fp16_autocast_balanced", 512.0, None),
            ("fp16_autocast_conservative", 512.0, SENSITIVE_FP16_OPS),
            ("fp16_autocast_aggressive", 2048.0, None),
        ]
        for name, data_max, excluded_ops in fp16_variants:
            output_path = onnx_dir / f"{name}.onnx"
            log_path = log_dir / f"{name}.log"

            def convert_fp16(out=output_path, dmax=data_max, excluded=excluded_ops):
                converted = convert_to_mixed_precision(
                    onnx_path=str(model_path),
                    low_precision_type="fp16",
                    nodes_to_exclude=None,
                    op_types_to_exclude=excluded,
                    data_max=dmax,
                    init_max=65504.0,
                    keep_io_types=True,
                    calibration_data=str(autocast_calib_dir),
                    providers=[args.calibration_ep],
                    opset=model_meta["opset"],
                )
                onnx.save(converted, str(out))

            run_variant(name, "fp16", output_path, log_path, convert_fp16, manifest_rows)

        # FP8 variants are calibrated separately at each input size.
        fp8_configs = [
            ("all_entropy_mha16", "entropy", SUPPORTED_FP8_OPS, "fp16"),
            ("all_max_mha16", "max", SUPPORTED_FP8_OPS, "fp16"),
            ("matmul_gemm_entropy_mha32", "entropy", ["MatMul", "Gemm"], "fp32"),
            ("conv_entropy_mha16", "entropy", ["Conv"], "fp16"),
        ]
        for size in sizes:
            calibration_array = make_calibration_array(
                pairs, size, args.color_order, args.pixel_divisor, mean, std
            )
            calibration_shapes = f"images:2x3x{size}x{size}"
            for suffix, method, op_types, mha_dtype in fp8_configs:
                name = f"fp8_fp16_{suffix}_{size}"
                output_path = onnx_dir / f"{name}.onnx"
                log_path = log_dir / f"{name}.log"

                def convert_fp8(
                    out=output_path,
                    method_value=method,
                    ops=list(op_types),
                    mha_value=mha_dtype,
                    array=calibration_array,
                    shape_spec=calibration_shapes,
                ):
                    reader = CalibrationDataProvider(
                        str(model_path), {"images": array}, calibration_shapes=shape_spec
                    )
                    modelopt_quantize(
                        onnx_path=str(model_path),
                        quantize_mode="fp8",
                        calibration_method=method_value,
                        calibration_data_reader=reader,
                        calibration_shapes=shape_spec,
                        calibration_eps=[args.calibration_ep],
                        op_types_to_quantize=list(ops),
                        high_precision_dtype="fp16",
                        mha_accumulation_dtype=mha_value,
                        direct_io_types=False,
                        opset=model_meta["opset"],
                        keep_intermediate_files=False,
                        use_external_data_format=False,
                        output_path=str(out),
                        log_level="INFO",
                        log_file=str(log_path.with_name(log_path.stem + "_quant.log")),
                    )

                run_variant(name, "fp8", output_path, log_path, convert_fp8, manifest_rows)
            del calibration_array

    success_rows = [x for x in manifest_rows if x["status"] == "SUCCESS"]
    failed_rows = [x for x in manifest_rows if x["status"] != "SUCCESS"]
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_model": model_path.name,
        "source_model_sha256": sha256_file(model_path),
        "source_model_size_bytes": model_path.stat().st_size,
        "source_model_metadata": model_meta,
        "modelopt_version": modelopt_version,
        "python": sys.version,
        "platform": platform.platform(),
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
        "onnx_version": onnx.__version__,
        "onnxruntime_version": ort.__version__,
        "onnxruntime_providers": ort.get_available_providers(),
        "execution_device": "CPU-only GitHub-hosted runner",
        "calibration_dataset": str(pairs_csv),
        "calibration_pair_count": len(pairs),
        "calibration_resolutions": sizes,
        "calibration_ep": args.calibration_ep,
        "preprocessing": {
            "color_order": args.color_order,
            "pixel_divisor": args.pixel_divisor,
            "mean": mean,
            "std": std,
        },
        "variants_attempted": len(manifest_rows),
        "variants_succeeded": len(success_rows),
        "variants_failed": len(failed_rows),
        "variants": manifest_rows,
        "important": [
            "FP16 AutoCast variants are calibrated with one supplied image pair to limit ReferenceRunner intermediate-output memory; model I/O remains FP32.",
            "FP8 variants use explicit Q/DQ nodes, high_precision_dtype=fp16, and preserve source opset.",
            "FP8 supported op types selected here: Conv, Gemm, MatMul.",
            "The source ONNX and calibration images are not included in the package.",
            "Calibration is intentionally limited to one 512x512 pair for this first CPU pass; treat FP8 scales as provisional and validate accuracy before deployment.",
            "Preprocessing must match the original Raco pipeline; defaults are RGB and float32 / 255 with no mean/std.",
        ],
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    write_csv(run_dir / "summary.csv", manifest_rows)
    readme = [
        "Raco ALIKED + LightGlue ModelOpt ONNX variants",
        "",
        f"Source: {model_path.name}",
        f"Source SHA256: {manifest['source_model_sha256']}",
        f"ModelOpt: {modelopt_version}",
        "Execution device: GitHub-hosted CPU runner",
        f"Calibration pairs: {len(pairs)}",
        f"Calibration resolutions: {sizes}",
        f"Calibration EP: {args.calibration_ep}",
        f"Succeeded: {len(success_rows)} / {len(manifest_rows)}",
        "",
        "The source model and calibration images are intentionally excluded.",
        "Inspect manifest.json and logs/ before compiling TensorRT engines.",
        "Run TensorRT Parser on each ONNX variant before building an ENGINE.",
    ]
    (run_dir / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")

    checksum_lines = []
    for path in sorted(onnx_dir.glob("*.onnx")):
        checksum_lines.append(f"{sha256_file(path)}  onnx/{path.name}")
    (run_dir / "SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    zip_path = output_root / f"raco_modelopt_variants_{timestamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for folder in (onnx_dir, log_dir):
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(run_dir))
        for filename in ("manifest.json", "summary.csv", "README.txt", "SHA256SUMS.txt"):
            archive.write(run_dir / filename, filename)

    package_mib = round(zip_path.stat().st_size / (1024 * 1024), 2)
    print("\nConversion complete")
    print(f"Succeeded: {len(success_rows)} / {len(manifest_rows)}")
    print(f"Failed: {len(failed_rows)}")
    print("Output directory:", run_dir)
    print("Package:", zip_path)
    print("Package size MiB:", package_mib)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("## Raco ModelOpt ONNX conversion\n\n")
            summary.write(f"- Source: {model_path.name}\n")
            summary.write(f"- ModelOpt: {modelopt_version}\n")
            summary.write("- Execution device: GitHub-hosted CPU runner\n")
            summary.write(f"- Calibration pairs: {len(pairs)}\n")
            summary.write(f"- Resolutions: {sizes}\n")
            summary.write(f"- Calibration EP: {args.calibration_ep}\n")
            summary.write(f"- Variants succeeded: {len(success_rows)} / {len(manifest_rows)}\n")
            summary.write(f"- Package: {zip_path} ({package_mib} MiB)\n\n")
            summary.write("| Variant | Status | Size MiB | FP8 Q/DQ | Build seconds |\n")
            summary.write("|---|---:|---:|---:|---:|\n")
            for row in manifest_rows:
                size_mib = round(row.get("file_size_bytes", 0) / (1024 * 1024), 2) if row.get("file_size_bytes") else "-"
                qdq = f"{row.get('quantize_linear_nodes', 0)}/{row.get('dequantize_linear_nodes', 0)}" if row.get("kind") == "fp8" else "-"
                summary.write(f"| {row['name']} | {row['status']} | {size_mib} | {qdq} | {row['build_seconds']} |\n")
            summary.write("\n**Note:** The source ONNX and calibration images are not included in the package.\n")

    if not success_rows:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
