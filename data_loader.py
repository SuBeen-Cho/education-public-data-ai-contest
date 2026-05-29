"""
data_loader.py — 공공데이터 xlsx 로드 및 통합 DataFrame 생성
실제 컬럼 매핑 기반 (2025-05-26 검증 완료)
"""

import pandas as pd
import numpy as np
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "공공데이터수집_20260518기준" / "3_연도별통합"
SCHOOL_DIR = DATA_DIR / "학교알리미_연도별통합"
KESS_DIR = DATA_DIR / "KESS_연도별통합"

# ── 실제 컬럼명 매핑 (xlsx에서 확인) ──
SCHOOL_COLS = {
    "school_name": "학교명",                                   # [0]
    "district_raw": "지역",                                    # [3] "서울특별시 강남구"
    "school_code": "정보공시 학교코드",                          # [4]
    "school_type": "설립구분",                                  # [6]
    "student_count": "학사_학생_성별학생수__계",                  # [278]
    "class_count_raw": "학사_학생_학교 현황__학급수(계)",          # [318] "28(3)" 형태!
    "student_count_display": "학사_학생_학교 현황__학생수(계)",    # [319]
    "students_per_class_raw": "학사_학생_학교 현황__학급당학생수",  # [320]
    "teacher_count": "교원_자격종별교원현황__계",                  # [51]
    "meal_cost_total": "급식_급식비부담주체__금액 계",             # [120]
    "meal_cost_per_student": "급식_급식비지출내역__학생 1인당 급식비 (1식 기준)", # [122]
    "meal_student_count": "급식_급식실시현황__급식 학생수",        # [125]
    # 강사 보정용 (F1' 교차검증, C1 교원 비교)
    "teacher_total_position": "교원_직위별교원현황__총계 (계)",    # [107] 직위별 총계 (강사 포함)
    "instructor_count": "교원_직위별교원현황__강사 (계)",          # [103] 강사수
    # 학년별 학생수 (C5-1: 진급 시 학생 이탈)
    "grade1_male": "학사_학생_성별학생수__1학년(남)",
    "grade1_female": "학사_학생_성별학생수__1학년(여)",
    "grade2_male": "학사_학생_성별학생수__2학년(남)",
    "grade2_female": "학사_학생_성별학생수__2학년(여)",
    "grade3_male": "학사_학생_성별학생수__3학년(남)",
    "grade3_female": "학사_학생_성별학생수__3학년(여)",
    # 보직교사 (C1-5: 학생↔보직교사 불균형)
    "head_teacher_count": "교원_직위별교원현황__보직교사 (계)",
}

# 학교회계 세입/세출 결산 (B1-3 / B1-4)
# 공립=국공립회계, 사립=사립교비회계 — 6개 카테고리 합산
PUBLIC_REVENUE_COLS = [
    "재정_국공립회계세입결산서__정부이전수입",
    "재정_국공립회계세입결산서__기타이전수입",
    "재정_국공립회계세입결산서__학부모부담수입",
    "재정_국공립회계세입결산서__지원금수입",
    "재정_국공립회계세입결산서__행정활동수입",
    "재정_국공립회계세입결산서__기타",
]
PUBLIC_EXPENSE_COLS = [
    "재정_국공립회계세출결산서__인적자원운용",
    "재정_국공립회계세출결산서__학생복지 /교육격차해소",
    "재정_국공립회계세출결산서__기본적 교육활동",
    "재정_국공립회계세출결산서__선택적 교육활동",
    "재정_국공립회계세출결산서__교육활동 지원",
    "재정_국공립회계세출결산서__학교 일반운영",
    "재정_국공립회계세출결산서__학교시설 확충",
    "재정_국공립회계세출결산서__학교 재무활동",
]
PRIVATE_REVENUE_COLS = [
    "재정_사립교비회계세입결산서__정부이전수입",
    "재정_사립교비회계세입결산서__기타이전수입",
    "재정_사립교비회계세입결산서__학부모부담수입",
    "재정_사립교비회계세입결산서__지원금수입",
    "재정_사립교비회계세입결산서__행정활동수입",
    "재정_사립교비회계세입결산서__기타",
]
PRIVATE_EXPENSE_COLS = [
    "재정_사립교비회계세출결산서__인적자원운용",
    "재정_사립교비회계세출결산서__학생복지 /교육격차해소",
    "재정_사립교비회계세출결산서__기본적 교육활동",
    "재정_사립교비회계세출결산서__선택적 교육활동",
    "재정_사립교비회계세출결산서__교육활동 지원",
    "재정_사립교비회계세출결산서__학교 일반운영",
    "재정_사립교비회계세출결산서__학교시설 확충",
    "재정_사립교비회계세출결산서__학교 재무활동",
]

