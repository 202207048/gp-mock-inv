# Stockmaster AI 개발 로드맵

이 문서는 현재 AI Server 구현과 향후 기능의 순서, 담당 경계를 정리합니다. 아래 상태는 구현 완료와 계획을 구분합니다.

## 현재 구현 완료 — DONE

- **뉴스 투자영향 분석:** 기사별 `POSITIVE`, `NEUTRAL`, `NEGATIVE`를 반환합니다.
- **뉴스 중요도 분석:** 기사별 0~1 점수와 `LOW`, `MEDIUM`, `HIGH` label을 반환합니다.
- **Runtime provider 전환:** AI Server 프로세스에서 OpenAI와 Gemini를 전환합니다. 기본 설정은 OpenAI `gpt-6-luna` / reasoning `none`, Gemini `gemini-3.8-flash`입니다. 자동 fallback은 없습니다.

- **종목 뉴스 종합 요약 통합:** 기존 Backend 인증 GET 응답을 유지하며 AI Server 요약 endpoint와 runtime provider에 연결했습니다. 기사별 요약과는 별도 기능입니다.

## 개발 순서

1. **NEXT — 기존 뉴스 분석 연결:** Backend 인증 proxy와 Frontend badge를 연결합니다. AI 분석에 실패해도 기존 뉴스 목록은 표시되어야 합니다.
2. **DONE — 기존 Backend 뉴스 요약의 AI Server 통합:** 담당자 승인 범위에서 구현·오프라인 검증했습니다. 기존 UI/API 계약을 유지하며 provider별 요약과 180초 캐시를 연결했습니다. 변경 검토 후 병합 예정입니다.
3. **PLANNED — 기사별 근거 기반 요약:** 제공된 기사 텍스트를 요약하고 핵심 내용을 최대 3개로 반환합니다. 입력이 발췌문이면 전체 기사 요약으로 표시하지 않습니다.
4. **PLANNED — 투자영향 근거 설명:** sentiment 판단과 함께 기사에 실제 포함된 근거를 짧게 제공합니다.
5. **PLANNED — 문맥형 투자 용어 설명:** 사용자가 선택한 용어와 주변 문맥을 설명합니다. 기존 검수 용어사전을 우선 활용합니다.
6. **DEFERRED — OCR 연계:** 선택 영역의 텍스트를 추출하고 사용자가 인식 결과를 확인·수정한 뒤 용어 설명에 전달합니다. OCR 자체와 AI 설명은 별도 책임으로 둡니다.
7. **DEFERRED — 유사 뉴스 그룹화 및 다중기사 요약:** 먼저 URL·기사 식별자 등으로 정확한 중복을 제거하고, 필요성이 확인되면 embedding 유사도로 후보를 그룹화합니다. 묶인 기사를 종합하는 요약은 그룹 품질 확인 후 진행합니다.
8. **DEFERRED — BYOK:** 보안과 사용자별 실행 격리를 설계한 뒤 Stockmaster 투자 기능에 한정해 검토합니다.

## 기본 구조와 책임

```text
Frontend
  → Backend
  → ai_server
  → OpenAI / Gemini
```

| 영역 | 책임 |
| --- | --- |
| Backend | 사용자 인증, 기존 뉴스 데이터 정리, AI Server proxy, timeout·오류 처리 |
| ai_server | 기능별 schema와 입력 지침, provider adapter, 입력·결과 검증 |
| Frontend | 뉴스·분석·요약·용어 설명 UI와 loading/error 상태 |

Frontend는 AI Server나 provider API를 직접 호출하지 않습니다. AI 기능이 실패해도 뉴스 원본을 사용할 수 있게 Backend와 Frontend가 처리합니다.

## 설계 원칙

- 기존 UI와 API 계약은 가능한 한 유지하고 변경은 팀 간 합의 후 진행합니다.
- 기존 Backend의 종목 뉴스 요약은 종목 뉴스와 등락 맥락을 다룹니다. 신규 기사별 요약은 제공된 한 기사의 내용을 정리하며 두 기능의 역할을 구분합니다. 기존 종목 요약의 AI Server 통합은 구현·오프라인 검증을 완료했고 변경 검토 대기 상태입니다.
- provider 오류 시 다른 provider를 자동 호출하지 않습니다.
- 현재 `RuntimeProviderManager`는 서버 프로세스의 기본 provider를 관리합니다. BYOK의 사용자별 선택과 키는 이에 넣지 않고, 추후 요청마다 전달되는 사용자별 `ProviderContext`로 분리해 설계합니다.
- BYOK 키를 로그나 Frontend 응답에 노출하지 않고, 평문 DB 영구 저장을 기본안으로 삼지 않습니다. 사용자 비용 발생을 명확히 알리고 provider/model 허용 목록을 둡니다.
- Stockmaster 투자 정보의 분석·학습 기능에 집중하며 범용 AI 채팅으로 확장하지 않습니다. 매수·매도 추천과 주가예측은 범위에서 제외합니다.
- Redis, vector DB, message queue, 별도 microservice는 실제 규모와 운영 요구가 생기기 전까지 도입하지 않습니다.

## 다음 단계

우선 기존 뉴스 분석을 Backend proxy와 Frontend에 연결해 현재 기능의 사용자 흐름을 완성합니다. 종목 뉴스 종합 요약 통합 변경을 검토한 뒤, 별도 기능인 기사별 요약과 투자영향 근거를 추가합니다. 이후 용어 설명을 텍스트 입력부터 제공하고 OCR을 연결합니다. 유사 뉴스와 BYOK는 앞선 기능의 필요성 및 사용자 격리·보안 준비가 확인된 뒤 진행합니다.