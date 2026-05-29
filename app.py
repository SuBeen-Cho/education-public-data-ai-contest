"""
app.py — FastAPI 메인 서버
EduData Watch 프로토타입 백엔드
LLM: Google Gemini 2.5 flash-lite (.env에서 키 로드)
"""

import os
import re
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from data_loader import load_and_merge_all, get_column_descriptions
from rule_engine import RuleEngine, RULE_META
from priority_scorer import calculate_priority_scores, get_top_n, get_score_distribution
from safe_executor import safe_execute, SecurityError

# .env 로드
load_dotenv(Path(__file__).parent / ".env")

# Gemini SDK
try:
    from google import genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    print("[WARN] google-genai 미설치. pip install google-genai")

# 전역 상태
app_state = {}

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

SYSTEM_PROMPT = """당신은 교육 정보공시 데이터 품질 분석 전문 어시스턴트입니다.

[도메인 지식]
- 데이터: 서울 42개 일반고(노원 17, 강남 13, 관악 12), 2023~2025년
- 소스: 학교알리미(KERIS), KESS(KEDI), NEIS(교육부)
- 핵심 관계: 학생수→학급수→교원수 (인과 방향)
- 기준일: 학생수=4/1, 교육통계=상반기4/1·하반기10/1
- 교원수 비교 시 강사 제외 (학교알리미 총계에 강사 포함, KESS에는 미포함)

[정상 예외 주요 항목]
- 신설/폐교/통폐합 시 모든 지표 급변은 정상
- 학폭 공시연도 ≠ 사안 발생 연도 (전학년도 기준)
- 급식비 입력단위 '천원' 미준수 가능성
- 교육청별 증감기준 자체 설정 가능 (기본 10%)
- 가해학생 조치 미실시 시 가해학생수 미포함

[출력 규칙]
- 절대 판정하지 마세요. 금지 표현: "오류다", "잘못이다", "이상하다", "이상치", "비정상", "수상하다"
- 권장 표현만 사용: "검토 후보", "검토 신호", "확인 필요", "확인 권장"
- 학교명은 result_data·입력 데이터의 실제 학교명을 그대로 사용. "OO고등학교", "[학교명]", "A고등학교", "B고", "가명" 같은 플레이스홀더·임의 학교명 절대 금지
- 비교 시 동료군(같은 구, 같은 설립유형) 자동 적용
- 결과에 항상 신뢰도(높음/중간/낮음) 표시
- 한국어로 응답하세요
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 데이터 로드"""
    print("[INFO] 데이터 로드 중...")
    try:
        df = load_and_merge_all()
        engine = RuleEngine(df)
        detections = engine.run_all()
        scores = calculate_priority_scores(detections)

        # 42교 통일: 검토 신호가 0건인 학교도 0점으로 목록·분포에 포함
        school_meta = df[["school_code", "school_name"]].drop_duplicates(subset=["school_code"])
        existing_codes = set(scores["school_code"]) if not scores.empty else set()
        missing = school_meta[~school_meta["school_code"].astype(str).isin(existing_codes)]
        if not missing.empty:
            zero_rows = pd.DataFrame([{
                "school_code": str(row["school_code"]),
                "school_name": str(row["school_name"]),
                "score": 0, "max_star": 0, "num_categories": 0,
                "categories": "", "cat_weight_sum": 0,
                "is_repeat": False, "num_detections": 0,
            } for _, row in missing.iterrows()])
            scores = pd.concat([scores, zero_rows], ignore_index=True)
            scores = scores.sort_values("score", ascending=False).reset_index(drop=True)
            scores["rank"] = range(1, len(scores) + 1)
            print(f"[INFO] 검토 신호 없는 학교 {len(missing)}교를 0점으로 보강")

        app_state["df"] = df
        app_state["detections"] = detections
        app_state["scores"] = scores
        app_state["columns"] = get_column_descriptions()

        # Gemini 클라이언트 초기화
        api_key = os.getenv("GOOGLE_API_KEY")
        if HAS_GEMINI and api_key:
            app_state["gemini"] = genai.Client(api_key=api_key)
            print(f"[INFO] Gemini 연결 완료 (모델: {GEMINI_MODEL})")
        else:
            app_state["gemini"] = None
            if not api_key:
                print("[WARN] GOOGLE_API_KEY 미설정. LLM 기능 비활성화.")
            if not HAS_GEMINI:
                print("[WARN] google-genai 미설치. LLM 기능 비활성화.")

        print(f"[INFO] 로드 완료: {len(df)}행, 탐지 {len(detections)}건, 학교 {len(scores)}교")
    except Exception as e:
        print(f"[ERROR] 데이터 로드 실패: {e}")
        import traceback; traceback.print_exc()
        app_state["df"] = pd.DataFrame()
        app_state["detections"] = pd.DataFrame()
        app_state["scores"] = pd.DataFrame()
        app_state["columns"] = get_column_descriptions()
        app_state["gemini"] = None

    yield
    print("[INFO] 서버 종료")


app = FastAPI(title="EduData Watch", lifespan=lifespan)

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── 모델 ──
class ChatRequest(BaseModel):
    query: str
    conversation_id: str = "default"
    history: list = []
    school_code: str = ""


class ChatResponse(BaseModel):
    plan: dict
    result_data: list
    report: str
    confidence: str
    follow_up_suggestions: list
    sixbox: Optional[dict] = None   # 학교+룰 컨텍스트가 명확할 때 6박스 첨부 (없으면 None)


# ── 엔드포인트 ──
@app.get("/")
async def root():
    return FileResponse(str(static_dir / "index.html"))


# 영어→한국어 컬럼명 변환
COL_KO = {
    "school_name": "학교명", "school_code": "학교코드", "district": "지역구",
    "school_type": "설립유형", "year": "연도",
    "student_count": "학생수", "class_count": "학급수", "teacher_count": "교원수",
    "students_per_class": "학급당학생수", "students_per_class_orig": "학급당학생수(원본)",
    "students_per_teacher": "교원1인당학생수",
    "bullying_cases": "학폭건수", "bullying_victims": "피해학생수",
    "bullying_protection": "보호조치건수", "bullying_perpetrators": "가해학생수",
    "graduation_rate": "진학률(%)", "meal_cost_total": "급식비총액",
    "meal_cost_per_student": "1인당급식비",
    "teacher_total_position": "교원총계(직위별)", "instructor_count": "강사수",
    "teacher_count_no_instructor": "교원수(강사제외)",
    "head_teacher_count": "보직교사수",
    "grade1_students": "1학년 학생수", "grade2_students": "2학년 학생수", "grade3_students": "3학년 학생수",
    "budget_revenue": "학교회계 세입", "budget_expense": "학교회계 세출",
    "kess_student_count": "KESS학생수", "kess_teacher_total": "KESS교원수",
    "student_count_yoy": "학생수변동률(%)", "class_count_yoy": "학급수변동률(%)",
    "teacher_count_yoy": "교원수변동률(%)", "meal_cost_total_yoy": "급식비변동률(%)",
    "teacher_no_inst_yoy": "교원수(강사제외)변동률(%)",
}

def _rename_cols_ko(data_list: list) -> list:
    """결과 데이터의 영어 컬럼명을 한국어로 변환"""
    result = []
    for row in data_list:
        new_row = {}
        for k, v in row.items():
            # _dist_mean 등 내부 컬럼 제거
            if k.endswith("_dist_mean") or k.endswith("_dist_median") or k.startswith("school_name_"):
                continue
            ko = COL_KO.get(k, k)
            new_row[ko] = v
        result.append(new_row)
    return result


@app.get("/api/dashboard")
async def dashboard():
    scores = app_state.get("scores", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())

    if scores.empty:
        return {
            "top3": [], "distribution": {}, "category_distribution": [],
            "total_detections": 0, "total_schools": 0, "data_basis": _data_basis(df),
        }

    top3 = get_top_n(scores, 3)
    dist = get_score_distribution(scores)

    # Top3 — 대표 탐지 + 한국어 카테고리/룰명 부착
    top3_enriched = [_enrich_school_summary(row, detections, df) for _, row in top3.iterrows()]

    # 카테고리별 탐지 분포 (한국어명 포함, 코드 보조)
    cat_dist = []
    if not detections.empty:
        cat_codes = detections["rule_id"].apply(_get_category_code)
        cat_counts = cat_codes.value_counts().to_dict()
        for code, ko in CATEGORY_NAMES_KO.items():
            cnt = int(cat_counts.get(code, 0))
            if cnt > 0:
                cat_dist.append({"code": code, "ko": ko, "count": cnt})
        cat_dist.sort(key=lambda x: -x["count"])

    # 룰별 분포 (좌측 필터 accordion용) — RULE_META 전체 25개 노출, 상태 메타 동봉
    # · active + 탐지 N건 → 'n건'
    # · active + 탐지 0건 → '0건'
    # · needs_mapping → '매핑 확인 필요'
    rule_counts = detections["rule_id"].value_counts().to_dict() if not detections.empty else {}
    rule_dist = []
    for rid, meta in RULE_META.items():
        cnt = int(rule_counts.get(rid, 0))
        cat = _get_category_code(rid)
        rule_dist.append({
            "rule_id": rid,
            "rule_name_ko": RULE_NAMES_KO.get(rid, meta.get("name", rid)),
            "category_code": cat,
            "category_ko": CATEGORY_NAMES_KO.get(cat, cat),
            "count": cnt,
            "status": meta.get("status", "active"),
            "mapping_note": meta.get("mapping_note", ""),
        })
    # 정렬: 카테고리 코드 → 같은 카테고리 내 (active 먼저, 탐지 많은순, 룰ID 사전순)
    _STATUS_ORDER = {"active": 0, "needs_mapping": 1, "no_source_confirmed": 2}
    rule_dist.sort(key=lambda x: (
        x["category_code"],
        _STATUS_ORDER.get(x["status"], 99),
        -x["count"],
        x["rule_id"],
    ))

    return {
        "top3": top3_enriched,
        "distribution": dist,
        "category_distribution": cat_dist,
        "rule_distribution": rule_dist,
        "rule_status_summary": _rule_status_summary(rule_counts),
        "districts_all": _seoul_25_districts(df),
        "total_detections": len(detections),
        "total_schools": len(scores),
        "zero_schools": int(len(scores[scores["score"] < 6])) if not scores.empty else 0,
        "data_basis": _data_basis(df),
    }


def _rule_status_summary(rule_counts: dict) -> dict:
    """룰별 상태 요약 — active/needs_mapping/no_source_confirmed 카운트와 표."""
    by_status = {"active": 0, "needs_mapping": 0, "no_source_confirmed": 0}
    rows = []
    for rid, meta in RULE_META.items():
        st = meta.get("status", "active")
        by_status[st] = by_status.get(st, 0) + 1
        rows.append({
            "rule_id": rid,
            "name": RULE_NAMES_KO.get(rid, meta.get("name", rid)),
            "category": meta.get("category", ""),
            "status": st,
            "detections": int(rule_counts.get(rid, 0)),
            "mapping_note": meta.get("mapping_note", ""),
        })
    return {"by_status": by_status, "rows": rows}


@app.get("/api/rule-status")
async def rule_status():
    """룰별 구현/매핑/실행/탐지 건수 표 (UI 표용 단일 엔드포인트)."""
    detections = app_state.get("detections", pd.DataFrame())
    rule_counts = detections["rule_id"].value_counts().to_dict() if not detections.empty else {}
    return _rule_status_summary(rule_counts)


@app.get("/api/schools")
async def school_list():
    scores = app_state.get("scores", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())
    if scores.empty:
        return []
    return [_enrich_school_summary(row, detections, df) for _, row in scores.iterrows()]


