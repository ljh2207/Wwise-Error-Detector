"""
Wwise Error Detector 설치 스크립트

실행:
    python install.py

동작:
    Wwise Add-ons 디렉터리에 JSON 파일을 배치해
    Wwise 시작 시 Tools 메뉴에 'Error Detector' 항목이 자동으로 나타나게 한다.
    이 작업은 한 번만 하면 된다.
"""
import json
import os
import sys


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_MAIN_PY = os.path.join(_SCRIPT_DIR, "main.py")
_PYTHON_EXE = sys.executable

# Wwise Add-ons 디렉터리 (AppData 기준)
_APPDATA = os.environ.get("APPDATA", "")
_ADDONS_DIR = os.path.join(_APPDATA, "Audiokinetic", "Wwise", "Add-ons")
_ADDON_JSON = os.path.join(_ADDONS_DIR, "WwiseErrorDetector.json")


def install():
    # ── 경로 확인 ──────────────────────────────────────────────────
    if not os.path.isfile(_MAIN_PY):
        print(f"[오류] main.py 를 찾을 수 없습니다: {_MAIN_PY}")
        return False

    if not _APPDATA:
        print("[오류] APPDATA 환경변수를 찾을 수 없습니다.")
        return False

    # ── Add-ons 디렉터리 생성 ──────────────────────────────────────
    os.makedirs(_ADDONS_DIR, exist_ok=True)
    print(f"[확인] Add-ons 디렉터리: {_ADDONS_DIR}")

    # ── Add-on JSON 작성 ──────────────────────────────────────────
    # Windows 경로는 슬래시로 변환 (Wwise가 두 형식 모두 지원)
    main_py_fwd = _MAIN_PY.replace("\\", "/")
    python_fwd  = _PYTHON_EXE.replace("\\", "/")

    addon_data = {
        "version": 1,
        "commands": [
            {
                "id": "com.wwise_error_detector.open",
                "displayName": "Open Error Detector",
                "program": python_fwd,
                "args": f'"{main_py_fwd}"',
                "startMode": "SingleSelectionSingleProcess",
                "mainMenu": {
                    "basePath": "Tools/Error Detector"
                }
            }
        ]
    }

    with open(_ADDON_JSON, "w", encoding="utf-8") as f:
        json.dump(addon_data, f, ensure_ascii=False, indent=2)

    print(f"[완료] Add-on JSON 생성: {_ADDON_JSON}")
    print()
    print("=" * 60)
    print(" 설치 완료!")
    print(" Wwise 를 재시작하면 Tools 메뉴에")
    print(" 'Error Detector > Open Error Detector' 항목이 표시됩니다.")
    print("=" * 60)
    return True


def uninstall():
    if os.path.isfile(_ADDON_JSON):
        os.remove(_ADDON_JSON)
        print(f"[완료] Add-on JSON 삭제: {_ADDON_JSON}")
        print("Wwise 재시작 후 Tools 메뉴에서 항목이 사라집니다.")
    else:
        print("[정보] 설치된 Add-on JSON 파일이 없습니다.")


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install()
