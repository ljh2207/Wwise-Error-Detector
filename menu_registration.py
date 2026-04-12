"""
Wwise Tools 메뉴 등록 모듈
ak.wwise.ui.commands.register 를 통해 Add-on 명령어를 Wwise 메뉴에 추가한다.
"""
import os
import sys
import logging

from waapi_manager import WaapiManager

logger = logging.getLogger(__name__)

_COMMAND_ID = "com.wwise_error_detector.open"
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_MAIN_SCRIPT = os.path.join(_SCRIPT_DIR, "main.py")
_PYTHON_EXE = sys.executable


def register(waapi: WaapiManager) -> bool:
    """
    Wwise Tools 메뉴에 'Error Detector' 항목을 등록한다.
    이미 실행 중인 인스턴스가 있으면 SingleInstanceInformation 으로 처리된다.
    """
    result = waapi.call(
        "ak.wwise.ui.commands.register",
        {
            "commands": [
                {
                    "id": _COMMAND_ID,
                    "displayName": "Error Detector",
                    "program": _PYTHON_EXE,
                    "args": f'"{_MAIN_SCRIPT}"',
                    "startMode": "SingleInstanceInformation",
                    "contextMenu": {
                        "basePath": "Error Detector"
                    },
                }
            ]
        },
    )
    if result is not None:
        logger.info("Tools 메뉴 등록 완료: Error Detector")
        return True
    logger.error("Tools 메뉴 등록 실패")
    return False


def unregister(waapi: WaapiManager):
    """등록된 명령어를 제거한다."""
    waapi.call(
        "ak.wwise.ui.commands.unregister",
        {"commands": [{"id": _COMMAND_ID}]},
    )
    logger.info("Tools 메뉴 등록 해제: Error Detector")
