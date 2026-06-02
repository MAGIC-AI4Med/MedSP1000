# Release TODO — manual audit checklist

The repo skeleton is populated and **all real secrets are scrubbed** (API keys,
cluster proxy credentials, internal IPs/endpoints). Remaining items below need a
human pass before going public.

## ✅ Done automatically
- Engine, data-processing, rubric, and analysis code copied into `src/` (no `*_backup_*.py`, no viewer/dashboard).
- `configs/config.example.yaml` written with env-var placeholders (real `config.yaml` with keys is **not** copied; `config.yaml` is gitignored — verify).
- Proxy credentials in `scripts/{run_cases,_bench_5model_runner,run_extract_rubric}.sh` replaced with `${HTTP_PROXY:-}`.
- Internal infra details (gateway name, proxy host/IP) redacted from `docs/*_models_notes.md` and script comments.
- `requirements.txt` pinned to the versions in the `mm` env.
- `src/simulate` imports cleanly under the `mm` env; all `src/` `.py` compile.

## ⬜ Needs your manual pass

### 1. Hardcoded absolute paths (`/mnt/petrelfs/liangcheng/...`)
Most are env-overridable defaults (`${MODEL_PATH:-...}`, `${PYTHON_BIN:-...}`) — fine to leave.
**These are hardcoded and should be relativized to the repo root:**
- `scripts/rescore_all_frozen_rubric.sh:38-40` — `ROOT=`, `TM=`, `PY=`
- `scripts/_bench_5model_runner.sh:24` — `cd <abs>`
- `src/analysis/compute_eval_stats.py:36` — `ROOT = Path("<abs>")`
- `src/analysis/compute_bench_5model.py:35,39` — input/output roots
- `src/simulate/controller.py:24` — review the constant on this line
- `src/{dataproc/test_codex.py, rubric/run_codex_extract_rubric.py}` — `CODEX_BIN` default + sandbox path

### 2. Codex Agent SDK (Node.js) dependency
`src/dataproc/` and `src/rubric/` drive the Codex CLI via a Node harness.
`node_modules/` was intentionally excluded. Document the install (`package.json` is
included) or vendor a thin wrapper. Stage (a) data-processing is not reproducible
without it.

### 3. Docs are still Chinese
`docs/PIPELINE.zh.md`, `docs/data_processing.zh.md`, and `docs/simulate.md` are the
original Chinese docs (kept verbatim so nothing is lost). Translate to English
before release, or keep both with an English summary.
- **`docs/simulate.md` has dangling references introduced by the prompt cleanup**:
  it links to the now-deleted `prompts_zh.py`, mentions the deleted
  `build_evaluator_system_prompt_v8_8class_legacy`, and explains how to "switch back
  to the Chinese version". Remove these when translating — the repo no longer ships
  a Chinese prompt or a legacy evaluator.

### 4. README placeholders
`README.md` still has `XXXX.XXXXX` arXiv ids, a placeholder project-page URL, and
`> 📋` editor notes (Table 1, requirements, data layout). Fill before publishing.

### 5. Data release
Raw/intermediate case data is **not** in this repo (goes to the HF dataset
`byrLLCC/MedSP1000`). Confirm the scenario directory layout doc matches the released
dataset, and that `scripts/generate_scenario_directories_json.py` points at the
released layout, not the local `a-case-datas/`.

### 6. Final secret sweep
Re-run before first push:
```bash
grep -rnE "sk-[A-Za-z0-9]{20,}|10\.1\.20\.50|wuchaoyi|key77qiqi|baichuan-ai\.com|pjlab\.org" . | grep -v config.example
```
(currently returns nothing)
