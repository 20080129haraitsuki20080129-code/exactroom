"""静的ファイル (フロントエンド) の回帰テスト。

ブラウザを使わずに検証できる約束事だけをここで守る。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HTML_FILES = ["index.html", "solve.html", "host.html"]
JS_FILES = ["api.js", "config.js", "home.js", "host.js", "solve.js", "mathfield.js"]


def read(*parts: str) -> str:
    return (STATIC.joinpath(*parts)).read_text(encoding="utf-8")


def strip_comments(source: str) -> str:
    """JS のコメントを落とす (説明文に書いた語で誤検出しないため)。

    文字列中の ``https://`` を壊さないよう、行コメントは
    「行頭から」または「空白の直後」に現れたものだけを落とす。
    """
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("//", "*")):
            continue
        marker = re.search(r"(?:^|\s)//", line)
        if marker and "://" not in line[marker.start():marker.start() + 4]:
            line = line[: marker.start()]
        lines.append(line)
    return "\n".join(lines)


def test_no_innerhtml_anywhere():
    """XSS 対策: 生 HTML の差し込み API を使わない。"""
    forbidden = ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"]
    for name in JS_FILES:
        source = strip_comments(read("js", name))
        for bad in forbidden:
            assert bad not in source, f"{name} に {bad} がある"


def test_no_inline_script_in_html():
    """CSP は script-src に 'unsafe-inline' を許していないので、本文付き
    <script> があるとページが動かない。"""
    for name in HTML_FILES:
        html = read(name)
        for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, re.S):
            assert not match.group(1).strip(), f"{name} にインライン script がある"


def test_form_controls_keep_16px_font():
    """iOS Safari はフォーカス時に 16px 未満の入力欄でページを自動ズームする。

    .mono が textarea の 16px を詳細度で上書きしていたので、
    フォーム要素だけ 16px に戻すルールが必要。
    """
    css = read("css", "app.css")
    assert re.search(
        r"input\.mono,\s*textarea\.mono,\s*select\.mono\s*\{[^}]*font-size:\s*16px",
        css,
    ), "フォーム要素の 16px 指定が無い"


def test_page_navigation_is_relative():
    """フロントを別ホストに置いても壊れないよう、ページ遷移は相対パスにする。"""
    pattern = re.compile(r"""location\.(?:href|replace)\s*[=(]\s*["']/""")
    for name in JS_FILES:
        source = strip_comments(read("js", name))
        assert not pattern.search(source), f"{name} に絶対パスの遷移がある"


def test_assets_are_referenced_under_static():
    """CSS/JS は /static/... を参照する (リポジトリルートを公開すれば
    同一オリジンでも静的ホスティングでも同じパスで解決できる)。"""
    for name in HTML_FILES:
        html = read(name)
        for ref in re.findall(r'(?:href|src)="([^"]+)"', html):
            if ref.endswith((".css", ".js", ".svg")):
                assert ref.startswith("/static/"), f"{name}: {ref}"


@pytest.mark.parametrize("name", HTML_FILES)
def test_mobile_viewport_meta(name):
    html = read(name)
    assert 'name="viewport"' in html
    assert "width=device-width" in html
    # ピンチズームを禁止しない (アクセシビリティ)
    assert "user-scalable=no" not in html
    assert "maximum-scale=1" not in html


def test_katex_is_not_trusted():
    """KaTeX の trust を有効にすると \\href などが使えてしまう。"""
    source = strip_comments(read("js", "mathfield.js"))
    assert "trust: false" in source


def test_cdn_has_fallback_candidates():
    """CDN 1 社に依存しない (config.js に複数候補があること)。"""
    config = read("js", "config.js")
    for key in ("mathlive", "katexJs", "katexCss"):
        block = re.search(rf"{key}:\s*\[(.*?)\]", config, re.S)
        assert block, f"{key} が無い"
        assert block.group(1).count("https://") >= 2, f"{key} の候補が 1 つしかない"


def test_polling_is_paused_when_tab_hidden():
    """タブが見えていないときはポーリングしない。"""
    for name in ("solve.js", "host.js"):
        source = strip_comments(read("js", name))
        assert "visibilityState" in source, f"{name} に可視性チェックが無い"


def test_math_input_is_created_once():
    """問題を素早く切り替えても math-field を二重生成しない。"""
    source = strip_comments(read("js", "solve.js"))
    assert "mathInputPromise" in source


def test_virtual_keyboard_spacing_is_handled():
    """MathLive の仮想キーボードが提出ボタンを覆わないようにする。"""
    source = strip_comments(read("js", "solve.js"))
    assert "geometrychange" in source


def test_mobile_home_screen_assets_exist():
    """スマホのホーム画面に追加したときに必要なファイルがあること。"""
    assert (STATIC / "apple-touch-icon.png").exists()
    assert (STATIC / "manifest.json").exists()
    assert (STATIC / "robots.txt").exists()
    # Safari が自動で取りに行くので PNG である必要がある
    assert (STATIC / "apple-touch-icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("name", HTML_FILES)
def test_html_declares_mobile_icons(name):
    html = read(name)
    assert 'rel="apple-touch-icon"' in html
    assert 'rel="manifest"' in html
    assert 'name="theme-color"' in html


def test_robots_disallows_indexing():
    """部屋は参加者だけのものなので検索エンジンに載せない。"""
    robots = read("robots.txt")
    assert "Disallow: /" in robots