@app.get("/api/school/{school_code}")
async def school_detail(school_code: str):
    detections = app_state.get("detections", pd.DataFrame())
    scores = app_state.get("scores", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())

    if detections.empty:
        raise HTTPException(404, "데이터 없음")

    school_det = detections[detections["school_code"] == school_code]
    school_score = scores[scores["school_code"] == school_code]

    if school_det.empty and school_score.empty:
        raise HTTPException(404, f"학교 {school_code} 없음")

    school_df = df[df["school_code"] == school_code].sort_values("year")
    school_name = school_det["school_name"].iloc[0] if not school_det.empty else (school_df["school_name"].iloc[0] if not school_df.empty else "")
    district = school_df["district"].iloc[0] if not school_df.empty else ""
    school_type = school_df["school_type"].iloc[0] if not school_df.empty else ""

    # 셀 상태 포함 데이터 테이블 구축
    data_table = _build_data_table(school_df, school_det, df)

    # 차트 데이터
    chart_data = _build_chart_data(school_df, df, district)

    # 검토 후보를 한국어 명칭으로 변환 + 관련 데이터 포함
    det_cards = _build_detection_cards(school_det, school_df, df, district)

    # 카테고리 한국어 목록
    cats_ko = list(set(d["category_ko"] for d in det_cards))

    result = {
        "school_code": school_code,
        "school_name": school_name,
        "district": district,
        "school_type": school_type,
        "score": float(school_score["score"].iloc[0]) if not school_score.empty else 0.0,
        "rank": int(school_score["rank"].iloc[0]) if not school_score.empty else 0,
        "is_repeat": bool(school_score["is_repeat"].iloc[0]) if not school_score.empty else False,
        "num_detections": int(school_score["num_detections"].iloc[0]) if not school_score.empty else 0,
        "num_rules": int(school_det["rule_id"].nunique()) if not school_det.empty else 0,
        "summary": {
            "detections": len(school_det),
            "categories_ko": cats_ko,
            "num_categories": len(cats_ko),
        },
        "data_table": data_table,
        "chart_data": chart_data,
        "detection_cards": det_cards,
    }

    # ⑦ 자가진단 리포트 — 학교 상세 하단용 종합 요약 (LLM 미사용, 정적 + 자동 주입)
    result["self_report"] = _build_self_report(result, school_df, df, det_cards, school_score)

    # Gemini 해석: 상단 요약 — 실패 시 안전 폴백 (임의 수치 X)
    client = app_state.get("gemini")
    if client and not school_det.empty:
        try:
            result["llm_explanation"] = _explain_with_gemini(client, school_det, school_df)
        except GeminiError as e:
            print(f"[WARN] llm_explanation Gemini 실패: {e}")
            result["llm_explanation"] = FALLBACK_AI_TEXT
        except Exception as e:
            print(f"[WARN] llm_explanation 예상치 못한 실패: {e}")
            result["llm_explanation"] = FALLBACK_AI_TEXT
    else:
        result["llm_explanation"] = "(LLM 비활성화 상태)"

    # 카테고리별 AI 해석은 별도 엔드포인트로 비동기 로드 (속도 최적화)

    return result


class CustomRequest(BaseModel):
    school_code: str = ""
    columns: list = []
    district_filter: str = "전체"
    question: str = ""


