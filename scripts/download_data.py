#!/usr/bin/env python
"""Download the MedSP1000 dataset from the Hugging Face Hub into ``data/MedSP1000``.

After downloading, it (optionally) regenerates the scenario manifest so the
benchmark is immediately runnable:

    python scripts/download_data.py
    bash scripts/run_simulate_cases.sh --examinee-model <MODEL> --sp-env-eval-model <JUDGE>

Behind a firewall, point HF at a mirror before running:

    export HF_ENDPOINT=https://hf-mirror.com
    python scripts/download_data.py

Useful flags / env vars:
    --repo-id            HF dataset id (default: byrLLCC/MedSP1000)
    --local-dir          download target (default: data/MedSP1000)
    --no-manifest        skip regenerating scenario_directories_full.json
    HF_TOKEN             access token, if the dataset is private
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO_ID = "byrLLCC/MedSP1000"
DEFAULT_LOCAL_DIR = REPO_ROOT / "data" / "MedSP1000"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download MedSP1000 from the Hugging Face Hub.")
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID, help="HF dataset repo id.")
    parser.add_argument(
        "--local-dir",
        default=str(DEFAULT_LOCAL_DIR),
        help="Local download directory (default: data/MedSP1000).",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Do not regenerate scenario_directories_full.json after download.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        from huggingface_hub import snapshot_download  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        raise SystemExit(
            "huggingface_hub is required: pip install huggingface_hub"
        ) from exc

    local_dir = Path(args.local_dir).resolve()
    local_dir.mkdir(parents=True, exist_ok=True)

    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
    print(f"repo_id={args.repo_id}")
    print(f"endpoint={endpoint}")
    print(f"local_dir={local_dir}")

    snapshot_download(
        repo_id=args.repo_id,
        repo_type="dataset",
        local_dir=str(local_dir),
        token=os.environ.get("HF_TOKEN"),
    )
    print("download complete")

    if not args.no_manifest:
        gen = REPO_ROOT / "scripts" / "generate_scenario_directories_json.py"
        print(f"regenerating manifest via {gen} ...")
        subprocess.run(
            [sys.executable, str(gen), "--data-dir", str(local_dir), "--pretty"],
            check=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
