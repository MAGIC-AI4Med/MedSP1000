"""
Action-category remap v2: 两层结构（L1 = 5+1 宏观维度 / L2 = 12+2 子项）。

L1 / L2 树形结构：
  1. 评估 Assessment
     1.1 病史采集
     1.2 体格检查与监护识别
  2. 诊断推理 Diagnostic Reasoning
     2.1 检查申请
     2.2 检查解读
     2.3 鉴别诊断与临床推理
  3. 治疗与处置 Management & Disposition
     3.1 用药/液体/氧
     3.2 技术性操作
     3.3 其他治疗决策
     3.4 会诊与处置去向
  4. 安全把关 Safety
     4.1 禁忌与危险动作      (反向打分: true 含义需逐项核实)
  5. 沟通与协作 Communication & Collaboration
     5.1 患者沟通
     5.2 团队协作
  -- 旁路：不进入临床能力平均
  0. 非临床元任务 Non-Clinical Meta
     0.1 教育/CCC 评估元任务
     0.2 平台/流程/文书操作
"""

from __future__ import annotations
import re

# ---------------- L1 / L2 标签与层级关系 ----------------

L1_CLINICAL = ["评估", "诊断推理", "治疗与处置", "安全把关", "沟通与协作"]
L1_EXCLUDED = "非临床元任务"
L1_ALL = L1_CLINICAL + [L1_EXCLUDED]

L2_OF_L1 = {
    "评估":           ["病史采集", "体格检查与监护识别"],
    "诊断推理":       ["检查申请", "检查解读", "鉴别诊断与临床推理"],
    "治疗与处置":     ["用药/液体/氧", "技术性操作", "其他治疗决策", "会诊与处置去向"],
    "安全把关":       ["禁忌与危险动作"],
    "沟通与协作":     ["患者沟通", "团队协作"],
    "非临床元任务":   ["教育/CCC评估元任务", "平台/流程/文书操作"],
}
L1_OF: dict[str, str] = {l2: l1 for l1, l2s in L2_OF_L1.items() for l2 in l2s}
L2_ALL = [l2 for l2s in L2_OF_L1.values() for l2 in l2s]

REVERSE_POLARITY_L2 = {"禁忌与危险动作"}


# ---------------- 1. 8 个原主类 → L2（除 病史采集与查体 与 其他类别） ----------------

PRIMARY_TO_L2: dict[str, str] = {
    "监护与状态识别": "体格检查与监护识别",
    "检查申请":       "检查申请",
    "检查解读":       "检查解读",
    "鉴别诊断更新":   "鉴别诊断与临床推理",
    "会诊与收治去向": "会诊与处置去向",
    "禁忌或危险动作": "禁忌与危险动作",
    # "病史采集与查体" → 按 item 关键词拆 病史采集 / 体格检查与监护识别
    # "治疗或干预"     → 按 item 关键词拆 用药/液体/氧 / 技术性操作 / 其他治疗决策
}


# ---------------- 2. 病史采集与查体 → 病史采集 / 体格检查与监护识别 ----------------

PE_KW = [
    r"查体", r"体格检查", r"听诊", r"触诊", r"视诊", r"叩诊",
    r"inspection", r"palpation", r"auscultat", r"percussion",
    r"测体温", r"测温", r"血压", r"脉搏", r"心率", r"呼吸频率",
    r"\bHR\b", r"\bBP\b", r"\bRR\b", r"SpO2", r"SaO2", r"血氧",
    r"vital sign", r"生命体征",
    r"反射", r"reflex", r"瞳孔", r"pupil",
    r"GCS", r"NIHSS",
    r"腹部查", r"胸部查", r"心脏查", r"肺部查", r"口腔查", r"皮肤查",
    r"abdominal exam", r"cardiac exam", r"pulmonary exam",
    r"neurological exam", r"neuro exam", r"physical exam",
    r"perform .*examination", r"examines? the patient",
    r"observe", r"观察",
]
PE_RE = re.compile("|".join(PE_KW), re.IGNORECASE)


def _split_history_or_pe(item: str) -> str:
    if PE_RE.search(item or ""):
        return "体格检查与监护识别"
    return "病史采集"


# ---------------- 3. 治疗或干预 → 3.1 / 3.2 / 3.3 拆分 ----------------

