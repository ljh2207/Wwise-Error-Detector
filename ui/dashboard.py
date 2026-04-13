"""
메인 대시보드 UI
"""
import csv
import json
import logging
import os
import queue
from datetime import datetime
from typing import Optional

from PyQt5.QtCore import Qt, QSettings, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon
from PyQt5.QtWidgets import (
    QAction, QApplication, QCheckBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMainWindow, QMessageBox,
    QPushButton, QSizePolicy, QSplitter, QStatusBar,
    QTableWidget, QTableWidgetItem, QTextEdit, QToolBar,
    QVBoxLayout, QWidget,
)

from error_classifier import WwiseError, make_error, reset_counter


class _AnalysisWorker(QThread):
    """AI CLI 를 별도 스레드에서 실행해 UI 블로킹을 방지한다."""
    finished = pyqtSignal(str)   # 분석 결과 텍스트
    progress = pyqtSignal(str)   # 진행 상황 메시지 (재시도 등)

    def __init__(self, error, engine: str = "claude", parent=None):
        super().__init__(parent)
        self._error = error
        self._engine = engine   # "claude" | "gemini"

    def run(self):
        if self._engine == "gemini":
            from ai_engine import analyze_gemini
            result = analyze_gemini(
                self._error,
                on_progress=lambda msg: self.progress.emit(msg),
            )
        else:
            from ai_engine import analyze
            result = analyze(self._error)
        self.finished.emit(result)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 인과관계 감지
# ------------------------------------------------------------------

# 파생 에러 코드 → 루트 에러 코드 목록
_DERIVATIVE_MAP: dict[str, list[str]] = {
    "ErrorCode_FileNotFound": ["ErrorCode_MediaErrorFromWwise"],
}
_TIMESTAMP_TOL_MS = 2000   # 타임스탬프 1차 허용 오차 (ms)
_IDX_FALLBACK_RANGE = 10   # 타임스탬프 매칭 실패 시 인덱스 기반 탐색 범위


def _build_causality(errors: list) -> tuple[dict, set]:
    """파생 에러를 감지하고 루트-파생 매핑을 반환한다.

    1차: 타임스탬프 2초 이내 매칭
    2차 폴백: 타임스탬프 매칭 실패 시 리스트 인덱스 근접도(_IDX_FALLBACK_RANGE) 기반 매칭

    Returns:
        root_to_derivatives: {root_idx: [derivative_idx, ...]}
        derivative_idxs:     파생 에러 인덱스 집합
    """
    root_to_derivatives: dict[int, list[int]] = {}
    derivative_idxs: set[int] = set()

    for i, err in enumerate(errors):
        root_codes = _DERIVATIVE_MAP.get(err.error_code)
        if not root_codes:
            continue

        # 1차: 타임스탬프 기반 — 가장 가까운 타임스탬프의 루트
        best_root: int | None = None
        best_ts_diff = float("inf")
        for j, root_err in enumerate(errors):
            if j == i or root_err.error_code not in root_codes:
                continue
            ts_diff = abs(err.timestamp_ms - root_err.timestamp_ms)
            if ts_diff <= _TIMESTAMP_TOL_MS and ts_diff < best_ts_diff:
                best_ts_diff = ts_diff
                best_root = j

        # 2차 폴백: 인덱스 근접도 — 가장 가까운 위치의 루트
        if best_root is None:
            best_idx_diff = float("inf")
            for j, root_err in enumerate(errors):
                if j == i or root_err.error_code not in root_codes:
                    continue
                idx_diff = abs(i - j)
                if idx_diff <= _IDX_FALLBACK_RANGE and idx_diff < best_idx_diff:
                    best_idx_diff = idx_diff
                    best_root = j

        if best_root is not None:
            root_to_derivatives.setdefault(best_root, []).append(i)
            derivative_idxs.add(i)

    return root_to_derivatives, derivative_idxs


# 테이블 컬럼 인덱스
COL_ID = 0
COL_TIME = 1
COL_OBJ_NAME = 2
COL_CAUSE = 3
COL_SOLUTION = 4
COL_FIX = 5

