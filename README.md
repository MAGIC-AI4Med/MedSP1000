# MedSP1000

### Evaluating Large Language Models in Dynamic Clinical Decision-Making with Standardized Patient Cases

[![Paper](https://img.shields.io/badge/paper-arXiv-b31b1b.svg)](https://arxiv.org/abs/2606.05112)
[![Dataset](https://img.shields.io/badge/🤗%20HuggingFace-Dataset-yellow.svg)](https://huggingface.co/datasets/byrLLCC/MedSP1000)
[![Project Page](https://img.shields.io/badge/project-page-blue.svg)](https://your-project-page.github.io)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> Cheng Liang\*, Pengcheng Qiu\*, Ya Zhang, Yanfeng Wang, Chaoyi Wu†, Weidi Xie†
> <br/>Shanghai Jiao Tong University · Shanghai Artificial Intelligence Laboratory
> <br/><sub>\* Equal contribution&nbsp;&nbsp;† Corresponding author</sub>

This repository is the official implementation of
[**MedSP1000**](https://arxiv.org/abs/2606.05112).


---

## Quick Start

```bash
# 1. Environment
conda create -n medsp1000 python=3.10 && conda activate medsp1000
pip install -r requirements.txt

# 2. Credentials — placeholders are expanded from the environment at load time,
#    so no secret is ever written into the repo.
cp configs/config.example.yaml configs/config.yaml
export OPENAI_BASE_URL="https://api.openai.com/v1"   # or your gateway
export OPENAI_API_KEY="sk-..."

# 3. Data — downloads scenario materials into data/MedSP1000/ and builds the
#    run manifest. The frozen ACGME rubrics already ship in this repo (rubrics/).
python scripts/download_data.py
#    behind a firewall: HF_ENDPOINT=https://hf-mirror.com python scripts/download_data.py

# 4. Run — closed-loop encounters, scored against the frozen rubric.
bash scripts/run_simulate_cases.sh \
    --examinee-model gpt-5.5 \
    --sp-env-eval-model gpt-5.5 \
    -j 4
```

Each scenario writes `final_evaluation_frozen_rubric.json` (per-item true/false over
the six ACGME competencies) under `runs/`. See [`docs/RUNNING.md`](docs/RUNNING.md)
for the full walkthrough, provider table, smoke tests, and test-time-compute options.

---

## Abstract

Large language models (LLMs) are increasingly proposed as clinical agents, yet
static, single-turn benchmarks cannot capture how a model **dynamically delivers
care across an encounter**: gathering information, planning treatment, and adapting
longitudinal management across successive patient states. Medical education has
long addressed an analogous challenge through **standardized patients (SPs)** —
trained actors who consistently portray clinical cases for safe, quantifiable
assessment.

We introduce **MedSP1000**, an SP-derived interactive benchmark for clinical-agent
evaluation. It converts peer-reviewed SP teaching cases into executable scenarios
with defined patient scripts, clinical environment contexts, and a human-validated
structured rubric. In each run, a **clinician agent** interacts in closed loop with
a **patient agent** and an **environment controller**, and its behaviour is scored
throughout the encounter against expert criteria from the original materials.

Applying MedSP1000 to general-purpose and medically specialized LLMs, we find that
strong static-benchmark performance does **not** reliably transfer to interactive
care: the best model (**GPT-5.5**) completes only **60.4%** of expert-defined rubric
items, the strongest medically specialized model reaches only **40.0%**, and extra
test-time compute produces no measurable gain.

<p align="center">
  <img src="assets/teaser.png" width="900" alt="MedSP1000 overview"/>
</p>

## Highlights

- 🏥 **SP-grounded** — built directly on peer-reviewed [MedEdPORTAL](https://www.mededportal.org/) teaching materials (1,073 source articles, 22,244 attachments).
- 🔁 **Interactive, multi-turn** — closed-loop encounters between a clinician agent, a patient agent, and an environment controller, with a standardized state-transition protocol.
- 📊 **Scale & breadth** — **1,638 interactive cases** across **17 clinical specialties**, scored with **24,602 rubric items**.
- 🧭 **ACGME-aligned scoring** — every action graded against a frozen rubric over the **6 ACGME core competencies** (PC, MK, SBP, ICS, PBLI, PROF).
- 👩‍⚕️ **Human-validated** — cases and trajectories checked by 12 clinicians (each independently double-scored).

## Framework

MedSP1000 has three stages: **(a)** an agentic data-processing pipeline that turns
heterogeneous MedEdPORTAL materials into role-specific scenario packets; **(b)** a
multi-agent evaluation loop over multiple clinical states; and **(c)** an evaluator
agent that scores the full trajectory against the rubric across the six ACGME
competencies.

<p align="center">
  <img src="assets/framework.png" width="900" alt="MedSP1000 framework"/>
</p>

## Results

Performance on static benchmarks does not reliably translate to interactive clinical
care. The strongest general model leads the strongest medically specialized model by
**20.4 points** in overall rubric completion.

<p align="center">
  <img src="assets/results-2.png" width="820" alt="Model performance on MedSP1000"/>
</p>

| Model | Overall rubric completion |
| ----- | ------------------------- |
| GPT-5.5 (best general)              | **60.4%** |
| Best medically specialized model    | 40.0% |

> 📋 *Replace with the full Table 1 (per-competency PC/MK/SBP/ICS/PBLI/PROF, micro/macro, 95% CIs). Per-specialty and test-time-compute results are in `assets/results-3.png` and `assets/tts-sub.png`.*

## Repository Structure

```
paper-release/
├── src/        # core simulation + evaluation code (clinician / patient / environment / evaluator agents)
├── configs/    # config.example.yaml (+ rubric-extraction prompt)
├── scripts/    # download, run, and analysis entry points
├── rubrics/    # frozen ACGME rubrics, one <case>_<scenarioN>.json per scenario
├── data/       # downloaded scenario materials (gitignored; populated by download_data.py)
├── docs/       # extended documentation
└── assets/     # figures
```

## Requirements

```setup
conda create -n medsp1000 python=3.10
conda activate medsp1000
pip install -r requirements.txt
```

**Configure credentials.** Copy the example config and provide your keys via
environment variables — the engine expands `${VAR}` / `${VAR:-default}`
placeholders at load time, so no secret is ever written into the repo:

```bash
cp configs/config.example.yaml configs/config.yaml
export OPENAI_BASE_URL="https://api.openai.com/v1"   # or your gateway
export OPENAI_API_KEY="sk-..."
# optional, only for the providers you use:
#   DEEPSEEK_API_KEY, QWEN_API_KEY, BAICHUAN_API_KEY,
#   MEDGEMMA_BASE_URL (local vLLM), HTTP_PROXY
```

`configs/config.yaml` is gitignored. The frozen ACGME rubrics used for scoring
ship in this repo under `rubrics/` (one `<case>_<scenario>.json` per scenario).

## Data

MedSP1000 is derived from MedEdPORTAL teaching materials and released on the
🤗 Hugging Face Hub:

**👉 [huggingface.co/datasets/byrLLCC/MedSP1000](https://huggingface.co/datasets/byrLLCC/MedSP1000)**

Download the scenario materials into `data/MedSP1000/` and build the run manifest
in one step:

```data
python scripts/download_data.py
# behind a firewall: HF_ENDPOINT=https://hf-mirror.com python scripts/download_data.py
```

This fetches the dataset and regenerates `scenario_directories_full.json` (the
list of scenarios the runner consumes). To rebuild the manifest by itself:

```bash
python scripts/generate_scenario_directories_json.py --pretty
```

**Quick subset (100 scenarios).** Don't want to run the full 1,638-scenario
benchmark? `subset.json` ships a 100-scenario, quality-controlled subset — every
scenario validated for rubric quality and simulation soundness. Expand it into a
manifest and run that instead:

```bash
python scripts/generate_scenario_directories_json.py --subset subset.json --pretty
```

**Layout.** Each scenario holds one folder per role agent; the frozen rubric for
that scenario lives in `rubrics/` in this repo (not in the dataset):

```
data/MedSP1000/<case_id>/<scenarioN>/
  examinee/               # materials visible to the clinician agent
  sp_actor/               # patient (standardized-patient) script
  environment_controller/ # labs / imaging / environment state
  evaluator/              # source scoring materials
rubrics/<case_id>_<scenarioN>.json   # frozen ACGME rubric used for scoring
```

## Running the Benchmark

```eval
bash scripts/run_simulate_cases.sh \
    --examinee-model <CLINICIAN_MODEL> \
    --sp-env-eval-model <JUDGE_MODEL> \
    -j <CONCURRENCY>
```

Defaults: case list `scenario_directories_full.json` (repo root), config
`configs/config.yaml`, logs under `runs/logs/`. Each scenario produces per-turn
transcripts, agent logs, and a `final_evaluation_frozen_rubric.json` scored over
the six ACGME competencies. Runs are idempotent and resumable: a status marker is
written per completed scenario, and re-running skips it.

Key flags (see `--help` for the full list):

| Flag | Purpose |
| ---- | ------- |
| `--examinee-model NAME` | Clinician (model under test). |
| `--sp-env-eval-model NAME` | Sets the SP, environment, and evaluator models together (must match for status markers). |
| `-j, --jobs N` | Concurrent scenarios. |
| `-c, --case-file PATH` | Alternate scenario manifest. |
| `--examinee-tts {off,single,bon,medagents}` | Test-time-compute strategy for the examinee. `off` (default) leaves the path untouched; non-`off` runs land in an isolated status subdir so prior results are never overwritten. |
| `--tts-n / --tts-experts / --tts-consensus-rounds / --tts-selector` | Tune `bon` / `medagents`. |
| `--dry-run` | Print the commands without calling any model. |


## Citation

```bibtex
@misc{liang2026evaluatinglargelanguagemodels,
      title={Evaluating Large Language Models in Dynamic Clinical Decision-Making with Standardized Patient Cases}, 
      author={Cheng Liang and Pengcheng Qiu and Ya Zhang and Yanfeng Wang and Chaoyi Wu and Weidi Xie},
      year={2026},
      eprint={2606.05112},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2606.05112}, 
}
```

## License

Released under the [MIT License](LICENSE).

## Acknowledgements

Source cases are drawn from [MedEdPORTAL](https://www.mededportal.org/). Scoring
follows the [ACGME Core Competencies](https://www.acgme.org/). The test-time-compute
study adapts the [MedAgents](https://github.com/gersteinlab/MedAgents) framework.

## Contact

If you have any question, don't hesitate to contact us!

- [liangcheng1026@sjtu.edu.cn](mailto:liangcheng1026@sjtu.edu.cn)
- [henrychur@sjtu.edu.cn](mailto:henrychur@sjtu.edu.cn)