# 시설/공시 컬럼 (E1-1, E1-2, E1-3: 입력 패턴 점검)
# 표시 컬럼은 단순화: 시설 항목 7개로 한정. 각 학교별 NaN 여부로 누락 패턴 추적.
FACILITY_COLS = {
    "fc_changing_room": "시설_교사현황__학생탈의실",
    "fc_shower": "시설_교사현황__학생샤워실",
    "fc_health_room": "시설_교사현황__보건실",
    "fc_cafeteria": "시설_교사현황__학생식당",
    "fc_dorm": "시설_교사현황__기숙사실수",
    "fc_av_room": "시설_교사현황__학습지원공간 시청각실",
    "fc_computer_room": "시설_교사현황__학습지원공간 컴퓨터실",
}

BULLY_COLS = {
    "cases_1": "사안심의__1학기_심의건수",           # [4]
    "cases_2": "사안심의__2학기_심의건수",           # [10]
    "victims_1": "사안심의__1학기_피해학생_학생수",    # [6]
    "victims_2": "사안심의__2학기_피해학생_학생수",    # [12]
    "protection_1": "사안심의__1학기_피해학생_보호조치건수", # [7]
    "protection_2": "사안심의__2학기_피해학생_보호조치건수", # [13]
    "perpetrator_1": "사안심의__1학기_가해학생_학생수",      # [8]
    "perpetrator_2": "사안심의__2학기_가해학생_학생수",      # [14]
}

KESS_COLS = {
    "school_name": "학교명",                    # [12]
    "year": "연도",                              # [1]
    "semester": "반기",                          # [0]
    "kess_student_count": "일반학급_학생수_계",    # [29]
    "kess_class_count": "편성학급수_계",           # [36]
    "kess_teacher_total": "교원수_총계_계",        # [65]
    "kess_teacher_regular": "교원수_정규_계",      # [68]
    "graduation_rate": "진학률 _전체(%)",          # [125]
    "students_per_teacher_kess": "교원1인당 학생수", # [107]
}


def parse_class_count(val) -> int:
    """'28(3)' → 28 (괄호 안은 특수학급, 제외)"""
    if pd.isna(val):
        return np.nan
    s = str(val).strip()
    match = re.match(r'(\d+)', s)
    return int(match.group(1)) if match else np.nan


