# EduData Watch 프로토타입

정보공시 자동 검증 · 이상치 탐지 시스템

## 실행 방법

```bash
# 1. 패키지 설치
pip install anthropic fastapi uvicorn

# 2. API 키 설정 (선택 — 없으면 LLM 기능 비활성화 상태로 동작)
export ANTHROPIC_API_KEY="your-key-here"

# 3. 서버 실행
cd prototype
python app.py
```

브라우저에서 http://localhost:8000 접속.

## 파일 구조

```
prototype/
├── app.py                  # FastAPI 메인 서버
├── data_loader.py          # xlsx → 통합 DataFrame
├── rule_engine.py          # v3 25개 룰 구현
├── priority_scorer.py      # 우선순위 점수 산출
├── safe_executor.py        # pandas 코드 안전 실행
├── static/
│   ├── index.html          # 메인 UI
│   ├── style.css           # 스타일
│   ├── app.js              # 프론트엔드 로직
│   └── pipeline-architecture.html  # E2E 파이프라인 다이어그램
└── README.md
```

## API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/dashboard` | 오늘의 3건 + 분포 |
| GET | `/api/schools` | 학교 목록 |
| GET | `/api/school/{code}` | 학교 상세 |
| POST | `/api/chat` | 대화형 탐색 |
| GET | `/api/stats` | 통계 요약 |
