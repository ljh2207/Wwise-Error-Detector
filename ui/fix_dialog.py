"""
수정 미리보기 다이얼로그 (Phase 4 구현 예정)
AI 가 제안한 수정 내용을 사용자에게 보여주고 승인/거부를 받는다.
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QPushButton,
)
from PyQt5.QtCore import Qt


class FixDialog(QDialog):
    def __init__(self, error, fix_description: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("수정 적용 확인")
        self.setMinimumSize(480, 320)
        self._accepted = False

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"<b>대상 오브젝트:</b> {error.object_path or error.object_name}"))
        layout.addWidget(QLabel(f"<b>에러 유형:</b> {error.error_type}"))
        layout.addWidget(QLabel("<b>적용될 수정 내용:</b>"))

        detail = QTextEdit()
        detail.setReadOnly(True)
        detail.setPlainText(fix_description)
        layout.addWidget(detail)

        layout.addWidget(
            QLabel("⚠  수정 후 Ctrl+Z 로 되돌릴 수 있습니다."),
        )

        btn_layout = QHBoxLayout()
        apply_btn = QPushButton("수정 적용")
        apply_btn.setDefault(True)
        cancel_btn = QPushButton("취소")
        apply_btn.clicked.connect(self._on_apply)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(apply_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _on_apply(self):
        self._accepted = True
        self.accept()

    @property
    def confirmed(self) -> bool:
        return self._accepted