COLUMNS = ["#", "시간(ms)", "오브젝트명", "원인", "해결 방법", "수정 가능"]
ERROR_ROW_COLOR = QColor(80, 20, 20)       # 어두운 적색 배경
FIXABLE_ROW_COLOR = QColor(20, 50, 20)     # 어두운 녹색 배경 (자동 수정 가능)


class Dashboard(QMainWindow):
    def __init__(self, waapi, monitor):
        super().__init__()
        self.waapi = waapi
        self.monitor = monitor
        self._errors: list[WwiseError] = []

        self.setWindowTitle("Wwise Error Detector")
        self.setMinimumSize(1100, 650)
        self._build_ui()
        self._apply_dark_style()
        self._load_settings()

        # 큐 드레인 타이머 (200ms 주기)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._drain_queue)
        self._poll_timer.start(200)

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── 툴바 ──────────────────────────────────────────────────────
        toolbar = QToolBar("메인 툴바", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._act_connect = QAction("WAAPI 연결", self)
        self._act_connect.triggered.connect(self._on_connect)
        toolbar.addAction(self._act_connect)

        self._act_disconnect = QAction("연결 해제", self)
        self._act_disconnect.triggered.connect(self._on_disconnect)
        self._act_disconnect.setEnabled(False)
        toolbar.addAction(self._act_disconnect)

        toolbar.addSeparator()

        self._act_start_cap = QAction("캡처 시작", self)
        self._act_start_cap.triggered.connect(self._on_start_capture)
        self._act_start_cap.setEnabled(False)
        toolbar.addAction(self._act_start_cap)

        self._act_stop_cap = QAction("캡처 중지", self)
        self._act_stop_cap.triggered.connect(self._on_stop_capture)
        self._act_stop_cap.setEnabled(False)
        toolbar.addAction(self._act_stop_cap)

        toolbar.addSeparator()

        act_clear = QAction("목록 초기화", self)
        act_clear.triggered.connect(self._on_clear)
        toolbar.addAction(act_clear)

        act_export = QAction("CSV 내보내기", self)
        act_export.triggered.connect(self._on_export_csv)
        toolbar.addAction(act_export)

        toolbar.addSeparator()

        self._chk_on_top = QCheckBox("Always on Top", self)
        self._chk_on_top.setStyleSheet("color: #d4d4d4; padding: 0 6px;")
        self._chk_on_top.toggled.connect(self._on_toggle_always_on_top)
        toolbar.addWidget(self._chk_on_top)

        self._chk_group = QCheckBox("그룹 보기", self)
        self._chk_group.setStyleSheet("color: #d4d4d4; padding: 0 6px;")
        self._chk_group.toggled.connect(self._on_toggle_group_view)
        toolbar.addWidget(self._chk_group)

        # ── 중앙 위젯 (스플리터) ─────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Vertical)
        main_layout.addWidget(splitter)

        # 에러 테이블
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(COL_SOLUTION, QHeaderView.Stretch)
        hh.resizeSection(COL_ID, 40)
        hh.resizeSection(COL_TIME, 80)
        hh.resizeSection(COL_OBJ_NAME, 160)
        hh.resizeSection(COL_CAUSE, 220)
        hh.resizeSection(COL_FIX, 70)
        self._table.currentItemChanged.connect(self._on_row_selected)
        splitter.addWidget(self._table)

        # 상세 패널
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(4, 4, 4, 4)

        detail_layout.addWidget(QLabel("▼  선택된 에러 상세 정보"))

        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        detail_layout.addWidget(self._detail_text)

        btn_row = QHBoxLayout()
        self._btn_claude = QPushButton("Claude 분석")
        self._btn_claude.setEnabled(False)
        self._btn_claude.clicked.connect(self._on_claude_analyze)
        self._btn_gemini = QPushButton("Gemini 분석")
        self._btn_gemini.setEnabled(False)
        self._btn_gemini.clicked.connect(self._on_gemini_analyze)
        self._btn_fix = QPushButton("수정 적용")
        self._btn_fix.setEnabled(False)
        self._btn_fix.clicked.connect(self._on_fix)
        self._btn_focus = QPushButton("Wwise에서 열기")
        self._btn_focus.setEnabled(False)
        self._btn_focus.clicked.connect(self._on_focus_object)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_focus)
        btn_row.addWidget(self._btn_claude)
        btn_row.addWidget(self._btn_gemini)
        btn_row.addWidget(self._btn_fix)
        detail_layout.addLayout(btn_row)

        splitter.addWidget(detail_widget)
        splitter.setSizes([430, 220])

        # ── 상태 바 ───────────────────────────────────────────────────
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._lbl_conn = QLabel("미연결")
        self._lbl_cap = QLabel("캡처 대기")
        self._lbl_count = QLabel("에러: 0개")
        sb.addWidget(self._lbl_conn)
        sb.addWidget(QLabel(" | "))
        sb.addWidget(self._lbl_cap)
        sb.addWidget(QLabel(" | "))
        sb.addWidget(self._lbl_count)

    def _apply_dark_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: "Segoe UI", sans-serif;
                font-size: 12px;
            }
            QToolBar {
                background-color: #2d2d2d;
                border-bottom: 1px solid #3c3c3c;
                spacing: 4px;
                padding: 2px;
            }
            QToolBar QToolButton {
                background-color: #3c3c3c;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px 10px;
            }
            QToolBar QToolButton:hover  { background-color: #505050; }
            QToolBar QToolButton:pressed { background-color: #252525; }
            QToolBar QToolButton:disabled { color: #666; }
            QTableWidget {
                background-color: #1e1e1e;
                gridline-color: #3c3c3c;
                color: #d4d4d4;
                selection-background-color: #264f78;
            }
            QHeaderView::section {
                background-color: #2d2d2d;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
                padding: 4px;
            }
            QTextEdit {
                background-color: #252526;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
            }
            QPushButton {
                background-color: #3c3c3c;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px 14px;
            }
            QPushButton:hover  { background-color: #505050; }
            QPushButton:pressed { background-color: #252525; }
            QPushButton:disabled { color: #666; }
            QStatusBar { background-color: #007acc; color: white; }
            QSplitter::handle { background-color: #3c3c3c; }
            QLabel { color: #d4d4d4; }
        """)

    # ------------------------------------------------------------------
    # 큐 드레인 (QTimer 콜백, 200ms 주기)
    # ------------------------------------------------------------------

    def _drain_queue(self):
        """pending 큐에서 원시 에러를 꺼내 테이블에 추가."""
        processed = 0
        while not self.monitor.pending.empty() and processed < 20:
            try:
                raw = self.monitor.pending.get_nowait()
            except queue.Empty:
                break

            obj_id   = raw.object_id
            obj_name = raw.object_name
            obj_path = self.monitor.resolve_object_path(obj_id)

            # object_id 없으면 description의 파일명으로 역추적
            if not obj_id:
                inf_id, inf_path, inf_name = self.monitor.resolve_object_from_filename(raw.description)
                if inf_id:
                    obj_id   = inf_id
                    obj_path = inf_path
                    obj_name = inf_name or obj_name

            error = make_error(
                {
                    "time": raw.time,
                    "type": raw.type,
                    "severity": raw.severity,
                    "objectName": obj_name,
                    "objectId": obj_id,
                    "gameObjectName": raw.game_object_name,
                    "description": raw.description,
                    "errorCodeName": raw.error_code_name,
                },
                object_path=obj_path,
            )
            self._errors.append(error)
            processed += 1

        if processed:
            self._lbl_count.setText(f"에러: {len(self._errors)}개")
            self._rebuild_table()

    # ------------------------------------------------------------------
    # 테이블 조작
    # ------------------------------------------------------------------

    def _rebuild_table(self):
        """현재 _errors 목록을 토글 상태에 맞게 테이블 전체를 다시 그린다."""
        self._table.setRowCount(0)
        if self._chk_group.isChecked():
            self._render_grouped()
        else:
            self._render_flat()

    def _render_flat(self):
        """파생 에러를 루트 바로 아래에 배치해 시각적 계층을 표시한다."""
        root_to_derivatives, derivative_idxs = _build_causality(self._errors)

        # 파생 에러 → 루트 인덱스 역방향 매핑
        derivative_to_root: dict[int, int] = {}
        for root_idx, deriv_list in root_to_derivatives.items():
            for di in deriv_list:
                derivative_to_root[di] = root_idx

        rendered: set[int] = set()
        display_num = 0

        for i in range(len(self._errors)):
            if i in rendered:
                continue

            if i in derivative_idxs:
                # 파생 에러가 루트보다 먼저 등장한 경우 → 루트부터 렌더링
                root_idx = derivative_to_root[i]
                if root_idx not in rendered:
                    display_num += 1
                    self._add_flat_row(root_idx, display_num, is_derivative=False)
                    rendered.add(root_idx)
                    for di in root_to_derivatives.get(root_idx, []):
                        if di not in rendered:
                            self._add_flat_row(di, display_num, is_derivative=True)
                            rendered.add(di)
                continue

            # 루트 또는 독립 에러
            display_num += 1
            self._add_flat_row(i, display_num, is_derivative=False)
            rendered.add(i)
            for di in root_to_derivatives.get(i, []):
                if di not in rendered:
                    self._add_flat_row(di, display_num, is_derivative=True)
                    rendered.add(di)

    def _add_flat_row(self, idx: int, display_num: int, is_derivative: bool):
        error = self._errors[idx]
        row = self._table.rowCount()
        self._table.insertRow(row)

        id_text = "└" if is_derivative else str(display_num)
        cells = [
            id_text,
            str(error.timestamp_ms),
            error.object_name,
            error.cause,
            error.solution,
            "가능" if error.fix_available else "",
        ]
        color = QColor(45, 10, 10) if is_derivative else (
            FIXABLE_ROW_COLOR if error.fix_available else ERROR_ROW_COLOR
        )

        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setBackground(color)
            if col == COL_ID:
                item.setTextAlignment(Qt.AlignCenter)
                if is_derivative:
                    item.setForeground(QColor("#888888"))
            if col == COL_FIX and error.fix_available:
                item.setForeground(QColor("#4ec9b0"))
            self._table.setItem(row, col, item)

        if is_derivative:
            self._table.item(row, COL_ID).setData(Qt.UserRole, {
                "type": "derivative",
                "idx": idx,
                "root_display_num": display_num,
            })
        else:
            self._table.item(row, COL_ID).setData(Qt.UserRole, idx)
        self._table.scrollToBottom()

    def _render_grouped(self):
        """(error_code, cause) 기준으로 에러를 묶고, 파생 에러는 루트 그룹에 배지로 표시한다."""
        from collections import OrderedDict

        # 1. 인과관계 파악
        root_to_derivatives, derivative_idxs = _build_causality(self._errors)

        # 2. 파생 에러가 아닌 것만 (error_code, cause)로 그룹핑
        groups: dict[tuple, list[int]] = OrderedDict()
        for i, error in enumerate(self._errors):
            if i in derivative_idxs:
                continue
            key = (error.error_code, error.cause)
            groups.setdefault(key, []).append(i)

        # 3. 각 그룹의 파생 에러 목록을 합산해 렌더링 (루트 행 + 파생 서브 행)
        for group_num, indices in enumerate(groups.values(), start=1):
            all_derivatives: list[int] = []
            for idx in indices:
                all_derivatives.extend(root_to_derivatives.get(idx, []))
            representative = self._errors[indices[0]]
            self._add_group_row(group_num, representative, len(indices), indices)
            if all_derivatives:
                self._add_group_derivative_row(all_derivatives)

    def _add_group_row(self, group_num: int, error: WwiseError,
                       count: int, indices: list[int]):
        row = self._table.rowCount()
        self._table.insertRow(row)

        id_text = str(group_num) if count == 1 else f"{group_num} (×{count})"
        cells = [
            id_text,
            str(error.timestamp_ms),
            error.object_name,
            error.cause,
            error.solution,
            "가능" if error.fix_available else "",
        ]
        color = FIXABLE_ROW_COLOR if error.fix_available else ERROR_ROW_COLOR

        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setBackground(color)
            if col == COL_ID:
                item.setTextAlignment(Qt.AlignCenter)
                if count > 1:
                    item.setForeground(QColor("#dcdcaa"))
            if col == COL_FIX and error.fix_available:
                item.setForeground(QColor("#4ec9b0"))
            self._table.setItem(row, col, item)

        self._table.item(row, COL_ID).setData(Qt.UserRole, {
            "type": "group",
            "indices": indices,
            "derivative_indices": [],
        })
        self._table.scrollToBottom()

    def _add_group_derivative_row(self, derivative_indices: list[int]):
        """그룹 루트 바로 아래에 파생 에러 서브 행을 추가한다."""
        count = len(derivative_indices)
        representative = self._errors[derivative_indices[0]]

        row = self._table.rowCount()
        self._table.insertRow(row)

        id_text = "└" if count == 1 else f"└(×{count})"
        cells = [
            id_text,
            str(representative.timestamp_ms),
            representative.object_name,
            representative.cause,
            representative.solution,
            "",
        ]

        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setBackground(QColor(45, 10, 10))
            if col == COL_ID:
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(QColor("#888888"))
            self._table.setItem(row, col, item)

        self._table.item(row, COL_ID).setData(Qt.UserRole, {
            "type": "group",
            "indices": derivative_indices,
            "derivative_indices": [],
        })
        self._table.scrollToBottom()

    def _add_table_row(self, error: WwiseError):
        row = self._table.rowCount()
        self._table.insertRow(row)

        cells = [
            str(error.id),
            str(error.timestamp_ms),
            error.object_name,
            error.cause,
            error.solution,
            "가능" if error.fix_available else "",
        ]
        color = FIXABLE_ROW_COLOR if error.fix_available else ERROR_ROW_COLOR

        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setBackground(color)
            if col == COL_ID:
                item.setTextAlignment(Qt.AlignCenter)
            if col == COL_FIX and error.fix_available:
                item.setForeground(QColor("#4ec9b0"))
            self._table.setItem(row, col, item)

        # 태그에 에러 객체 인덱스 저장 (나중에 선택 이벤트에서 사용)
        self._table.item(row, COL_ID).setData(Qt.UserRole, len(self._errors) - 1)
        self._table.scrollToBottom()

    def _on_row_selected(self, current, _previous):
        if current is None:
            return
        idx_item = self._table.item(current.row(), COL_ID)
        if idx_item is None:
            return
        data = idx_item.data(Qt.UserRole)
        if data is None:
            return

        if isinstance(data, dict) and data.get("type") == "group":
            indices = data["indices"]
            derivative_indices = data.get("derivative_indices", [])
            if not indices or indices[0] >= len(self._errors):
                return
            error = self._errors[indices[0]]
            self._show_detail(error, group_count=len(indices),
                              derivative_indices=derivative_indices)
        elif isinstance(data, dict) and data.get("type") == "derivative":
            idx = data["idx"]
            if idx >= len(self._errors):
                return
            error = self._errors[idx]
            self._show_detail(error, root_display_num=data["root_display_num"])
        else:
            if data >= len(self._errors):
                return
            error = self._errors[data]
            self._show_detail(error)

        has_id = bool(error.object_id)
        self._btn_focus.setEnabled(has_id)
        self._btn_claude.setEnabled(True)
        self._btn_gemini.setEnabled(True)
        self._btn_fix.setEnabled(error.fix_available and not error.fix_applied)

    def _show_detail(self, error: WwiseError, group_count: int = 1,
                     derivative_indices: list[int] | None = None,
                     root_display_num: int | None = None):
        lines = []
        if root_display_num is not None:
            lines += [
                f"[ 파생 에러 — {root_display_num}번 에러로 인해 발생 ]",
                "─" * 60,
            ]
        elif group_count > 1:
            lines += [
                f"[ 그룹 보기: 동일 원인 에러 {group_count}개 묶음 — 대표 에러 표시 ]",
                "─" * 60,
            ]
        lines += [
            f"오브젝트 경로 : {error.object_path or '(조회 실패)'}",
            f"오브젝트명   : {error.object_name}",
            f"Game Object  : {error.game_object_name or ''}",
            f"에러 유형    : {error.error_type}",
            f"에러 코드    : {error.error_code}",
            f"원본 메시지  : {error.description}",
            f"시간(ms)     : {error.timestamp_ms}",
            "─" * 60,
            f"원인         : {error.cause}",
            f"해결 방법    : {error.solution}",
            f"자동 수정    : {'가능' if error.fix_available else '불가'}",
        ]
        if derivative_indices:
            lines += [
                "─" * 60,
                f"[ 파생 에러 {len(derivative_indices)}개 — 이 에러로 인해 함께 발생 ]",
            ]
            for di in derivative_indices[:10]:
                if di < len(self._errors):
                    de = self._errors[di]
                    lines.append(f"  • {de.error_code}: {de.description}")
            if len(derivative_indices) > 10:
                lines.append(f"  ... 외 {len(derivative_indices) - 10}개")
        if error.ai_analyzed and error.ai_analysis:
            lines += ["─" * 60, "[ Claude 분석 결과 ]", "", error.ai_analysis]
        if error.gemini_analyzed and error.gemini_analysis:
            lines += ["─" * 60, "[ Gemini 분석 결과 ]", "", error.gemini_analysis]
        self._detail_text.setPlainText("\n".join(lines))

    def _selected_error(self) -> Optional[WwiseError]:
        row = self._table.currentRow()
        if row < 0:
            return None
        idx_item = self._table.item(row, COL_ID)
        if idx_item is None:
            return None
        data = idx_item.data(Qt.UserRole)
        if data is None:
            return None
        if isinstance(data, dict) and data.get("type") == "group":
            indices = data["indices"]
            if not indices or indices[0] >= len(self._errors):
                return None
            return self._errors[indices[0]]
        if isinstance(data, dict) and data.get("type") == "derivative":
            idx = data["idx"]
            if idx >= len(self._errors):
                return None
            return self._errors[idx]
        if data >= len(self._errors):
            return None
        return self._errors[data]

    # ------------------------------------------------------------------
    # 버튼 핸들러
    # ------------------------------------------------------------------

    def _on_connect(self):
        if self.waapi.is_connected:
            return
        if self.waapi.connect():
            self.monitor.start_monitoring()
            self._lbl_conn.setText("연결됨")
            self._act_connect.setEnabled(False)
            self._act_disconnect.setEnabled(True)
            self._act_start_cap.setEnabled(True)
            self._register_menu()
        else:
            QMessageBox.warning(
                self, "연결 실패",
                "WAAPI 연결에 실패했습니다.\n"
                "Wwise Authoring Tool이 실행 중인지 확인하세요.\n"
                "(Project > User Preferences > Enable WAAPI 활성화 필요)"
            )

    def _on_disconnect(self):
        self.monitor.stop_monitoring()
        self.waapi.disconnect()
        self._lbl_conn.setText("미연결")
        self._lbl_cap.setText("캡처 대기")
        self._act_connect.setEnabled(True)
        self._act_disconnect.setEnabled(False)
        self._act_start_cap.setEnabled(False)
        self._act_stop_cap.setEnabled(False)

    def _on_start_capture(self):
        if self.monitor.start_capture():
            self._lbl_cap.setText("캡처 중...")
            self._act_start_cap.setEnabled(False)
            self._act_stop_cap.setEnabled(True)
        else:
            QMessageBox.warning(self, "오류", "Capture 시작에 실패했습니다.")

    def _on_stop_capture(self):
        if self.monitor.stop_capture():
            self._lbl_cap.setText(f"캡처 완료 ({datetime.now().strftime('%H:%M:%S')})")
            self._act_start_cap.setEnabled(True)
            self._act_stop_cap.setEnabled(False)

    def _on_clear(self):
        self._table.setRowCount(0)
        self._errors.clear()
        self._detail_text.clear()
        reset_counter()
        self._lbl_count.setText("에러: 0개")
        self._btn_claude.setEnabled(False)
        self._btn_gemini.setEnabled(False)
        self._btn_fix.setEnabled(False)
        self._btn_focus.setEnabled(False)

    def _on_export_csv(self):
        if not self._errors:
            QMessageBox.information(self, "내보내기", "내보낼 에러가 없습니다.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV 내보내기", "wwise_errors.csv", "CSV 파일 (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["#", "시간(ms)", "오브젝트명",
                                  "에러 유형", "에러 코드", "원본 메시지",
                                  "원인", "해결 방법", "자동수정가능"])
                for e in self._errors:
                    writer.writerow([
                        e.id, e.timestamp_ms,
                        e.object_name, e.error_type, e.error_code,
                        e.description, e.cause, e.solution,
                        "Y" if e.fix_available else "N",
                    ])
            QMessageBox.information(self, "내보내기 완료", f"저장됨: {path}")
        except Exception as ex:
            QMessageBox.critical(self, "내보내기 실패", str(ex))

    def _on_claude_analyze(self):
        error = self._selected_error()
        if not error:
            return
        self._btn_claude.setEnabled(False)
        self._btn_claude.setText("분석 중...")
        self._detail_text.setPlainText("Claude 가 분석 중입니다. 잠시 기다려주세요...")
        self._worker = _AnalysisWorker(error, engine="claude")
        self._worker.finished.connect(lambda result: self._on_claude_done(error, result))
        self._worker.start()

    def _on_claude_done(self, error, result: str):
        error.ai_analyzed = True
        error.ai_analysis = result
        self._show_detail(error)
        self._btn_claude.setText("Claude 분석")
        self._btn_claude.setEnabled(True)

    def _on_gemini_analyze(self):
        error = self._selected_error()
        if not error:
            return
        self._btn_gemini.setEnabled(False)
        self._btn_gemini.setText("분석 중...")
        self._detail_text.setPlainText("Gemini 가 분석 중입니다. 잠시 기다려주세요...")
        self._worker_gemini = _AnalysisWorker(error, engine="gemini")
        self._worker_gemini.finished.connect(lambda result: self._on_gemini_done(error, result))
        self._worker_gemini.progress.connect(self._detail_text.setPlainText)
        self._worker_gemini.start()

    def _on_gemini_done(self, error, result: str):
        error.gemini_analyzed = True
        error.gemini_analysis = result
        self._show_detail(error)
        self._btn_gemini.setText("Gemini 분석")
        self._btn_gemini.setEnabled(True)

    def _on_fix(self):
        error = self._selected_error()
        if not error:
            return
        from ui.fix_dialog import FixDialog
        from auto_fixer import apply_fix, describe_fix

        preview = describe_fix(error)
        dlg = FixDialog(error, preview, parent=self)
        if dlg.exec_() and dlg.confirmed:
            success, msg = apply_fix(self.waapi, error)
            if success:
                error.fix_applied = True
                self._btn_fix.setEnabled(False)
                row = self._table.currentRow()
                for col in range(self._table.columnCount()):
                    item = self._table.item(row, col)
                    if item:
                        item.setBackground(QColor(20, 60, 20))
                # 수정 완료 후 프로젝트 저장
                self.waapi.call("ak.wwise.core.project.save")
                QMessageBox.information(self, "수정 완료", msg)
            else:
                QMessageBox.warning(self, "수정 실패", msg)

    def _on_toggle_group_view(self, _checked: bool):
        self._rebuild_table()

    def _on_toggle_always_on_top(self, checked: bool):
        flags = self.windowFlags()
        if checked:
            self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
        self.show()

    def _on_focus_object(self):
        error = self._selected_error()
        if not error or not error.object_id:
            return
        result = self.waapi.call(
            "ak.wwise.ui.commands.execute",
            {"command": "FindInProjectExplorerSelectionChannel1", "objects": [error.object_id]},
        )
        if result is None:
            QMessageBox.warning(self, "포커스 실패", "Wwise Project Explorer에서 오브젝트를 찾을 수 없습니다.")


    # ------------------------------------------------------------------
    # Tools 메뉴 등록
    # ------------------------------------------------------------------

    def _register_menu(self):
        import menu_registration
        menu_registration.register(self.waapi)

    # ------------------------------------------------------------------
    # 종료 처리
    # ------------------------------------------------------------------

    def _load_settings(self):
        s = QSettings("WwiseErrorDetector", "Dashboard")
        on_top = s.value("always_on_top", False, type=bool)
        if on_top:
            self._chk_on_top.setChecked(True)

    def _save_settings(self):
        s = QSettings("WwiseErrorDetector", "Dashboard")
        s.setValue("always_on_top", self._chk_on_top.isChecked())

    def closeEvent(self, event):
        self._save_settings()
        self._poll_timer.stop()
        if self.waapi.is_connected:
            import menu_registration
            menu_registration.unregister(self.waapi)
            self.monitor.stop_monitoring()
            self.waapi.disconnect()
        event.accept()
