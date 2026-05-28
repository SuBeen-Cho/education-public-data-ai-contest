"""
rule_engine.py — v3 룰셋 Python 구현
검증 결과 반영: C2 추가, C1-1 임계값 수정, F1' KESS 컬럼명 수정
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass


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

    def run_all(self) -> pd.DataFrame:
        """전체 룰 실행"""
        self.detections = []

        self._check_c1_1()   # 학생↔학급 역방향
        self._check_c1_8()   # 학급당학생수 급변
        self._check_c1_3()   # 학생↔교원 불균형
        self._check_c3_3()   # 미조치 피해
        self._check_b1_1()   # 학생·교원 급변동
        self._check_b1_6()   # 학폭 급증
        self._check_b1_5()   # 진학률 급변동 (신규)
        self._check_c2_3()   # 급식비 변동
        self._check_d2_2()   # IQR 이탈
        self._check_c5_1()   # 코호트 단절
        self._check_e2_2()   # 3년 정체
        self._check_f1_prime()  # 교차 불일치

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
            "detail": d.detail, "values": str(d.values),
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

    # ── C1-1: 학생↔학급 역방향 변동 ──
    # v3 기준: 방향 반대 + 학급수 변동 2학급 이상
    def _check_c1_1(self):
        df_sorted = self.df.sort_values(["school_code", "year"])
        for school_code, group in df_sorted.groupby("school_code"):
            if len(group) < 2:
                continue
            for i in range(1, len(group)):
                curr = group.iloc[i]
                prev = group.iloc[i - 1]
                s_yoy = curr.get("student_count_yoy", np.nan)
                c_yoy = curr.get("class_count_yoy", np.nan)
                if pd.isna(s_yoy) or pd.isna(c_yoy):
                    continue
                c_curr = curr.get("class_count", 0)
                c_prev = prev.get("class_count", 0)
                class_diff = abs(c_curr - c_prev) if (pd.notna(c_curr) and pd.notna(c_prev)) else 0
                # 조건: 방향 반대 + 학급수 2학급 이상 변동
                if s_yoy * c_yoy < 0 and class_diff >= 2:
                    self._add(curr, "C1-1", "학생↔학급 역방향 변동", 3, "C1",
                              f"학생수 {s_yoy:+.1f}% / 학급수 {c_yoy:+.1f}% (학급 {class_diff}개 변동)",
                              {"student_yoy": round(s_yoy, 1), "class_yoy": round(c_yoy, 1), "class_diff": int(class_diff)})

    # ── C1-8: 학급당학생수 급변 (±1.5명/학급) ──
    def _check_c1_8(self):
        df_sorted = self.df.sort_values(["school_code", "year"])
        for school_code, group in df_sorted.groupby("school_code"):
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
                              {"prev": round(vals[i-1], 1), "curr": round(vals[i], 1), "diff": round(diff, 1)})

    # ── C1-3: 학생 안정 + 교원 급변 ──
    def _check_c1_3(self):
        for _, row in self.df.dropna(subset=["student_count_yoy", "teacher_count_yoy"]).iterrows():
            s, t = row["student_count_yoy"], row["teacher_count_yoy"]
            if abs(s) < 3 and abs(t) >= 10:
                self._add(row, "C1-3", "학생↔교원 불균형", 2, "C1",
                          f"학생수 {s:+.1f}%(안정) 교원수 {t:+.1f}%(급변)",
                          {"student_yoy": round(s, 1), "teacher_yoy": round(t, 1)})

    # ── C3-3: 미조치 피해 (등급 A/B/X) ──
    def _check_c3_3(self):
        for _, row in self.df.iterrows():
            v = int(row.get("bullying_victims", 0) or 0)
            p = int(row.get("bullying_protection", 0) or 0)
            perp = int(row.get("bullying_perpetrators", 0) or 0)
            if v > 0 and p == 0:
                # 등급X: 가해학생수=0 → 자체해결/이월 추정 → 제외 (매뉴얼 p44 Q3)
                if perp == 0:
                    continue
                star = 3 if v >= 3 else 2
                grade = "A" if v >= 3 else "B"
                self._add(row, f"C3-3{grade}", "미조치 피해", star, "C3",
                          f"피해학생 {v}명 / 보호조치 {p}건 / 가해학생 {perp}명",
                          {"victims": v, "protection": p, "perpetrators": perp})

    # ── B1-1: 학생·교원 ±10% ──
    def _check_b1_1(self):
        for col, name in [("student_count_yoy", "학생수"), ("teacher_count_yoy", "교원수")]:
            if col not in self.df.columns:
                continue
            for _, row in self.df.dropna(subset=[col]).iterrows():
                yoy = row[col]
                if abs(yoy) >= 10:
                    self._add(row, "B1-1", f"{name} 급변동", 2, "B1",
                              f"{name} 전년대비 {yoy:+.1f}%",
                              {"field": name, "yoy": round(yoy, 1)})

    # ── B1-6: 학폭 0~1건→5건+ ──
    def _check_b1_6(self):
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

    # ── B1-5: 진학률 ±15%p 급변동 ──
    def _check_b1_5(self):
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
                    self._add(group.iloc[i], "B1-5", "진학률 급변동", 3, "B1",
                              f"진학률 {rates[i-1]:.1f}%→{rates[i]:.1f}% ({diff:+.1f}%p)",
                              {"prev": round(rates[i-1], 1), "curr": round(rates[i], 1), "diff_pp": round(diff, 1)})

    # ── C2-3: 급식비 변동 ──
    def _check_c2_3(self):
        if "meal_cost_total_yoy" not in self.df.columns:
            return
        df = self.df.dropna(subset=["meal_cost_total_yoy", "student_count_yoy"])
        for _, row in df.iterrows():
            m_yoy = row["meal_cost_total_yoy"]
            s_yoy = row["student_count_yoy"]
            # 학생수 안정(±5%) + 급식비 ±10% → ★2
            if abs(s_yoy) < 5 and abs(m_yoy) >= 10:
                if abs(m_yoy) >= 30:
                    self._add(row, "C2-3+", "급식비 강한 변동", 3, "C2",
                              f"급식비 {m_yoy:+.1f}% (학생수 {s_yoy:+.1f}%)",
                              {"meal_yoy": round(m_yoy, 1), "student_yoy": round(s_yoy, 1)})
                else:
                    self._add(row, "C2-3", "급식비 변동", 2, "C2",
                              f"급식비 {m_yoy:+.1f}% (학생수 {s_yoy:+.1f}%)",
                              {"meal_yoy": round(m_yoy, 1), "student_yoy": round(s_yoy, 1)})

    # ── D2-2: IQR 1.5배 이탈 ──
    def _check_d2_2(self):
        for col, name in [("students_per_class", "학급당학생수"), ("students_per_teacher", "교원1인당학생수")]:
            if col not in self.df.columns:
                continue
            for year in self.df["year"].unique():
                yr = self.df[(self.df["year"] == year)].dropna(subset=[col])
                if len(yr) < 10:
                    continue
                q1, q3 = yr[col].quantile(0.25), yr[col].quantile(0.75)
                iqr = q3 - q1
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                for _, row in yr[(yr[col] < lo) | (yr[col] > hi)].iterrows():
                    self._add(row, "D2-2", f"{name} 극단값", 3, "D2",
                              f"{name} {row[col]:.1f} (범위: {lo:.1f}~{hi:.1f})",
                              {"value": round(row[col], 1), "lower": round(lo, 1), "upper": round(hi, 1)})

    # ── C5-1: 전년 대비 학생수 변동 범위 밖 (-7%~+3%) ──
    def _check_c5_1(self):
        if "student_count_yoy" not in self.df.columns:
            return
        for _, row in self.df.dropna(subset=["student_count_yoy"]).iterrows():
            chg = row["student_count_yoy"]
            if chg < -7 or chg > 3:
                self._add(row, "C5-1", "학생수 변동 정상범위(-7~+3%) 밖", 3, "C5",
                          f"학생수 변동 {chg:+.1f}% (정상: -7%~+3%)",
                          {"change_pct": round(chg, 1)})

    # ── E2-2: 3년 동일값 (복붙 의심) ──
    def _check_e2_2(self):
        cols = ["student_count", "teacher_count", "class_count",
                "meal_cost_per_student", "students_per_class"]
        name_map = {"student_count": "학생수", "teacher_count": "교원수", "class_count": "학급수",
                    "meal_cost_per_student": "1인당급식비", "students_per_class": "학급당학생수"}
        noise_values = {0, 1, 100, 0.0, 1.0, 100.0}  # 상수 필터
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
                    self._add(row, "E2-2", f"{cname} 3년 정체", 2, "E2",
                              f"{cname} 3년 동일값 ({vals[-1]:.1f})" if isinstance(vals[-1], float) else f"{cname} 3년 동일값 ({int(vals[-1])})",
                              {"value": float(vals[-1]), "years": 3})

    # ── F1': 교원수 교차 불일치 (강사 보정 적용) ──
    def _check_f1_prime(self):
        kess_col = "kess_teacher_total"
        if kess_col not in self.df.columns:
            return
        for _, row in self.df.iterrows():
            # 보정 공식: 학교알리미 직위별총계 - 강사(계) = KESS 비교용
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
    print(f"탐지 결과: {len(results)}건")
    print(f"\n룰별 건수:")
    print(results["rule_id"].value_counts())
