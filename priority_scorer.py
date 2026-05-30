"""
priority_scorer.py — 학교별 종합 이상치 점수 (v4 점수체계, 2026-05-30 전환).

★ 등급제 → 점수제 전환. 두 단위로 점수가 매겨진다:
  · s_r (탐지 건별)   = w_d × min(2, m_r)              범위 0~10
  · S_school (학교별) = 0.6·V + 0.2·C + 0.2·R          범위 0~100

학교 종합 점수의 3요소:
  V (값)   : 대분류별 감쇠 후 합산.
             각 대분류 안에서 최고 s_r 1건은 100% 반영, 나머지는 ×0.3.
             V = min(100, (Σ_대분류[max(s_r) + 0.3·Σ나머지]) / 40 × 100)
  C (구조) : 탐지된 대분류 수 / 9 × 100.
             대분류 9개 = C1·C2·C3·B1·D2·E·C5·F1'·G1 (E는 E1+E2 통합).
  R (반복) : 같은 룰이 3년 연속 탐지되면 100, 아니면 0.

라벨 (사용자 노출, 0~100):
  75~100 = critical  · 50~75 = major  · 25~50 = minor  · 0~25 = warning
"""

import ast
import pandas as pd
import numpy as np

from rule_engine import RULE_META


# ── 대분류 9개 (v4 점수체계) ──
# E1·E2는 단일 대분류 "E"로 통합. F1은 노출 시 F1'.
BIG_CATEGORY_MAP = {
    "C1": "C1", "C2": "C2", "C3": "C3", "B1": "B1",
    "D2": "D2", "E1": "E", "E2": "E",
    "C5": "C5", "F1": "F1'", "G1": "G1",
}
BIG_CATEGORY_COUNT = 9        # C 분모 (E1+E2 통합 기준)
V_NORMALIZER = 40.0           # V 정규화 분모 (감쇠 후 합산을 0~100으로 환산)
DAMP_RATIO = 0.3              # 같은 대분류 내 2번째+ 건 감쇠 계수
S_CAP = 10.0                  # s_r 상한
M_CAP = 2.0                   # m_r 상한
WEIGHT_V, WEIGHT_C, WEIGHT_R = 0.6, 0.2, 0.2

# 사용자 노출 라벨 임계 (0~100)
LABEL_THRESHOLDS = {
    "critical": 75,
    "major":    50,
    "minor":    25,
}


# ── 소분류·대분류 추출 ──
def extract_category(rule_id: str) -> str:
    """C3-3A → C3, B1-5 → B1, F1'-1 → F1. (앱과 동일 규약 유지)"""
    base = str(rule_id).split("-")[0]
    return base.rstrip("'")


def big_category(rule_id_or_small_cat: str) -> str:
    """소분류 코드 또는 룰 ID를 대분류 9개로 매핑."""
    small = extract_category(rule_id_or_small_cat) if "-" in str(rule_id_or_small_cat) else str(rule_id_or_small_cat)
    return BIG_CATEGORY_MAP.get(small, small)


def label_for(score: float) -> str:
    """점수 → 4단계 라벨 코드."""
    s = float(score) if score is not None and not (isinstance(score, float) and np.isnan(score)) else 0.0
    if s >= LABEL_THRESHOLDS["critical"]:
        return "critical"
    if s >= LABEL_THRESHOLDS["major"]:
        return "major"
    if s >= LABEL_THRESHOLDS["minor"]:
        return "minor"
    return "warning"


# ── s_r 계산 (탐지 건별) ──
def _to_float(x, default: float = 0.0) -> float:
    if x is None:
        return default
    try:
        if isinstance(x, str):
            x = x.strip()
            if not x:
                return default
        v = float(x)
        if np.isnan(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def _coerce_values(values):
    """DataFrame 셀에 저장된 values를 dict로 정규화 (legacy str(dict) 대응)."""
    if isinstance(values, dict):
        return values
    if isinstance(values, str) and values:
        try:
            parsed = ast.literal_eval(values)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, SyntaxError):
            pass
    return {}


