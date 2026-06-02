from __future__ import annotations

import os
import re
from pathlib import Path

from .sim_types import CaseDefinition, Document, RoleBundle


ROLE_DIR_ALIASES = {
    "examinee": ["受试者", "examinee"],
    "sp": ["SP", "sp"],
    "environment": ["环境控制", "environment"],
    "evaluator": ["评估者", "evaluator"],
}

ROLE_DIR_VARIANTS = {
    "examinee": ["受试者材料"],
    "sp": ["SP材料", "SP角色", "SP扮演者", "sp_actor"],
    "environment": ["环境控制者", "环境控制材料", "environment_controller"],
    "evaluator": ["评估者资料", "评分标准"],
}

ROLE_DIR_NOISE_TOKENS = ("材料", "资料", "角色")

TEXT_FILE_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".html",
    ".xml",
}

_DIR_NAME_NORMALIZE_PATTERN = re.compile(r"[\s_\-()\[\]{}<>/\\|,:：;；，。·]+")


def _normalize_dir_name(name: str) -> str:
    normalized = _DIR_NAME_NORMALIZE_PATTERN.sub("", name).casefold()
    for token in ROLE_DIR_NOISE_TOKENS:
        normalized = normalized.replace(token.casefold(), "")
    return normalized


def _score_role_dir_name(role_name: str, dir_name: str) -> int:
    aliases = ROLE_DIR_ALIASES[role_name]
    variants = ROLE_DIR_VARIANTS[role_name]
    normalized_name = _normalize_dir_name(dir_name)

    if dir_name in aliases:
        return 400
    if dir_name in variants:
        return 300

    normalized_aliases = {_normalize_dir_name(alias) for alias in aliases}
    normalized_variants = {_normalize_dir_name(variant) for variant in variants}
    if normalized_name in normalized_aliases:
        return 200
    if normalized_name in normalized_variants:
        return 100
    return 0


def _resolve_role_dirs(
    case_root: Path,
    *,
    preset_role_dirs: dict[str, Path] | None = None,
) -> dict[str, Path]:
    resolved_role_dirs = dict(preset_role_dirs or {})
    unresolved_roles = [
        role_name for role_name in ROLE_DIR_ALIASES if role_name not in resolved_role_dirs
    ]
    if not unresolved_roles:
        return resolved_role_dirs

    used_dirs = {path.resolve() for path in resolved_role_dirs.values()}
    available_dirs: list[Path] = []
    for child in sorted(case_root.iterdir()):
        resolved_child = child.resolve()
        if not child.is_dir() or child.name.startswith(".") or resolved_child in used_dirs:
            continue
        available_dirs.append(resolved_child)

    role_candidates: dict[str, list[tuple[int, Path]]] = {role_name: [] for role_name in unresolved_roles}
    for candidate_dir in available_dirs:
        matched_roles: list[tuple[str, int]] = []
        for role_name in unresolved_roles:
            score = _score_role_dir_name(role_name, candidate_dir.name)
            if score <= 0:
                continue
            matched_roles.append((role_name, score))
            role_candidates[role_name].append((score, candidate_dir))
        if len(matched_roles) > 1:
            matched_text = ", ".join(
                f"{role_name}({score})" for role_name, score in sorted(matched_roles)
            )
            raise FileNotFoundError(
                f"目录名存在歧义: {candidate_dir.name} 可匹配多个角色: {matched_text}"
            )

    for role_name in unresolved_roles:
        candidates = sorted(
            role_candidates[role_name],
            key=lambda item: (-item[0], len(item[1].name), item[1].name.casefold()),
        )
        if not candidates:
            available_names = [path.name for path in available_dirs]
            raise FileNotFoundError(
                f"未找到角色目录: {role_name} (case_root={case_root}, available_dirs={available_names})"
            )
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            candidate_names = [path.name for _, path in candidates]
            raise FileNotFoundError(
                f"角色目录存在多个同优先级候选: {role_name} -> {candidate_names}"
            )
        resolved_role_dirs[role_name] = candidates[0][1]

    return resolved_role_dirs


