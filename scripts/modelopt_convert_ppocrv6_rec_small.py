# -*- coding: utf-8 -*-
"""ModelOpt conversion pipeline for RapidOCR PP-OCRv6_rec_small.onnx.

The FP16 variants explicitly set keep_io_types=True.
For FP8 Q/DQ, ModelOpt 0.48.0 exposes the equivalent I/O-preservation control
as direct_io_types=False; the script verifies public input/output dtypes after
every conversion.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import shutil
import sys
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnx
import onnxruntime as ort
import torch
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from modelopt.onnx.autocast import convert_to_mixed_precision
from modelopt.onnx.quantization import quantize as modelopt_quantize
from modelopt.onnx.quantization.calib_utils import CalibrationDataProvider

EXPECTED_SHA256 = "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"
EXPECTED_MODEL_NAME = "PP-OCRv6_rec_small.onnx"
MODEL_INPUT_HEIGHT = 48
MODEL_INPUT_WIDTH = 320
SUPPORTED_FP8_OPS = ("Conv", "Gemm", "MatMul")
SENSITIVE_FP16_OPS = ("Softmax", "Sigmoid", "Tanh", "Exp", "Log", "Pow")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def value_shape(value: onnx.ValueInfoProto) -> list[Any]:
    dims: list[Any] = []
    for dim in value.type.tensor_type.shape.dim:
        dims.append(dim.dim_param if dim.dim_param else dim.dim_value)
    return dims


def io_signature(model: onnx.ModelProto) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for kind, values in (("input", model.graph.input), ("output", model.graph.output)):
        for value in values:
            result[f"{kind}:{value.name}"] = {
                "elem_type": int(value.type.tensor_type.elem_type),
                "shape": value_shape(value),
            }
    return result


def inspect_model(path: Path) -> dict[str, Any]:
    model = onnx.load(str(path), load_external_data=True)
    onnx.checker.check_model(model)
    inputs = []
    outputs = []
    for value in model.graph.input:
        inputs.append({
            "name": value.name,
            "shape": value_shape(value),
            "elem_type": int(value.type.tensor_type.elem_type),
        })
    for value in model.graph.output:
        outputs.append({
            "name": value.name,
            "shape": value_shape(value),
            "elem_type": int(value.type.tensor_type.elem_type),
        })
    opset = max(
        (x.version for x in model.opset_import if x.domain in ("", "ai.onnx")),
        default=0,
    )
    return {
        "opset": int(opset),
        "node_count": len(model.graph.node),
        "initializer_count": len(model.graph.initializer),
        "input": inputs,
        "outputs": outputs,
        "op_types": sorted({node.op_type for node in model.graph.node}),
        "io_signature": io_signature(model),
    }


def verify_io_types(path: Path, expected: dict[str, dict[str, Any]]) -> None:
    model = onnx.load(str(path), load_external_data=True)
    actual = io_signature(model)
    expected_types = {key: value["elem_type"] for key, value in expected.items()}
    actual_types = {key: value["elem_type"] for key, value in actual.items()}
    if actual_types != expected_types:
        raise RuntimeError(
            "Public ONNX I/O types changed. "
            f"Expected={expected_types}, actual={actual_types}"
        )


def find_font_paths() -> tuple[Path, Path]:
    cjk_candidates = [
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]
    latin_candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    cjk = next((p for p in cjk_candidates if p.exists()), None)
    latin = next((p for p in latin_candidates if p.exists()), None)
    if cjk is None:
        raise FileNotFoundError("Noto Sans CJK font not found; install fonts-noto-cjk.")
    if latin is None:
        latin = cjk
    return cjk, latin


def render_calibration_image(
    text: str,
    index: int,
    width: int,
    cjk_font_path: Path,
    latin_font_path: Path,
    rng: np.random.Generator,
) -> np.ndarray:
    height = MODEL_INPUT_HEIGHT
    bg = int(rng.integers(238, 256))
    image = Image.new("RGB", (width, height), (bg, bg, bg))
    draw = ImageDraw.Draw(image)
    font_path = cjk_font_path if any(ord(ch) > 127 for ch in text) else latin_font_path
    font_size = int(rng.integers(21, 29))
    font = ImageFont.truetype(str(font_path), font_size, index=0)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=0)
    text_height = bbox[3] - bbox[1]
    y = max(0, (height - text_height) // 2 - bbox[1] + int(rng.integers(-2, 3)))
    x = int(rng.integers(3, 10))
    shade = int(rng.integers(0, 70))
    draw.text((x, y), text, font=font, fill=(shade, shade, shade))

    if index % 3 == 1:
        draw.line(
            (0, int(rng.integers(2, height - 2)), width, int(rng.integers(2, height - 2))),
            fill=(int(rng.integers(180, 235)),) * 3,
            width=1,
        )
    if index % 4 == 2:
        image = image.filter(ImageFilter.GaussianBlur(radius=0.35))
    arr = np.asarray(image).astype(np.int16)
    noise = rng.normal(0.0, float(rng.uniform(0.0, 2.0)), arr.shape)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def preprocess_rec_image(bgr: np.ndarray, height: int, width: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    resized_w = min(width, max(1, int(math.ceil(height * (w / float(h))))))
    resized = cv2.resize(bgr, (resized_w, height), interpolation=cv2.INTER_LINEAR)
    # Match RapidOCR PP-OCRv6 recognition preprocessing:
    # OpenCV BGR input, CHW float32, /255, then (x - 0.5) / 0.5.
    normalized = resized.astype(np.float32).transpose((2, 0, 1)) / 255.0
    normalized -= 0.5
    normalized /= 0.5
    padded = np.zeros((3, height, width), dtype=np.float32)
    padded[:, :, :resized_w] = normalized
    return np.ascontiguousarray(padded)


def generate_calibration_set(
    destination: Path,
    count: int,
    height: int,
    width: int,
    seed: int,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    destination.mkdir(parents=True, exist_ok=True)
    images_dir = destination / "images"
    npz_dir = destination / "autocast_npz"
    images_dir.mkdir(parents=True, exist_ok=True)
    npz_dir.mkdir(parents=True, exist_ok=True)

    cjk_font, latin_font = find_font_paths()
    rng = np.random.default_rng(seed)
    texts = [
        "OCR ModelOpt 2026-11-06 0123456789",
        "快速棕色狐狸跳过懒狗 2026",
        "東京駅 7:45 / platform 12",
        "ABCD-1234 / 98.76% / $123.45",
        "混合文本 Test 文字 recognition",
        "OpenAI TensorRT 11.4 FP16 FP8",
        "商品编号: ZX-2048, 価格 ¥12,800",
        "Invoice No. 2026-1106 / Total 1,234.56",
        "模型识别测试：中文、English、12345",
        "Version 3.10.0  SHA256 6f327246",
        "大阪・東京・京都 2026年11月",
        "HELLO world OCR 0123456789",
    ]
    rows = []
    arrays = []
    for i in range(count):
        text = texts[i % len(texts)]
        approx_width = int(28 + len(text) * float(rng.uniform(13.0, 19.0)))
        canvas_width = int(np.clip(approx_width, 160, 760))
        bgr = render_calibration_image(
            text, i, canvas_width, cjk_font, latin_font, rng
        )
        image_path = images_dir / f"rec_calib_{i + 1:02d}.png"
        if not cv2.imwrite(str(image_path), bgr):
            raise RuntimeError(f"Could not write calibration image: {image_path}")
        tensor = preprocess_rec_image(bgr, height, width)
        batch = tensor[None, ...]
        npz_path = npz_dir / f"rec_calib_{i + 1:02d}.npz"
        np.savez_compressed(npz_path, x=batch)
        arrays.append(tensor)
        rows.append({
            "sample": i + 1,
            "text": text,
            "image": str(image_path.relative_to(destination)),
            "npz": str(npz_path.relative_to(destination)),
            "original_width": int(bgr.shape[1]),
            "model_input_shape": f"1x3x{height}x{width}",
        })

    with (destination / "calibration_manifest.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    array = np.ascontiguousarray(np.stack(arrays, axis=0), dtype=np.float32)
    return array, rows


def inspect_variant(path: Path) -> dict[str, Any]:
    model = onnx.load(str(path), load_external_data=True)
    onnx.checker.check_model(model)
    fp16_initializers = sum(
        1 for x in model.graph.initializer if x.data_type == onnx.TensorProto.FLOAT16
    )
    fp8_types = {
        getattr(onnx.TensorProto, "FLOAT8E4M3FN", -100),
        getattr(onnx.TensorProto, "FLOAT8E4M3FNUZ", -101),
        getattr(onnx.TensorProto, "FLOAT8E5M2", -102),
        getattr(onnx.TensorProto, "FLOAT8E5M2FNUZ", -103),
    }
    fp8_initializers = sum(
        1 for x in model.graph.initializer if x.data_type in fp8_types
    )
    q_nodes = sum(1 for n in model.graph.node if n.op_type == "QuantizeLinear")
    dq_nodes = sum(1 for n in model.graph.node if n.op_type == "DequantizeLinear")
    return {
        "file_size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "opset": max(
            (x.version for x in model.opset_import if x.domain in ("", "ai.onnx")),
            default=0,
        ),
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
    expected_io: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> None:
    start = time.time()
    row: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "status": "FAILED",
        "keep_io_types": kind == "fp16",
        "io_preservation": "direct_io_types=false" if kind == "fp8" else "keep_io_types=true",
        "output": str(output_path.name),
        "log": str(log_path.name),
        "error": "",
    }
    if extra:
        row.update(extra)
    try:
        if output_path.exists():
            output_path.unlink()
        convert_fn()
        if not output_path.is_file():
            raise FileNotFoundError(f"Converter did not create {output_path}")
        model = onnx.load(str(output_path), load_external_data=True)
        onnx.checker.check_model(model)
        verify_io_types(output_path, expected_io)
        stats = inspect_variant(output_path)
        if kind == "fp8" and (
            stats["quantize_linear_nodes"] == 0 or stats["dequantize_linear_nodes"] == 0
        ):
            raise RuntimeError(
                "FP8 model contains no QuantizeLinear/DequantizeLinear nodes"
            )
        row.update(stats)
        row["status"] = "SUCCESS"
        print(f"[SUCCESS] {name}: {stats}", flush=True)
    except Exception as exc:
        row["error"] = repr(exc)
        row["traceback"] = traceback.format_exc()
        print(f"[FAILED] {name}: {exc!r}", flush=True)
        traceback.print_exc()
    row["build_seconds"] = round(time.time() - start, 2)
    rows.append(row)
    log_path.write_text(
        json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--calibration-count", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--calibration-width", type=int, default=320)
    args = parser.parse_args()

    model_path = args.model.resolve()
    output_root = args.output_root.resolve()
    if not model_path.is_file():
        raise FileNotFoundError(model_path)
    actual_sha = sha256_file(model_path)
    if actual_sha.lower() != EXPECTED_SHA256:
        raise RuntimeError(
            f"Source SHA256 mismatch: expected {EXPECTED_SHA256}, got {actual_sha}"
        )
    if args.calibration_count < 2:
        raise ValueError("Use at least two synthetic calibration samples.")
    output_root.mkdir(parents=True, exist_ok=True)

    source_meta = inspect_model(model_path)
    if len(source_meta["input"]) != 1:
        raise RuntimeError(f"Expected one input tensor, got {source_meta['input']}")
    input_info = source_meta["input"][0]
    input_name = input_info["name"]
    input_shape = input_info["shape"]
    if len(input_shape) != 4:
        raise RuntimeError(f"Expected NCHW recognition input, got {input_shape}")
    if input_shape[1] not in (3, "C", "channel"):
        raise RuntimeError(f"Expected 3 input channels, got {input_shape}")
    height = int(input_shape[2]) if isinstance(input_shape[2], int) and input_shape[2] > 0 else MODEL_INPUT_HEIGHT
    width = int(input_shape[3]) if isinstance(input_shape[3], int) and input_shape[3] > 0 else args.calibration_width
    shape_spec = f"{input_name}:1x3x{height}x{width}"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"run_{timestamp}"
    onnx_dir = run_dir / "onnx"
    logs_dir = run_dir / "logs"
    calibration_dir = run_dir / "calibration"
    onnx_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    calibration_dir.mkdir(parents=True, exist_ok=True)

    modelopt_version = importlib.metadata.version("nvidia-modelopt")
    rapidocr_version = importlib.metadata.version("rapidocr")
    calibration_array, calibration_rows = generate_calibration_set(
        calibration_dir, args.calibration_count, height, width, args.seed
    )
    calibration_shapes = shape_spec
    expected_io = source_meta["io_signature"]

    print("PP-OCRv6_rec_small ModelOpt conversion", flush=True)
    print("Source:", model_path, flush=True)
    print("Source bytes:", model_path.stat().st_size, flush=True)
    print("Source SHA256:", actual_sha, flush=True)
    print("RapidOCR:", rapidocr_version, "ModelOpt:", modelopt_version, flush=True)
    print("Python:", sys.version.split()[0], "PyTorch:", torch.__version__, flush=True)
    print("ONNX Runtime:", ort.__version__, "providers:", ort.get_available_providers(), flush=True)
    print("Model metadata:", json.dumps(source_meta, ensure_ascii=False), flush=True)
    print("Calibration:", len(calibration_rows), "samples", shape_spec, flush=True)
    print("Required FP16 keep_io_types=True", flush=True)
    print("FP8 public I/O preservation: direct_io_types=False", flush=True)

    # Baseline ONNX Runtime preflight using one real-shaped synthetic input.
    baseline_session = ort.InferenceSession(
        str(model_path), providers=["CPUExecutionProvider"]
    )
    baseline_outputs = baseline_session.run(
        None, {input_name: calibration_array[:1]}
    )
    print(
        "Baseline ORT preflight: PASS; output shapes:",
        [list(x.shape) for x in baseline_outputs],
        flush=True,
    )
    del baseline_session, baseline_outputs

    manifest_rows: list[dict[str, Any]] = []
    autocast_dir = calibration_dir / "autocast_npz"

    # FP16: all variants explicitly preserve public input/output tensor types.
    fp16_configs = [
        (
            "fp16_autocast_balanced",
            512.0,
            list(SENSITIVE_FP16_OPS),
        ),
        (
            "fp16_autocast_conservative",
            512.0,
            list(SENSITIVE_FP16_OPS) + [
                "Div", "ReduceMean", "LayerNormalization", "BatchNormalization"
            ],
        ),
        ("fp16_autocast_aggressive", 2048.0, None),
    ]
    for name, data_max, excluded_ops in fp16_configs:
        output_path = onnx_dir / f"{name}.onnx"
        log_path = logs_dir / f"{name}.json"

        def convert_fp16(
            out=output_path,
            dmax=data_max,
            excluded=excluded_ops,
        ):
            try:
                converted = convert_to_mixed_precision(
                    onnx_path=str(model_path),
                    low_precision_type="fp16",
                    nodes_to_exclude=None,
                    op_types_to_exclude=excluded,
                    data_max=dmax,
                    init_max=65504.0,
                    keep_io_types=True,
                    calibration_data=str(autocast_dir),
                    providers=["cpu"],
                    opset=max(19, source_meta["opset"]),
                )
            except Exception as exc:
                print(
                    f"FP16 calibrated AutoCast failed for {out.name}: {exc!r}; "
                    "retrying without ReferenceRunner activation capture.",
                    flush=True,
                )
                converted = convert_to_mixed_precision(
                    onnx_path=str(model_path),
                    low_precision_type="fp16",
                    nodes_to_exclude=None,
                    op_types_to_exclude=excluded,
                    data_max=float("inf"),
                    init_max=65504.0,
                    keep_io_types=True,
                    calibration_data=None,
                    providers=["cpu"],
                    opset=max(19, source_meta["opset"]),
                )
            onnx.save(converted, str(out))

        run_variant(
            name, "fp16", output_path, log_path, convert_fp16,
            expected_io, manifest_rows,
            {"data_max": data_max, "op_types_to_exclude": excluded_ops,
             "keep_io_types_argument": True},
        )

    # FP8: ModelOpt 0.48.0 uses direct_io_types=False to keep source I/O types.
    graph_ops = set(source_meta["op_types"])
    fp8_ops = [op for op in SUPPORTED_FP8_OPS if op in graph_ops]
    fp8_configs: list[tuple[str, str, list[str], bool]] = []
    if fp8_ops:
        fp8_configs.append(("all_entropy", "entropy", fp8_ops, True))
        if "Conv" in fp8_ops:
            fp8_configs.append(("conv_entropy", "entropy", ["Conv"], True))
        matrix_ops = [op for op in ("MatMul", "Gemm") if op in fp8_ops]
        if matrix_ops:
            fp8_configs.append(("matmul_gemm_entropy", "entropy", matrix_ops, True))
        fp8_configs.append(("all_max", "max", fp8_ops, False))

    seen_configs: set[tuple[str, tuple[str, ...]]] = set()
    for suffix, method, op_types, per_node in fp8_configs:
        config_key = (method, tuple(op_types))
        if config_key in seen_configs:
            continue
        seen_configs.add(config_key)
        name = f"fp8_fp16_{suffix}_{method}_512"
        output_path = onnx_dir / f"{name}.onnx"
        log_path = logs_dir / f"{name}.json"

        def convert_fp8(
            out=output_path,
            method_value=method,
            ops=list(op_types),
            use_per_node=per_node,
        ):
            def run_quantize(per_node_value: bool) -> None:
                reader = CalibrationDataProvider(
                    str(model_path),
                    calibration_array,
                    calibration_shapes=calibration_shapes,
                )
                modelopt_quantize(
                    onnx_path=str(model_path),
                    quantize_mode="fp8",
                    calibration_method=method_value,
                    calibration_data_reader=reader,
                    calibration_shapes=calibration_shapes,
                    calibration_eps=["cpu"],
                    op_types_to_quantize=ops,
                    high_precision_dtype="fp16",
                    mha_accumulation_dtype="fp16",
                    direct_io_types=False,
                    opset=max(19, source_meta["opset"]),
                    keep_intermediate_files=False,
                    use_external_data_format=False,
                    output_path=str(out),
                    calibrate_per_node=per_node_value,
                    log_level="INFO",
                    log_file=str(log_path.with_suffix(".quant.log")),
                )

            try:
                run_quantize(use_per_node)
            except Exception as exc:
                if not use_per_node:
                    raise
                print(
                    f"FP8 per-node calibration failed for {out.name}: {exc!r}; "
                    "retrying with calibrate_per_node=False.",
                    flush=True,
                )
                if out.exists():
                    out.unlink()
                run_quantize(False)

        run_variant(
            name, "fp8", output_path, log_path, convert_fp8,
            expected_io, manifest_rows,
            {
                "calibration_method": method,
                "op_types_to_quantize": op_types,
                "calibrate_per_node_initial": per_node,
                "direct_io_types": False,
                "keep_io_types_equivalent": True,
            },
        )

    succeeded_fp16 = sum(
        row["status"] == "SUCCESS" and row["kind"] == "fp16"
        for row in manifest_rows
    )
    succeeded_fp8 = sum(
        row["status"] == "SUCCESS" and row["kind"] == "fp8"
        for row in manifest_rows
    )
    manifest = {
        "task": "PP-OCRv6_rec_small ModelOpt conversion",
        "source_model": model_path.name,
        "source_model_sha256": actual_sha,
        "source_model_bytes": model_path.stat().st_size,
        "rapidocr_version": rapidocr_version,
        "modelopt_version": modelopt_version,
        "python": sys.version,
        "platform": platform.platform(),
        "torch": torch.__version__,
        "onnx": onnx.__version__,
        "onnxruntime": ort.__version__,
        "execution_provider": "CPUExecutionProvider",
        "source_metadata": source_meta,
        "calibration": {
            "kind": "synthetic OCR text-line images",
            "count": len(calibration_rows),
            "input_shape_per_sample": shape_spec,
            "preprocessing": "RapidOCR BGR -> resize to H=48, pad W=320, CHW, /255, (x-0.5)/0.5",
            "seed": args.seed,
        },
        "io_policy": {
            "fp16": "keep_io_types=True (explicitly passed)",
            "fp8": "direct_io_types=False (ModelOpt 0.48.0 preserves source I/O types)",
        },
        "variants_attempted": len(manifest_rows),
        "variants_succeeded": succeeded_fp16 + succeeded_fp8,
        "fp16_succeeded": succeeded_fp16,
        "fp8_succeeded": succeeded_fp8,
        "variants": manifest_rows,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    csv_path = run_dir / "summary.csv"
    csv_fields = sorted({key for row in manifest_rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(manifest_rows)

    readme = [
        "PP-OCRv6_rec_small ModelOpt outputs",
        "",
        f"Source SHA256: {actual_sha}",
        f"RapidOCR: {rapidocr_version}",
        f"ModelOpt: {modelopt_version}",
        f"FP16 successes: {succeeded_fp16}",
        f"FP8 successes: {succeeded_fp8}",
        "",
        "All FP16 variants explicitly pass keep_io_types=True.",
        "FP8 variants pass direct_io_types=False in ModelOpt 0.48.0 to preserve source I/O dtypes.",
        "Calibration uses synthetic OCR text-line crops and RapidOCR recognition preprocessing.",
        "Synthetic calibration is suitable for conversion experimentation, not final accuracy certification.",
        "Compare all outputs against the FP32 source on a representative OCR validation set before deployment.",
        "",
    ]
    (run_dir / "README_FIRST.txt").write_text("\n".join(readme), encoding="utf-8")

    source_copy = run_dir / "source" / EXPECTED_MODEL_NAME
    source_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_path, source_copy)

    sums_path = run_dir / "SHA256SUMS.txt"
    with sums_path.open("w", encoding="utf-8") as f:
        for path in sorted(run_dir.rglob("*.onnx")):
            f.write(f"{sha256_file(path)}  {path.relative_to(run_dir).as_posix()}\n")

    package_path = output_root / f"ppocrv6_rec_small_modelopt_{timestamp}.zip"
    with zipfile.ZipFile(
        package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
    ) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(run_dir).as_posix())

    print("FP16 successes:", succeeded_fp16, flush=True)
    print("FP8 successes:", succeeded_fp8, flush=True)
    print("Output directory:", run_dir, flush=True)
    print("Output package:", package_path, package_path.stat().st_size, flush=True)

    if succeeded_fp16 < 1:
        raise RuntimeError("No FP16 variant succeeded; inspect logs and manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
