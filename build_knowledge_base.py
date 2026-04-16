"""
Wwise 공식 에러 도움말 HTML → Knowledge Base JSON 변환 스크립트

사용법:
  python build_knowledge_base.py
  python build_knowledge_base.py --src "C:\\path\\to\\ko" --out wwise_error_kb.json

소스: Wwise Contextual Help 디렉터리의 ErrorCode_*.html 파일 (한국어)
출력: wwise_error_kb.json (error_classifier.py 가 런타임에 로드)
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from html.parser import HTMLParser

DEFAULT_SRC = r"C:\Audiokinetic\Wwise2025.1.5.9095\Authoring\Help\Contextual Help\ko"
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wwise_error_kb.json")

# 원인 섹션 제목 변형 목록
_CAUSE_HEADINGS = {"유력한 원인:", "오류가 발생할 수 있는 이유:"}
_SOLUTION_HEADING = "권장 해결 단계:"


class _WwiseHelpParser(HTMLParser):
    """단일 ErrorCode_*.html 파일을 파싱하여 구조화된 데이터를 추출한다."""

    def __init__(self):
        super().__init__()
        self.title: str = ""
        self.descriptions: list[str] = []   # 설명 단락들
        self.causes: list[str] = []
        self.solutions: list[str] = []

        # 파싱 상태 머신
        self._in_h4_title = False
        self._in_sect3_p = False            # sect3 직속 <p> (설명)
        self._in_itemizedlist = False        # <div class="itemizedlist"> 안
        self._current_section: str = ""     # "causes" | "solutions" | ""
        self._in_li_p = False               # <li> 안의 <p>
        self._li_buf: list[str] = []        # 현재 li 텍스트 버퍼
        self._in_section_title_b = False    # itemizedlist 섹션 제목 <b> 안

        self._tag_stack: list[str] = []     # 태그 스택 (중첩 추적)
        self._sect3_depth = 0               # sect3 div 깊이
        self._desc_done = False             # 설명 단락 수집 완료 여부

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self._tag_stack.append(tag)

        if tag == "div":
            cls = attrs_dict.get("class", "")
            if "sect3" in cls:
                self._sect3_depth += 1
            if "itemizedlist" in cls:
                self._in_itemizedlist = True

        elif tag == "h4":
            if attrs_dict.get("class") == "title":
                self._in_h4_title = True

        elif tag == "p":
            parent = self._tag_stack[-2] if len(self._tag_stack) >= 2 else ""
            grandparent = self._tag_stack[-3] if len(self._tag_stack) >= 3 else ""

            # sect3 직속 <p> (설명): <div class="sect3"> → <div class="titlepage"> 이후 직속 <p>
            # 실제 구조: sect3 > p (설명용) / sect3 > div.itemizedlist > p.title
            if self._sect3_depth > 0 and not self._in_itemizedlist and not self._desc_done:
                # itemizedlist 밖의 p는 설명 단락
                self._in_sect3_p = True

            elif self._in_itemizedlist and attrs_dict.get("class") == "title":
                # 섹션 제목 <p class="title"> — 다음 <b> 내용으로 섹션 구분
                self._in_section_title_b = False  # 다음 <b>를 기다림

            elif self._in_itemizedlist and parent == "li":
                self._in_li_p = True

        elif tag == "b":
            if self._in_itemizedlist:
                self._in_section_title_b = True

        elif tag == "li":
            self._li_buf = []

    def handle_endtag(self, tag):
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if tag == "div":
            parent_cls = ""
            # div 닫힘: sect3 깊이 감소 처리는 간단히 태그 스택으로 추적
            if self._in_itemizedlist:
                # itemizedlist 종료 감지 (중첩 없음)
                self._in_itemizedlist = False
                self._current_section = ""

        elif tag == "h4":
            self._in_h4_title = False

        elif tag == "p":
            if self._in_sect3_p:
                self._in_sect3_p = False
            elif self._in_li_p:
                self._in_li_p = False
            elif self._in_section_title_b:
                self._in_section_title_b = False

        elif tag == "b":
            self._in_section_title_b = False

        elif tag == "li":
            text = _clean(" ".join(self._li_buf))
            if text:
                if self._current_section == "causes":
                    self.causes.append(text)
                elif self._current_section == "solutions":
                    self.solutions.append(text)
            self._li_buf = []

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return

        if self._in_h4_title:
            self.title += text

        elif self._in_sect3_p:
            self.descriptions.append(text)

        elif self._in_section_title_b:
            # 섹션 제목 텍스트로 현재 섹션 결정
            stripped = text.strip()
            if stripped in _CAUSE_HEADINGS:
                self._current_section = "causes"
                self._desc_done = True   # 설명 수집 완료
            elif stripped == _SOLUTION_HEADING:
                self._current_section = "solutions"
                self._desc_done = True

        elif self._in_li_p or (self._in_itemizedlist and
                               self._tag_stack and self._tag_stack[-1] in ("li", "p", "code", "a", "span")):
            self._li_buf.append(text)


def _clean(text: str) -> str:
    """HTML 파싱 후 공백 정규화."""
    return re.sub(r"\s+", " ", text).strip()


def _make_summary(text: str, max_len: int = 80) -> str:
    """긴 텍스트에서 첫 문장을 추출하고 max_len 이내로 자른다."""
    if not text:
        return ""
    # 한국어 문장 끝 감지 (마침표/닝다/습니다 등)
    for sep in (". ", "。", ".\n", " (", " -"):
        idx = text.find(sep)
        if 10 < idx < max_len:
            return text[:idx + 1].strip()
    return text[:max_len].rstrip(".,") + ("..." if len(text) > max_len else "")


def parse_html_file(path: str) -> dict | None:
    """HTML 파일 하나를 파싱해 KB 엔트리 dict 또는 None 반환."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"  [읽기 오류] {path}: {e}", file=sys.stderr)
        return None

    parser = _WwiseHelpParser()
    parser.feed(content)

    title = _clean(parser.title)
    description = _clean(" ".join(parser.descriptions))
    causes = [_clean(c) for c in parser.causes if _clean(c)]
    solutions = [_clean(s) for s in parser.solutions if _clean(s)]

    if not title and not description:
        return None

    # cause_summary: 설명 첫 문장 > 첫 번째 원인 항목 순으로 시도
    if description:
        cause_summary = _make_summary(description)
    elif causes:
        cause_summary = _make_summary(causes[0])
    else:
        cause_summary = title[:80] if title else ""

    # solution_summary: 첫 번째 해결 단계 항목
    # 비구체적 안내("공식 문서를 참고하세요", "Capture Log에서 찾으세요" 등)는 빈 문자열로 처리
    _VAGUE = ("공식 문서를 참고하세요", "Capture Log", "다음을 참조하세요")
    raw_summary = _make_summary(solutions[0]) if solutions else ""
    solution_summary = "" if any(p in raw_summary for p in _VAGUE) else raw_summary

    return {
        "title": title,
        "description": description,
        "causes": causes,
        "solutions": solutions,
        "cause_summary": cause_summary,
        "solution_summary": solution_summary,
    }