@app.post("/api/custom-analysis")
async def custom_analysis(req: CustomRequest):
    """커스텀 분석 — LLM 코드 생성 없이 서버 직접 분석"""
    df = app_state.get("df", pd.DataFrame())
    if df.empty:
        raise HTTPException(500, "데이터 미로드")

    # 지역 필터
    filtered = df.copy()
    if req.district_filter and req.district_filter != "전체":
        filtered = filtered[filtered["district"] == req.district_filter]

    # 학교 필터 (선택된 학교가 있으면 해당 학교 + 동료군)
    school_name = ""
    if req.school_code:
        school_data = filtered[filtered["school_code"] == req.school_code]
        if not school_data.empty:
            school_name = school_data["school_name"].iloc[0]

    # 선택 컬럼으로 시계열 + 동료군 데이터 추출
    cols = [c for c in req.columns if c in df.columns]
    if not cols:
        cols = ["student_count", "teacher_count"]

    years = sorted(filtered["year"].unique())
    display_cols = ["school_name", "year"] + cols

    # 결과 테이블 구축
    if req.school_code and school_name:
        # 해당 학교의 시계열
        school_rows = filtered[filtered["school_code"] == req.school_code][display_cols].sort_values("year")
        # 동료군 평균
        peer_rows = []
        for yr in years:
            yr_data = filtered[filtered["year"] == yr]
            row = {"school_name": "동료군 평균", "year": int(yr)}
            for c in cols:
                if c in yr_data.columns:
                    val = yr_data[c].mean()
                    row[c] = round(float(val), 1) if pd.notna(val) else None
            peer_rows.append(row)
        result_data = school_rows.to_dict(orient="records") + peer_rows
    else:
        # 전체 학교 비교
        result_data = filtered[display_cols].sort_values(["year", "school_name"]).head(30).to_dict(orient="records")

    # 변동률 계산
    anomalies = []
    if req.school_code:
        school_sorted = filtered[filtered["school_code"] == req.school_code].sort_values("year")
        for c in cols:
            vals = school_sorted[c].dropna().values
            if len(vals) >= 2:
                change = (vals[-1] - vals[-2]) / vals[-2] * 100 if vals[-2] != 0 else 0
                if abs(change) >= 10:
                    col_label = next((lbl for lbl, key in TABLE_METRICS if key == c), c)
                    anomalies.append(f"{col_label} 전년대비 {change:+.1f}%")

    # LLM 해석 (결과 데이터 기반, 코드 생성 아님)
    client = app_state.get("gemini")
    ai_result = {"해석": "", "정상사유": "", "확인권장": ""}
    if client:
        try:
            data_summary = json.dumps(result_data[:8], ensure_ascii=False, default=str)
            col_labels = [next((lbl for lbl, key in TABLE_METRICS if key == c), c) for c in cols]
            anomaly_text = ", ".join(anomalies) if anomalies else "특이사항 없음"

            target_label = school_name if school_name else "전체 학교 비교"
            prompt = f"""{target_label}의 {', '.join(col_labels)} 분석 결과:
데이터: {data_summary}
변동률 신호: {anomaly_text}

정확히 3줄만 응답:
해석: (핵심 발견 1~2문장)
정상사유: (가능한 이유 쉼표 구분)
확인권장: (담당자 행동 1문장)

주의: "이상치", "비정상" 단정 표현 금지. 학교명은 입력 데이터의 실제 학교명만 사용. "OO고등학교", "[학교명]", "A고등학교" 같은 플레이스홀더 금지."""
            text = _call_gemini(client, prompt)
            for line in text.strip().split("\n"):
                line = line.strip()
                if line.startswith("해석:"):
                    ai_result["해석"] = line[3:].strip()
                elif "정상" in line and ":" in line:
                    ai_result["정상사유"] = line.split(":", 1)[1].strip()
                elif "확인" in line and ":" in line:
                    ai_result["확인권장"] = line.split(":", 1)[1].strip()
            if not ai_result["해석"]:
                ai_result["해석"] = text.strip()[:200]
        except Exception as e:
            # 사용자 노출 문구는 통일된 폴백으로. 개발자 메시지는 서버 로그로만.
            print(f"[WARN] custom_analysis AI 해석 실패: {e}")
            ai_result["해석"] = FALLBACK_AI_TEXT

    # 결과 데이터 숫자 정리
    for row in result_data:
        for k, v in row.items():
            if isinstance(v, float):
                row[k] = round(v, 1) if v != int(v) else int(v)

    # 이상값 셀 위치 계산 (10%+ 변동)
    highlight_cells = []
    if req.school_code and len(years) >= 2:
        school_sorted = filtered[filtered["school_code"] == req.school_code].sort_values("year")
        for c in cols:
            vals = school_sorted[c].dropna().values
            col_label = next((lbl for lbl, key in TABLE_METRICS if key == c), c)
            if len(vals) >= 2:
                for j in range(1, len(vals)):
                    if vals[j-1] != 0:
                        chg = abs((vals[j] - vals[j-1]) / vals[j-1] * 100)
                        if chg >= 10:
                            highlight_cells.append({"row": j, "col": col_label})

    return {
        "columns": [next((lbl for lbl, key in TABLE_METRICS if key == c), c) for c in cols],
        "result_data": _rename_cols_ko(result_data),
        "anomalies": anomalies,
        "highlight_cells": highlight_cells,
        "ai": ai_result,
        "school_name": school_name,
        "confidence": "높음" if anomalies else "중간",
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat_explore(req: ChatRequest):
    """자연어 챗봇. 어떤 실패에서도 500 대신 안전한 ChatResponse 반환."""
    df_full = app_state.get("df", pd.DataFrame())
    scores = app_state.get("scores", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())

    if df_full.empty:
        _log_route("fallback_help", req.query)
        return _chat_fallback_response("데이터가 아직 로드되지 않았습니다.", req.query)

    # ── deterministic guards (Gemini 호출 전 1차 분기) ──
    # 의도가 명확하고 LLM이 답할 게 없는 입력만 가로챔. 애매하면 Gemini로.
    # 학교 컨텍스트가 없을 때만 적용 (학교 상세 안에서의 후속 질문은 모두 LLM으로).
    if not req.school_code:
        # 1) 인사 + 무의미 입력
        gr = _handle_greeting_query(req.query)
        if gr is not None:
            _log_route(gr["plan"]["analysis_plan"].split(": ")[-1], req.query)
            return ChatResponse(**gr)
        # 2) 감사/종료
        th = _handle_thanks_query(req.query)
        if th is not None:
            _log_route("thanks", req.query)
            return ChatResponse(**th)
        # 3) 사용법/정체성
        hp = _handle_help_query(req.query)
        if hp is not None:
            _log_route("help", req.query)
            return ChatResponse(**hp)
        # 4) 데이터 범위 밖 (날씨·주식·시간 등 명백한 무관 의도)
        rg = _handle_range_guard_query(req.query)
        if rg is not None:
            _log_route("range_guard", req.query)
            return ChatResponse(**rg)
        # 5) 우선순위/Top/N위 — scores/detections로 직접 응답
        pri = _handle_priority_query(req.query, df_full, scores, detections)
        if pri is not None:
            _log_route("priority", req.query)
            return ChatResponse(**pri)

    # 학교 컨텍스트 필터
    df = df_full
    if req.school_code:
        df = df[df["school_code"] == req.school_code].copy()
        if df.empty:
            _log_route("fallback_help", req.query)
            return _chat_fallback_response(f"학교 코드 {req.school_code}의 데이터를 찾지 못했습니다.", req.query)

    columns_desc = app_state.get("columns", {})
    client = app_state.get("gemini")

    if not client:
        _log_route("fallback_help", req.query)
        return _fallback_analysis(req.query, df, columns_desc)

    # 학교명 조회 (실명만)
    school_name_for_report = ""
    if req.school_code:
        sn = df_full[df_full["school_code"] == req.school_code]["school_name"]
        if not sn.empty:
            school_name_for_report = str(sn.iloc[0])

    # ── 1단계: 분석 계획 ──
    try:
        plan = _get_analysis_plan(client, req.query, columns_desc, req.history)
    except GeminiError as e:
        print(f"[WARN] chat plan Gemini 실패: {e}")
        _log_route("fallback_help", req.query)
        return _chat_fallback_response(FALLBACK_AI_TEXT, req.query)

    # JSON 파싱 실패(plan is None) — 기본 코드 fallback 폐기, 안내 응답으로
    if plan is None:
        _log_route("fallback_help", req.query)
        return ChatResponse(**_build_fallback_help_response())

    # ── 2단계: 안전 실행 ──
    # 정책: 기본 데이터표 fallback 제거. 실행 실패는 안내 응답으로, 정상 빈 결과는 별도 안내.
    code = plan.get("pandas_code", "").strip()
    if not code:
        # LLM이 pandas_code를 못 만든 경우 — 분석 의도 불명확
        print(f"[WARN] pandas_code 비어 있음. query='{req.query[:60]}'")
        _log_route("fallback_help", req.query)
        return ChatResponse(**_build_fallback_help_response())

    try:
        result_df = safe_execute(code, df)
    except Exception as e:
        # 코드 생성은 됐는데 실행 단계 실패 — 안내 응답으로 (기본 데이터표 X)
        print(f"[WARN] pandas_code 실행 실패: {e}")
        _log_route("fallback_help", req.query)
        return ChatResponse(**_build_fallback_help_response())

    # ── 3단계: 결과 정리 (가명 컬럼 제거) ──
    is_empty_df = isinstance(result_df, pd.DataFrame) and result_df.empty
    if isinstance(result_df, pd.DataFrame):
        # school_name_anon이 결과에 섞여 들어오면 제거 (실명 정책)
        if "school_name_anon" in result_df.columns:
            result_df = result_df.drop(columns=["school_name_anon"])
        result_data_raw = result_df.head(20).to_dict(orient="records")
    else:
        result_data_raw = []
    result_data = _rename_cols_ko(result_data_raw)

    # 정상 실행 + 결과 0건 → "실패"가 아닌 정상 빈 결과 안내. 기본 데이터표 절대 X.
    if is_empty_df or not result_data:
        _log_route("empty_result", req.query)
        return ChatResponse(**_build_empty_result_response(plan))

    # ── 4단계: 보고서 생성 (실패 시 폴백) ──
    try:
        report = _generate_report(client, plan, result_data, req.query, school_name_for_report)
    except GeminiError as e:
        print(f"[WARN] chat report Gemini 실패: {e}")
        report = FALLBACK_AI_TEXT
        plan["confidence"] = "중간"

    suggestions = _generate_suggestions(plan, result_data)
    _log_route("llm", req.query)

    return ChatResponse(
        plan=plan, result_data=result_data,
        report=report,
        confidence=plan.get("confidence", "중간"),
        follow_up_suggestions=suggestions,
        sixbox=None,
    )


def _build_fallback_help_response() -> dict:
    """LLM이 코드 못 만들거나 실행 실패 — 안내 응답. 기본 데이터표 X."""
    return {
        "plan": {
            "analysis_plan": "코드 생성/실행 단계에서 의도를 확정하지 못함",
            "columns_used": [],
            "criteria": "",
            "pandas_code": "",
            "comparison": "",
            "confidence": "낮음",
        },
        "result_data": [],
        "report": (
            "질문을 이해하지 못했습니다. **학교명·지역·룰 ID·검토 신호** 중심으로 물어봐 주세요.\n\n"
            "예: '노원구에서 학생수가 줄어든 학교' / '강남구 검토 우선도 1위 학교는?' / 'C5-1 진급 이탈 잡힌 학교'"
        ),
        "confidence": "낮음",
        "follow_up_suggestions": _EXAMPLE_SUGGESTIONS,
        "sixbox": None,
    }


def _build_empty_result_response(plan: dict) -> dict:
    """정상 실행됐지만 결과 0건 — 실패 아님. 조건 조정 안내."""
    return {
        "plan": plan,
        "result_data": [],
        "report": "조건에 맞는 학교가 없습니다. 조건을 조금 넓혀 다시 확인해 주세요.",
        "confidence": "높음",
        "follow_up_suggestions": _EXAMPLE_SUGGESTIONS,
        "sixbox": None,
    }


def _chat_fallback_response(message: str, query: str) -> ChatResponse:
    """챗봇 응답 안전 폴백 (500 방지)"""
    return ChatResponse(
        plan={
            "analysis_plan": "AI 응답 지연·실패로 인한 안전 폴백",
            "columns_used": [],
            "criteria": "",
            "pandas_code": "",
            "comparison": "",
            "confidence": "낮음",
        },
        result_data=[],
        report=message,
        confidence="낮음",
        follow_up_suggestions=[
            "검토 우선 후보를 다시 보여줘",
            "우선 검토 신호만 요약해줘",
            "강남구 검토 후보만 보여줘",
        ],
        sixbox=None,
    )


# ──────────────────────────────────────────────────────────────
# 챗봇 라우팅 / 가드 (Gemini 호출 전 deterministic 분기)
# ──────────────────────────────────────────────────────────────
# 라우팅 로그/카운터 — 내부용. 사용자 화면 노출 X.
# 나중에 "LLM 호출 없이 처리한 질의 비율" 설명용으로 카운트만 남김.
ROUTE_COUNTER: dict = {}


def _log_route(route: str, query: str):
    """라우팅 단계 카운트 + 로그 한 줄."""
    ROUTE_COUNTER[route] = ROUTE_COUNTER.get(route, 0) + 1
    safe = (query or "").replace("\n", " ")[:60]
    print(f"[CHAT] route={route} · query='{safe}'")


# 인사·감사·도움말은 "전체 메시지가 그 의도일 때만" 처리.
# substring 매칭으로 잡으면 "안녕하세요, 개포고 학생수 알려줘"도 잡혀 실제 질의가 사라짐.
# → 접두어 strip 후 남은 실질 질의가 없을 때만 가드 적용.

_GREETING_PREFIXES = (
    "안녕하세요", "안녕히 계세요", "안녕히 가세요", "안녕",
    "반가워요", "반갑습니다", "반가워", "반갑",
    "하이요", "하이", "ㅎㅇ", "hi", "hello", "hey",
    "방가워요", "방가",
    "굿모닝", "굿이브닝", "굿밤",
)
_THANKS_PREFIXES = (
    "고맙습니다", "고마워요", "고마워", "고맙",
    "감사합니다", "감사해요", "감사",
    "수고하셨습니다", "수고했어", "수고",
    "잘 가", "잘가", "잘 있어", "안녕히",
    "thank you", "thanks", "thx", "ty",
)
_HELP_PHRASES = (
    "넌 누구", "너는 누구", "당신은 누구", "넌 뭐", "너 뭐",
    "뭐 할 수 있", "뭐할 수 있", "뭐가 가능", "할 수 있는 게",
    "사용법", "어떻게 써", "어떻게 사용", "어떻게 쓰", "어떻게 동작",
    "도움말", "헬프", "help", "사용 방법",
    "어떤 질문", "어떤거 물어", "뭐 물어",
    "넌 뭐야", "너는 뭐야", "정체",
)
# 의미 없는 짧은 입력 — 멀티턴 컨텍스트는 안 쓰므로 "?" 단독도 무의미 입력.
_NOISE_INPUTS = ("ㅋㅋ", "ㅋㅋㅋ", "ㅎㅎ", "ㅎㅎㅎ", "ㅠㅠ", "ㅠ", "ㅗㅜ", "ㅇㅇ", "응", "넵", "넹", "...", "..", ".", "?", "??", "???")

# 데이터 범위 밖 키워드 — 명백히 공시·교육 데이터와 무관한 질의
_OUT_OF_SCOPE_KEYWORDS = (
    "날씨", "기온", "비 와", "눈 와",
    "주식", "코인", "비트코인", "환율", "금리",
    "오늘 몇", "지금 몇 시", "지금 시각",
    "요리", "레시피", "음식 추천", "맛집",
    "노래 추천", "영화 추천", "드라마 추천",
    "축구", "야구", "농구",
    "번역", "영어로", "한자로",
    "코딩", "프로그래밍", "파이썬", "javascript",
    "친구", "연애", "사주", "운세",
)


def _strip_prefixes(q_low: str, prefixes: tuple) -> str:
    """질의에서 인사/감사 접두어를 길이 긴 순으로 한 번 제거. 남은 문자열 반환."""
    candidates = sorted(prefixes, key=lambda p: -len(p))
    for kw in candidates:
        if q_low.startswith(kw.lower()):
            rest = q_low[len(kw):].lstrip(" ,.!?~^^。，！？")
            return rest
    return q_low


def _is_noise_only(q: str) -> bool:
    """무의미 입력(이모티콘·자음 반복 등) 단독 여부."""
    if not q:
        return True
    if q in _NOISE_INPUTS:
        return True
    # 한글 자모/문장부호만 있고 의미 있는 글자 없는 경우
    if len(q) <= 3 and not any(c.isalnum() and ord(c) > 127 for c in q) and not any(c.isalnum() and c.isascii() for c in q):
        # alphanumeric 없음 + 한글 음절(가-힣) 없음 → 의미 X
        if not any('가' <= c <= '힣' for c in q):
            return True
    return False


def _build_simple_response(report: str, suggestions: list, route: str) -> dict:
    """공통 안내 응답 빌더 — guard 분기 공용."""
    return {
        "plan": {
            "analysis_plan": f"deterministic guard: {route}",
            "columns_used": [],
            "criteria": "",
            "pandas_code": "",
            "comparison": "",
            "confidence": "높음",
        },
        "result_data": [],
        "report": report,
        "confidence": "높음",
        "follow_up_suggestions": suggestions,
        "sixbox": None,
    }


_EXAMPLE_SUGGESTIONS = [
    "강남구 검토 우선도 1위 학교는?",
    "우선 검토 신호만 요약해줘",
    "노원구에서 학생수가 줄어든 학교",
    "학교폭력 조치 확인 신호가 있는 학교는?",
]


def _handle_greeting_query(query: str):
    """인사 가드 — 전체 메시지가 인사이거나, 인사 접두어 제거 후 남은 질의가 없을 때만 처리.
    'ㅋㅋ' 같은 무의미 입력도 함께 처리."""
    q = (query or "").strip()
    if not q:
        return None
    # 1) 무의미 입력 단독
    if _is_noise_only(q):
        report = (
            "메시지가 비어 있거나 인식하지 못했습니다. 공시 데이터에 대해 질문해 주세요.\n\n"
            "예: 노원구에서 학생수가 줄어든 학교 / 강남구 검토 우선도 1위 학교는?"
        )
        return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="empty_input")
    # 2) 인사 접두어 strip → 남은 질의 있으면 greeting 아님
    rest = _strip_prefixes(q.lower(), _GREETING_PREFIXES)
    if rest:
        return None
    report = (
        "안녕하세요. **EduData Watch 공시 데이터 챗봇**입니다.\n\n"
        "공시 데이터에 자연어로 질문해 보세요. 예시:\n"
        "- 강남구 검토 우선도 1위 학교는?\n"
        "- 우선 검토 신호만 요약해줘\n"
        "- 노원구에서 학생수가 줄어든 학교\n"
        "- 학교 상세 화면에서 이 학교 1분 브리핑 생성\n\n"
        "본 챗봇은 판정하지 않고 확인을 돕습니다. 자유롭게 물어봐 주세요."
    )
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="greeting")


def _handle_thanks_query(query: str):
    """감사/종료 — 전체 메시지가 감사 의도일 때만."""
    q = (query or "").strip()
    if not q or len(q) > 30:
        return None
    rest = _strip_prefixes(q.lower(), _THANKS_PREFIXES)
    if rest:
        return None
    report = "도움이 됐다면 다행입니다. 추가로 확인이 필요한 학교나 검토 신호가 있으면 언제든 물어봐 주세요."
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="thanks")


def _handle_help_query(query: str):
    """사용법/정체성 안내 — 핵심 문구 substring 매칭 (이건 의도가 명확해서 substring OK)."""
    q = (query or "").strip()
    if not q or len(q) > 40:
        return None
    q_low = q.lower()
    if not any(p.lower() in q_low for p in _HELP_PHRASES):
        return None
    report = (
        "**EduData Watch 공시 데이터 챗봇**입니다.\n\n"
        "교육 공시 데이터(서울 3구 일반고 42교·2023~2025년)에 자연어로 질문하면, "
        "결정론적 룰셋과 데이터 분석으로 검토 후보를 보여드립니다.\n\n"
        "물어볼 수 있는 것:\n"
        "- 학교명·구·룰 ID·검토 우선도 기준 조회\n"
        "- 학생수·교원수·학폭·진학률·급식비 등 지표 변화\n"
        "- 동료군(같은 구) 비교\n"
        "- 우선순위·Top N\n\n"
        "본 챗봇은 판정하지 않고 확인을 돕습니다."
    )
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="help")


