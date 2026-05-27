# 교육 공공데이터 AI 활용대회

**정보공시 자동 검증 · 이상치 탐지 시스템**

제8회 교육 공공데이터 AI 활용대회 출품작 (일반부문 — AI 활용 아이디어 기획)

## 프로젝트 소개

교육 정보공시 데이터의 항목 간 일관성, 시계열 맥락, 유사학교 비교 등을 자동 검증하여 검토가 필요한 항목을 탐지하는 시스템입니다.

기존 공시 검증 체계가 다루지 못하는 **맥락 기반 검증 영역**을 보완합니다.

## 기술 스택

- **룰 엔진**: Python (pandas, openpyxl)
- **맥락 추론**: LLM (프롬프트 기반 인터페이스)
- **데이터**: 학교알리미, KESS, NEIS 교육 공공데이터

## 활용 데이터

| 출처 | 설명 |
|------|------|
| [학교알리미](https://www.schoolinfo.go.kr/) | 학교현황, 교원현황, 급식비, 재정, 학교폭력 |
| [KESS](https://kess.kedi.re.kr/) | 학생수, 교원수, 학급수, 진학률 |
| [NEIS](https://open.neis.go.kr/) | 학교 기본정보 (설립유형, 학교유형) |

## Contributors

<a href="https://github.com/SuBeen-Cho"><img src="https://github.com/SuBeen-Cho.png" width="60" style="border-radius:50%"/></a>
<a href="https://github.com/jaedol2023-oss"><img src="https://github.com/jaedol2023-oss.png" width="60" style="border-radius:50%"/></a>

## 라이선스

본 프로젝트는 교육 공공데이터 AI 활용대회 출품작입니다.
활용 데이터의 출처와 라이선스는 각 기관의 공공데이터 이용 정책을 따릅니다.
