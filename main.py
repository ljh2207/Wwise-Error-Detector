"""
Wwise Error Detector - 진입점

실행:
    python main.py

동작:
    1. WAAPI 연결 시도
    2. Tools 메뉴에 'Error Detector' 등록
    3. 에러 대시보드 창 오픈
    4. Capture Log 에서 Error 수신 시 실시간 목록 갱신
"""
import json
import logging
import os
import sys

from PyQt5.QtWidgets import QApplication

# 로그 설정
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ErrorDetector")

# 이 파일의 디렉터리를 경로에 추가
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load_config() -> dict:
    config_path = os.path.join(_HERE, "config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("config.json 없음 — 기본값 사용")
        return {}
    except Exception as e:
        logger.error("config.json 읽기 실패: %s", e)
        return {}


def main():
    config = _load_config()
    waapi_url = config.get("waapi_url", "ws://127.0.0.1:8080/waapi")

    from waapi_manager import WaapiManager
    from capture_monitor import CaptureMonitor
    from ui.dashboard import Dashboard

    app = QApplication(sys.argv)
    app.setApplicationName("Wwise Error Detector")

    waapi = WaapiManager(url=waapi_url)
    monitor = CaptureMonitor(waapi)

    window = Dashboard(waapi, monitor)
    window.show()

    # 시작 시 자동 연결 시도
    logger.info("WAAPI 자동 연결 시도: %s", waapi_url)
    if waapi.connect():
        monitor.start_monitoring()
        window._lbl_conn.setText("연결됨")
        window._act_connect.setEnabled(False)
        window._act_disconnect.setEnabled(True)
        window._act_start_cap.setEnabled(True)
        logger.info("연결 성공 — 대시보드 준비 완료")
    else:
        logger.warning("자동 연결 실패 — 수동으로 연결하세요")

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
