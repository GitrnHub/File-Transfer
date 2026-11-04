# -*- coding: utf-8 -*-
"""Generate deterministic synthetic image pairs for Raco ModelOpt calibration.

The images are used only as representative calibration inputs. Each pair is
created from a textured source image and a known perspective warp.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np


def color(rng):
    return tuple(int(v) for v in rng.integers(15, 245, size=3))


def make_scene(seed: int, size: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    fine = rng.normal(127, 38, (size, size, 3)).astype(np.float32)
    low_side = max(32, size // 16)
    mid_side = max(64, size // 4)
    low = cv2.resize(
        rng.integers(0, 256, (low_side, low_side, 3), dtype=np.uint8),
        (size, size), interpolation=cv2.INTER_CUBIC
    ).astype(np.float32)
    mid = cv2.resize(
        rng.integers(0, 256, (mid_side, mid_side, 3), dtype=np.uint8),
        (size, size), interpolation=cv2.INTER_LINEAR
    ).astype(np.float32)
    image = np.clip(0.56 * fine + 0.24 * mid + 0.20 * low, 0, 255).astype(np.uint8)

    for _ in range(90):
        p0 = tuple(int(v) for v in rng.integers(0, size, size=2))
        p1 = tuple(int(v) for v in rng.integers(0, size, size=2))
        cv2.line(image, p0, p1, color(rng), int(rng.integers(1, 4)), cv2.LINE_AA)

    for _ in range(70):
        x, y = (int(v) for v in rng.integers(0, size, size=2))
        radius = int(rng.integers(5, max(6, size // 24)))
        cv2.circle(image, (x, y), radius, color(rng), int(rng.choice([-1, 1, 2])), cv2.LINE_AA)

    for _ in range(35):
        pts = rng.integers(0, size, size=(int(rng.integers(3, 7)), 2), dtype=np.int32)
        cv2.polylines(image, [pts], True, color(rng), int(rng.integers(1, 4)), cv2.LINE_AA)

    for index in range(24):
        x = int(rng.integers(0, max(1, size - 150)))
        y = int(rng.integers(25, size - 5))
        cv2.putText(
            image, f"RACO {seed % 1000:03d}-{index:02d}", (x, y),
            cv2.FONT_HERSHEY_SIMPLEX, float(rng.uniform(0.45, 1.0)),
            color(rng), int(rng.integers(1, 3)), cv2.LINE_AA
        )
    return image


def make_homography(rng: np.random.Generator, size: int, level: str) -> np.ndarray:
    limits = {
        "easy": (5.0, 0.96, 1.04, 20.0, 0.7e-5),
        "medium": (11.0, 0.90, 1.10, 45.0, 1.5e-5),
        "hard": (17.0, 0.84, 1.16, 68.0, 2.2e-5),
    }
    max_angle, scale_min, scale_max, shift, perspective = limits[level]
    angle = float(rng.uniform(-max_angle, max_angle))
    scale = float(rng.uniform(scale_min, scale_max))
    tx, ty = rng.uniform(-shift, shift, size=2)
    affine = cv2.getRotationMatrix2D((size / 2.0, size / 2.0), angle, scale)
    affine[0, 2] += tx
    affine[1, 2] += ty
    H = np.eye(3, dtype=np.float64)
    H[:2, :] = affine
    H[2, 0] = float(rng.uniform(-perspective, perspective))
    H[2, 1] = float(rng.uniform(-perspective, perspective))
    return H


def generate(output: Path, pairs: int, size: int, seed: int, quality: int):
    if pairs < 3:
        raise ValueError("At least three pairs (easy, medium, hard) are required.")
    if size < 512 or size % 32:
        raise ValueError("Size must be >=512 and divisible by 32.")

    images = output / "images"
    homographies = output / "homographies"
    images.mkdir(parents=True, exist_ok=True)
    homographies.mkdir(parents=True, exist_ok=True)
    rows = []

    for index in range(pairs):
        pair_id = f"synthetic_{index + 1:04d}"
        difficulty = ("easy", "medium", "hard")[index % 3]
        local_seed = seed + index * 7919
        rng = np.random.default_rng(local_seed)
        image0 = make_scene(local_seed, size)
        H = make_homography(rng, size, difficulty)
        image1 = cv2.warpPerspective(
            image0, H, (size, size), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT101
        )

        if difficulty == "easy":
            alpha, beta, sigma = float(rng.uniform(.92, 1.08)), float(rng.uniform(-8, 8)), 0.8
        elif difficulty == "medium":
            alpha, beta, sigma = float(rng.uniform(.80, 1.20)), float(rng.uniform(-18, 18)), 1.8
        else:
            alpha, beta, sigma = float(rng.uniform(.70, 1.28)), float(rng.uniform(-28, 28)), 3.0

        image1 = np.clip(image1.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)
        if difficulty != "easy":
            image1 = cv2.GaussianBlur(image1, (0, 0), float(rng.uniform(0.0, sigma)))
        if difficulty == "hard" and rng.random() < 0.45:
            for _ in range(int(rng.integers(1, 3))):
                w = int(rng.integers(size // 18, size // 8))
                h = int(rng.integers(size // 18, size // 8))
                x = int(rng.integers(0, size - w))
                y = int(rng.integers(0, size - h))
                cv2.rectangle(image1, (x, y), (x + w, y + h), color(rng), -1)

        p0 = images / f"{pair_id}_a.jpg"
        p1 = images / f"{pair_id}_b.jpg"
        hp = homographies / f"{pair_id}_H.txt"
        if not cv2.imwrite(str(p0), image0, [cv2.IMWRITE_JPEG_QUALITY, quality]):
            raise RuntimeError(f"Could not write {p0}")
        if not cv2.imwrite(str(p1), image1, [cv2.IMWRITE_JPEG_QUALITY, quality]):
            raise RuntimeError(f"Could not write {p1}")
        np.savetxt(str(hp), H, fmt="%.14g")
        rows.append({
            "pair_id": pair_id,
            "image0": f"images/{p0.name}",
            "image1": f"images/{p1.name}",
            "homography": f"homographies/{hp.name}",
            "difficulty": difficulty,
        })
        print(f"[{index + 1}/{pairs}] {pair_id} {difficulty}")

    with (output / "pairs.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["pair_id", "image0", "image1", "homography", "difficulty"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print("Synthetic calibration set:", output)
    print("Pairs:", pairs, "size:", size)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=24)
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument("--jpeg-quality", type=int, default=96)
    args = parser.parse_args()
    generate(args.output, args.pairs, args.size, args.seed, args.jpeg_quality)


if __name__ == "__main__":
    main()