def build(src_dir: str, out_path: str) -> None:
    """src_dir 의 모든 ErrorCode_*.html 을 파싱해 out_path 에 JSON 저장."""
    if not os.path.isdir(src_dir):
        print(f"[오류] 소스 디렉터리를 찾을 수 없습니다: {src_dir}", file=sys.stderr)
        sys.exit(1)

    html_files = sorted(
        f for f in os.listdir(src_dir)
        if f.startswith("ErrorCode_") and f.endswith(".html")
    )
    if not html_files:
        print(f"[오류] ErrorCode_*.html 파일이 없습니다: {src_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"파싱 대상: {len(html_files)}개 파일")

    kb: dict = {}
    failed = 0
    for fname in html_files:
        error_code = fname[:-5]   # ".html" 제거
        entry = parse_html_file(os.path.join(src_dir, fname))
        if entry:
            kb[error_code] = entry
        else:
            print(f"  [스킵] {fname}", file=sys.stderr)
            failed += 1

    # _meta 메타데이터
    # Wwise 버전을 경로에서 추출 시도
    wwise_version = "unknown"
    for part in src_dir.replace("\\", "/").split("/"):
        if re.match(r"Wwise\d{4}", part):
            # e.g. "Wwise2025.1.5.9095" → "2025.1.5.9095"
            ver_match = re.search(r"Wwise(\d{4}\.\d+\.\d+\.\d+)", part)
            if ver_match:
                wwise_version = ver_match.group(1)
            break

    result = {
        "_meta": {
            "wwise_version": wwise_version,
            "generated_at": datetime.now().isoformat(),
            "source_path": src_dir,
            "entry_count": len(kb),
        }
    }
    result.update(kb)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {len(kb)}개 엔트리 → {out_path}")
    if failed:
        print(f"  스킵: {failed}개 파일")


def main():
    parser = argparse.ArgumentParser(
        description="Wwise ErrorCode HTML → Knowledge Base JSON 변환기"
    )
    parser.add_argument(
        "--src", default=DEFAULT_SRC,
        help=f"Wwise 한국어 도움말 디렉터리 (기본: {DEFAULT_SRC})"
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT,
        help=f"출력 JSON 경로 (기본: {DEFAULT_OUT})"
    )
    args = parser.parse_args()
    build(args.src, args.out)


if __name__ == "__main__":
    main()