def compute_s_r(rule_id: str, values) -> float:
    """탐지 건별 이상치 점수 s_r = w_d × min(2, m_r). 범위 0~10."""
    meta = RULE_META.get(str(rule_id), {})
    risk = float(meta.get("risk", 0) or 0)
    if risk <= 0:
        return 0.0

    vals = _coerce_values(values)
    m_type = meta.get("m_type", "fixed")

    if m_type == "fixed":
        m_r = float(meta.get("m_const", 0) or 0)
    elif m_type == "binary":
        src = meta.get("m_source", "")
        size = abs(_to_float(vals.get(src)))
        min_size = float(meta.get("m_min_size", 1) or 1)
        m_r = size / min_size
    elif m_type == "continuous":
        src = meta.get("m_source", "")
        v = abs(_to_float(vals.get(src)))
        thresh = float(meta.get("m_threshold", 1) or 1)
        m_r = (v - thresh) / thresh if v >= thresh else 0.0
    elif m_type == "asymmetric":
        # C5-1: 음수 방향(이탈) / 양수 방향(과잉) 임계가 다름
        src = meta.get("m_source", "")
        v = _to_float(vals.get(src))
        if v < 0:
            thresh = float(meta.get("m_down", 1) or 1)
            m_r = (abs(v) - thresh) / thresh if abs(v) >= thresh else 0.0
        else:
            thresh = float(meta.get("m_up", 1) or 1)
            m_r = (v - thresh) / thresh if v >= thresh else 0.0
    else:
        m_r = 0.0

    m_r = max(0.0, min(M_CAP, m_r))
    return round(min(S_CAP, risk * m_r), 2)


# ── 학교 종합 점수 ──
def _compute_v(s_by_big: dict) -> float:
    """대분류별 감쇠 후 합산 → V 점수 (0~100)."""
    v_raw = 0.0
    for big_cat, s_list in s_by_big.items():
        if not s_list:
            continue
        s_sorted = sorted(s_list, reverse=True)
        v_raw += s_sorted[0] + DAMP_RATIO * sum(s_sorted[1:])
    return min(100.0, (v_raw / V_NORMALIZER) * 100.0)


def _compute_c(big_cats: set) -> float:
    """탐지된 대분류 수 / 9 × 100."""
    return min(100.0, (len(big_cats) / BIG_CATEGORY_COUNT) * 100.0)


def _compute_r(detections_group: pd.DataFrame) -> float:
    """같은 룰이 3개 연도에서 탐지되면 100, 아니면 0."""
    if detections_group.empty or "rule_id" not in detections_group.columns:
        return 0.0
    years_by_rule = detections_group.groupby("rule_id")["year"].nunique()
    if years_by_rule.empty:
        return 0.0
    return 100.0 if int(years_by_rule.max()) >= 3 else 0.0


