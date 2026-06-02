# <Paper Title>

[![Paper](https://img.shields.io/badge/paper-arXiv-b31b1b.svg)](https://arxiv.org/abs/XXXX.XXXXX)
[![Project Page](https://img.shields.io/badge/project-page-blue.svg)](https://your-project-page.github.io)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

This repository is the official implementation of
[**<Paper Title>**](https://arxiv.org/abs/XXXX.XXXXX) (`<Venue Year>`).

> 📋 *One-paragraph summary of the paper. For this work: a multi-agent
> simulation framework for evaluating clinical reasoning in medical LLMs.
> Each case is run as an interactive encounter between four role agents —
> **examinee**, **standardized patient (SP)**, **environment controller**,
> and **evaluator** — and scored against a frozen, ACGME-aligned rubric
> (PC / MK / SBP / ICS / PBLI / PROF). Replace this with your abstract.*

<p align="center">
  <img src="assets/teaser.png" width="720" alt="Framework overview"/>
</p>

---

## Overview

- **<N> scenarios** across **<M> cases**, each with materials for four role agents.
- **Frozen unified rubric** for cross-model comparability (scores are computed
  over a fixed item set, not re-extracted per run).
- Benchmarked across **<K> examinee models** under a fixed SP/environment/evaluator setup.

```
paper-release/
├── src/        # core simulation + evaluation code
├── configs/    # model / pipeline configuration files
├── scripts/    # data-generation, run, and analysis entry points
├── docs/       # extended documentation
└── assets/     # figures used in the paper / README
```

## Requirements

To set up the environment:

```setup
conda create -n medeval python=3.10
conda activate medeval
pip install -r requirements.txt
```

> 📋 *List any external dependencies that are not pip-installable (model
> weights, API keys, cluster/vLLM setup) and where to obtain them. Set API
> credentials via environment variables, e.g. `OPENAI_API_KEY`,
> `DEEPSEEK_API_KEY`, `MEDGEMMA_BASE_URL`.*

## Data

> 📋 *Describe how to obtain the case/scenario data and the expected layout.
> If the data is released separately (e.g. object storage / HuggingFace),
> link it here. Each scenario directory holds the four role-agent materials.*

```data
# Example: regenerate the scenario manifest
python scripts/generate_scenario_directories_json.py --pretty
```

## Running the Benchmark

To run the simulation-based evaluation on the benchmark:

```eval
bash scripts/run_simulate_cases.sh \
    --case-file scenario_directories_full.json \
    --examinee-model <MODEL_NAME> \
    --sp-env-eval-model <JUDGE_MODEL> \
    -j <CONCURRENCY>
```

> 📋 *Describe each flag and the expected output directory layout (per-run
> transcripts, agent logs, and `final_evaluation_frozen_rubric.json`).
> Runs are idempotent and resumable via status markers.*

To re-score existing runs against the frozen rubric:

```rescore
python scripts/rerun_evaluator.py --run-dir <RUN_DIR>
```

## Pre-trained / Released Models

> 📋 *If you release any model weights or adapters, link them here with the
> exact config used to produce the reported numbers. For API-only models,
> list the exact model IDs and decoding settings instead.*

| Model        | Source            | Notes                          |
| ------------ | ----------------- | ------------------------------ |
| `<model-id>` | `<provider/link>` | `<temperature / thinking off>` |

## Results

Our framework yields the following results on the benchmark
(scores = mean fraction of rubric items satisfied, frozen unified rubric):

| Examinee Model | Overall | PC | MK | SBP | ICS | PBLI | PROF |
| -------------- | ------- | -- | -- | --- | --- | ---- | ---- |
| `<model A>`    | `<x.xx>`| .. | .. | ..  | ..  | ..   | ..   |
| `<model B>`    | `<x.xx>`| .. | .. | ..  | ..  | ..   | ..   |

> 📋 *Include the main table(s) from the paper and a one-line note on how to
> reproduce them (which script regenerates the table/figure).*

## Citation

If you find this work useful, please cite:

```bibtex
@article{<key>,
  title   = {<Paper Title>},
  author  = {<Authors>},
  journal = {<Venue>},
  year    = {<Year>},
  url     = {https://arxiv.org/abs/XXXX.XXXXX}
}
```

## License

This project is released under the terms of the [LICENSE](LICENSE) file.

## Acknowledgements

> 📋 *Credit datasets, frameworks, and prior work you build on
> (e.g. ACGME competency framework, MedAgents).*