# 3.2 技术性操作（**优先匹配**：插管/穿刺/CPR/导管 等明确的 procedural 动作）
PROCEDURE_KW = [
    r"插管", r"intubat", r"extubat", r"拔管",
    r"CPR", r"心肺复苏", r"胸外按压", r"compression",
    r"除颤", r"defibril", r"cardiovers", r"心脏复律",
    r"穿刺", r"置管", r"catheter", r"导尿", r"中心静脉",
    r"central line", r"central venous", r"venous access", r"静脉通路", r"建立 ?IV",
    r"血气", r"\bABG\b",
    r"缝合", r"suture", r"切开", r"incision", r"清创",
    r"引流", r"drain", r"chest tube", r"\bLP\b", r"lumbar puncture", r"腰穿",
    r"airway management", r"气道", r"紧急气道",
    r"球囊", r"BMV", r"AMBU", r"bag.?mask", r"球囊面罩",
    r"intraosseous", r"\bIO\b", r"骨内通路",
    r"ET tube", r"ETT", r"气管导管",
    r"抽吸", r"suction",
    r"复苏(?!药)",  # 复苏 alone but not 复苏药
    r"操作步骤", r"操作完成度",
    r"颈托", r"骨折固定", r"夹板", r"splint", r"reduction", r"复位",
    r"包扎", r"压迫止血",
]
PROCEDURE_RE = re.compile("|".join(PROCEDURE_KW), re.IGNORECASE)

# 3.1 用药/液体/氧（药物给予、液体复苏、给氧、输血、疫苗）
MED_KW = [
    # 给药动作
    r"给予", r"administer", r"给药", r"静推", r"肌注", r"皮下", r"\bIV push\b",
    r"\bIV\b(?! line| access)", r"\bIM\b", r"\bSC\b", r"\bPO\b", r"oral",
    # 剂量单位
    r"\bmg\b", r"\bmcg\b", r"\bmg/kg\b", r"\bIU\b", r"\bunits?\b", r"\bmL\b",
    # 液体
    r"输液", r"补液", r"晶体", r"胶体", r"saline", r"\bNS\b", r"normal saline",
    r"林格", r"lactated ringer", r"\bLR\b", r"\bIVF\b", r"fluid resuscitation",
    r"输血", r"transfusion", r"血制品", r"\bPRBC\b", r"FFP", r"platelet",
    # 给氧
    r"给氧", r"oxygen", r"\bO2\b", r"鼻导管", r"nasal cannula", r"面罩(?!.*气道)",
    r"non-?rebreather", r"\bNRB\b", r"BiPAP", r"CPAP", r"NIV",
    # 通用药名/类药
    r"antibiotic", r"抗生素", r"insulin", r"胰岛素",
    r"epinephrine", r"肾上腺素", r"atropine", r"阿托品",
    r"naloxone", r"纳洛酮", r"glucose", r"葡萄糖",
    r"morphine", r"吗啡", r"fentanyl", r"芬太尼",
    r"lorazepam", r"midazolam", r"咪达唑仑", r"地西泮",
    r"镇痛", r"analgesic", r"镇静", r"sedat",
    r"thrombolytic", r"溶栓", r"\btPA\b", r"alteplase",
    r"拉贝洛尔", r"labetalol", r"nicardipine", r"hydralazine",
    r"vaccine", r"vaccinat", r"immunization", r"疫苗", r"接种",
    r"steroid", r"激素", r"hydrocortisone",
    r"heparin", r"肝素", r"warfarin",
    r"bronchodilat", r"albuterol", r"沙丁胺醇",
    r"diuretic", r"利尿", r"furosemide", r"呋塞米",
]
MED_RE = re.compile("|".join(MED_KW), re.IGNORECASE)


def _split_treatment(item: str) -> str:
    """治疗或干预 → 用药/液体/氧 / 技术性操作 / 其他治疗决策"""
    s = item or ""
    if PROCEDURE_RE.search(s):
        return "技术性操作"
    if MED_RE.search(s):
        return "用药/液体/氧"
    return "其他治疗决策"


# ---------------- 4. 其他类别 子类名 → L2（含教育元任务 vs 平台元任务的区分） ----------------