def _handle_range_guard_query(query: str):
    """공시 데이터 범위 밖 — 명백히 무관한 의도 (날씨·주식·시간 등)만 가드."""
    q = (query or "").strip()
    if not q:
        return None
    q_low = q.lower()
    if not any(k in q_low for k in _OUT_OF_SCOPE_KEYWORDS):
        return None
    report = (
        "본 챗봇은 **교육 공시 데이터** 분석 도구입니다.\n\n"
        "현재 표본 범위: 서울 **강남·노원·관악구** 일반고 **42교**, **2023~2025년** 공시.\n\n"
        "이 범위 안에서 학교·지표·검토 신호에 대해 질문해 주세요."
    )
    return _build_simple_response(report, _EXAMPLE_SUGGESTIONS, route="range_guard")


@app.get("/api/chat/route-stats")
async def chat_route_stats():
    """챗봇 라우팅 카운터 (내부용). 사용자 화면 노출 X.
    LLM 호출 없이 처리한 비율 등을 발표·심사 자료로 활용.

    분류:
    - pre_llm_guard: Gemini 호출 전 deterministic 분기 (greeting/thanks/help/range_guard/priority/empty_input)
    - llm_post: Gemini 거친 응답 (llm 정상 + empty_result 정상 빈결과 + fallback_help 실패안내)
    """
    total = sum(ROUTE_COUNTER.values()) or 0
    by_route = dict(sorted(ROUTE_COUNTER.items(), key=lambda kv: -kv[1]))

    pre_llm_routes = ("greeting", "thanks", "help", "priority", "range_guard", "empty_input")
    pre_llm = sum(ROUTE_COUNTER.get(r, 0) for r in pre_llm_routes)

    llm_post_routes = ("llm", "empty_result", "fallback_help")
    llm_post = sum(ROUTE_COUNTER.get(r, 0) for r in llm_post_routes)

    return {
        "total": total,
        "by_route": by_route,
        "pre_llm_guard_count": pre_llm,
        "llm_post_count": llm_post,
        "pre_llm_ratio": round(pre_llm / total, 3) if total else 0.0,
        "llm_post_ratio": round(llm_post / total, 3) if total else 0.0,
        # 세부 (LLM 거친 것 중 정상 vs 빈 결과 vs 실패)
        "llm_breakdown": {
            "llm_success": ROUTE_COUNTER.get("llm", 0),
            "empty_result": ROUTE_COUNTER.get("empty_result", 0),
            "fallback_help": ROUTE_COUNTER.get("fallback_help", 0),
        },
    }


# ── #6: 우선순위·Top 질의는 LLM 우회, scores/detections로 직접 응답 ──
_PRIORITY_KEYWORDS = ("우선순위", "가장 높은", "가장 우선", "최우선", "상위", "top", "TOP", "Top", "1위", "검토 우선")
_DISTRICTS_KO = ("강남", "노원", "관악")


def _handle_priority_query(query: str, df: pd.DataFrame, scores: pd.DataFrame, detections: pd.DataFrame):
    """'우선순위가 가장 높은 학교' 류 질문 → scores 기준 응답. 매칭 안 되면 None."""
    if scores is None or scores.empty or df is None or df.empty:
        return None
    q = (query or "").strip()
    if not any(k in q for k in _PRIORITY_KEYWORDS):
        return None

    # district 필터
    district = next((d for d in _DISTRICTS_KO if d in q), None)
    scope = scores
    scope_label = "전체"
    if district:
        district_codes = set(df[df["district"] == district]["school_code"].astype(str))
        scope = scores[scores["school_code"].astype(str).isin(district_codes)].copy()
        scope_label = f"{district}구"
    if scope.empty:
        return None

    # 표시 건수 N
    m = re.search(r"top\s*(\d+)|상위\s*(\d+)|(\d+)\s*위|(\d+)\s*교", q.lower())
    n = 5
    if m:
        nums = [int(g) for g in m.groups() if g]
        if nums:
            n = nums[0]
    elif any(k in q for k in ("1위", "가장 높은", "가장 우선", "최우선")):
        n = 1
    n = max(1, min(n, 10))

    top = scope.head(n)

    result_data = []
    for _, row in top.iterrows():
        rep = _representative_detection(detections, row["school_code"])
        rep_text = ""
        if rep is not None:
            rid = str(rep.get("rule_id", ""))
            rep_text = f"{RULE_NAMES_KO.get(rid, rid)} ({rid})"
        sf = df[df["school_code"] == row["school_code"]]
        district_v = str(sf["district"].iloc[0]) if not sf.empty else ""
        result_data.append({
            "순위": int(row.get("rank", 0)),
            "학교명": str(row["school_name"]),
            "지역구": district_v,
            "검토 우선도 지수": f"{float(row.get('score', 0)):.1f}",
            "검토 신호 수": int(row.get("num_detections", 0)),
            "관련 카테고리 수": int(row.get("num_categories", 0)),
            "반복 신호": "반복" if bool(row.get("is_repeat", False)) else "단발",
            "대표 검토 신호": rep_text,
        })

    sixbox_attached = None
    if n == 1 and result_data:
        s = top.iloc[0]
        rep = _representative_detection(detections, s["school_code"])
        rep_line = ""
        if rep is not None:
            rid = str(rep.get("rule_id", ""))
            rep_line = f" 대표 검토 신호: {RULE_NAMES_KO.get(rid, rid)}({rid}) — {rep.get('detail','')}."
            # 챗봇 응답에 대표 룰 6박스 첨부 — 학교+룰 컨텍스트 명확
            try:
                school_df_one = df[df["school_code"] == str(s["school_code"])]
                district_one = str(school_df_one["district"].iloc[0]) if not school_df_one.empty else ""
                dyn_label = str(rep.get("col_label", "") or "").strip()
                dyn_key = str(rep.get("col_key", "") or "").strip()
                if dyn_key:
                    col_pairs_one = [{"key": dyn_key, "label": _canon_label(dyn_label or dyn_key)}]
                else:
                    col_pairs_one = [{"key": k, "label": _canon_label(label)} for label, k in RULE_COLUMNS.get(rid, [])]
                rule_obj_one = {
                    "rule_id": rid,
                    "rule_name_ko": RULE_NAMES_KO.get(rid, rid),
                    "year": int(rep.get("year", 0)),
                    "detail": str(rep.get("detail", "")),
                    "col_labels": [p["label"] for p in col_pairs_one],
                    "col_keys": [p["key"] for p in col_pairs_one],
                    "col_pairs": col_pairs_one,
                }
                sixbox_attached = _build_sixbox(rule_obj_one, school_df_one, df, district_one)
            except Exception as e:
                print(f"[WARN] priority sixbox 생성 실패: {e}")
                sixbox_attached = None
        report = (
            f"{scope_label} 기준 검토 우선도가 가장 높은 학교는 **{s['school_name']}**입니다 "
            f"(검토 우선도 지수 {float(s['score']):.1f}, 전체 순위 {int(s['rank'])}위, "
            f"검토 신호 {int(s.get('num_detections', 0))}건, {int(s.get('num_categories', 0))}개 카테고리).{rep_line}\n\n"
            f"본 답변은 앱 내부의 검토 우선도 지수(scores)와 룰 기반 검토 신호(detections)를 그대로 사용한 결과이며, "
            f"추가 분석 기준을 만들지 않았습니다. 지수는 학교 평가가 아니라 확인 순서를 돕는 내부 분석값입니다."
        )
    else:
        report = (
            f"{scope_label} 기준 검토 우선도 상위 {len(result_data)}교를 추출했습니다. "
            f"이 결과는 앱의 검토 우선 후보와 동일한 기준(검토 우선도 지수·룰 기반 검토 신호)을 사용합니다."
        )

    return {
        "plan": {
            "analysis_plan": f"{scope_label} 학교들을 검토 우선도 지수 기준 정렬 → 상위 {n}교 추출",
            "columns_used": ["score", "rank", "num_detections", "num_categories", "is_repeat"],
            "criteria": "검토 우선도 지수 = 핵심 신호 강도 + 영역 가중치 + 반복 신호 가중치 (내부 분석 지수, 학교 평가 점수 아님)",
            "pandas_code": "",
            "comparison": "scores DataFrame 사용 (앱 내부 통일 기준)",
            "confidence": "높음",
        },
        "result_data": result_data,
        "report": report,
        "confidence": "높음",
        "follow_up_suggestions": [
            "1순위 학교 상세 보여줘",
            "우선 검토 신호만 요약해줘",
            "강남구 검토 후보만 보여줘" if district != "강남" else "전체 검토 후보 보여줘",
            "이 학교들의 공통 검토 신호는?",
        ],
        "sixbox": sixbox_attached,
    }


@app.get("/api/school/{school_code}/ai/{category_ko}")
async def category_ai(school_code: str, category_ko: str):
    """카테고리별 AI 해석 (비동기 로드). 실패 시 안전 폴백 (UI 빈칸 방지)."""
    client = app_state.get("gemini")
    if not client:
        return {**FALLBACK_CATEGORY, "해석": "LLM 비활성화 상태입니다."}

    detections = app_state.get("detections", pd.DataFrame())
    df = app_state.get("df", pd.DataFrame())
    school_det = detections[detections["school_code"] == school_code]
    school_df = df[df["school_code"] == school_code]
    school_name = school_df["school_name"].iloc[0] if not school_df.empty else ""
    district = school_df["district"].iloc[0] if not school_df.empty else ""

    det_cards = _build_detection_cards(school_det, school_df, df, district)
    card = next((c for c in det_cards if c["category_ko"] == category_ko), None)
    if not card:
        return {**FALLBACK_CATEGORY, "해석": "해당 카테고리 데이터가 없습니다."}

    try:
        return _explain_category(client, card, school_name, district)
    except GeminiError as e:
        print(f"[WARN] category_ai Gemini 실패 ({school_code}/{category_ko}): {e}")
        return dict(FALLBACK_CATEGORY)
    except Exception as e:
        print(f"[WARN] category_ai 예상치 못한 실패 ({school_code}/{category_ko}): {e}")
        return dict(FALLBACK_CATEGORY)


@app.get("/api/stats")
async def stats():
    df = app_state.get("df", pd.DataFrame())
    detections = app_state.get("detections", pd.DataFrame())
    # 내부 룰 메타(star)는 노출 명칭을 신호 강도 기준으로 변경. UI 분기에는 쓰지 않음.
    return {
        "data_rows": len(df),
        "schools": df["school_name"].nunique() if not df.empty else 0,
        "years": sorted(df["year"].unique().tolist()) if not df.empty else [],
        "total_detections": len(detections),
        "priority_signal_count": len(detections[detections["star"] == 3]) if not detections.empty else 0,
        "normal_signal_count": len(detections[detections["star"] == 2]) if not detections.empty else 0,
        "rules_triggered": detections["rule_id"].nunique() if not detections.empty else 0,
    }


# ── 한국어 명칭 매핑 ──

RULE_NAMES_KO = {
    "C1-1": "학생↔학급 역방향 변동", "C1-2": "학생↔학급 완만 역방향 변동",
    "C1-3": "학생↔교원 불균형", "C1-4": "학급↔교원 불균형",
    "C1-5": "학생↔보직교사 불균형", "C1-7": "교원1인당학생수 급변",
    "C1-8": "학급당학생수 급변",
    "C3-3A": "미조치 피해 (강력)", "C3-3B": "미조치 피해 (참고)",
    "B1-1": "학생·학급·교원 급변동(이중)", "B1-2": "학생·학급·교원 급변동(단년)",
    "B1-3": "학교회계 변동", "B1-4": "학교회계 강한 변동",
    "B1-5": "진학률 급변동", "B1-6": "학폭 심의 급증",
    "C2-3": "급식비 변동", "C2-3+": "급식비 강한 변동",
    "D2-1": "유사학교 대비 상하위 10%", "D2-2": "유사학교 대비 극단값",
    "C5-1": "진급 시 학생 이탈",
    "E1-1": "3년 연속 미입력", "E1-2": "단독 미입력 (동료군 다 입력)",
    "E1-3": "공시 의무 항목 미제출",
    "E2-2": "3년 동일값 반복",
    "F1'-1": "교원수 교차 불일치",
    # G1-1: 본교 단일 시계열의 누적 단조 추세를 점검.
    # 동료군 대비 비교(방향 역행 / 변동성 차이)는 G1-2 / G1-3 후속 룰로 검토.
    "G1-1": "다년 단조 추세",
}