def _iter_text_files(role_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(role_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(role_dir).parts):
            continue
        if path.suffix.lower() not in TEXT_FILE_SUFFIXES and path.suffix:
            continue
        files.append(path)
    return files


def _load_role_bundle(role_name: str, role_dir: Path) -> RoleBundle:
    files = _iter_text_files(role_dir)
    if not files:
        raise FileNotFoundError(f"角色目录中没有可读取的文本材料: {role_dir}")
    documents = [
        Document(path=path, content=path.read_text(encoding="utf-8")) for path in files
    ]
    return RoleBundle(name=role_name, visible_files=files, documents=documents)


def _infer_initial_minutes_since_ingestion(role_bundles: dict[str, RoleBundle]) -> int:
    candidate_values: list[int] = []
    patterns = [
        re.compile(r"(?:服|吃|摄入)[^\n]{0,20}?(\d+)\s*分钟[前后]"),
        re.compile(r"(\d+)\s*分钟[前后][^\n]{0,20}?(?:服|吃|摄入)"),
    ]
    for bundle in role_bundles.values():
        text = bundle.compiled_text(strip_strikethrough=True)
        for pattern in patterns:
            for match in pattern.finditer(text):
                try:
                    value = int(match.group(1))
                except (TypeError, ValueError):
                    continue
                if 0 <= value <= 24 * 60:
                    candidate_values.append(value)
    if candidate_values:
        return min(candidate_values)
    return 0


def _infer_initial_patient_status(role_bundles: dict[str, RoleBundle]) -> str:
    preferred_roles = ["examinee", "sp", "environment"]
    snippets: list[str] = []
    for role_name in preferred_roles:
        bundle = role_bundles.get(role_name)
        if bundle is None:
            continue
        text = bundle.compiled_text(strip_strikethrough=True)
        for keyword in ["焦虑", "无急性", "生命体征", "背痛", "主诉"]:
            if keyword in text and keyword not in snippets:
                snippets.append(keyword)
    if not snippets:
        return "未评估"
    if "焦虑" in snippets and "无急性" in snippets:
        return "焦虑外观，但无急性危重表现"
    if "背痛" in snippets:
        return "主诉背痛，待进一步评估"
    return "未评估"


def load_case(
    *,
    case_root: str | Path | None = None,
    examinee_dir: str | Path | None = None,
    sp_dir: str | Path | None = None,
    environment_dir: str | Path | None = None,
    evaluator_dir: str | Path | None = None,
) -> CaseDefinition:
    explicit_dirs = {
        "examinee": examinee_dir,
        "sp": sp_dir,
        "environment": environment_dir,
        "evaluator": evaluator_dir,
    }

    resolved_case_root = Path(case_root).resolve() if case_root is not None else None
    role_dirs: dict[str, Path] = {}
    for role_name, explicit_dir in explicit_dirs.items():
        if explicit_dir is not None:
            role_dirs[role_name] = Path(explicit_dir).resolve()
    if resolved_case_root is not None:
        role_dirs = _resolve_role_dirs(
            resolved_case_root,
            preset_role_dirs=role_dirs,
        )

    missing_roles = [role_name for role_name in ROLE_DIR_ALIASES if role_name not in role_dirs]
    if missing_roles:
        missing_text = ", ".join(missing_roles)
        raise FileNotFoundError(
            f"缺少角色目录: {missing_text}。请传入 --case-root 或四个角色目录。"
        )

    if resolved_case_root is None:
        resolved_case_root = Path(
            os.path.commonpath([str(path) for path in role_dirs.values()])
        )

    source_scenario_root: Path | None = None
    source_case_root: Path = resolved_case_root
    scenario_id = ""
    scenario_title = ""

    if resolved_case_root.name.lower().startswith("scenario"):
        source_scenario_root = resolved_case_root
        source_case_root = resolved_case_root.parent
        scenario_id = resolved_case_root.name
        scenario_title = resolved_case_root.name

    role_bundles = {
        role_name: _load_role_bundle(role_name, role_dir)
        for role_name, role_dir in role_dirs.items()
    }

    return CaseDefinition(
        case_root=resolved_case_root,
        case_id=source_case_root.name,
        case_title=source_case_root.name,
        role_bundles=role_bundles,
        role_dirs=role_dirs,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        source_case_root=source_case_root,
        source_scenario_root=source_scenario_root,
        runtime_hints={
            "initial_minutes_since_encounter": 0,
            "initial_minutes_since_ingestion": _infer_initial_minutes_since_ingestion(role_bundles),
            "initial_stage": "initial_assessment",
            "initial_patient_status": _infer_initial_patient_status(role_bundles),
        },
    )