SUBCATEGORY_TO_L2: dict[str, str] = {
    # ---- 患者沟通 ----
    "知情同意与沟通":     "患者沟通",
    "沟通与坏消息告知":   "患者沟通",
    "沟通与医患关系":     "患者沟通",
    "沟通与告知":         "患者沟通",
    "沟通与文化胜任力":   "患者沟通",
    "坏消息告知与沟通":   "患者沟通",
    "沟通表现":           "患者沟通",
    "沟通姿态与非语言行为":"患者沟通",
    "沟通与人际技能":     "患者沟通",
    "沟通与关系建立":     "患者沟通",
    "沟通与同意":         "患者沟通",
    "宣教与沟通":         "患者沟通",
    "沟通与解释":         "患者沟通",
    "宣教":               "患者沟通",
    "宣教与解释":         "患者沟通",
    "沟通与专业性":       "患者沟通",
    "沟通与职业素养":     "患者沟通",
    "职业素养与医患沟通": "患者沟通",
    "沟通与专业表现":     "患者沟通",
    "沟通与职业表现":     "患者沟通",
    "核心咨询回应与决策": "患者沟通",
    "作答聚焦与决策框架": "患者沟通",
    "咨询聚焦与决策表达": "患者沟通",
    "作答聚焦与最终建议": "患者沟通",
    "答题聚焦与信息使用": "患者沟通",
    "关键探针":           "患者沟通",
    "题干信息应用":       "患者沟通",
    "作答质量与题干信息使用": "患者沟通",

    # ---- 团队协作 ----
    "团队协作与沟通":     "团队协作",
    "团队协作":           "团队协作",
    "沟通与团队协作":     "团队协作",
    "团队协作与领导":     "团队协作",
    "沟通与团队表现":     "团队协作",
    "团队沟通与协作":     "团队协作",
    "团队资源管理":       "团队协作",
    "团队组织与分工":     "团队协作",
    "团队准备与沟通":     "团队协作",
    "团队协作与资源管理": "团队协作",

    # ---- 折回 治疗与处置（之后再按 item 二次拆分到 3.1/3.2/3.3） ----
    "未执行的治疗选项":         "__治疗占位__",
    "治疗决策与用药实施":       "__治疗占位__",
    "预防咨询与指南表述":       "__治疗占位__",
    "预防接种咨询与推荐":       "__治疗占位__",
    "操作识别与并发症讨论":     "__治疗占位__",
    "操作完成度判定":           "__治疗占位__",
    "操作结果未完成":           "__治疗占位__",
    "操作关键步骤完成度":       "__治疗占位__",
    "关键动作顺序":             "__治疗占位__",
    "预防建议与适应证判断":     "__治疗占位__",
    "教学目标与技能操作":       "__治疗占位__",

    # ---- 折回 检查申请 ----
    "未执行的检查申请":   "检查申请",
    "检查申请细化":       "检查申请",
    "检查申请":           "检查申请",

    # ---- 折回 检查解读 ----
    "检查解读细化":       "检查解读",

    # ---- 折回 病史采集 ----
    "自杀风险评估":       "病史采集",
    "筛查建议":           "病史采集",

    # ---- 折回 鉴别诊断 ----
    "最终诊断":           "鉴别诊断与临床推理",

    # ---- 折回 会诊与处置去向 ----
    "会诊与收治":         "会诊与处置去向",
    "处置去向":           "会诊与处置去向",

    # ---- 教育/CCC 元任务 ----
    "教育项目结构与评估认知":     "教育/CCC评估元任务",
    "医学教育项目分析与CCC评估":  "教育/CCC评估元任务",
    "医学教育项目评估与CCC运行":  "教育/CCC评估元任务",
    "教育测量任务完成情况":       "教育/CCC评估元任务",
    "评估材料完整性与可评分性":   "教育/CCC评估元任务",
    "学习改进与系统实践":         "教育/CCC评估元任务",
    "复盘理解目标":               "教育/CCC评估元任务",
    "课程参与与反馈":             "教育/CCC评估元任务",
    "评分边界":                   "教育/CCC评估元任务",
    "反思与学习":                 "教育/CCC评估元任务",
    "学习与反思目标":             "教育/CCC评估元任务",
    "综合表现条目":               "教育/CCC评估元任务",
    "知识性学习目标":             "教育/CCC评估元任务",
    "综合目标":                   "教育/CCC评估元任务",
    "评估框架目标":               "教育/CCC评估元任务",
    "教学讨论":                   "教育/CCC评估元任务",
    "知识讨论":                   "教育/CCC评估元任务",

    # ---- 平台/流程/文书 元任务 ----
    "平台流程与病例文书完成度":   "平台/流程/文书操作",
    "流程与时间管理":             "平台/流程/文书操作",
    "模拟导航与界面使用":         "平台/流程/文书操作",
    "仿真导航与任务执行":         "平台/流程/文书操作",
    "场景导航与任务流程":         "平台/流程/文书操作",
    "文书记录":                   "平台/流程/文书操作",
    "场景导航与系统交互":         "平台/流程/文书操作",
    "时间流程指标":               "平台/流程/文书操作",
    "评分表中重复归属项目":       "平台/流程/文书操作",
    "文书与专业协作":             "平台/流程/文书操作",
}