def safe_numeric(val):
    """안전한 숫자 변환"""
    if pd.isna(val):
        return np.nan
    s = str(val).strip()
    s = s.replace(",", "").replace(" ", "")
    if s in ("", "-", "N/A", "비대상", "비공개"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        match = re.match(r'[\d.]+', s)
        return float(match.group()) if match else np.nan


def extract_district(raw: str) -> str:
    """'서울특별시 강남구' → '강남'"""
    if pd.isna(raw):
        return ""
    s = str(raw).strip()
    match = re.search(r'(노원|강남|관악)', s)
    return match.group(1) if match else s.split()[-1].replace("구", "")


def load_school_alimi(year: int) -> pd.DataFrame:
    """학교알리미 메인+학폭 시트 로드"""
    path = SCHOOL_DIR / f"{year}_학교알리미_후처리.xlsx"
    df_raw = pd.read_excel(path, sheet_name="메인")

    result = pd.DataFrame()
    result["school_name"] = df_raw[SCHOOL_COLS["school_name"]]
    result["district"] = df_raw[SCHOOL_COLS["district_raw"]].apply(extract_district)
    result["school_code"] = df_raw[SCHOOL_COLS["school_code"]].astype(str)
    result["school_type"] = df_raw[SCHOOL_COLS["school_type"]]
    result["year"] = year

    # 학생수
    result["student_count"] = df_raw[SCHOOL_COLS["student_count"]].apply(safe_numeric)

    # 학급수 — "28(3)" 파싱!
    result["class_count"] = df_raw[SCHOOL_COLS["class_count_raw"]].apply(parse_class_count)

    # 교원수
    result["teacher_count"] = df_raw[SCHOOL_COLS["teacher_count"]].apply(safe_numeric)

    # 학급당학생수 (원본값)
    result["students_per_class_orig"] = df_raw[SCHOOL_COLS["students_per_class_raw"]].apply(safe_numeric)

    # 급식비
    result["meal_cost_total"] = df_raw[SCHOOL_COLS["meal_cost_total"]].apply(safe_numeric)
    result["meal_cost_per_student"] = df_raw[SCHOOL_COLS["meal_cost_per_student"]].apply(safe_numeric)

    # 강사 보정용: 직위별 총계 - 강사 = KESS 비교용 교원수
    if SCHOOL_COLS["teacher_total_position"] in df_raw.columns:
        result["teacher_total_position"] = df_raw[SCHOOL_COLS["teacher_total_position"]].apply(safe_numeric)
    else:
        result["teacher_total_position"] = result["teacher_count"]

    if SCHOOL_COLS["instructor_count"] in df_raw.columns:
        result["instructor_count"] = df_raw[SCHOOL_COLS["instructor_count"]].apply(safe_numeric).fillna(0)
    else:
        result["instructor_count"] = 0

    # 강사 제외 교원수 (F1', C1 교원비교에 사용)
    result["teacher_count_no_instructor"] = result["teacher_total_position"] - result["instructor_count"]

    # 학년별 학생수 (C5-1 진급 시 이탈 추적용) — 1+2+3학년 남/여 합산
    for grade in (1, 2, 3):
        m_key = f"grade{grade}_male"
        f_key = f"grade{grade}_female"
        m_col = SCHOOL_COLS.get(m_key)
        f_col = SCHOOL_COLS.get(f_key)
        if m_col in df_raw.columns and f_col in df_raw.columns:
            m = df_raw[m_col].apply(safe_numeric).fillna(0)
            f = df_raw[f_col].apply(safe_numeric).fillna(0)
            result[f"grade{grade}_students"] = (m + f).astype(int)
        else:
            result[f"grade{grade}_students"] = np.nan

    # 보직교사 (C1-5)
    head_col = SCHOOL_COLS.get("head_teacher_count")
    if head_col and head_col in df_raw.columns:
        result["head_teacher_count"] = df_raw[head_col].apply(safe_numeric)
    else:
        result["head_teacher_count"] = np.nan

    # 학교회계 세입/세출 결산 합계 (B1-3, B1-4) — 설립유형별 분기
    school_type_series = result["school_type"]
    def _sum_cols(row_idx, cols):
        total = 0.0; any_value = False
        for c in cols:
            if c in df_raw.columns:
                v = safe_numeric(df_raw[c].iloc[row_idx])
                if pd.notna(v):
                    total += float(v); any_value = True
        return total if any_value else np.nan

    rev_list, exp_list = [], []
    for i in range(len(df_raw)):
        st = school_type_series.iloc[i]
        if st == "공립":
            rev_list.append(_sum_cols(i, PUBLIC_REVENUE_COLS))
            exp_list.append(_sum_cols(i, PUBLIC_EXPENSE_COLS))
        else:  # 사립 (또는 미상 → 사립으로 fallback)
            rev_list.append(_sum_cols(i, PRIVATE_REVENUE_COLS))
            exp_list.append(_sum_cols(i, PRIVATE_EXPENSE_COLS))
    result["budget_revenue"] = rev_list
    result["budget_expense"] = exp_list

    # 시설 컬럼 (E1-1/E1-2: 미입력 패턴 점검)
    for our_name, src_col in FACILITY_COLS.items():
        if src_col in df_raw.columns:
            result[our_name] = df_raw[src_col].apply(safe_numeric)
        else:
            result[our_name] = np.nan

    # 학폭 시트
    try:
        df_bully = pd.read_excel(path, sheet_name="학교폭력심의결과")
        bully = pd.DataFrame()
        bully["school_name"] = df_bully["학교명"]

        for key, col in BULLY_COLS.items():
            if col in df_bully.columns:
                bully[key] = df_bully[col].apply(safe_numeric).fillna(0)
            else:
                bully[key] = 0

        bully["bullying_cases"] = bully["cases_1"] + bully["cases_2"]
        bully["bullying_victims"] = bully["victims_1"] + bully["victims_2"]
        bully["bullying_protection"] = bully["protection_1"] + bully["protection_2"]
        bully["bullying_perpetrators"] = bully["perpetrator_1"] + bully["perpetrator_2"]
        bully = bully[["school_name", "bullying_cases", "bullying_victims", "bullying_protection", "bullying_perpetrators"]]

        result = result.merge(bully, on="school_name", how="left")
    except Exception as e:
        print(f"  [WARN] {year} 학폭 시트 로드 실패: {e}")
        result["bullying_cases"] = 0
        result["bullying_victims"] = 0
        result["bullying_protection"] = 0
        result["bullying_perpetrators"] = 0

    for bc in ["bullying_cases", "bullying_victims", "bullying_protection", "bullying_perpetrators"]:
        result[bc] = result[bc].fillna(0).astype(int)

    return result


def load_kess(year: int) -> pd.DataFrame:
    """KESS 데이터 로드 (상반기 기준)"""
    path = KESS_DIR / f"{year}_KESS_후처리.xlsx"
    # 시트명이 '통합'일 수 있음
    try:
        df_raw = pd.read_excel(path, sheet_name="통합")
    except Exception:
        df_raw = pd.read_excel(path)

    # 상반기만 사용 (4/1 기준)
    if "반기" in df_raw.columns:
        df_raw = df_raw[df_raw["반기"] == "상반기"].copy()

    result = pd.DataFrame()
    for our_name, xlsx_name in KESS_COLS.items():
        if xlsx_name in df_raw.columns:
            result[our_name] = df_raw[xlsx_name]
        else:
            result[our_name] = np.nan

    # 숫자 변환
    for col in ["kess_student_count", "kess_class_count", "kess_teacher_total",
                 "kess_teacher_regular", "graduation_rate", "students_per_teacher_kess"]:
        if col in result.columns:
            result[col] = result[col].apply(safe_numeric)

    result["year"] = year
    return result


def load_and_merge_all() -> pd.DataFrame:
    """3년치 데이터 로드 → 통합 DataFrame"""
    frames = []

    for year in [2023, 2024, 2025]:
        print(f"[INFO] {year}년 데이터 로드 중...")
        try:
            df_school = load_school_alimi(year)
            print(f"  학교알리미: {len(df_school)}행")
        except Exception as e:
            print(f"  [ERROR] 학교알리미 {year}: {e}")
            continue

        try:
            df_kess = load_kess(year)
            print(f"  KESS: {len(df_kess)}행")
            # 학교명 기준 병합
            kess_merge_cols = [c for c in df_kess.columns if c not in ("year",)]
            df_merged = df_school.merge(
                df_kess[kess_merge_cols],
                left_on="school_name",
                right_on="school_name",
                how="left"
            )
        except Exception as e:
            print(f"  [WARN] KESS {year} 병합 실패: {e}")
            df_merged = df_school

        frames.append(df_merged)

    if not frames:
        raise RuntimeError("데이터를 하나도 로드하지 못했습니다.")

    df = pd.concat(frames, ignore_index=True)
    print(f"\n[INFO] 통합 완료: {len(df)}행 x {len(df.columns)}열")

    # 파생값 계산
    df = calculate_derived(df)
    # 가명 처리
    df = anonymize(df)

    return df


def calculate_derived(df: pd.DataFrame) -> pd.DataFrame:
    """파생 컬럼 계산"""
    # 학급당 학생수 (직접 계산)
    df["students_per_class"] = np.where(
        df["class_count"] > 0,
        (df["student_count"] / df["class_count"]).round(1),
        np.nan
    )
    # 교원 1인당 학생수
    df["students_per_teacher"] = np.where(
        df["teacher_count"] > 0,
        (df["student_count"] / df["teacher_count"]).round(1),
        np.nan
    )

    # 전년 대비 변동률 (학교별)
    df = df.sort_values(["school_code", "year"]).reset_index(drop=True)
    yoy_cols = [
        "student_count", "class_count", "teacher_count", "meal_cost_total",
        "teacher_count_no_instructor",  # C1-3 강사 제외 교원 YoY
        "head_teacher_count",            # C1-5 보직교사 YoY
        "budget_revenue", "budget_expense",  # B1-3, B1-4
        "students_per_teacher",          # C1-7
    ]
    for col in yoy_cols:
        if col in df.columns:
            df[f"{col}_yoy"] = df.groupby("school_code")[col].pct_change() * 100
    # 별칭 (가이드라인 명세 일치)
    if "teacher_count_no_instructor_yoy" in df.columns:
        df["teacher_no_inst_yoy"] = df["teacher_count_no_instructor_yoy"]

    # 동료군(구별) 통계 — 전 지표
    peer_cols = [
        "student_count", "class_count", "teacher_count",
        "students_per_class", "students_per_teacher",
        "bullying_cases", "bullying_victims", "bullying_protection", "bullying_perpetrators",
        "graduation_rate", "meal_cost_total", "meal_cost_per_student",
    ]
    for col in peer_cols:
        if col in df.columns:
            df[f"{col}_dist_mean"] = df.groupby(["district", "year"])[col].transform("mean").round(1)

    return df


def anonymize(df: pd.DataFrame) -> pd.DataFrame:
    """가명 처리"""
    df["school_name_original"] = df["school_name"]
    prefix_map = {"노원": "A", "강남": "B", "관악": "C"}
    anon_map = {}
    for district in sorted(df["district"].unique()):
        p = prefix_map.get(district, "X")
        schools = sorted(df[df["district"] == district]["school_name"].unique())
        for i, s in enumerate(schools, 1):
            anon_map[s] = f"서울 {district}구 {p}{i:02d}고"
    df["school_name_anon"] = df["school_name"].map(anon_map)
    return df


def get_column_descriptions() -> dict:
    # 실명 정책: 가명 컬럼은 LLM 노출에서 제외 (school_name만 단일 출처)
    return {
        "school_name": "학교명",
        "district": "구 (노원/강남/관악)",
        "school_type": "설립유형 (공립/사립)",
        "year": "연도 (2023/2024/2025)",
        "student_count": "학생수",
        "class_count": "학급수",
        "teacher_count": "교원수",
        "students_per_class": "학급당 학생수",
        "students_per_teacher": "교원 1인당 학생수",
        "bullying_cases": "학폭 심의 건수 (1+2학기 합계)",
        "bullying_victims": "피해학생 수 (1+2학기 합계)",
        "bullying_protection": "보호조치 건수 (1+2학기 합계)",
        "meal_cost_total": "급식비 총액",
        "meal_cost_per_student": "학생 1인당 급식비 (1식)",
        "kess_student_count": "KESS 학생수 (교차검증)",
        "kess_teacher_total": "KESS 교원수 총계 (교차검증)",
        "graduation_rate": "진학률 (%)",
        "student_count_yoy": "학생수 전년대비 변동률 (%)",
        "class_count_yoy": "학급수 전년대비 변동률 (%)",
        "teacher_count_yoy": "교원수 전년대비 변동률 (%)",
        "meal_cost_total_yoy": "급식비 전년대비 변동률 (%)",
    }


if __name__ == "__main__":
    df = load_and_merge_all()
    print(f"\n{'='*60}")
    print(f"최종: {len(df)}행 x {len(df.columns)}열")
    print(f"학교 수: {df['school_name'].nunique()}")
    print(f"연도: {sorted(df['year'].unique())}")
    print(f"구 분포: {df.groupby('district')['school_name'].nunique().to_dict()}")
    print(f"\n핵심 컬럼 결측률:")
    for c in ["student_count", "class_count", "teacher_count", "bullying_cases", "kess_student_count", "graduation_rate"]:
        if c in df.columns:
            missing = df[c].isna().sum()
            print(f"  {c}: {missing}/{len(df)} ({missing/len(df)*100:.0f}%)")
    print(f"\n샘플 (2025년 상위 5교):")
    cols = ["school_name", "district", "year", "student_count", "class_count",
            "teacher_count", "students_per_class", "bullying_cases", "bullying_victims", "bullying_protection"]
    cols = [c for c in cols if c in df.columns]
    print(df[df["year"] == 2025][cols].head(5).to_string(index=False))
