# -*- coding: utf-8 -*-
"""End-to-end CPU smoke test for the exact Raco ModelOpt conversion script."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import cv2
import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


def make_tiny_model(path: Path) -> None:
    rng = np.random.default_rng(20261105)
    w_conv = rng.normal(0, 0.04, (8, 3, 3, 3)).astype(np.float32)
    b_conv = rng.normal(0, 0.01, (8,)).astype(np.float32)
    w_fc = rng.normal(0, 0.08, (8, 4)).astype(np.float32)
    b_fc = rng.normal(0, 0.01, (4,)).astype(np.float32)

    nodes = [
        helper.make_node("Conv", ["images", "conv_w", "conv_b"], ["conv_out"],
                         pads=[1, 1, 1, 1], strides=[1, 1], name="conv_test"),
        helper.make_node("Relu", ["conv_out"], ["relu_out"], name="relu_test"),
        helper.make_node("GlobalAveragePool", ["relu_out"], ["pool_out"], name="pool_test"),
        helper.make_node("Flatten", ["pool_out"], ["flat_out"], axis=1, name="flatten_test"),
        helper.make_node("Gemm", ["flat_out", "fc_w", "fc_b"], ["scores"], name="gemm_test"),
    ]
    graph = helper.make_graph(
        nodes,
        "modelopt_cpu_smoke",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [2, 3, 32, 32])],
        [helper.make_tensor_value_info("scores", TensorProto.FLOAT, [2, 4])],
        initializer=[
            numpy_helper.from_array(w_conv, "conv_w"),
            numpy_helper.from_array(b_conv, "conv_b"),
            numpy_helper.from_array(w_fc, "fc_w"),
            numpy_helper.from_array(b_fc, "fc_b"),
        ],
    )
    model = helper.make_model(
        graph,
        producer_name="raco-modelopt-cpu-smoke",
        opset_imports=[helper.make_opsetid("", 20)],
    )
    model.ir_version = 10
    onnx.checker.check_model(model)
    onnx.save(model, str(path))


def make_calibration_pairs(root: Path, count: int = 4) -> None:
    images = root / "images"
    images.mkdir(parents=True, exist_ok=True)
    rows = []
    rng = np.random.default_rng(20261105)
    for index in range(count):
        p0 = images / f"pair_{index:02d}_a.png"
        p1 = images / f"pair_{index:02d}_b.png"
        img0 = rng.integers(0, 256, (48, 48, 3), dtype=np.uint8)
        img1 = cv2.GaussianBlur(img0, (3, 3), 0.4)
        cv2.imwrite(str(p0), img0)
        cv2.imwrite(str(p1), img1)
        rows.append({
            "pair_id": f"smoke_{index:02d}",
            "image0": f"images/{p0.name}",
            "image1": f"images/{p1.name}",
        })
    with (root / "pairs.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["pair_id", "image0", "image1"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    converter = repo_root / "scripts" / "modelopt_convert_raco_onnx.py"
    with tempfile.TemporaryDirectory(prefix="modelopt_cpu_smoke_") as temp:
        root = Path(temp)
        model = root / "tiny_raco.onnx"
        calibration = root / "calibration"
        output = root / "output"
        make_tiny_model(model)
        make_calibration_pairs(calibration, 4)

        command = [
            sys.executable, str(converter),
            "--model", str(model),
            "--calibration-root", str(calibration),
            "--output-root", str(output),
            "--max-pairs", "4",
            "--resolutions", "32",
            "--calibration-ep", "cpu",
            "--color-order", "RGB",
            "--pixel-divisor", "255.0",
        ]
        print("Running:", subprocess.list2cmdline(command))
        result = subprocess.run(command, cwd=repo_root, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ModelOpt converter smoke test failed: exit={result.returncode}")

        packages = sorted(output.glob("raco_modelopt_variants_*.zip"))
        if not packages:
            raise RuntimeError("Converter did not create a ZIP package")
        with zipfile.ZipFile(packages[-1]) as archive:
            onnx_files = [n for n in archive.namelist() if n.endswith(".onnx")]
            if not onnx_files:
                raise RuntimeError("ZIP package contains no ONNX files")
            print("ZIP:", packages[-1].name)
            print("ONNX variants:", onnx_files)
            manifest = json.loads(archive.read("manifest.json"))
            print("Success:", manifest["variants_succeeded"], "/", manifest["variants_attempted"])
            fp8_successes = [
                row for row in manifest["variants"]
                if row.get("kind") == "fp8" and row.get("status") == "SUCCESS"
            ]
            for row in manifest["variants"]:
                print(
                    f"VARIANT {row.get('status')} {row.get('name')} "
                    f"error={row.get('error', '')}"
                )
            for name in archive.namelist():
                if name.startswith("logs/") and name.endswith(".log"):
                    log_text = archive.read(name).decode("utf-8", errors="replace")
                    if "Traceback" in log_text or "Error" in log_text or "ERROR" in log_text:
                        print(f"--- diagnostic log: {name} ---")
                        print("\n".join(log_text.splitlines()[-50:]))
            if manifest["variants_succeeded"] < 1:
                raise RuntimeError("No ModelOpt variant succeeded in CPU smoke test")
            if not fp8_successes:
                raise RuntimeError("All FP8 variants failed in CPU smoke test; inspect diagnostics above")
    print("MODELopt CPU end-to-end smoke test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