CATEGORY_NAMES_KO = {
    "C1": "학생·자원 연동 점검", "C3": "미조치 피해 점검",
    "B1": "전년 대비 급변동", "C2": "학생·재정 연동 점검",
    "D2": "유사학교 대비 편차", "C5": "학년 진급 인원 점검",
    "E1": "누락 패턴 점검", "E2": "수치 미갱신 점검",
    "F1": "연계 시점 차이 점검",
    "G1": "장기 추세 점검",
}

# 데이터 테이블에 표시할 지표 목록 (단일 라벨, 중복 행 없음).
# RULE_COLUMNS·차트 등에서 다른 라벨로 들어와도 LABEL_ALIAS로 정규화하여 같은 행으로 매칭.
TABLE_METRICS = [
    ("학생수", "student_count"),
    ("1학년 학생수", "grade1_students"),
    ("2학년 학생수", "grade2_students"),
    ("학급수", "class_count"),
    ("교원수", "teacher_count"),
    ("교원수(강사제외)", "teacher_count_no_instructor"),
    ("보직교사수", "head_teacher_count"),
    ("학급당학생수", "students_per_class"),
    ("교원1인당학생수", "students_per_teacher"),
    ("학폭 심의건수", "bullying_cases"),
    ("피해학생수", "bullying_victims"),
    ("보호조치건수", "bullying_protection"),
    ("가해학생수", "bullying_perpetrators"),
    ("진학률(%)", "graduation_rate"),
    ("급식비총액(천원)", "meal_cost_total"),
    ("1인당급식비(원)", "meal_cost_per_student"),
    ("학교회계 세입", "budget_revenue"),
    ("학교회계 세출", "budget_expense"),
]

# 라벨 동의어 — 다양한 곳에서 들어오는 라벨을 정식 라벨로 정규화
LABEL_ALIAS = {
    "진학률": "진학률(%)",
    "급식비총액": "급식비총액(천원)",
    "급식비 총액": "급식비총액(천원)",
    "1인당급식비": "1인당급식비(원)",
    "1인당 급식비": "1인당급식비(원)",
    "학폭건수": "학폭 심의건수",
    # 시설 (rule_engine._fc_name이 내려주는 라벨과 chart_cols 라벨 통일)
    "기숙사": "기숙사실수",
}

def _canon_label(label: str) -> str:
    """라벨 정규화 — 별칭이 있으면 정식 라벨, 없으면 원본."""
    return LABEL_ALIAS.get(label, label)


def _build_data_table(school_df, detections_df, full_df) -> list:
    """셀 상태 포함 시계열 데이터 테이블"""
    if school_df.empty:
        return []

    years = sorted(school_df["year"].unique())
    district = school_df["district"].iloc[0] if "district" in school_df.columns else ""

    # 탐지된 (연도, 컬럼) 쌍 수집 — E2-2 등 동적 컬럼 룰은 detection.col_key를 우선 사용
    detected_cells = set()
    for _, d in detections_df.iterrows():
        yr = int(d.get("year", 0))
        rid = d.get("rule_id", "")
        dyn_col = str(d.get("col_key", "") or "").strip()
        if dyn_col:
            detected_cells.add((yr, dyn_col))
        else:
            for _, col_key in RULE_COLUMNS.get(rid, []):
                detected_cells.add((yr, col_key))

    rows = []
    for label, col in TABLE_METRICS:
        if col not in school_df.columns:
            continue
        row = {"지표": label, "col_key": col}
        # 동료군 평균 — school_df에 이미 _dist_mean이 계산되어 있음
        peer_col = f"{col}_dist_mean"
        if peer_col in school_df.columns:
            last_yr = school_df[school_df["year"] == years[-1]]
            if not last_yr.empty and pd.notna(last_yr[peer_col].iloc[0]):
                row["동료군평균"] = round(float(last_yr[peer_col].iloc[0]), 1)
            else:
                row["동료군평균"] = None
        else:
            row["동료군평균"] = None

        for yr in years:
            yr_row = school_df[school_df["year"] == yr]
            if yr_row.empty:
                row[str(yr)] = {"value": None, "status": "empty"}
                continue
            val = yr_row[col].iloc[0]
            if pd.isna(val):
                row[str(yr)] = {"value": None, "status": "empty"}
                continue

            val = float(val)
            status = "normal"
            if (yr, col) in detected_cells:
                status = "detected"

            row[str(yr)] = {"value": round(val, 1) if val != int(val) else int(val), "status": status}

        rows.append(row)
    return rows


def _build_chart_data(school_df, full_df, district) -> dict:
    """Chart.js용 데이터 — 본교 시계열 + 동료군 평균 + 동료군 범위(min/max).
    룰 단위 Evidence Chart에서 룰 코드별로 표시 컬럼을 선택해서 사용."""
    if school_df.empty:
        return {}

    years = sorted(school_df["year"].unique())
    labels = [int(y) for y in years]

    def _peer_subset(y):
        return full_df[(full_df["district"] == district) & (full_df["year"] == y)] if district else full_df[full_df["year"] == y]

    def get_vals(col):
        out = []
        for y in years:
            sub = school_df[school_df["year"] == y]
            if sub.empty or pd.isna(sub[col].iloc[0]):
                out.append(None)
            else:
                out.append(round(float(sub[col].iloc[0]), 1))
        return out

    def get_peer_mean(col):
        out = []
        for y in years:
            sub = _peer_subset(y)[col].dropna()
            out.append(round(float(sub.mean()), 1) if len(sub) > 0 else None)
        return out

    def get_peer_minmax(col):
        mins, maxs = [], []
        for y in years:
            sub = _peer_subset(y)[col].dropna()
            if len(sub) > 0:
                mins.append(round(float(sub.min()), 1))
                maxs.append(round(float(sub.max()), 1))
            else:
                mins.append(None); maxs.append(None)
        return mins, maxs

    # 차트용 컬럼 후보 (룰 단위 Evidence Chart에서 선택해서 표시) — 단일 라벨, 별칭 따로 처리
    # 시설 7개(fc_*)도 포함: E1-1/E1-2 Status Timeline용. NaN(미입력)인 경우도 시계열에 그대로 표시.
    chart_cols = [
        ("student_count", "학생수"),
        ("grade1_students", "1학년 학생수"),
        ("grade2_students", "2학년 학생수"),
        ("class_count", "학급수"),
        ("teacher_count", "교원수"),
        ("teacher_count_no_instructor", "교원수(강사제외)"),
        ("head_teacher_count", "보직교사수"),
        ("students_per_class", "학급당학생수"),
        ("students_per_teacher", "교원1인당학생수"),
        ("bullying_cases", "학폭 심의건수"),
        ("bullying_victims", "피해학생수"),
        ("bullying_protection", "보호조치건수"),
        ("bullying_perpetrators", "가해학생수"),
        ("graduation_rate", "진학률(%)"),
        ("meal_cost_total", "급식비총액(천원)"),
        ("meal_cost_per_student", "1인당급식비(원)"),
        ("budget_revenue", "학교회계 세입"),
        ("budget_expense", "학교회계 세출"),
        # 시설 (E1-1 / E1-2 Status Timeline)
        ("fc_changing_room", "학생탈의실"),
        ("fc_shower", "학생샤워실"),
        ("fc_health_room", "보건실"),
        ("fc_cafeteria", "학생식당"),
        ("fc_dorm", "기숙사실수"),
        ("fc_av_room", "시청각실"),
        ("fc_computer_room", "컴퓨터실"),
    ]

    series = {}  # 라벨 또는 col_key → {self, peer_mean, peer_min, peer_max}
    for col_key, label in chart_cols:
        if col_key not in school_df.columns:
            continue
        self_vals = get_vals(col_key)
        peer_mean = get_peer_mean(col_key)
        peer_min, peer_max = get_peer_minmax(col_key)
        payload = {
            "self": self_vals,
            "peer_mean": peer_mean,
            "peer_min": peer_min,
            "peer_max": peer_max,
        }
        # 정식 라벨로도, col_key로도 접근 가능 (프론트에서 col_keys 우선 매칭 시 안정)
        series[label] = payload
        series[col_key] = payload

    return {
        "labels": labels,
        "series": series,
        # ↓ 하위 호환 (기존 키 그대로 유지)
        "학생수": series.get("학생수", {}).get("self") or [None] * len(years),
        "학급수": series.get("학급수", {}).get("self") or [None] * len(years),
        "교원수": series.get("교원수", {}).get("self") or [None] * len(years),
        "학급당학생수": series.get("학급당학생수", {}).get("self") or [None] * len(years),
        "학폭건수": series.get("학폭건수", {}).get("self") or [None] * len(years),
        "피해학생수": series.get("피해학생수", {}).get("self") or [None] * len(years),
        "보호조치건수": series.get("보호조치건수", {}).get("self") or [None] * len(years),
        "진학률": series.get("진학률", {}).get("self") or [None] * len(years),
        "동료군_학생수": series.get("학생수", {}).get("peer_mean") or [None] * len(years),
        "동료군_교원수": series.get("교원수", {}).get("peer_mean") or [None] * len(years),
        "동료군_학급당학생수": series.get("학급당학생수", {}).get("peer_mean") or [None] * len(years),
    }


def _get_category_code(rule_id: str) -> str:
    """C3-3A → C3, B1-5 → B1, F1'-1 → F1"""
    base = rule_id.split("-")[0]
    return base.rstrip("'")


def _categories_ko(categories_str: str) -> list:
    """'C1, C3, B1' → [{'code':'C1','ko':'학생·자원 연동 점검'}, ...]"""
    if not categories_str or pd.isna(categories_str):
        return []
    codes = [c.strip() for c in str(categories_str).split(",") if c.strip()]
    return [{"code": c, "ko": CATEGORY_NAMES_KO.get(c, c)} for c in codes]


def _representative_detection(detections_df: pd.DataFrame, school_code: str):
    """학교의 가장 심각한 탐지(최고 별, 최신 연도) 1건"""
    school = detections_df[detections_df["school_code"] == school_code]
    if school.empty:
        return None
    return school.sort_values(["star", "year"], ascending=[False, False]).iloc[0]


def _enrich_school_summary(row, detections_df: pd.DataFrame, df: pd.DataFrame) -> dict:
    """학교 1건에 대표 탐지 + 한국어 카테고리/룰명 + 메타 부착.
    검토 우선도 지수는 score를 float로 노출 (소수점 1자리 표시는 프론트에서 처리).
    max_star는 내부 룰 메타로만 유지 — UI에서 등급 분기에 쓰지 않음."""
    score_f = float(row["score"])
    item = {
        "school_code": str(row["school_code"]),
        "school_name": str(row["school_name"]),
        "score": score_f,                       # 표기·정렬은 프론트에서 .toFixed(1)
        "rank": int(row.get("rank", 0)),
        "max_star": int(row.get("max_star", 0)),  # 내부 메타 (UI 분기 X)
        "num_categories": int(row.get("num_categories", 0)),
        "num_detections": int(row.get("num_detections", 0)),
        "is_repeat": bool(row.get("is_repeat", False)),
        "categories_ko": _categories_ko(row.get("categories", "")),
    }
    school_rows = df[df["school_code"] == row["school_code"]]
    if not school_rows.empty:
        item["district"] = str(school_rows["district"].iloc[0])
        item["school_type"] = str(school_rows["school_type"].iloc[0])
    # 룰 필터 정확 매칭용 — 해당 학교가 트리거한 전체 rule_id 집합 (중복 제거)
    school_det = detections_df[detections_df["school_code"] == row["school_code"]]
    item["rule_ids"] = sorted({str(r) for r in school_det["rule_id"].unique()}) if not school_det.empty else []
    rep = _representative_detection(detections_df, row["school_code"])
    if rep is not None:
        rid = str(rep.get("rule_id", ""))
        cat = _get_category_code(rid)
        item["rep"] = {
            "rule_id": rid,
            "rule_name_ko": RULE_NAMES_KO.get(rid, rid),
            "category_code": cat,
            "category_ko": CATEGORY_NAMES_KO.get(cat, cat),
            "detail": str(rep.get("detail", "")),
            "year": int(rep.get("year", 0)),
            "star": int(rep.get("star", 0)),
        }
    return item


