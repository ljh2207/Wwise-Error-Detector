# Wwise Error Detector

Wwise Capture Log의 에러를 실시간으로 감지·분류하고, Claude / Gemini AI를 통한 분석 및 자동 수정 기능을 제공하는 PyQt5 대시보드입니다.

---

## 주요 기능

- Wwise WAAPI를 통한 Capture Log 에러 실시간 모니터링
- 에러 유형별 분류 및 그룹핑
- 인과관계 분석 (파생 에러 시각화)
- Claude / Gemini CLI를 활용한 AI 에러 분석
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
| `anthropic` | Claude AI 분석 |

### 3. Gemini 인증 설정 (선택)

Gemini AI 분석 기능을 사용하려면 `_gemini_secrets.py` 파일을 생성합니다.

```python
# _gemini_secrets.py
GEMINI_CLIENT_ID = "your-client-id"
GEMINI_CLIENT_SECRET = "your-client-secret"
```

> 이 파일은 `.gitignore`에 포함되어 있어 저장소에 커밋되지 않습니다.

### 4. Claude API 키 설정 (선택)

Claude AI 분석 기능을 사용하려면 `config.json`에 API 키를 입력합니다.

```json
{
    "anthropic_api_key": "sk-ant-..."
}
```

### 5. Wwise Tools 메뉴 등록

Wwise의 Tools 메뉴에서 바로 실행할 수 있도록 Add-on을 등록합니다.

```bash
python install.py
```

등록 후 Wwise를 재시작하면 **Tools > Error Detector > Open Error Detector** 메뉴 항목이 나타납니다.

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

Wwise 메뉴에서 **Tools > Error Detector > Open Error Detector** 클릭

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

## 설정 (config.json)

| 항목 | 기본값 | 설명 |
|---|---|---|
| `waapi_url` | `ws://127.0.0.1:8080/waapi` | WAAPI 연결 주소 |
| `anthropic_api_key` | `""` | Claude API 키 |
| `auto_fix_enabled` | `false` | 자동 수정 기능 활성화 여부 |
| `poll_interval_ms` | `200` | Capture Log 폴링 간격 (ms) |

---

## 라이선스

MIT