# ---------------- 主入口 ----------------

def to_l2(category: str, subcategory: str, item: str) -> str:
    """返回 L2 名（14 个之一）。"""
    if category == "其他类别":
        target = SUBCATEGORY_TO_L2.get(subcategory or "", "平台/流程/文书操作")
        if target == "__治疗占位__":
            return _split_treatment(item)
        return target
    if category == "病史采集与查体":
        return _split_history_or_pe(item)
    if category == "治疗或干预":
        return _split_treatment(item)
    return PRIMARY_TO_L2.get(category, "平台/流程/文书操作")


def to_l1(l2: str) -> str:
    return L1_OF[l2]


def remap(category: str, subcategory: str, item: str) -> tuple[str, str]:
    """返回 (l1, l2)。"""
    l2 = to_l2(category, subcategory, item)
    return L1_OF[l2], l2


def remap_with_provenance(category: str, subcategory: str, item: str):
    """返回 (l1, l2, provenance) — provenance 解释命中规则。"""
    if category == "其他类别":
        if subcategory in SUBCATEGORY_TO_L2:
            target = SUBCATEGORY_TO_L2[subcategory]
            if target == "__治疗占位__":
                l2 = _split_treatment(item)
                return L1_OF[l2], l2, "other_to_treatment_then_split"
            return L1_OF[target], target, "other_explicit"
        return L1_OF["平台/流程/文书操作"], "平台/流程/文书操作", "other_unmapped_default"
    if category == "病史采集与查体":
        l2 = _split_history_or_pe(item)
        prov = "pe_keyword" if l2 == "体格检查与监护识别" else "history_default"
        return L1_OF[l2], l2, prov
    if category == "治疗或干预":
        l2 = _split_treatment(item)
        if l2 == "技术性操作":
            prov = "treatment_procedure_kw"
        elif l2 == "用药/液体/氧":
            prov = "treatment_med_kw"
        else:
            prov = "treatment_default"
        return L1_OF[l2], l2, prov
    if category in PRIMARY_TO_L2:
        l2 = PRIMARY_TO_L2[category]
        return L1_OF[l2], l2, "primary"
    return L1_OF["平台/流程/文书操作"], "平台/流程/文书操作", "unknown_primary"


# ============================================================
# v3 ACGME 6 Core Competencies (Holmboe 2020 / Milestones 2.0)
# ------------------------------------------------------------
# L1 = 6 ACGME 大类（PC / MK / SBP / ICS / PBLI / PROF）
# L2 = 92 个原始类别节点（8 主类 + 84 个 其他类别 子类；
#      `检查申请` 主类 与 `其他类别/检查申请` 子类同名合并为同一 L2）
# 与 v1/v2 完全独立、静态查表、无 item-keyword 路由。
# 详见 z-stat-figure/action_taxonomy_v3.md
# ============================================================

ACGME_L1_CN = {
    "PC":   "Patient Care & Procedural Skills",
    "MK":   "Medical Knowledge",
    "SBP":  "Systems-Based Practice",
    "ICS":  "Interpersonal & Communication Skills",
    "PBLI": "Practice-Based Learning & Improvement",
    "PROF": "Professionalism",
}
ACGME_L1_ALL = list(ACGME_L1_CN.keys())  # ["PC","MK","SBP","ICS","PBLI","PROF"]