def _build_self_report(detail_result: dict, school_df, full_df, det_cards: list, school_score) -> dict:
    """⑦ 자가진단 리포트 — 한 학교의 종합 요약. LLM 미사용, 정적 + 자동 주입.
    포함: 학교명·지수·순위·주요 검토 신호 Top N·카테고리 요약·동료군 대비 요약·확인 권장."""
    rank_total = int(school_score["rank"].iloc[0]) if not school_score.empty else 0
    score_f = float(school_score["score"].iloc[0]) if not school_score.empty else 0.0
    grade_label = "우선 검토" if score_f >= 16 else "일반 검토" if score_f >= 11 else "참고"

    # 주요 검토 신호 Top 5 — 카드에서 severity·연도 순으로
    flat_rules = []
    for cat in det_cards:
        for r in cat.get("rules", []):
            flat_rules.append({
                "rule_id": r["rule_id"],
                "rule_name_ko": r["rule_name_ko"],
                "category_ko": cat["category_ko"],
                "year": r["year"],
                "star": r.get("star", 0),
                "detail": r.get("detail", ""),
            })
    flat_rules.sort(key=lambda x: (-x["star"], -x["year"], x["rule_id"]))
    top_signals = flat_rules[:5]

    # 카테고리 요약 — 카테고리별 룰 수·세부 룰 수
    cat_summary = []
    for cat in det_cards:
        seen_rules = {r["rule_id"] for r in cat.get("rules", [])}
        cat_summary.append({
            "category_ko": cat["category_ko"],
            "cat_code": cat.get("cat_code", ""),
            "total_detections": len(cat.get("rules", [])),
            "rule_count": len(seen_rules),
            "is_repeat": bool(cat.get("is_repeat", False)),
        })

    # 동료군 대비 요약 — 핵심 4지표(학생수·교원수·학급당학생수·1인당급식비)에서 본교 vs 동료군
    peer_summary = []
    if not school_df.empty:
        last_yr = sorted(school_df["year"].unique())[-1]
        last = school_df[school_df["year"] == last_yr]
        for col, label, unit in [
            ("student_count", "학생수", "명"),
            ("teacher_count", "교원수", "명"),
            ("students_per_class", "학급당학생수", "명/학급"),
            ("meal_cost_per_student", "1인당 급식비", "원"),
        ]:
            peer_col = f"{col}_dist_mean"
            if col in last.columns and peer_col in last.columns:
                self_v = last[col].iloc[0]
                peer_v = last[peer_col].iloc[0]
                if pd.notna(self_v) and pd.notna(peer_v) and peer_v != 0:
                    diff_pct = (float(self_v) - float(peer_v)) / float(peer_v) * 100
                    peer_summary.append({
                        "label": label,
                        "unit": unit,
                        "self": round(float(self_v), 1),
                        "peer": round(float(peer_v), 1),
                        "diff_pct": round(float(diff_pct), 1),
                    })

    # 확인 권장 — Top 신호에서 SIXBOX_GUIDE recommend 추출 (중복 제거 최대 5건)
    recommends = []
    seen_rids = set()
    for s in top_signals:
        rid = s["rule_id"]
        if rid in seen_rids:
            continue
        seen_rids.add(rid)
        guide = SIXBOX_GUIDE.get(rid)
        if guide and guide.get("recommend"):
            recommends.append({"rule_id": rid, "rule_name_ko": s["rule_name_ko"], "text": guide["recommend"]})
        if len(recommends) >= 5:
            break

    return {
        "school_name": detail_result.get("school_name", ""),
        "district": detail_result.get("district", ""),
        "school_type": detail_result.get("school_type", ""),
        "score": round(score_f, 1),
        "grade_label": grade_label,
        "rank": rank_total,
        "num_detections": detail_result.get("num_detections", 0),
        "num_rules": detail_result.get("num_rules", 0),
        "num_categories": len(det_cards),
        "is_repeat": detail_result.get("is_repeat", False),
        "year_range": f"{int(school_df['year'].min())}~{int(school_df['year'].max())}" if not school_df.empty else "",
        "top_signals": top_signals,
        "category_summary": cat_summary,
        "peer_summary": peer_summary,
        "recommends": recommends,
    }


SEOUL_DISTRICTS_25 = [
    "강남", "강동", "강북", "강서", "관악", "광진", "구로", "금천", "노원", "도봉",
    "동대문", "동작", "마포", "서대문", "서초", "성동", "성북", "송파", "양천", "영등포",
    "용산", "은평", "종로", "중구", "중랑",
]


def _seoul_25_districts(df: pd.DataFrame) -> list:
    """서울 25개 구 — 보유/미보유 상태 포함"""
    if df is None or df.empty:
        return [{"name": d, "active": False, "schools": 0} for d in SEOUL_DISTRICTS_25]
    out = []
    for d in SEOUL_DISTRICTS_25:
        sub = df[df["district"] == d]
        cnt = int(sub["school_code"].nunique()) if not sub.empty else 0
        out.append({"name": d, "active": cnt > 0, "schools": cnt})
    return out


def _data_basis(df: pd.DataFrame) -> dict:
    """공시 데이터 기준 메타 (대시보드 헤더 문구용)"""
    if df.empty:
        return {}
    years = sorted(int(y) for y in df["year"].unique())
    districts = sorted(df["district"].dropna().unique().tolist())
    return {
        "years": years,
        "year_range": f"{years[0]}~{years[-1]}" if years else "",
        "districts": districts,
        "schools": int(df["school_name"].nunique()),
        "source": "학교알리미 · KESS 공시 데이터",
    }


# ── 공통 6박스 스키마 (룰 단위 + 챗봇 재사용) ──
# 사용자 화면에 안정적으로 표시. LLM 미사용 → 응답 지연/실패에도 깨지지 않음.
# 박스 6개 항목 (요청 사양):
#   1. 핵심 발견 / 2. 수치 변화 / 3. 패턴 해석 / 4. 동료군 맥락 / 5. 정상 예외 가능성 / 6. 확인 권장
# 룰 ID별 정적 가이드 — 3·5·6 박스는 룰 의미 기반 작성. 1·2·4는 detection·school_df로 자동 주입.

SIXBOX_GUIDE = {
    "C1-1": {
        "pattern": "학생수와 학급수가 반대 방향으로 움직임. 자원 배분이 의도된 결과인지 확인 필요.",
        "normal": "학급 신설·통폐합, 학년 정원 조정, 특수학급 별도 운영.",
        "recommend": "학생수·학급수 입력 원본을 함께 확인하고, 학급 신설/통폐합 여부와 학생 전출입을 점검해 주세요.",
    },
    "C1-2": {
        "pattern": "학생수와 학급수가 반대 방향(1학급 변동). C1-1보다 완만한 신호.",
        "normal": "학급 1개 신설/폐지가 자연스러운 학교 규모 조정과 함께 일어난 경우.",
        "recommend": "학급수 1학급 변동의 사유와 학생 전입·전출 추이를 함께 확인해 주세요.",
    },
    "C1-3": {
        "pattern": "학생수는 안정(±5% 이내)인데 강사 제외 교원수가 10% 또는 5명 이상 변동.",
        "normal": "정년 퇴임·임용·인사 발령 시기, 기간제 교원 비율 변화.",
        "recommend": "교원 자격종별 집계 기준(강사 포함 여부)을 확인하고, 정원·기간제·강사 분류를 점검해 주세요.",
    },
    "C1-4": {
        "pattern": "학급수 변동(0~1)은 작은데 강사 제외 교원수가 5명 이상 변동.",
        "normal": "정년·퇴직·이동, 기간제 교원 일괄 임용.",
        "recommend": "교원 분류 정확성과 학급 운영 기준을 함께 확인해 주세요.",
    },
    "C1-5": {
        "pattern": "학생수는 안정인데 보직교사 2명 이상 변동.",
        "normal": "학교 조직 개편, 부장 교사 인사 발령, 보직 분리/통합.",
        "recommend": "보직 인사 발령 사유와 보직 분류 기준을 확인해 주세요.",
    },
    "C1-7": {
        "pattern": "교원1인당학생수가 20% 이상 변동. 학생수 또는 교원수 단독 변화일 가능성.",
        "normal": "학생 신·편입학 또는 교원 정원 조정.",
        "recommend": "학생수·교원수 변화 원인을 분리해서 확인해 주세요.",
    },
    "C1-8": {
        "pattern": "학급당 학생수가 1.5명 이상 급변. 학급수 또는 학생수 파싱 정확성 점검.",
        "normal": "학급 신설/폐지가 학년 중간에 일어난 경우.",
        "recommend": "학급수 원본 표기('28(3)' 등 괄호 포함 시 총학급수 파싱 정확성)를 우선 확인해 주세요.",
    },
    "C3-3A": {
        "pattern": "피해학생 3명 이상 + 보호조치 0건 + 가해학생 존재(선도조치 수행 추정).",
        "normal": "보호조치 미실시 사유, 보호조치 후 입력 누락, 가해학생 별도 조치 미입력.",
        "recommend": "피해학생 보호조치 미실시 사유 또는 미입력 사유를 확인해 주세요. 가해학생 조치 별도 미입력 시 가해학생수 포함 여부도 점검합니다.",
    },
    "C3-3B": {
        "pattern": "피해학생 1~2명 + 보호조치 0건 + 가해학생 존재. C3-3A보다 약한 신호.",
        "normal": "보호조치 입력 누락, 학년도 기준 차이.",
        "recommend": "보호조치·가해조치 입력 누락 여부를 확인하고, 학년도(공시연도 -1) 기준이 맞는지 함께 점검해 주세요.",
    },
    "B1-1": {
        "pattern": "전년 10% 이상 변동 + 직전 2년 평균 대비 3배 — 갑자기 튄 해.",
        "normal": "통폐합·정원 조정·이동, 신·편입학 일시 집중.",
        "recommend": "직전 2년 평균 대비 변동 폭의 사유(통폐합·정원 조정·이동 등)를 확인해 주세요.",
    },
    "B1-2": {
        "pattern": "전년 10% 이상 변동 — 단년 변동(시계열 부족 또는 3배 미만).",
        "normal": "정원 변동, 인사 발령, 학생 일시 집중.",
        "recommend": "변동 사유와 다음 해 추이까지 함께 확인해 주세요.",
    },
    "B1-3": {
        "pattern": "학교회계 세입 또는 세출이 전년 대비 ±30% 변동.",
        "normal": "1회성 사업 반영, 시설 확충, 정부 이전수입 조정.",
        "recommend": "회계 카테고리별 변동 원인과 1회성/경상 구분을 확인해 주세요.",
    },
    "B1-4": {
        "pattern": "학교회계 세입 또는 세출이 ±50% 변동. 강한 변동.",
        "normal": "대규모 시설 사업, 정책 자금 일괄 반영.",
        "recommend": "변동 카테고리·금액·사업명을 함께 확인해 주세요.",
    },
    "B1-5": {
        "pattern": "진학률 15%p 이상 변동. 20%p 이상이면 강한 신호.",
        "normal": "졸업생 분모 변동, 진학 분류 기준 변경(전문대·해외대 포함 여부).",
        "recommend": "진학률 산정 분모와 진학 분류 기준을 확인해 주세요.",
    },
    "B1-6": {
        "pattern": "학폭 심의 건수 0~1건에서 5건 이상으로 급증.",
        "normal": "특정 사안 집중 발생, 학년도(공시연도 -1) 기준 차이.",
        "recommend": "학폭 심의 건수의 학년도 기준(공시연도 -1)을 확인하고, 실태조사 결과와 함께 점검해 주세요.",
    },
    "C2-3": {
        "pattern": "학생수 안정(±5%)인데 급식비 ±10% 변동.",
        "normal": "단가 변동, 운영 업체 변경, 식수 일시 변동.",
        "recommend": "급식비 입력단위(천원)와 사업 항목 분류를 확인해 주세요.",
    },
    "C2-3+": {
        "pattern": "급식비 ±30% 강한 변동. 단위 혼동(천원↔원) 가능성.",
        "normal": "1회성 사업, 정책 자금 반영.",
        "recommend": "급식비 단위 혼동 또는 1회성 사업 반영 여부를 우선 확인해 주세요.",
    },
    "D2-1": {
        "pattern": "동료군(같은 구·연도) 백분위 10% 미만 또는 90% 초과 — 상위·하위 10%.",
        "normal": "학교 유형 특성(소규모·예술·특수), 시설 차이.",
        "recommend": "학교 유형 특성을 고려해 동료군 정의가 적정한지 확인해 주세요.",
    },
    "D2-2": {
        "pattern": "IQR 1.5배 외부 또는 중앙값 대비 50% 이상 차이 — 강한 극단값.",
        "normal": "학교 유형(소규모·예술고 등), 시설 차이, 사업 특성.",
        "recommend": "학교 유형 특성에 따른 정상 예외 가능성을 확인하고, 동료군 정의가 적정한지 함께 점검해 주세요.",
    },
    "C5-1": {
        "pattern": "전년 1학년 → 당해 2학년 진급률이 -7~+3% 범위 밖. 자퇴·전출 집중 가능.",
        "normal": "신·편입학·전출입·자퇴·휴학·검정고시 전환.",
        "recommend": "신·편입학, 전출·전입, 자퇴·휴학 등 학생수 변동 사유를 확인해 주세요.",
    },
    "E1-1": {
        "pattern": "시설 컬럼이 3년 연속 미입력(NaN). 미입력 vs 시설 미보유 구분이 어려움.",
        "normal": "해당 시설 미보유, 입력 항목 누락.",
        "recommend": "해당 시설의 보유 여부와 입력 누락 여부를 확인해 주세요.",
    },
    "E1-2": {
        "pattern": "동료군 입력률 90% 이상인데 본교만 미입력 — 단독 누락 신호.",
        "normal": "본교만 해당 시설 미보유, 입력 시기 차이.",
        "recommend": "동료군 대비 본교만 미입력한 사유와 시설 보유 여부를 확인해 주세요.",
    },
    "E1-3": {
        "pattern": "공시 의무 항목 미제출 점검 — 현재 학교 유형별 의무 항목 매핑 확인 대기.",
        "normal": "—",
        "recommend": "학교 유형별 공시 의무 항목 매핑이 확정되면 실 탐지를 수행합니다.",
    },
    "E2-2": {
        "pattern": "3년간 동일 수치 반복(노이즈 정수 0~5 제외). 미갱신 가능성 점검.",
        "normal": "실제 변동 없음, 단위·산식·반올림 동일 유지.",
        "recommend": "3년간 동일 수치가 실제 변동 없음인지, 입력 갱신 누락인지 확인해 주세요.",
    },
    "F1'-1": {
        "pattern": "학교알리미(강사 제외) vs KESS 교원수 차이 3명 이상.",
        "normal": "강사 포함/미포함 기준 차이, 발령 시점 차이.",
        "recommend": "학교알리미 교원총계에서 강사를 제외하고 KESS와 비교해 주세요.",
    },
    "G1-1": {
        "pattern": "단년 변동은 작지만 다년에 걸쳐 같은 방향으로 누적 8% 이상 변화. B1 단년 급변에는 안 잡히는 누적 변화를 점검합니다. (본교 단일 시계열 기준 · 동료군 대비 비교는 G1-2/G1-3 후속 룰로 검토)",
        "normal": "지역 인구 변화, 학교 운영 변화, 정책 영향, 물가 상승.",
        "recommend": "다년 누적 변동의 사유(인구·운영·정책·물가 등)를 함께 확인하고 추세를 지속 모니터링해 주세요.",
    },
}


