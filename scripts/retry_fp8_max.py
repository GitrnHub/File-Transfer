#!/usr/bin/env python3
"""Retry the single FP8 max-calibration variant without per-node shape inference."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path

import onnx
from onnx import TensorProto

# Executed as python scripts/retry_fp8_max.py, so scripts/ is on sys.path.
import modelopt_convert_raco_onnx as base
from modelopt.onnx.quantization import quantize as modelopt_quantize
from modelopt.onnx.quantization.calib_utils import CalibrationDataProvider


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--calibration-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    run_dir = args.output_root / "fp8_max_retry"
    run_dir.mkdir(parents=True, exist_ok=True)
    onnx_dir = run_dir / "onnx"
    logs_dir = run_dir / "logs"
    onnx_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)

    result = {
        "source_model": args.model.name,
        "source_model_sha256": sha256(args.model),
        "modelopt_version": base.importlib.metadata.version("nvidia-modelopt"),
        "python": sys.version,
        "platform": platform.platform(),
        "calibration_pair_count": 1,
        "calibration_resolution": 512,
        "calibration_ep": "cpu",
        "calibration_method": "max",
        "calibrate_per_node": False,
        "status": "FAILED",
        "error": "",
    }

    log_path = logs_dir / "fp8_fp16_all_max_mha16_512_quant.log"
    output_path = onnx_dir / "fp8_fp16_all_max_mha16_512.onnx"
    start = time.time()
    try:
        pairs = base.read_pairs(args.calibration_root / "pairs.csv", 1)
        calibration = base.make_calibration_array(
            pairs, 512, "RGB", 255.0, None, None
        )
        shape_spec = "images:2x3x512x512"

        fp8_model_path = run_dir / "raco_fp8_ort_compatible.onnx"
        base.make_fp8_ort_compatible_model(args.model, fp8_model_path)
        ort_model = base.ort.InferenceSession(
            str(fp8_model_path), providers=["CPUExecutionProvider"]
        )
        del ort_model
        print("FP8 ORT-compatible source preflight: PASS", flush=True)

        reader = CalibrationDataProvider(
            str(args.model), {"images": calibration}, calibration_shapes=shape_spec
        )
        print("Starting FP8 max calibration with calibrate_per_node=False", flush=True)
        modelopt_quantize(
            onnx_path=str(fp8_model_path),
            quantize_mode="fp8",
            calibration_method="max",
            calibration_data_reader=reader,
            calibration_shapes=shape_spec,
            calibration_eps=["cpu"],
            op_types_to_quantize=list(base.SUPPORTED_FP8_OPS),
            high_precision_dtype="fp16",
            mha_accumulation_dtype="fp16",
            direct_io_types=False,
            opset=base.inspect_model(args.model)["opset"],
            keep_intermediate_files=False,
            use_external_data_format=False,
            output_path=str(output_path),
            calibrate_per_node=False,
            log_level="INFO",
            log_file=str(log_path),
        )
        model = onnx.load(str(output_path), load_external_data=True)
        onnx.checker.check_model(model)
        q_nodes = sum(n.op_type == "QuantizeLinear" for n in model.graph.node)
        dq_nodes = sum(n.op_type == "DequantizeLinear" for n in model.graph.node)
        if q_nodes == 0 or dq_nodes == 0:
            raise RuntimeError(f"FP8 output has no Q/DQ nodes: Q={q_nodes}, DQ={dq_nodes}")
        result.update({
            "status": "SUCCESS",
            "output_file": output_path.name,
            "file_size_bytes": output_path.stat().st_size,
            "sha256": sha256(output_path),
            "node_count": len(model.graph.node),
            "initializer_count": len(model.graph.initializer),
            "quantize_linear_nodes": q_nodes,
            "dequantize_linear_nodes": dq_nodes,
        })
        print(f"FP8 max retry SUCCESS: Q={q_nodes}, DQ={dq_nodes}", flush=True)
    except Exception as exc:
        result["error"] = repr(exc)
        (logs_dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print("FP8 max retry FAILED:", repr(exc), flush=True)
        traceback.print_exc()
    finally:
        result["build_seconds"] = round(time.time() - start, 2)
        (run_dir / "manifest.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (run_dir / "README.txt").write_text(
            "Raco FP8 max calibration retry\n"
            f"Status: {result['status']}\n"
            "This retry disables per-node calibration to bypass the ONNX Runtime "
            "SymbolicShapeInference Range-node AssertionError observed with max calibration.\n"
            "If successful, compare accuracy and TensorRT parser behavior before deployment.\n",
            encoding="utf-8",
        )
    return 0 if result["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
