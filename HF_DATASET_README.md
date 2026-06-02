---
license: mit
language:
- en
task_categories:
- text-generation
- question-answering
tags:
- medical
- clinical
- healthcare
- standardized-patients
- agent
- interactive-evaluation
- benchmark
- acgme
pretty_name: MedSP1000
size_categories:
- 1K<n<10K
---

# MedSP1000

### Evaluating Large Language Models in Dynamic Clinical Decision-Making with Standardized Patient Cases

[![Paper](https://img.shields.io/badge/paper-arXiv-b31b1b.svg)](https://arxiv.org/abs/XXXX.XXXXX)
[![Code](https://img.shields.io/badge/GitHub-code-181717.svg)](https://github.com/your-org/MedSP1000)

> 🚧 **The dataset is being prepared and will be available within one week (by June 9, 2026).**
> This page currently describes the dataset; the downloadable files will be uploaded shortly. Thanks for your patience!

---

## Dataset Summary

**MedSP1000** is a standardized-patient (SP)–derived **interactive** benchmark for evaluating large
language models as clinical agents. Unlike static, single-turn medical QA, each item is an executable
**multi-turn encounter**: a clinician agent interacts in a closed loop with a patient agent and an
environment controller across successive patient states, and its behaviour is scored throughout the
encounter against an expert-defined, human-validated rubric.

The benchmark is built directly on peer-reviewed [MedEdPORTAL](https://www.mededportal.org/) SP
teaching materials, converting heterogeneous source articles into role-specific scenario packets via
an agentic data-processing pipeline.

## Highlights

- 🏥 **SP-grounded** — derived from peer-reviewed [MedEdPORTAL](https://www.mededportal.org/) teaching materials.
- 🔁 **Interactive, multi-turn** — closed-loop encounters between a clinician agent, a patient agent, and an environment controller, with a standardized state-transition protocol.
- 📊 **Scale & breadth** — **1,638 interactive cases** across **17 clinical specialties**, scored with **24,602 rubric items**.
- 🧭 **ACGME-aligned scoring** — every action graded against a frozen rubric over the **6 ACGME core competencies** (PC, MK, SBP, ICS, PBLI, PROF).
- 👩‍⚕️ **Human-validated** — cases and trajectories checked by clinicians with independent double-scoring.

## Dataset Structure

> 📋 *Field schema and per-scenario layout will be finalized with the data upload. Each scenario
> packet holds the materials for the four role agents (clinician / patient / environment / evaluator)
> plus the frozen ACGME rubric used for scoring.*

```python
from datasets import load_dataset

ds = load_dataset("byrLLCC/MedSP1000")
```

## Source & Provenance

Source cases are drawn from [MedEdPORTAL](https://www.mededportal.org/). Each released case carries a
traceable mapping back to its original MedEdPORTAL article (standard DOI link of the form
`https://www.mededportal.org/doi/10.15766/mep_2374-8265.<id>`) for citation and verification.

## Scoring

Scoring follows the [ACGME Core Competencies](https://www.acgme.org/). Each clinician action is graded
against a frozen rubric over the six competencies — Patient Care (PC), Medical Knowledge (MK),
Systems-Based Practice (SBP), Interpersonal & Communication Skills (ICS), Practice-Based Learning &
Improvement (PBLI), and Professionalism (PROF). A run's score is the fraction of expert-defined rubric
items completed across the full trajectory.

## Citation

```bibtex
@article{liang2026medsp1000,
  title   = {MedSP1000: Evaluating Large Language Models in Dynamic Clinical Decision-Making with Standardized Patient Cases},
  author  = {Liang, Cheng and Qiu, Pengcheng and Zhang, Ya and Wang, Yanfeng and Wu, Chaoyi and Xie, Weidi},
  journal = {<Venue>},
  year    = {2026},
  url     = {https://arxiv.org/abs/XXXX.XXXXX}
}
```

## License

Released under the MIT License.

## Acknowledgements

Source cases are drawn from [MedEdPORTAL](https://www.mededportal.org/). Scoring follows the
[ACGME Core Competencies](https://www.acgme.org/).