def _build_sixbox(rule, school_df, full_df, district):
    """공통 6박스 — 룰 1개 + 학교 시계열 → 6박스 dict.
    LLM 미사용. detection·school_df 데이터로 자동 주입. 룰 ID 미등록 시 기본 가이드."""
    rid = rule.get("rule_id", "")
    guide = SIXBOX_GUIDE.get(rid, {
        "pattern": "본교 수치와 동료군 추이를 함께 확인할 신호입니다.",
        "normal": "—",
        "recommend": "본교 값과 동료군 값을 비교하여 정상 예외 가능성과 입력 정확성을 확인해 주세요.",
    })
    # 1. 핵심 발견 — detection.detail (또는 룰명 기반 폴백)
    finding = rule.get("detail", "") or rule.get("rule_name_ko", "")
    # 2. 수치 변화 — col_pairs 첫 컬럼의 3년 시계열
    numbers = ""
    if not school_df.empty and rule.get("col_keys"):
        first_key = rule["col_keys"][0]
        if first_key in school_df.columns:
            yrs = sorted(school_df["year"].unique())
            vals = []
            for y in yrs:
                row = school_df[school_df["year"] == y]
                if not row.empty and pd.notna(row[first_key].iloc[0]):
                    v = float(row[first_key].iloc[0])
                    vals.append(f"{int(v):,}" if v == int(v) else f"{v:.1f}")
                else:
                    vals.append("—")
            label = rule.get("col_labels", [first_key])[0] if rule.get("col_labels") else first_key
            numbers = f"{label}: " + " → ".join(vals)
    # 3. 패턴 해석 — 룰 정적 가이드
    pattern = guide["pattern"]
    # 4. 동료군 맥락 — col_pairs 첫 컬럼의 동료군 평균(최근 연도)
    peer = ""
    if not school_df.empty and rule.get("col_keys") and district:
        first_key = rule["col_keys"][0]
        peer_col = f"{first_key}_dist_mean"
        if peer_col in school_df.columns:
            last_yr = sorted(school_df["year"].unique())[-1]
            last_row = school_df[school_df["year"] == last_yr]
            if not last_row.empty and pd.notna(last_row[peer_col].iloc[0]):
                p = float(last_row[peer_col].iloc[0])
                self_v = last_row[first_key].iloc[0] if first_key in last_row.columns and pd.notna(last_row[first_key].iloc[0]) else None
                label = rule.get("col_labels", [first_key])[0] if rule.get("col_labels") else first_key
                if self_v is not None:
                    s = float(self_v)
                    diff_pct = ((s - p) / p * 100) if p != 0 else 0
                    sign = "+" if diff_pct >= 0 else ""
                    peer = f"{district}구 동료군 평균 {p:.1f} 대비 본교 {s:.1f} ({sign}{diff_pct:.1f}%)"
                else:
                    peer = f"{district}구 동료군 평균 {p:.1f}"
    if not peer:
        peer = "동료군 비교 데이터가 제한적입니다."
    # 5. 정상 예외 가능성 — 룰 정적 가이드
    normal = guide["normal"]
    # 6. 확인 권장 — 룰 정적 가이드
    recommend = guide["recommend"]
    return {
        "finding": finding,
        "numbers": numbers,
        "pattern": pattern,
        "peer": peer,
        "normal": normal,
        "recommend": recommend,
    }


# 룰 → 관련 컬럼 매핑 (Evidence Chart·룰 카드 표시용)
RULE_COLUMNS = {
    "C1-1":  [("학생수","student_count"), ("학급수","class_count")],
    "C1-2":  [("학생수","student_count"), ("학급수","class_count")],
    "C1-3":  [("학생수","student_count"), ("교원수","teacher_count")],
    "C1-4":  [("학급수","class_count"), ("교원수","teacher_count")],
    "C1-5":  [("학생수","student_count"), ("보직교사수","head_teacher_count")],
    "C1-7":  [("교원1인당학생수","students_per_teacher")],
    "C1-8":  [("학급당학생수","students_per_class")],
    "C3-3A": [("피해학생수","bullying_victims"), ("보호조치건수","bullying_protection"), ("가해학생수","bullying_perpetrators")],
    "C3-3B": [("피해학생수","bullying_victims"), ("보호조치건수","bullying_protection"), ("가해학생수","bullying_perpetrators")],
    "B1-1":  [("학생수","student_count"), ("학급수","class_count"), ("교원수","teacher_count")],
    "B1-2":  [("학생수","student_count"), ("학급수","class_count"), ("교원수","teacher_count")],
    "B1-3":  [("학교회계 세입","budget_revenue"), ("학교회계 세출","budget_expense")],
    "B1-4":  [("학교회계 세입","budget_revenue"), ("학교회계 세출","budget_expense")],
    "B1-5":  [("진학률(%)","graduation_rate")],
    "B1-6":  [("학폭 심의건수","bullying_cases")],
    "C2-3":  [("급식비총액(천원)","meal_cost_total"), ("학생수","student_count")],
    "C2-3+": [("급식비총액(천원)","meal_cost_total"), ("학생수","student_count")],
    "D2-1":  [("학급당학생수","students_per_class"), ("교원1인당학생수","students_per_teacher"), ("1인당급식비(원)","meal_cost_per_student")],
    "D2-2":  [("학급당학생수","students_per_class"), ("교원1인당학생수","students_per_teacher"), ("1인당급식비(원)","meal_cost_per_student")],
    "C5-1":  [("1학년 학생수","grade1_students"), ("2학년 학생수","grade2_students")],
    "E1-1":  [],   # 시설 미입력 — 차트로 표현하지 않음
    "E1-2":  [],
    "E1-3":  [],
    "E2-2":  [("학급수","class_count"), ("교원수","teacher_count"), ("학생수","student_count")],
    "F1'-1": [("교원수(강사제외)","teacher_count_no_instructor")],
    "G1-1":  [("학생수","student_count"), ("교원수","teacher_count"), ("진학률(%)","graduation_rate"), ("1인당급식비(원)","meal_cost_per_student")],
}