# 8 主类 → ACGME L1（L2 = 主类名本身）
V3_MAIN_TO_L1: dict[str, str] = {
    "病史采集与查体":   "PC",
    "监护与状态识别":   "PC",
    "检查申请":         "PC",
    "治疗或干预":       "PC",
    "检查解读":         "MK",
    "鉴别诊断更新":     "MK",
    "会诊与收治去向":   "SBP",
    "禁忌或危险动作":   "SBP",
}

# `其他类别` 85 个子类 → (L1, L2)
# L2 = 子类名本身（除 `检查申请` 同名子类合并到主类 L2）
V3_SUB_TO_L1_L2: dict[str, tuple[str, str]] = {
    # ---- PC 子类（13 项；外加同名合并 检查申请） ----
    "未执行的治疗选项":         ("PC", "未执行的治疗选项"),
    "自杀风险评估":             ("PC", "自杀风险评估"),
    "未执行的检查申请":         ("PC", "未执行的检查申请"),
    "治疗决策与用药实施":       ("PC", "治疗决策与用药实施"),
    "预防咨询与指南表述":       ("PC", "预防咨询与指南表述"),
    "预防接种咨询与推荐":       ("PC", "预防接种咨询与推荐"),
    "检查申请":                 ("PC", "检查申请"),  # 同名合并到主类 L2
    "检查申请细化":             ("PC", "检查申请细化"),
    "操作识别与并发症讨论":     ("PC", "操作识别与并发症讨论"),
    "操作完成度判定":           ("PC", "操作完成度判定"),
    "操作结果未完成":           ("PC", "操作结果未完成"),
    "操作关键步骤完成度":       ("PC", "操作关键步骤完成度"),
    "关键动作顺序":             ("PC", "关键动作顺序"),
    "预防建议与适应证判断":     ("PC", "预防建议与适应证判断"),
    "筛查建议":                 ("PC", "筛查建议"),
    "教学目标与技能操作":       ("PC", "教学目标与技能操作"),

    # ---- MK 子类（2 项） ----
    "检查解读细化":             ("MK", "检查解读细化"),
    "最终诊断":                 ("MK", "最终诊断"),

    # ---- SBP 子类（12 项） ----
    "平台流程与病例文书完成度": ("SBP", "平台流程与病例文书完成度"),
    "流程与时间管理":           ("SBP", "流程与时间管理"),
    "文书与专业协作":           ("SBP", "文书与专业协作"),
    "模拟导航与界面使用":       ("SBP", "模拟导航与界面使用"),
    "场景导航与任务流程":       ("SBP", "场景导航与任务流程"),
    "仿真导航与任务执行":       ("SBP", "仿真导航与任务执行"),
    "文书记录":                 ("SBP", "文书记录"),
    "场景导航与系统交互":       ("SBP", "场景导航与系统交互"),
    "时间流程指标":             ("SBP", "时间流程指标"),
    "会诊与收治":               ("SBP", "会诊与收治"),
    "处置去向":                 ("SBP", "处置去向"),
    "评分表中重复归属项目":     ("SBP", "评分表中重复归属项目"),

    # ---- ICS 患者沟通子群（23 项） ----
    "知情同意与沟通":           ("ICS", "知情同意与沟通"),
    "沟通与坏消息告知":         ("ICS", "沟通与坏消息告知"),
    "沟通与医患关系":           ("ICS", "沟通与医患关系"),
    "沟通与告知":               ("ICS", "沟通与告知"),
    "沟通与文化胜任力":         ("ICS", "沟通与文化胜任力"),
    "坏消息告知与沟通":         ("ICS", "坏消息告知与沟通"),
    "沟通表现":                 ("ICS", "沟通表现"),
    "沟通与人际技能":           ("ICS", "沟通与人际技能"),
    "沟通姿态与非语言行为":     ("ICS", "沟通姿态与非语言行为"),
    "沟通与关系建立":           ("ICS", "沟通与关系建立"),
    "宣教与沟通":               ("ICS", "宣教与沟通"),
    "沟通与同意":               ("ICS", "沟通与同意"),
    "沟通与解释":               ("ICS", "沟通与解释"),
    "核心咨询回应与决策":       ("ICS", "核心咨询回应与决策"),
    "作答聚焦与决策框架":       ("ICS", "作答聚焦与决策框架"),
    "关键探针":                 ("ICS", "关键探针"),
    "作答聚焦与最终建议":       ("ICS", "作答聚焦与最终建议"),
    "咨询聚焦与决策表达":       ("ICS", "咨询聚焦与决策表达"),
    "答题聚焦与信息使用":       ("ICS", "答题聚焦与信息使用"),
    "题干信息应用":             ("ICS", "题干信息应用"),
    "作答质量与题干信息使用":   ("ICS", "作答质量与题干信息使用"),
    "宣教":                     ("ICS", "宣教"),
    "宣教与解释":               ("ICS", "宣教与解释"),

    # ---- ICS 团队协作子群（10 项） ----
    "团队协作与沟通":           ("ICS", "团队协作与沟通"),
    "团队协作":                 ("ICS", "团队协作"),
    "沟通与团队协作":           ("ICS", "沟通与团队协作"),
    "团队协作与领导":           ("ICS", "团队协作与领导"),
    "沟通与团队表现":           ("ICS", "沟通与团队表现"),
    "团队沟通与协作":           ("ICS", "团队沟通与协作"),
    "团队资源管理":             ("ICS", "团队资源管理"),
    "团队组织与分工":           ("ICS", "团队组织与分工"),
    "团队准备与沟通":           ("ICS", "团队准备与沟通"),
    "团队协作与资源管理":       ("ICS", "团队协作与资源管理"),

    # ---- PBLI 子类（17 项） ----
    "教育项目结构与评估认知":   ("PBLI", "教育项目结构与评估认知"),
    "医学教育项目分析与CCC评估":("PBLI", "医学教育项目分析与CCC评估"),
    "医学教育项目评估与CCC运行":("PBLI", "医学教育项目评估与CCC运行"),
    "教育测量任务完成情况":     ("PBLI", "教育测量任务完成情况"),
    "评估材料完整性与可评分性": ("PBLI", "评估材料完整性与可评分性"),
    "学习改进与系统实践":       ("PBLI", "学习改进与系统实践"),
    "复盘理解目标":             ("PBLI", "复盘理解目标"),
    "课程参与与反馈":           ("PBLI", "课程参与与反馈"),
    "评分边界":                 ("PBLI", "评分边界"),
    "反思与学习":               ("PBLI", "反思与学习"),
    "学习与反思目标":           ("PBLI", "学习与反思目标"),
    "综合表现条目":             ("PBLI", "综合表现条目"),
    "知识性学习目标":           ("PBLI", "知识性学习目标"),
    "综合目标":                 ("PBLI", "综合目标"),
    "评估框架目标":             ("PBLI", "评估框架目标"),
    "教学讨论":                 ("PBLI", "教学讨论"),
    "知识讨论":                 ("PBLI", "知识讨论"),

    # ---- PROF 子类（5 项） ----
    "沟通与职业素养":           ("PROF", "沟通与职业素养"),
    "职业素养与医患沟通":       ("PROF", "职业素养与医患沟通"),
    "沟通与专业表现":           ("PROF", "沟通与专业表现"),
    "沟通与专业性":             ("PROF", "沟通与专业性"),
    "沟通与职业表现":           ("PROF", "沟通与职业表现"),
}


def remap_acgme(category: str, subcategory: str = "") -> tuple[str, str]:
    """v3：返回 (l1_acgme_code, l2_name)。

    - category ∈ 8 主类：返回 (V3_MAIN_TO_L1[category], category)
    - category == "其他类别"：按 subcategory 静态查表
    - 未知输入：raise KeyError（v3 设计上覆盖率 100%，不应出现兜底）
    """
    sub = subcategory or ""
    if category == "其他类别":
        if sub in V3_SUB_TO_L1_L2:
            return V3_SUB_TO_L1_L2[sub]
        raise KeyError(f"v3 未识别的 其他类别 子类: {sub!r}")
    if category in V3_MAIN_TO_L1:
        return V3_MAIN_TO_L1[category], category
    raise KeyError(f"v3 未识别的主类: {category!r}")


def to_acgme_l1(category: str, subcategory: str = "") -> str:
    """v3：仅返回 ACGME L1 代号（PC/MK/SBP/ICS/PBLI/PROF）。"""
    return remap_acgme(category, subcategory)[0]


def to_acgme_l2(category: str, subcategory: str = "") -> str:
    """v3：仅返回 L2 节点名（92 个之一）。"""
    return remap_acgme(category, subcategory)[1]