def calculate_priority_scores(detections_df: pd.DataFrame) -> pd.DataFrame:
    """탐지 DataFrame → 학교별 종합 점수 DataFrame.

    출력 컬럼:
      score (0~100), v_score, c_score, r_score,
      num_categories (소분류 수), categories (소분류 코드 콤마),
      n_big_categories (대분류 수, C 분모용),
      is_repeat, num_detections, max_star (deprecated, 0), cat_weight_sum (deprecated, 0),
      rank.
    """
    out_cols = [
        "school_code", "school_name", "score",
        "v_score", "c_score", "r_score",
        "max_star", "num_categories", "categories",
        "n_big_categories", "cat_weight_sum",
        "is_repeat", "num_detections", "rank",
    ]
    if detections_df.empty:
        return pd.DataFrame(columns=out_cols)

    det = detections_df.copy()
    # s_r 부여
    det["s_r"] = det.apply(
        lambda r: compute_s_r(str(r["rule_id"]), r.get("values", {})), axis=1
    )
    det["_small_cat"] = det["rule_id"].apply(extract_category)
    det["_big_cat"] = det["_small_cat"].apply(big_category)

    scores = []
    for school_code, group in det.groupby("school_code"):
        school_name = group["school_name"].iloc[0]

        # 대분류별 s_r 묶음 (감쇠용)
        s_by_big: dict = {}
        for big_cat, sub in group.groupby("_big_cat"):
            s_by_big[big_cat] = sub["s_r"].tolist()

        v_score = _compute_v(s_by_big)
        big_cats = set(s_by_big.keys())
        c_score = _compute_c(big_cats)
        r_score = _compute_r(group)
        s_school = WEIGHT_V * v_score + WEIGHT_C * c_score + WEIGHT_R * r_score

        small_cats = sorted(set(group["_small_cat"]))
        max_star = int(group["star"].max()) if "star" in group.columns else 0

        scores.append({
            "school_code": str(school_code),
            "school_name": str(school_name),
            "score": round(s_school, 1),
            "v_score": round(v_score, 1),
            "c_score": round(c_score, 1),
            "r_score": round(r_score, 1),
            "max_star": max_star,                              # deprecated, 내부 호환
            "num_categories": len(small_cats),                 # 소분류 수 (기존 의미 유지)
            "categories": ", ".join(small_cats),
            "n_big_categories": len(big_cats),                 # 대분류 수 (C 분모와 일치)
            "cat_weight_sum": 0,                               # deprecated (구 산식 잔존 필드)
            "is_repeat": bool(r_score >= 100.0),
            "num_detections": len(group),
        })

    result = pd.DataFrame(scores)
    result = result.sort_values("score", ascending=False).reset_index(drop=True)
    result["rank"] = range(1, len(result) + 1)
    return result[out_cols]


def get_top_n(scores_df: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    return scores_df.head(n)


def get_score_distribution(scores_df: pd.DataFrame) -> dict:
    """4단계 분포 — critical · major · minor · warning."""
    if scores_df.empty:
        return {"critical": 0, "major": 0, "minor": 0, "warning": 0}
    s = scores_df["score"]
    return {
        "critical": int((s >= LABEL_THRESHOLDS["critical"]).sum()),
        "major":    int(((s >= LABEL_THRESHOLDS["major"]) & (s < LABEL_THRESHOLDS["critical"])).sum()),
        "minor":    int(((s >= LABEL_THRESHOLDS["minor"]) & (s < LABEL_THRESHOLDS["major"])).sum()),
        "warning":  int((s < LABEL_THRESHOLDS["minor"]).sum()),
    }


if __name__ == "__main__":
    # 단독 실행 시 간단한 검증.
    from data_loader import load_and_merge_all
    from rule_engine import RuleEngine

    df = load_and_merge_all()
    engine = RuleEngine(df)
    detections = engine.run_all()
    print(f"\n탐지 {len(detections)}건")
    if not detections.empty:
        det_with_sr = detections.copy()
        det_with_sr["s_r"] = det_with_sr.apply(
            lambda r: compute_s_r(str(r["rule_id"]), r.get("values", {})), axis=1
        )
        # 룰별 s_r 분포
        rule_summary = det_with_sr.groupby("rule_id")["s_r"].agg(["count", "mean", "max"]).round(2)
        print("\n룰별 s_r 분포 (탐지수 / 평균 / 최대):")
        print(rule_summary.sort_values("max", ascending=False).to_string())

    scores = calculate_priority_scores(detections)
    if not scores.empty:
        print(f"\n점수 산출 학교: {len(scores)}교  ·  4단계 분포: {get_score_distribution(scores)}")
        print("\n상위 10교:")
        cols = ["rank", "school_name", "score", "v_score", "c_score", "r_score",
                "n_big_categories", "num_detections", "is_repeat"]
        print(scores[cols].head(10).to_string(index=False))