def _build_detection_cards(detections_df, school_df, full_df, district) -> list:
    """카테고리별 그룹핑 + 관련 컬럼 데이터 포함"""
    years = sorted(school_df["year"].unique()) if not school_df.empty else []

    # 카테고리별 그룹핑
    cat_groups = {}
    for _, d in detections_df.iterrows():
        rid = d.get("rule_id", "")
        cat = _get_category_code(rid)
        cat_ko = CATEGORY_NAMES_KO.get(cat, cat)
        if cat_ko not in cat_groups:
            cat_groups[cat_ko] = {"category_ko": cat_ko, "cat_code": cat, "max_star": 0, "rules": [], "years_set": set()}
        star = int(d.get("star", 0))
        cat_groups[cat_ko]["max_star"] = max(cat_groups[cat_ko]["max_star"], star)
        cat_groups[cat_ko]["years_set"].add(int(d.get("year", 0)))
        # detection 단위 동적 col_label/col_key (E2-2, D2, B1, E1 등). 없으면 RULE_COLUMNS 기본.
        dyn_label = str(d.get("col_label", "") or "").strip()
        dyn_key = str(d.get("col_key", "") or "").strip()
        if dyn_key:
            # 매칭은 col_key, 표시는 한국어 라벨 — 짝 보존
            col_pairs = [{"key": dyn_key, "label": _canon_label(dyn_label or dyn_key)}]
        else:
            col_pairs = [{"key": k, "label": _canon_label(label)}
                         for label, k in RULE_COLUMNS.get(rid, [])]
        col_keys = [p["key"] for p in col_pairs]
        col_labels = [p["label"] for p in col_pairs]
        rule_obj = {
            "rule_id": rid,
            "rule_name_ko": RULE_NAMES_KO.get(rid, rid),
            "year": int(d.get("year", 0)),
            "star": star,
            "detail": d.get("detail", ""),
            "col_labels": col_labels,    # 표시용 (한국어)
            "col_keys": col_keys,        # 매칭용 (영문 컬럼 ID)
            "col_pairs": col_pairs,      # 짝 보존 — 프론트 차트가 우선 사용
        }
        rule_obj["sixbox"] = _build_sixbox(rule_obj, school_df, full_df, district)
        cat_groups[cat_ko]["rules"].append(rule_obj)

    # 카테고리별로 관련 컬럼 데이터 테이블 생성
    # — detection 단위 col_label/col_key가 있는 룰은 그 컬럼 우선, 없으면 RULE_COLUMNS 기본
    detections_by_cat = {}
    for _, d in detections_df.iterrows():
        c_code = _get_category_code(d.get("rule_id", ""))
        c_ko = CATEGORY_NAMES_KO.get(c_code, c_code)
        detections_by_cat.setdefault(c_ko, []).append(d)

    result = []
    for cat_ko, group in sorted(cat_groups.items(), key=lambda x: -x[1]["max_star"]):
        # 관련 컬럼 수집
        related_cols = set()
        for d in detections_by_cat.get(cat_ko, []):
            dyn_key = str(d.get("col_key", "") or "").strip()
            dyn_label = str(d.get("col_label", "") or "").strip()
            if dyn_key and dyn_label:
                related_cols.add((_canon_label(dyn_label), dyn_key))
            else:
                for label, col in RULE_COLUMNS.get(d.get("rule_id", ""), []):
                    related_cols.add((_canon_label(label), col))

        # 미니 데이터 테이블 (해당 카테고리 컬럼만)
        mini_table = []
        for label, col in sorted(related_cols):
            if col not in school_df.columns:
                continue
            row = {"지표": label}
            for yr in years:
                yr_data = school_df[school_df["year"] == yr]
                val = yr_data[col].iloc[0] if not yr_data.empty and pd.notna(yr_data[col].iloc[0]) else None
                if val is not None:
                    val = round(float(val), 1) if float(val) != int(float(val)) else int(float(val))
                row[str(int(yr))] = val
            # 동료군 평균
            peer_col = f"{col}_dist_mean"
            if peer_col in school_df.columns and years:
                last = school_df[school_df["year"] == years[-1]]
                if not last.empty and pd.notna(last[peer_col].iloc[0]):
                    row["동료군"] = round(float(last[peer_col].iloc[0]), 1)
                else:
                    row["동료군"] = None
            else:
                row["동료군"] = None
            mini_table.append(row)

        group["data_table"] = mini_table
        group["col_keys"] = [col for _, col in sorted(related_cols)]  # 기존 컬럼 키 목록
        group["years"] = sorted(group["years_set"])
        group["is_repeat"] = len(group["years_set"]) >= 3
        del group["years_set"]
        result.append(group)

    return result


# ── Gemini 호출 함수 ──

class GeminiError(Exception):
    """Gemini 호출 실패 (지연·할당량·503 등). 호출부에서 폴백 처리."""
    pass


def _call_gemini(client, prompt: str, system: str = SYSTEM_PROMPT) -> str:
    """Gemini API 호출 공통 함수. 실패 시 GeminiError."""
    full_prompt = f"{system}\n\n---\n\n{prompt}"
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=full_prompt,
        )
        text = getattr(response, "text", None)
        if not text:
            raise GeminiError("응답 본문이 비어 있음")
        return text
    except GeminiError:
        raise
    except Exception as e:
        # 503·할당량·지연·네트워크 등 모두 GeminiError로 표준화
        raise GeminiError(f"{type(e).__name__}: {e}")


# 사용자 노출용 안전 폴백 메시지 — 임의 수치 만들지 않음. 톤·표현 통일.
FALLBACK_AI_TEXT = "AI 보조 해석을 불러오지 못했습니다. 원천 데이터와 검토 신호를 기준으로 확인해 주세요."
FALLBACK_CATEGORY = {
    "해석": "AI 보조 해석을 불러오지 못했습니다. 원천 데이터와 검토 신호를 기준으로 확인해 주세요.",
    "정상사유": "",
    "확인권장": "본 화면의 검토 후보 수치를 직접 확인해 주세요.",
}


def _explain_category(client, card: dict, school_name: str, district: str) -> dict:
    """카테고리별 AI 해석 (해석/정상사유/확인권장)"""
    rules_text = "\n".join(f"- {r['year']}년: {r['detail']}" for r in card["rules"])
    table_text = ""
    if card.get("data_table"):
        for row in card["data_table"]:
            vals = " / ".join(f"{k}:{v}" for k, v in row.items() if k != "지표" and v is not None)
            table_text += f"  {row['지표']}: {vals}\n"

    prompt = f"""{school_name}({district}구) "{card['category_ko']}" 카테고리에서 추출된 검토 후보 요약:
{rules_text}
{table_text}
정확히 3줄만 응답. 장황하게 쓰지 마. 숫자 위주로 간결하게.
해석: (동료군 대비 핵심 차이 1문장)
정상사유: (가능한 이유 2~3개, 쉼표 구분)
확인권장: (담당자 행동 1문장)

주의: "이상하다", "비정상", "오류" 같은 단정형 표현 금지. "{school_name}"을 그대로 사용하고 다른 학교명·플레이스홀더 만들지 마."""

    text = _call_gemini(client, prompt)
    result = {"해석": "", "정상사유": "", "확인권장": ""}
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("해석:"):
            result["해석"] = line[3:].strip()
        elif line.startswith("정상사유:") or line.startswith("정상 사유:"):
            result["정상사유"] = line.split(":", 1)[1].strip()
        elif line.startswith("확인권장:") or line.startswith("확인 권장:"):
            result["확인권장"] = line.split(":", 1)[1].strip()
    # 파싱 실패 시 전체를 해석에
    if not result["해석"]:
        result["해석"] = text.strip()[:200]
    return result


def _explain_with_gemini(client, detections_df, school_df) -> str:
    det_list = []
    for _, d in detections_df.iterrows():
        rid = d.get("rule_id", "")
        det_list.append(f"{d.get('year')}년 {RULE_NAMES_KO.get(rid, rid)}: {d.get('detail','')}")
    det_text = " / ".join(det_list)

    prompt = f"""아래 학교의 검토 후보 요약을 작성해주세요.

검토 후보 항목: {det_text}

반드시 아래 형식으로 3줄 응답 (각 줄 30~50자, 구체적 수치 포함):
① [가장 우선] 어떤 지표에서 어떤 검토 신호가 추출됐는지 수치와 함께 설명
② [패턴] 몇 년 연속인지, 몇 개 영역에서 동시 검토 후보로 잡혔는지 등
③ [확인 권장] 담당자가 구체적으로 무엇을 확인해야 하는지

주의: "이상하다", "비정상", "오류" 같은 단정형 표현 금지. "검토 신호 / 확인 필요" 사용."""
    return _call_gemini(client, prompt)


def _get_analysis_plan(client, query: str, columns: dict, history: list) -> dict:
    cols_text = "\n".join(f"- {k}: {v}" for k, v in columns.items())
    history_text = "\n".join(f"Q: {h.get('query','')} → {h.get('summary','')}" for h in history[-3:]) if history else "없음"

    prompt = f"""사용자 질문을 분석하여 pandas DataFrame 분석 계획을 JSON으로 작성하세요.

[사용 가능한 컬럼]
{cols_text}

[이전 대화]
{history_text}

[사용자 질문]
{query}

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{
    "analysis_plan": "분석 계획 설명",
    "columns_used": ["컬럼1", "컬럼2"],
    "criteria": "적용할 수치 기준과 근거",
    "pandas_code": "result = df[...].groupby(...)...",
    "comparison": "비교 기준",
    "confidence": "높음/중간/낮음"
}}

pandas_code 규칙:
- 결과를 반드시 result 변수에 할당
- df 변수 사용 (pd, np 사용 가능)
- import 금지, 파일 I/O 금지
- 최대 10줄 이내
- NaN 처리: .dropna() 또는 .fillna() 사용"""

    text = _call_gemini(client, prompt, SYSTEM_PROMPT + "\n\n반드시 JSON 형식으로만 응답하세요. 마크다운 코드블록 없이 순수 JSON만.")

    # JSON 추출
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 정책: 기본 코드(데이터표 head) fallback 폐기.
        # 파싱 실패는 "정상 결과"가 아니라 실패 → 호출부에서 안내 응답으로 처리.
        print(f"[WARN] _get_analysis_plan JSON 파싱 실패. 원본 앞부분: {text[:200]}")
        return None


def _generate_report(client, plan: dict, result_data: list, query: str, school_name: str = "") -> str:
    # 실명 정책: school_name이 있으면 그것만, 없으면 result_data의 실제 학교명만 사용 — 플레이스홀더 절대 금지
    if school_name:
        target_clause = f"[대상 학교] {school_name}"
        name_rule = (
            f'주의: "[학교명]", "OO고등학교", "A고등학교" 같은 플레이스홀더/가명 금지. '
            f'반드시 "{school_name}"을 그대로 사용하고, 다른 학교명을 만들어내지 마.'
        )
        intro = f"아래는 {school_name}의 검토 후보 분석 결과입니다."
        finding_clause = f'1. 핵심 발견 — {school_name}의 구체적 수치를 포함하여 1문장'
    else:
        target_clause = "[대상] 복수 학교 비교 (특정 학교 지정 없음)"
        name_rule = (
            '주의: 학교명은 [분석 결과] result_data 안의 실제 학교명을 그대로 사용. '
            '"OO고등학교", "[학교명]", "A고등학교" 같은 플레이스홀더·가명 절대 금지. '
            '데이터에 없는 학교명을 만들어내지 마.'
        )
        intro = "아래는 검토 후보 분석 결과입니다."
        finding_clause = "1. 핵심 발견 — 결과 데이터의 실제 학교명과 구체적 수치를 포함하여 1문장"

    prompt = f"""{intro} 담당자를 위한 보고서를 작성해주세요.

{target_clause}
[사용자 질문] {query}
[분석 계획] {plan.get('analysis_plan', '')}
[적용 기준] {plan.get('criteria', '')}
[분석 결과]
{json.dumps(result_data[:10], ensure_ascii=False, indent=2, default=str)}

보고서 형식:
{finding_clause}
2. 상세 결과 — 연도별 수치 나열 (학교명 명시)
3. 맥락 해석 — 동료군 대비 비교
4. 확인 권장 사항

{name_rule}
"이상치", "비정상" 단정 표현 금지. "검토 후보 / 검토 신호 / 확인 필요" 사용."""
    return _call_gemini(client, prompt)


def _generate_suggestions(plan: dict, result_data: list) -> list:
    suggestions = []
    columns = plan.get("columns_used", [])
    if "teacher_count" in columns and "student_count" in columns:
        suggestions.append("여기에 급식비도 추가해서 보고 싶어")
    if "student_count" in columns:
        suggestions.append("학생수 3년 추이를 보여줘")
    suggestions.append("강남구만 따로 볼 수 있어?")
    suggestions.append("이 학교 전체 룰 점검 결과 보여줘")
    return suggestions[:4]


def _fallback_analysis(query: str, df: pd.DataFrame, columns: dict) -> ChatResponse:
    """Gemini 비활성 시 호출되던 함수. 정책 변경: 기본 데이터표 반환 금지 → 안내 응답."""
    return ChatResponse(**_build_fallback_help_response())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
