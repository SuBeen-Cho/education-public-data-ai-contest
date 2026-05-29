"""
priority_scorer.py — 학교별 우선순위 점수 산출
v4 공식: S = α×max(★) + Σ(카테고리별 가중치) + γ×R
카테고리별 가중치 차등화: 학생안전(3) > 데이터급변·연동(2) > 통계참고(1)
"""

import pandas as pd

ALPHA = 3  # ★ 등급 가중치
GAMMA = 5  # 3년 반복 가중치

# 카테고리별 가중치 (맥락적 심각도 반영)
CATEGORY_WEIGHT = {
    "C3": 3,  # 학생 안전 (미조치 피해) — 가장 심각
    "B1": 2,  # 전년 대비 급변동 (진학률 -35%p 등)
    "C1": 2,  # 학생·자원 연동 (항목 간 모순)
    "C2": 2,  # 학생·재정 연동 (급식비 비리 사례)
    "F1": 2,  # 교차 불일치 (시스템 간)
    "C5": 1,  # 코호트 단절 (확인 필요하나 상대적 경미)
    "D2": 1,  # 동료군 편차 (통계적 참고 수준)
    "E2": 1,  # 미갱신 의심 (복붙 의심이지 확정 아님)
    "E1": 1,  # 반복 공백
    "F3": 1,  # 동일지표 불일치
    "G1": 1,  # 장기 추세 점검 (단년 급변 X, 누적 드리프트 — 참고)
}


def extract_category(rule_id: str) -> str:
    """룰 ID에서 카테고리 추출 (C3-3A → C3, B1-5 → B1, F1'-1 → F1)"""
    base = rule_id.split("-")[0]
    return base.rstrip("'")


def calculate_priority_scores(detections_df: pd.DataFrame) -> pd.DataFrame:
    """
    탐지 결과 DataFrame → 학교별 우선순위 점수

    v4 공식: S = α×max(★) + Σ(카테고리별 가중치) + γ×R
    - α×max(★): 최고 별 등급 × 3
    - Σ(카테고리별 가중치): C3=3, B1/C1/C2/F1=2, D2/E2/C5=1 합산
    - γ×R: 3년 반복 시 +5
    """
    if detections_df.empty:
        return pd.DataFrame(columns=[
            "school_code", "school_name", "score", "max_star",
            "num_categories", "categories", "is_repeat",
            "num_detections", "rank", "cat_weight_sum"
        ])

    scores = []
    for school_code, group in detections_df.groupby("school_code"):
        school_name = group["school_name"].iloc[0]

        max_star = int(group["star"].max())

        categories = set(group["rule_id"].apply(extract_category))
        num_categories = len(categories)

        # 카테고리별 가중치 합산
        cat_weight_sum = sum(CATEGORY_WEIGHT.get(cat, 1) for cat in categories)

        # 3년 반복
        years_by_rule = group.groupby("rule_id")["year"].nunique()
        is_repeat = int(years_by_rule.max() >= 3) if not years_by_rule.empty else 0

        # v4 점수
        score = (ALPHA * max_star
                 + 2 * cat_weight_sum
                 + GAMMA * is_repeat)

        scores.append({
            "school_code": school_code,
            "school_name": school_name,
            "score": score,
            "max_star": max_star,
            "num_categories": num_categories,
            "categories": ", ".join(sorted(categories)),
            "cat_weight_sum": cat_weight_sum,
            "is_repeat": bool(is_repeat),
            "num_detections": len(group),
        })

    result = pd.DataFrame(scores)
    result = result.sort_values("score", ascending=False).reset_index(drop=True)
    result["rank"] = range(1, len(result) + 1)
    return result


def get_top_n(scores_df: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    return scores_df.head(n)


def get_score_distribution(scores_df: pd.DataFrame) -> dict:
    if scores_df.empty:
        return {"21-25": 0, "16-20": 0, "11-15": 0, "6-10": 0, "0": 0}
    return {
        "21-25": len(scores_df[scores_df["score"] >= 21]),
        "16-20": len(scores_df[(scores_df["score"] >= 16) & (scores_df["score"] < 21)]),
        "11-15": len(scores_df[(scores_df["score"] >= 11) & (scores_df["score"] < 16)]),
        "6-10": len(scores_df[(scores_df["score"] >= 6) & (scores_df["score"] < 11)]),
        "0": len(scores_df[scores_df["score"] < 6]),
    }
