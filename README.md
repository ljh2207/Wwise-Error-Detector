# Wwise Error Detector

Wwise Capture Log의 에러를 실시간으로 감지·분류하고, 공식 Wwise 문서 기반 Knowledge Base 및 Claude / Gemini AI를 통한 분석과 자동 수정 기능을 제공하는 PyQt5 대시보드입니다.

---

## 주요 기능

- Wwise WAAPI를 통한 Capture Log 에러 실시간 모니터링
- 에러 유형별 분류 및 그룹핑
- 인과관계 분석 (파생 에러 시각화)
- **공식 Wwise 에러 문서 Knowledge Base** — 239개 에러 코드를 AI 호출 없이 즉시 분류
- Claude / Gemini CLI를 활용한 AI 에러 분석 (분석 중 버튼 재클릭 시 취소 가능)
- 동일 에러 유형 분석 결과 캐시 (반복 분석 생략)
- 자동 수정(Auto Fix) 기능
- Wwise Tools 메뉴에 통합

---

## 요구 사항

- Python 3.9 이상
- Wwise 2022.x 이상 (WAAPI 활성화 필요)
- Claude CLI 또는 Gemini CLI (AI 분석 기능 사용 시)

---

## 설치

### 1. 저장소 클론

```bash
git clone https://github.com/ljh2207/Wwise-Error-Detector.git
cd Wwise-Error-Detector
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

설치되는 패키지:

| 패키지 | 용도 |
|---|---|
| `waapi-client` | Wwise WAAPI 연결 |
| `PyQt5` | GUI 대시보드 |

### 3. Claude CLI 설치 (선택)

Claude AI 분석 기능을 사용하려면 Claude Code CLI가 설치되어 있어야 합니다.
API 키 설정은 불필요하며, Claude Code의 세션 인증을 그대로 사용합니다.

Claude Code 설치: https://claude.ai/code

### 4. Gemini CLI 설치 (선택)

Gemini AI 분석 기능을 사용하려면 Gemini CLI가 설치되어 있어야 합니다.

```bash
npm install -g @google/gemini-cli
```

설치 후 인증:

```bash
gemini
```

처음 실행 시 브라우저에서 Google 계정 로그인이 진행됩니다. 인증 완료 후 `gemini -p "..."` 형식으로 사용할 수 있습니다.

### 5. Wwise Tools 메뉴 등록

Wwise의 Tools 메뉴에서 바로 실행할 수 있도록 Add-on을 등록합니다.

```bash
python install.py
```

등록 후 Wwise를 재시작하면 **Tools > Error Detector** 메뉴 항목이 나타납니다.

등록을 해제하려면:

```bash
python install.py --uninstall
```

---

## 실행

### 독립 실행

```bash
python main.py
```

콘솔 창 없이 실행:

```bash
launch.bat
```

### Wwise에서 실행

Wwise 메뉴에서 **Tools > Error Detector** 클릭

---

## Wwise WAAPI 활성화

Wwise에서 WAAPI를 사용하려면 **Project Settings > WAAPI**에서 활성화해야 합니다.

기본 연결 주소: `ws://127.0.0.1:8080/waapi`

`config.json`에서 변경 가능합니다.

```json
{
    "waapi_url": "ws://127.0.0.1:8080/waapi"
}
```

---

## 에러 분류 파이프라인

에러가 감지되면 아래 순서로 분류됩니다.

```
WAAPI 에러 수신
    │
    ▼
① Knowledge Base 조회 (error_code 키, O(1))
    │  매칭 시 → 공식 Wwise 문서 기반 원인/해결 텍스트 반환
    │
    ▼ (미매칭)
② Regex 패턴 매칭 (WAAPI 자동 수정 가능 여부 포함)
    │
    ▼ (미매칭)
③ AI 분석 (Claude / Gemini CLI)
```

---

## Knowledge Base

`wwise_error_kb.json`에 Wwise 공식 한국어 도움말의 239개 에러 코드 문서가 포함되어 있습니다. 에러를 선택하면 상세 패널 하단에 공식 원인/해결 단계가 표시됩니다.

AI 분석 시에도 KB 데이터가 프롬프트에 자동으로 포함되어 더 정확한 분석 결과를 제공합니다.

### Wwise 버전 변경 시 KB 재생성

```bash
python build_knowledge_base.py --src "C:\Audiokinetic\Wwise버전\Authoring\Help\Contextual Help\ko"
```

`--src` 생략 시 기본 경로(`Wwise2025.1.5.9095`)를 사용합니다.

---

## AI 분석 동작 방식

Claude / Gemini 분석 버튼을 클릭하면 선택된 에러 정보를 프롬프트로 구성해 CLI에 전달합니다. KB에 매칭되는 에러는 공식 문서가 프롬프트에 함께 포함됩니다.

| 상황 | 동작 |
|---|---|
| Wwise 실행 중 (WAAPI 포트 8080 응답) | MCP 도구(sk-wwise-browse 등) 포함 실행 — AI가 프로젝트 실제 상태 조회 가능 |
| Wwise 꺼짐 | MCP 없이 텍스트 기반 분석만 수행 |
| 동일 에러 유형 재분석 | 캐시(`analysis_cache.json`)에서 즉시 반환 |
| 분석 중 버튼 재클릭 | 진행 중인 분석 취소 |

### AI 컨텍스트 파일

- `CLAUDE.md` — Claude Code 세션에 자동 로드되는 Wwise 전문가 컨텍스트
- `GEMINI.md` — Gemini CLI 세션에 자동 로드되는 Wwise 전문가 컨텍스트

---

## 설정 (config.json)

| 항목 | 기본값 | 설명 |
|---|---|---|
| `waapi_url` | `ws://127.0.0.1:8080/waapi` | WAAPI 연결 주소 |
| `auto_fix_enabled` | `false` | 자동 수정 기능 활성화 여부 |
| `poll_interval_ms` | `200` | Capture Log 폴링 간격 (ms) |

---

## 라이선스

MIT
