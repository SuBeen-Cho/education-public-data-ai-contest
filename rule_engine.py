"""
rule_engine.py — 룰셋 v4 Python 구현 (2026-05-30 점수제 전환).

· 룰별 상태 메타: active(실행 가능) / needs_mapping(컬럼 매핑 확인 필요) /
  no_source_confirmed(원천 범위에서 확인 불가)
· "오류·이상치" 단정 X. 검토 신호·확인 필요 어조 유지.
· 점수 메타 (v4): risk(w_d 위험도) + m_type(연속·이진·고정·비대칭) + m_* 파라미터.
  스코어러(priority_scorer)가 RULE_META를 읽어 s_r = w_d × min(2, m_r)를 계산.
· star(별 등급)은 내부 호환용 잔존. 사용자 노출 금지, 점수 계산에 미사용.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass


# ── 룰 메타 (구현/매핑/사용 컬럼/카테고리/점수 메타) ──
# status: 'active' | 'needs_mapping' | 'no_source_confirmed'
#
# 점수 메타 (v4):
#   risk      : w_d (5 · 3 · 2 · 0). 0이면 점수 미산정(비활성).
#   m_type    : 'continuous' | 'binary' | 'fixed' | 'asymmetric'
#   m_const   : fixed 전용 — 고정 m_r 상수 (0~2).
#   m_threshold: continuous 전용 — 임계값. m_r = (|values[m_source]| - thresh)/thresh.
#   m_min_size: binary 전용 — 최소 규모. m_r = |values[m_source]| / min_size.
#   m_source  : continuous/binary/asymmetric — 탐지 dict 안 값 키.
#   m_down/m_up: asymmetric (C5-1) — 음수/양수 방향 비대칭 임계.
RULE_META = {
    # C1 학생·자원 연동 (위험도 3)
    "C1-1":  {"category": "C1", "name": "학생↔학급 역방향 변동",       "star": 3, "status": "active",
              "cols": ["student_count", "class_count"],
              "risk": 3, "m_type": "binary",     "m_source": "class_diff",   "m_min_size": 2},
    "C1-2":  {"category": "C1", "name": "학생↔학급 완만 역방향 변동",   "star": 2, "status": "active",
              "cols": ["student_count", "class_count"],
              "risk": 3, "m_type": "fixed",      "m_const": 0.5},  # 학급 1개=최소 단위
    "C1-3":  {"category": "C1", "name": "학생↔교원 불균형",            "star": 2, "status": "active",
              "cols": ["student_count_yoy", "teacher_no_inst_yoy", "teacher_count_no_instructor"],
              "risk": 3, "m_type": "continuous", "m_source": "teacher_yoy",  "m_threshold": 10},
    "C1-4":  {"category": "C1", "name": "학급↔교원 불균형",            "star": 1, "status": "active",
              "cols": ["class_count", "teacher_count_no_instructor"],
              "risk": 3, "m_type": "binary",     "m_source": "teacher_diff", "m_min_size": 5},
    "C1-5":  {"category": "C1", "name": "학생↔보직교사 불균형",         "star": 1, "status": "inactive",
              "cols": ["student_count_yoy", "head_teacher_count"],
              "risk": 0, "m_type": "fixed",      "m_const": 0,
              "mapping_note": "v4: 정의서·코드 조건 불일치 — 점수 산정·탐지 모두 보류(inactive). 전국/확장 시 활성 예정. 코드는 잔존."},
    "C1-7":  {"category": "C1", "name": "교원1인당학생수 급변",         "star": 1, "status": "active",
              "cols": ["students_per_teacher"],
              "risk": 3, "m_type": "continuous", "m_source": "yoy",          "m_threshold": 20},
    "C1-8":  {"category": "C1", "name": "학급당학생수 급변",            "star": 3, "status": "active",
              "cols": ["students_per_class"],
              "risk": 3, "m_type": "continuous", "m_source": "diff",         "m_threshold": 1.5},
    # C3 미조치 피해 (위험도 5)
    "C3-3A": {"category": "C3", "name": "미조치 피해 (강력)",          "star": 3, "status": "active",
              "cols": ["bullying_victims", "bullying_protection", "bullying_discipline"],
              "risk": 5, "m_type": "binary",     "m_source": "victims",      "m_min_size": 3},
    "C3-3B": {"category": "C3", "name": "미조치 피해 (참고)",          "star": 2, "status": "active",
              "cols": ["bullying_victims", "bullying_protection", "bullying_discipline"],
              "risk": 5, "m_type": "binary",     "m_source": "victims",      "m_min_size": 3},
    # B1 전년 대비 급변동 (위험도 3)
    "B1-1":  {"category": "B1", "name": "학생·학급·교원 급변동(이중)",  "star": 2, "status": "active",
              "cols": ["student_count", "class_count", "teacher_count"],
              "risk": 3, "m_type": "continuous", "m_source": "yoy",          "m_threshold": 10},
    "B1-2":  {"category": "B1", "name": "학생·학급·교원 급변동(단년)",  "star": 2, "status": "active",
              "cols": ["student_count", "class_count", "teacher_count"],
              "risk": 3, "m_type": "continuous", "m_source": "yoy",          "m_threshold": 10},
    "B1-3":  {"category": "B1", "name": "학교회계 변동",               "star": 2, "status": "active",
              "cols": ["budget_revenue", "budget_expense"],
              "risk": 3, "m_type": "continuous", "m_source": "yoy",          "m_threshold": 30},
    "B1-4":  {"category": "B1", "name": "학교회계 강한 변동",           "star": 3, "status": "active",
              "cols": ["budget_revenue", "budget_expense"],
              "risk": 3, "m_type": "continuous", "m_source": "yoy",          "m_threshold": 50},
    "B1-5":  {"category": "B1", "name": "진학률 급변동",               "star": 2, "status": "active",
              "cols": ["graduation_rate"],
              "risk": 3, "m_type": "continuous", "m_source": "diff_pp",      "m_threshold": 15},
    "B1-6":  {"category": "B1", "name": "학폭 심의 급증",              "star": 3, "status": "active",
              "cols": ["bullying_cases"],
              "risk": 3, "m_type": "binary",     "m_source": "curr",         "m_min_size": 5},
    # D2 유사학교 대비 (위험도 2)
    "D2-1":  {"category": "D2", "name": "유사학교 대비 상하위 10%",    "star": 2, "status": "active",
              "cols": ["students_per_class", "students_per_teacher", "meal_cost_per_student"],
              "risk": 2, "m_type": "fixed",      "m_const": 0.8},
    "D2-2":  {"category": "D2", "name": "유사학교 대비 극단값",        "star": 3, "status": "active",
              "cols": ["students_per_class", "students_per_teacher", "meal_cost_per_student"],
              "risk": 2, "m_type": "fixed",      "m_const": 1.0},
    # C2 학생·재정 연동 (위험도 3)
    "C2-3":  {"category": "C2", "name": "급식비 변동",                 "star": 2, "status": "active",
              "cols": ["meal_cost_total", "student_count"],
              "risk": 3, "m_type": "continuous", "m_source": "meal_yoy",     "m_threshold": 10},
    "C2-3+": {"category": "C2", "name": "급식비 강한 변동",            "star": 3, "status": "active",
              "cols": ["meal_cost_total", "student_count"],
              "risk": 3, "m_type": "continuous", "m_source": "meal_yoy",     "m_threshold": 30},
    # E2 수치 미갱신 (위험도 2, E 대분류로 통합)
    "E2-2":  {"category": "E2", "name": "3년 동일값 반복",             "star": 2, "status": "active",
              "cols": ["student_count", "teacher_count", "class_count", "meal_cost_per_student", "meal_cost_total", "students_per_class", "students_per_teacher", "graduation_rate", "budget_revenue", "budget_expense"],
              "risk": 2, "m_type": "fixed",      "m_const": 0.8},
    # E1 누락 (위험도 2)
    "E1-1":  {"category": "E1", "name": "3년 연속 미입력",             "star": 2, "status": "active",
              "cols": ["fc_changing_room", "fc_shower", "fc_cafeteria", "fc_dorm", "fc_av_room", "fc_computer_room"],
              "risk": 2, "m_type": "fixed",      "m_const": 0.5},
    "E1-2":  {"category": "E1", "name": "단독 미입력 (동료군 다 입력)", "star": 3, "status": "active",
              "cols": ["fc_changing_room", "fc_shower", "fc_cafeteria", "fc_dorm", "fc_av_room", "fc_computer_room"],
              "risk": 2, "m_type": "fixed",      "m_const": 1.0},
    "E1-3":  {"category": "E1", "name": "공시 의무 항목 미제출",       "star": 2, "status": "needs_mapping",
              "cols": [],
              "risk": 2, "m_type": "fixed",      "m_const": 1.0,
              "mapping_note": "학교 유형별 공시 의무 항목 매핑 테이블이 원천 범위에서 단일 확정 불가. 현재 수집 범위에서 '의무 vs 선택' 구분 컬럼 확인 필요."},
    # C5 학년 진급 (위험도 2, 비대칭 임계)
    "C5-1":  {"category": "C5", "name": "진급 시 학생 이탈",           "star": 3, "status": "active",
              "cols": ["grade1_students", "grade2_students"],
              "risk": 2, "m_type": "asymmetric", "m_source": "rate",
              "m_down": 7, "m_up": 3},
    # F1' 교차 불일치 (위험도 3)
    "F1'-1": {"category": "F1", "name": "교원수 교차 불일치",          "star": 2, "status": "active",
              "cols": ["teacher_count_no_instructor", "kess_teacher_total"],
              "risk": 3, "m_type": "binary",     "m_source": "diff",         "m_min_size": 3},
    # G1 장기 추세 점검 (위험도 2, 신규) — v4 산식: m_r = 변동폭 / 전체평균변동폭
    "G1-1":  {"category": "G1", "name": "다년 단조 추세",              "star": 2, "status": "active",
              "cols": ["student_count", "teacher_count", "class_count", "students_per_class",
                       "bullying_cases", "graduation_rate", "meal_cost_per_student"],
              "risk": 2, "m_type": "ratio",      "m_source": "m_ratio"},
    "G1-2":  {"category": "G1", "name": "추세 급변동",                  "star": 2, "status": "active",
              "cols": ["student_count", "teacher_count", "class_count", "students_per_class",
                       "bullying_cases", "graduation_rate", "meal_cost_per_student"],
              "risk": 2, "m_type": "ratio",      "m_source": "m_ratio"},
}


# ── G1 중복 제거용: 기존 룰이 같은 학교+같은 지표를 이미 잡고 있으면 G1에서 제외 ──
# detection.values["col_key"]가 명시적으로 있는 룰은 그 키와 직접 비교.
# 명시적 col_key 없이 단일 지표를 보는 룰은 아래 표로 보강.
G1_INDICATORS = (
    "student_count", "teacher_count", "class_count", "students_per_class",
    "bullying_cases", "graduation_rate", "meal_cost_per_student",
)
EXISTING_RULE_INDICATORS = {
    "B1-5": {"graduation_rate"},
    "B1-6": {"bullying_cases"},
    "C1-1": {"class_count"},
    "C1-2": {"class_count"},
    "C1-3": {"teacher_count"},   # 강사 제외 교원, 의미상 동일 지표군
    "C1-4": {"teacher_count"},
    "C1-8": {"students_per_class"},
    "C2-3":  {"meal_cost_per_student"},  # 급식비 변동은 1인당 급식비 추세와 의미적으로 겹침
    "C2-3+": {"meal_cost_per_student"},
}


@dataclass
class Detection:
    school_code: str
    school_name: str
    year: int
    rule_id: str
    rule_name: str
    star: int
    category: str
    detail: str
    values: dict


class RuleEngine:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.detections: list[Detection] = []

    # ── 실행기 ──
    def run_all(self) -> pd.DataFrame:
        self.detections = []

        # C1
        self._check_c1_1()    # 학생↔학급 역방향 (2학급 이상)
        self._check_c1_2()    # 학생↔학급 완만 역방향 (1학급)
        self._check_c1_3()    # 학생↔교원 불균형 (강사 제외)
        self._check_c1_4()    # 학급↔교원 불균형
        # C1-5는 RULE_META status에 따라 호출 (v4 비활성 — 메타 단일 출처)
        if RULE_META.get("C1-5", {}).get("status") == "active":
            self._check_c1_5()
        self._check_c1_7()    # 교원1인당학생수 급변
        self._check_c1_8()    # 학급당학생수 급변

        # C3
        self._check_c3_3()    # 미조치 피해 (선도조치 + 반복 승격)

        # B1
        self._check_b1_1_b1_2()  # 학생·학급·교원 ±10% 이중조건/단년
        self._check_b1_3_b1_4()  # 학교회계 세입·세출 ±30%/±50%
        self._check_b1_5()       # 진학률 급변동
        self._check_b1_6()       # 학폭 심의 급증

        # D2
        self._check_d2_1()    # 유사학교 백분위 10%/90%
        self._check_d2_2()    # IQR 또는 중앙값 50% 외부

        # C2
        self._check_c2_3()    # 급식비 변동

        # E1 (시설 미입력 패턴)
        self._check_e1_1()    # 3년 연속 미입력
        self._check_e1_2()    # 단독 미입력

        # E2
        self._check_e2_2()    # 3년 동일값 반복 (확장 필터)

        # C5
        self._check_c5_1()    # 진급 시 학생 이탈 (학년별)

        # F1'
        self._check_f1_prime()  # 교원수 교차 불일치

        # G1 — 장기 추세 (반드시 다른 룰 이후에 호출 — 중복 제거 로직이 기존 탐지 집합을 참조)
        self._check_g1()      # 다년 단조 추세(G1-1) + 추세 급변동(G1-2)

        return self._to_dataframe()

    def _to_dataframe(self) -> pd.DataFrame:
        if not self.detections:
            return pd.DataFrame(columns=[
                "school_code", "school_name", "year", "rule_id",
                "rule_name", "star", "category", "detail", "values"
            ])
        return pd.DataFrame([{
            "school_code": d.school_code, "school_name": d.school_name,
            "year": d.year, "rule_id": d.rule_id, "rule_name": d.rule_name,
            "star": d.star, "category": d.category,
            # values는 dict 그대로 보관 (스코어러가 m_r 산식에 사용).
            "detail": d.detail, "values": d.values if isinstance(d.values, dict) else {},
            # 동적 컬럼 매핑용 — values dict에 col_key/col_label이 있으면 노출 (E2-2 등 다중 컬럼 룰)
            "col_key": d.values.get("col_key", "") if isinstance(d.values, dict) else "",
            "col_label": d.values.get("col_label", "") if isinstance(d.values, dict) else "",
        } for d in self.detections])

    def _add(self, row, rule_id, rule_name, star, category, detail, values):
        self.detections.append(Detection(
            school_code=str(row.get("school_code", "")),
            school_name=str(row.get("school_name", "")),
            year=int(row.get("year", 0)),
            rule_id=rule_id, rule_name=rule_name,
            star=star, category=category,
            detail=detail, values=values,
        ))

    # ────────────────────────────────────────────────
    # C1 — 학생·자원 연동
    # ────────────────────────────────────────────────

    def _check_c1_1(self):
        """C1-1: 방향 반대 + 학급수 2학급 이상 변동"""
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            for i in range(1, len(group)):
                curr = group.iloc[i]; prev = group.iloc[i - 1]
                s_yoy = curr.get("student_count_yoy", np.nan)
                c_yoy = curr.get("class_count_yoy", np.nan)
                if pd.isna(s_yoy) or pd.isna(c_yoy):
                    continue
                c_curr = curr.get("class_count", 0); c_prev = prev.get("class_count", 0)
                class_diff = abs(c_curr - c_prev) if (pd.notna(c_curr) and pd.notna(c_prev)) else 0
                if s_yoy * c_yoy < 0 and class_diff >= 2:
                    self._add(curr, "C1-1", "학생↔학급 역방향 변동", 3, "C1",
                              f"학생수 {s_yoy:+.1f}% / 학급수 {c_yoy:+.1f}% (학급 {int(class_diff)}개 변동)",
                              {"student_yoy": round(s_yoy, 1), "class_yoy": round(c_yoy, 1), "class_diff": int(class_diff)})

    def _check_c1_2(self):
        """C1-2: 방향 반대 + 학급수 변동 1학급 (완만 역방향)"""
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            for i in range(1, len(group)):
                curr = group.iloc[i]; prev = group.iloc[i - 1]
                s_yoy = curr.get("student_count_yoy", np.nan)
                c_yoy = curr.get("class_count_yoy", np.nan)
                if pd.isna(s_yoy) or pd.isna(c_yoy):
                    continue
                c_curr = curr.get("class_count", 0); c_prev = prev.get("class_count", 0)
                class_diff = abs(c_curr - c_prev) if (pd.notna(c_curr) and pd.notna(c_prev)) else 0
                if s_yoy * c_yoy < 0 and class_diff == 1:
                    self._add(curr, "C1-2", "학생↔학급 완만 역방향 변동", 2, "C1",
                              f"학생수 {s_yoy:+.1f}% / 학급수 {c_yoy:+.1f}% (학급 1개 변동)",
                              {"student_yoy": round(s_yoy, 1), "class_yoy": round(c_yoy, 1), "class_diff": 1})

    def _check_c1_3(self):
        """C1-3: 학생수 5% 이내 + (강사 제외 교원수 10% 이상 또는 5명 이상 변동)"""
        t_yoy_col = "teacher_no_inst_yoy"
        t_abs_col = "teacher_count_no_instructor"
        if t_yoy_col not in self.df.columns:
            return
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            for i in range(1, len(group)):
                curr = group.iloc[i]; prev = group.iloc[i - 1]
                s = curr.get("student_count_yoy", np.nan)
                t = curr.get(t_yoy_col, np.nan)
                if pd.isna(s) or pd.isna(t):
                    continue
                t_curr = curr.get(t_abs_col, np.nan); t_prev = prev.get(t_abs_col, np.nan)
                t_diff = abs(t_curr - t_prev) if (pd.notna(t_curr) and pd.notna(t_prev)) else 0
                if abs(s) <= 5 and (abs(t) >= 10 or t_diff >= 5):
                    self._add(curr, "C1-3", "학생↔교원 불균형", 2, "C1",
                              f"학생수 {s:+.1f}%(안정) 교원수(강사제외) {t:+.1f}% ({int(t_diff)}명 변동)",
                              {"student_yoy": round(s, 1), "teacher_yoy": round(t, 1), "teacher_diff": int(t_diff)})

    def _check_c1_4(self):
        """C1-4: 학급수 변동 0~1학급 + 강사 제외 교원수 5명 이상 변동"""
        t_abs_col = "teacher_count_no_instructor"
        if t_abs_col not in self.df.columns:
            return
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            for i in range(1, len(group)):
                curr = group.iloc[i]; prev = group.iloc[i - 1]
                c_curr = curr.get("class_count", np.nan); c_prev = prev.get("class_count", np.nan)
                t_curr = curr.get(t_abs_col, np.nan); t_prev = prev.get(t_abs_col, np.nan)
                if pd.isna(c_curr) or pd.isna(c_prev) or pd.isna(t_curr) or pd.isna(t_prev):
                    continue
                class_diff = abs(c_curr - c_prev)
                t_diff = abs(t_curr - t_prev)
                if class_diff <= 1 and t_diff >= 5:
                    self._add(curr, "C1-4", "학급↔교원 불균형", 1, "C1",
                              f"학급수 {int(class_diff)}개 변동(안정) 교원수(강사제외) {int(t_diff)}명 변동",
                              {"class_diff": int(class_diff), "teacher_diff": int(t_diff)})

    def _check_c1_5(self):
        """C1-5: 학생수 변동 5% 이내 + 보직교사 변동 2명 이상"""
        head_col = "head_teacher_count"
        if head_col not in self.df.columns:
            return
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            for i in range(1, len(group)):
                curr = group.iloc[i]; prev = group.iloc[i - 1]
                s_yoy = curr.get("student_count_yoy", np.nan)
                h_curr = curr.get(head_col, np.nan); h_prev = prev.get(head_col, np.nan)
                if pd.isna(s_yoy) or pd.isna(h_curr) or pd.isna(h_prev):
                    continue
                h_diff = abs(h_curr - h_prev)
                if abs(s_yoy) <= 5 and h_diff >= 2:
                    self._add(curr, "C1-5", "학생↔보직교사 불균형", 1, "C1",
                              f"학생수 {s_yoy:+.1f}%(안정) 보직교사 {int(h_curr)}명({int(h_curr - h_prev):+d}명)",
                              {"student_yoy": round(s_yoy, 1), "head_prev": int(h_prev), "head_curr": int(h_curr), "diff": int(h_diff)})

    def _check_c1_7(self):
        """C1-7: 교원1인당학생수 YoY 20% 이상 변동"""
        col = "students_per_teacher"
        if col not in self.df.columns:
            return
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            vals = group[col].values
            years = group["year"].values
            for i in range(1, len(vals)):
                if pd.isna(vals[i]) or pd.isna(vals[i - 1]) or vals[i - 1] == 0:
                    continue
                yoy = (vals[i] - vals[i - 1]) / vals[i - 1] * 100
                if abs(yoy) >= 20:
                    self._add(group.iloc[i], "C1-7", "교원1인당학생수 급변", 1, "C1",
                              f"교원1인당학생수 {vals[i-1]:.1f}→{vals[i]:.1f} ({yoy:+.1f}%)",
                              {"prev": round(float(vals[i-1]), 1), "curr": round(float(vals[i]), 1), "yoy": round(float(yoy), 1)})

    def _check_c1_8(self):
        """C1-8: 학급당학생수 ±1.5명/학급 변동"""
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            vals = group["students_per_class"].values
            for i in range(1, len(vals)):
                if pd.isna(vals[i]) or pd.isna(vals[i - 1]):
                    continue
                diff = vals[i] - vals[i - 1]
                if abs(diff) >= 1.5:
                    self._add(group.iloc[i], "C1-8", "학급당학생수 급변", 3, "C1",
                              f"학급당학생수 {diff:+.1f}명 ({vals[i-1]:.1f}→{vals[i]:.1f})",
                              {"prev": round(float(vals[i-1]), 1), "curr": round(float(vals[i]), 1), "diff": round(float(diff), 1)})

    # ────────────────────────────────────────────────
    # C3 — 미조치 피해
    # ────────────────────────────────────────────────

    def _check_c3_3(self):
        """C3-3A/B: 피해학생 >0 + 보호조치 0 + 선도조치 건수 >0 (가해 측 선도조치 수행 확인).
        등급A: 피해 3명 이상 / 등급B: 1~2명. 동일 학교 3회 이상 누적이면 B→A 승격."""
        # 안전 가드: 선도조치 컬럼은 C3-3 전제. 없으면 0 대체 대신 명시적으로 드러내야 한다.
        if "bullying_discipline" not in self.df.columns:
            raise RuntimeError(
                "C3-3 룰 전제 컬럼 'bullying_discipline'(선도교육조치 건수)가 데이터에 없습니다. "
                "data_loader.BULLY_COLS의 discipline_1·discipline_2 매핑 확인 필요."
            )
        school_flags = {}  # school_code → list of (year, idx-in-self.detections)
        for _, row in self.df.iterrows():
            v = int(row.get("bullying_victims", 0) or 0)
            p = int(row.get("bullying_protection", 0) or 0)
            disc = int(row.get("bullying_discipline", 0) or 0)
            if v > 0 and p == 0:
                if disc == 0:
                    continue  # 등급X 자체해결/이월 추정 (가해 측 선도조치도 없음)
                if v >= 3:
                    grade, star = "A", 3
                else:
                    grade, star = "B", 2
                self._add(row, f"C3-3{grade}", "미조치 피해", star, "C3",
                          f"피해학생 {v}명 / 보호조치 {p}건 / 선도조치 {disc}건",
                          {"victims": v, "protection": p, "discipline": disc})
                sc = str(row.get("school_code", ""))
                school_flags.setdefault(sc, []).append(len(self.detections) - 1)

        # 동일 학교 3회 이상이면 모든 B를 A로 승격
        for sc, idxs in school_flags.items():
            if len(idxs) >= 3:
                for k in idxs:
                    d = self.detections[k]
                    if d.rule_id == "C3-3B":
                        self.detections[k] = Detection(
                            school_code=d.school_code, school_name=d.school_name,
                            year=d.year, rule_id="C3-3A", rule_name="미조치 피해 (반복 승격)",
                            star=3, category="C3", detail=d.detail + " · 3회 이상 반복 누적 승격",
                            values={**d.values, "repeat_promote": True}
                        )

    # ────────────────────────────────────────────────
    # B1 — 전년 대비 급변동
    # ────────────────────────────────────────────────

    def _check_b1_1_b1_2(self):
        """B1-1: 10% 이상 + 직전 2년 변동 대비 3배 (3년 시계열 필요).
        B1-2: 10% 이상 (시계열 부족 또는 3배 미만의 단년 변동).
        대상: 학생수·학급수·교원수."""
        targets = [("student_count", "학생수"), ("class_count", "학급수"), ("teacher_count", "교원수")]
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            group = group.sort_values("year").reset_index(drop=True)
            for col, name in targets:
                if col not in group.columns:
                    continue
                vals = group[col].values
                for i in range(1, len(vals)):
                    if pd.isna(vals[i]) or pd.isna(vals[i - 1]) or vals[i - 1] == 0:
                        continue
                    yoy = (vals[i] - vals[i - 1]) / vals[i - 1] * 100
                    if abs(yoy) < 10:
                        continue
                    promoted = False
                    if i >= 2 and pd.notna(vals[i - 2]):
                        prev_diff = abs(vals[i - 1] - vals[i - 2])
                        curr_diff = abs(vals[i] - vals[i - 1])
                        if prev_diff > 0:
                            ratio = curr_diff / prev_diff
                            if ratio >= 3.0:
                                self._add(group.iloc[i], "B1-1", "학생·학급·교원 급변동(이중)", 2, "B1",
                                          f"{name} 전년대비 {yoy:+.1f}% · 직전 변동 대비 {ratio:.1f}배",
                                          {"field": name, "col_key": col, "col_label": name,
                                           "yoy": round(float(yoy), 1), "ratio": round(float(ratio), 1)})
                                promoted = True
                    if not promoted:
                        self._add(group.iloc[i], "B1-2", "학생·학급·교원 급변동(단년)", 2, "B1",
                                  f"{name} 전년대비 {yoy:+.1f}%",
                                  {"field": name, "col_key": col, "col_label": name,
                                   "yoy": round(float(yoy), 1)})

    def _check_b1_3_b1_4(self):
        """B1-3: 학교회계 세입 또는 세출 ±30% 변동.
        B1-4: ±50% 변동 (강한 변동, ★★★)."""
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            group = group.sort_values("year").reset_index(drop=True)
            for col, name in [("budget_revenue", "학교회계 세입"), ("budget_expense", "학교회계 세출")]:
                if col not in group.columns:
                    continue
                vals = group[col].values
                for i in range(1, len(vals)):
                    if pd.isna(vals[i]) or pd.isna(vals[i - 1]) or vals[i - 1] == 0:
                        continue
                    yoy = (vals[i] - vals[i - 1]) / vals[i - 1] * 100
                    if abs(yoy) >= 50:
                        self._add(group.iloc[i], "B1-4", "학교회계 강한 변동", 3, "B1",
                                  f"{name} 전년대비 {yoy:+.1f}%",
                                  {"field": name, "col_key": col, "col_label": name, "yoy": round(float(yoy), 1)})
                    elif abs(yoy) >= 30:
                        self._add(group.iloc[i], "B1-3", "학교회계 변동", 2, "B1",
                                  f"{name} 전년대비 {yoy:+.1f}%",
                                  {"field": name, "col_key": col, "col_label": name, "yoy": round(float(yoy), 1)})

    def _check_b1_5(self):
        """B1-5: 진학률 15%p 이상 변동. 20%p 이상이면 ★★★, 아니면 ★★."""
        if "graduation_rate" not in self.df.columns:
            return
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            rates = group["graduation_rate"].values
            for i in range(1, len(rates)):
                if pd.isna(rates[i]) or pd.isna(rates[i - 1]):
                    continue
                diff = rates[i] - rates[i - 1]
                if abs(diff) >= 15:
                    star = 3 if abs(diff) >= 20 else 2
                    self._add(group.iloc[i], "B1-5", "진학률 급변동", star, "B1",
                              f"진학률 {rates[i-1]:.1f}%→{rates[i]:.1f}% ({diff:+.1f}%p)",
                              {"prev": round(float(rates[i-1]), 1), "curr": round(float(rates[i]), 1), "diff_pp": round(float(diff), 1)})

    def _check_b1_6(self):
        """B1-6: 학폭 0~1건 → 5건 이상."""
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            cases = group["bullying_cases"].fillna(0).values
            for i in range(1, len(cases)):
                if cases[i - 1] <= 1 and cases[i] >= 5:
                    self._add(group.iloc[i], "B1-6", "학폭 심의 급증", 3, "B1",
                              f"학폭 {int(cases[i-1])}→{int(cases[i])}건",
                              {"prev": int(cases[i-1]), "curr": int(cases[i])})

    # ────────────────────────────────────────────────
    # D2 — 유사학교 대비 편차
    # ────────────────────────────────────────────────

    def _check_d2_1(self):
        """D2-1: 동료군(연도 기준) 백분위 10% 미만 또는 90% 초과."""
        for col, name in [("students_per_class", "학급당학생수"),
                          ("students_per_teacher", "교원1인당학생수"),
                          ("meal_cost_per_student", "1인당 급식비")]:
            if col not in self.df.columns:
                continue
            for year in self.df["year"].unique():
                yr = self.df[self.df["year"] == year].dropna(subset=[col])
                if len(yr) < 10:
                    continue
                p10 = yr[col].quantile(0.10)
                p90 = yr[col].quantile(0.90)
                for _, row in yr.iterrows():
                    v = row[col]
                    if v <= p10:
                        self._add(row, "D2-1", "유사학교 대비 상하위 10%", 2, "D2",
                                  f"{name} {v:.1f} (하위 10% 기준 {p10:.1f})",
                                  {"field": name, "col_key": col, "col_label": name,
                                   "value": round(float(v), 1), "p10": round(float(p10), 1), "side": "low"})
                    elif v >= p90:
                        self._add(row, "D2-1", "유사학교 대비 상하위 10%", 2, "D2",
                                  f"{name} {v:.1f} (상위 10% 기준 {p90:.1f})",
                                  {"field": name, "col_key": col, "col_label": name,
                                   "value": round(float(v), 1), "p90": round(float(p90), 1), "side": "high"})

    def _check_d2_2(self):
        """D2-2: IQR 1.5배 외부 OR 중앙값 대비 50% 이상 차이."""
        for col, name in [("students_per_class", "학급당학생수"),
                          ("students_per_teacher", "교원1인당학생수"),
                          ("meal_cost_per_student", "1인당 급식비")]:
            if col not in self.df.columns:
                continue
            for year in self.df["year"].unique():
                yr = self.df[self.df["year"] == year].dropna(subset=[col])
                if len(yr) < 10:
                    continue
                q1, q3 = yr[col].quantile(0.25), yr[col].quantile(0.75)
                iqr = q3 - q1
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                median = yr[col].median()
                for _, row in yr.iterrows():
                    v = row[col]
                    is_iqr = (v < lo) or (v > hi)
                    is_median = (median > 0) and (abs(v - median) / median >= 0.5)
                    if is_iqr or is_median:
                        reason = []
                        if is_iqr:
                            reason.append(f"IQR 범위 {lo:.1f}~{hi:.1f} 밖")
                        if is_median:
                            reason.append(f"중앙값 {median:.1f} 대비 50% 이상")
                        self._add(row, "D2-2", "유사학교 대비 극단값", 3, "D2",
                                  f"{name} {v:.1f} ({' / '.join(reason)})",
                                  {"field": name, "col_key": col, "col_label": name,
                                   "value": round(float(v), 1), "lo": round(float(lo), 1),
                                   "hi": round(float(hi), 1), "median": round(float(median), 1)})

    # ────────────────────────────────────────────────
    # C2 — 학생·재정 연동
    # ────────────────────────────────────────────────

    def _check_c2_3(self):
        """C2-3: 학생수 안정(±5%) + 급식비 ±10% (강한 +30%)."""
        if "meal_cost_total_yoy" not in self.df.columns:
            return
        df = self.df.dropna(subset=["meal_cost_total_yoy", "student_count_yoy"])
        for _, row in df.iterrows():
            m_yoy = row["meal_cost_total_yoy"]
            s_yoy = row["student_count_yoy"]
            if abs(s_yoy) < 5 and abs(m_yoy) >= 10:
                if abs(m_yoy) >= 30:
                    self._add(row, "C2-3+", "급식비 강한 변동", 3, "C2",
                              f"급식비 {m_yoy:+.1f}% (학생수 {s_yoy:+.1f}%)",
                              {"meal_yoy": round(m_yoy, 1), "student_yoy": round(s_yoy, 1)})
                else:
                    self._add(row, "C2-3", "급식비 변동", 2, "C2",
                              f"급식비 {m_yoy:+.1f}% (학생수 {s_yoy:+.1f}%)",
                              {"meal_yoy": round(m_yoy, 1), "student_yoy": round(s_yoy, 1)})

    # ────────────────────────────────────────────────
    # E1 — 누락 패턴
    # ────────────────────────────────────────────────

    # 시설 7개 컬럼을 기준. 학교가 시설 보유 여부를 입력하지 않으면 NaN.
    _FACILITY_COLS = ["fc_changing_room", "fc_shower", "fc_cafeteria", "fc_dorm", "fc_av_room", "fc_computer_room"]

    def _check_e1_1(self):
        """E1-1: 동일 시설 컬럼이 3년 연속 NaN (3년 연속 미입력)."""
        avail = [c for c in self._FACILITY_COLS if c in self.df.columns]
        if not avail:
            return
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 3:
                continue
            group = group.sort_values("year")
            for col in avail:
                vals = group[col].values
                if len(vals) >= 3 and all(pd.isna(v) for v in vals[-3:]):
                    name = self._fc_name(col)
                    self._add(group.iloc[-1], "E1-1", "3년 연속 미입력", 2, "E1",
                              f"{name} 3년 연속 미입력",
                              {"field": name, "col_key": col, "col_label": name, "years_missing": 3})

    def _check_e1_2(self):
        """E1-2: 동료군(같은 구·연도) 90% 이상이 입력했는데 본교만 미입력."""
        avail = [c for c in self._FACILITY_COLS if c in self.df.columns]
        if not avail:
            return
        for year in self.df["year"].unique():
            yr = self.df[self.df["year"] == year]
            for col in avail:
                # 동료군 입력률(구별)
                for district in yr["district"].dropna().unique():
                    sub = yr[yr["district"] == district]
                    if len(sub) < 5:
                        continue
                    fill_rate = sub[col].notna().mean()
                    if fill_rate < 0.90:
                        continue
                    # 본교만 NaN인 경우
                    missing = sub[sub[col].isna()]
                    for _, row in missing.iterrows():
                        name = self._fc_name(col)
                        self._add(row, "E1-2", "단독 미입력 (동료군 다 입력)", 3, "E1",
                                  f"{name} 미입력 · 동료군({district}구) 입력률 {fill_rate*100:.0f}%",
                                  {"field": name, "col_key": col, "col_label": name,
                                   "district": str(district), "peer_fill_rate": round(float(fill_rate), 2)})

    # E1-3 — 학교 유형별 공시 의무 항목 매핑은 원천 범위에서 단일 확정 불가.
    # 룰 함수는 두되 RULE_META[E1-3].status = 'needs_mapping'으로 표시하고 탐지 0건.
    # (run_all에서 별도 호출하지 않음. RULE_META 메타로 UI에 상태 노출.)

    @staticmethod
    def _fc_name(col: str) -> str:
        return {
            "fc_changing_room": "학생탈의실",
            "fc_shower": "학생샤워실",
            "fc_health_room": "보건실",
            "fc_cafeteria": "학생식당",
            "fc_dorm": "기숙사실수",
            "fc_av_room": "시청각실",
            "fc_computer_room": "컴퓨터실",
        }.get(col, col)

    # ────────────────────────────────────────────────
    # E2 — 수치 미갱신
    # ────────────────────────────────────────────────

    def _check_e2_2(self):
        """E2-2: 3년 동일값 반복. 소규모 정수 0~5와 비율 상수(100) 제외."""
        cols = [
            "student_count", "teacher_count", "class_count",
            "meal_cost_per_student", "meal_cost_total",
            "students_per_class", "students_per_teacher",
            "graduation_rate", "budget_revenue", "budget_expense",
        ]
        name_map = {
            "student_count": "학생수", "teacher_count": "교원수", "class_count": "학급수",
            "meal_cost_per_student": "1인당 급식비", "meal_cost_total": "급식비총액",
            "students_per_class": "학급당학생수", "students_per_teacher": "교원1인당학생수",
            "graduation_rate": "진학률",
            "budget_revenue": "학교회계 세입", "budget_expense": "학교회계 세출",
        }
        noise_values = {0, 1, 2, 3, 4, 5, 100, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 100.0}
        for col in cols:
            if col not in self.df.columns:
                continue
            for _, group in self.df.groupby("school_code"):
                if len(group) < 3:
                    continue
                vals = group.sort_values("year")[col].dropna().values
                if len(vals) >= 3 and len(set(vals[-3:])) == 1 and vals[-1] not in noise_values:
                    row = group.sort_values("year").iloc[-1]
                    cname = name_map.get(col, col)
                    v = vals[-1]
                    detail = f"{cname} 3년 동일값 ({v:.1f})" if isinstance(v, float) else f"{cname} 3년 동일값 ({int(v)})"
                    # E2-2는 탐지된 실제 컬럼이 다양함 — col_key/col_label을 detection 단위로 노출
                    self._add(row, "E2-2", "3년 동일값 반복", 2, "E2",
                              detail, {"field": cname, "col_key": col, "col_label": cname, "value": float(v), "years": 3})

    # ────────────────────────────────────────────────
    # C5 — 학년 진급
    # ────────────────────────────────────────────────

    def _check_c5_1(self):
        """C5-1: t년 1학년 → t+1년 2학년 진급 인원 변동률이 -7%~+3% 밖."""
        if "grade1_students" not in self.df.columns or "grade2_students" not in self.df.columns:
            return
        df_sorted = self.df.sort_values(["school_code", "year"])
        for _, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            group = group.sort_values("year").reset_index(drop=True)
            for i in range(1, len(group)):
                prev = group.iloc[i - 1]; curr = group.iloc[i]
                g1_prev = prev.get("grade1_students", np.nan)
                g2_curr = curr.get("grade2_students", np.nan)
                if pd.isna(g1_prev) or pd.isna(g2_curr) or g1_prev == 0:
                    continue
                rate = (g2_curr - g1_prev) / g1_prev * 100
                if rate < -7 or rate > 3:
                    diff = int(g2_curr - g1_prev)
                    self._add(curr, "C5-1", "진급 시 학생 이탈", 3, "C5",
                              f"전년 1학년 {int(g1_prev)}명 → 당해 2학년 {int(g2_curr)}명 ({diff:+d}명, {rate:+.1f}%)",
                              {"g1_prev": int(g1_prev), "g2_curr": int(g2_curr), "diff": diff, "rate": round(float(rate), 1)})

    # ────────────────────────────────────────────────
    # F1' — 교차 불일치
    # ────────────────────────────────────────────────

    # G1 적용 지표 (7개) — RULE_META["G1-1"]["cols"]와 일치
    _G1_TARGETS = [
        ("student_count",          "학생수"),
        ("teacher_count",          "교원수"),
        ("class_count",            "학급수"),
        ("students_per_class",     "학급당학생수"),
        ("bullying_cases",         "학폭 심의건수"),
        ("graduation_rate",        "진학률(%)"),
        ("meal_cost_per_student",  "1인당급식비(원)"),
    ]

    def _g1_school_series(self):
        """학교별 3년 시계열(v0,v1,v2)을 지표별로 묶어 반환.
        반환: {col: [(school_code, group_last_row, v0, v1, v2, cum_pct, yoy1, yoy2), ...]}"""
        out = {col: [] for col, _ in self._G1_TARGETS}
        df_sorted = self.df.sort_values(["school_code", "year"])
        for sc, group in df_sorted.groupby("school_code"):
            if len(group) < 3:
                continue
            group = group.sort_values("year").reset_index(drop=True)
            for col, _name in self._G1_TARGETS:
                if col not in group.columns:
                    continue
                vals = group[col].values
                if len(vals) < 3:
                    continue
                v0, v1, v2 = vals[-3], vals[-2], vals[-1]
                if pd.isna(v0) or pd.isna(v1) or pd.isna(v2) or v0 == 0:
                    continue
                yoy1 = (v1 - v0) / v0 * 100
                yoy2 = (v2 - v1) / v1 * 100 if v1 != 0 else 0
                cum_pct = (v2 - v0) / v0 * 100
                out[col].append((str(sc), group.iloc[-1], float(v0), float(v1), float(v2),
                                 float(cum_pct), float(yoy1), float(yoy2)))
        return out

    def _g1_already_covered(self, school_code: str, indicator_col: str) -> bool:
        """같은 학교+같은 지표를 기존 룰(B1/C1/C2 등)이 이미 탐지했는지 검사.
        - detection.values["col_key"]가 명시적으로 같은 col이면 True
        - 명시적 col_key 없는 룰은 EXISTING_RULE_INDICATORS로 매칭"""
        for det in self.detections:
            if det.school_code != school_code:
                continue
            if det.rule_id.startswith("G1"):
                continue
            det_col = ""
            if isinstance(det.values, dict):
                det_col = str(det.values.get("col_key", "") or "").strip()
            if det_col and det_col == indicator_col:
                return True
            cover = EXISTING_RULE_INDICATORS.get(det.rule_id, set())
            if indicator_col in cover:
                return True
        return False

    def _check_g1(self):
        """G1-1 (다년 단조 추세) + G1-2 (추세 급변동).
        공통 산식 — m_r = |이 학교 누적변동| / |전체평균변동(signed)|
        G1-1 트리거: 3년 단조 + 누적 ±8% + 단년 ±10% 미만 (B1 미해당)
        G1-2 트리거: 같은 방향(부호 일치) + |누적| ≥ 2 × |전체평균변동|
        중복 제거: 같은 학교+같은 지표를 기존 룰이 이미 탐지하면 제외."""
        series_by_col = self._g1_school_series()

        # 1) 지표별 전체 평균 변동(signed) — 단일 출처 baseline
        peer_mean = {}
        for col, _ in self._G1_TARGETS:
            arr = [t[5] for t in series_by_col.get(col, [])]
            peer_mean[col] = (sum(arr) / len(arr)) if arr else 0.0

        for col, name in self._G1_TARGETS:
            entries = series_by_col.get(col, [])
            if not entries:
                continue
            pm_signed = peer_mean[col]
            pm_abs = abs(pm_signed)
            # baseline 0 가까우면 의미 없음 — 안전 하한 1.0%
            baseline = max(pm_abs, 1.0)

            for sc, row_last, v0, v1, v2, cum_pct, yoy1, yoy2 in entries:
                # ── G1-1: 단조 + 누적 8% + 단년 B1 미해당 ──
                cond_monotonic = ((yoy1 > 0 and yoy2 > 0) or (yoy1 < 0 and yoy2 < 0))
                cond_no_b1 = abs(yoy1) < 10 and abs(yoy2) < 10
                cond_cum_8 = abs(cum_pct) >= 8
                if cond_monotonic and cond_no_b1 and cond_cum_8:
                    if self._g1_already_covered(sc, col):
                        continue
                    m_ratio = abs(cum_pct) / baseline
                    direction = "감소" if cum_pct < 0 else "증가"
                    # 회귀 기울기·R^2 (UI 차트용)
                    xs = [0, 1, 2]; ys = [v0, v1, v2]
                    mean_x = 1.0; mean_y = sum(ys) / 3
                    ss_xy = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(3))
                    ss_xx = sum((xs[i] - mean_x) ** 2 for i in range(3))
                    slope = ss_xy / ss_xx if ss_xx else 0.0
                    intercept = mean_y - slope * mean_x
                    ss_tot = sum((y - mean_y) ** 2 for y in ys)
                    ss_res = sum((ys[i] - (slope * xs[i] + intercept)) ** 2 for i in range(3))
                    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
                    self._add(row_last, "G1-1", "다년 단조 추세", 2, "G1",
                              f"{name} 3년 누적 {cum_pct:+.1f}% ({v0:.1f}→{v1:.1f}→{v2:.1f}) · 단조 {direction} · 전체평균 {pm_signed:+.1f}%",
                              {"field": name, "col_key": col, "col_label": name,
                               "cumulative_pct": round(float(cum_pct), 1),
                               "peer_mean_signed": round(float(pm_signed), 1),
                               "peer_mean_abs": round(float(pm_abs), 1),
                               "m_ratio": round(float(m_ratio), 2),
                               "slope_per_year": round(float(slope), 2),
                               "r_squared": round(float(r2), 3),
                               "direction": direction,
                               "values_3y": [round(v0, 1), round(v1, 1), round(v2, 1)],
                               "yoy1": round(yoy1, 1), "yoy2": round(yoy2, 1)})
                    continue  # G1-1이 잡으면 G1-2 중복 안 봄

                # ── G1-2: 같은 방향 + |누적| ≥ 2 × |전체평균| ──
                # peer 평균이 +/- 어느 한쪽으로 충분히 기울어 있어야 의미 있음
                if pm_abs < 1.0:  # 평균이 사실상 0이면 "같은 방향" 비교 무의미
                    continue
                same_dir = (cum_pct > 0 and pm_signed > 0) or (cum_pct < 0 and pm_signed < 0)
                if not same_dir:
                    continue
                if abs(cum_pct) < 2 * pm_abs:
                    continue
                if self._g1_already_covered(sc, col):
                    continue
                m_ratio = abs(cum_pct) / baseline
                direction = "감소" if cum_pct < 0 else "증가"
                self._add(row_last, "G1-2", "추세 급변동", 2, "G1",
                          f"{name} 3년 누적 {cum_pct:+.1f}% ({v0:.1f}→{v1:.1f}→{v2:.1f}) · 전체평균 {pm_signed:+.1f}%의 {m_ratio:.1f}배",
                          {"field": name, "col_key": col, "col_label": name,
                           "cumulative_pct": round(float(cum_pct), 1),
                           "peer_mean_signed": round(float(pm_signed), 1),
                           "peer_mean_abs": round(float(pm_abs), 1),
                           "m_ratio": round(float(m_ratio), 2),
                           "direction": direction,
                           "values_3y": [round(v0, 1), round(v1, 1), round(v2, 1)],
                           "yoy1": round(yoy1, 1), "yoy2": round(yoy2, 1)})

    def _check_f1_prime(self):
        """F1'-1: 학교알리미(강사 제외) vs KESS 교원수 3명 이상 차이."""
        kess_col = "kess_teacher_total"
        if kess_col not in self.df.columns:
            return
        for _, row in self.df.iterrows():
            alimi_corrected = row.get("teacher_count_no_instructor")
            kess = row.get(kess_col)
            if pd.isna(alimi_corrected) or pd.isna(kess):
                continue
            diff = abs(alimi_corrected - kess)
            if diff >= 3:
                instructor = int(row.get("instructor_count", 0) or 0)
                self._add(row, "F1'-1", "교원수 교차 불일치", 2, "F1",
                          f"학교알리미 {int(alimi_corrected)}명(강사{instructor}명 제외) vs KESS {int(kess)}명 (차이 {int(diff)}명)",
                          {"alimi_corrected": int(alimi_corrected), "kess": int(kess), "diff": int(diff), "instructors": instructor})


if __name__ == "__main__":
    from data_loader import load_and_merge_all
    df = load_and_merge_all()
    engine = RuleEngine(df)
    results = engine.run_all()
    print(f"\n탐지 결과: {len(results)}건")
    print(f"\n룰별 건수:")
    print(results["rule_id"].value_counts().sort_index())
