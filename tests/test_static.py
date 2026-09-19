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
    # max(16px, 1rem) なので、文字サイズを拡大しても 16px を下回らない
    assert re.search(
        r"input\.mono,\s*textarea\.mono,\s*select\.mono\s*\{[^}]*"
        r"font-size:\s*max\(16px,",
        css,
    ), "フォーム要素の 16px 下限指定が無い"
    assert re.search(
        r"input\[type=\"text\"\][^{]*\{[^}]*font-size:\s*max\(16px,", css
    ), "入力欄の 16px 下限指定が無い"


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


# --------------------------------------------------------------------------
# ユニバーサルデザイン
# --------------------------------------------------------------------------


def test_verdict_is_not_conveyed_by_color_alone():
    """色覚に依らず正誤が分かること (記号とことばを併記する)。"""
    source = strip_comments(read("js", "api.js"))
    assert "verdictBadge" in source
    for mark in ("○", "✕", "△"):
        assert mark in source, f"判定の記号 {mark} が無い"
    for word in ("正解", "不正解", "判定できず"):
        assert word in source, f"判定のことば {word} が無い"

    # 判定を描く側が、色クラスだけの表示に戻っていないこと
    for name in ("solve.js", "host.js"):
        page = strip_comments(read("js", name))
        assert "verdictBadge" in page, f"{name} が verdictBadge を使っていない"


@pytest.mark.parametrize("name", HTML_FILES)
def test_text_size_switcher_exists(name):
    """文字サイズを利用者が変えられること。"""
    html = read(name)
    for size in ("normal", "large", "xlarge"):
        assert f'id="textsize-{size}"' in html, f"{name}: 文字サイズ {size} のボタンが無い"
    assert 'aria-label="文字サイズ"' in html


@pytest.mark.parametrize("name", HTML_FILES)
def test_skip_link_for_keyboard_users(name):
    """キーボード利用者が本文へ直接飛べること。"""
    html = read(name)
    assert 'class="skip-link"' in html
    assert 'href="#main"' in html
    assert 'id="main"' in html


@pytest.mark.parametrize("name", HTML_FILES)
def test_ud_font_is_requested_with_fallback(name):
    """UD フォントを使い、読み込めなくても端末の標準フォントで表示できること。"""
    html = read(name)
    assert "BIZ+UDPGothic" in html, f"{name}: UD フォントを読み込んでいない"
    css = read("css", "app.css")
    assert "BIZ UDPGothic" in css
    # フォールバックが用意されていること
    assert "sans-serif" in css


def test_text_size_scales_the_whole_page():
    css = read("css", "app.css")
    assert re.search(r"html\s*\{[^}]*font-size:\s*var\(--base-font\)", css)
    for size in ("large", "xlarge"):
        assert f':root[data-textsize="{size}"]' in css, f"{size} の定義が無い"
    # html に font-size: 1rem を書くと root が初期値 (16px) に戻り、
    # 文字サイズの切り替えが効かなくなる
    for match in re.finditer(r"(^|\n)\s*html[^{]*\{([^}]*)\}", css):
        selector = css[match.start():match.start() + match.group(0).index("{")]
        if "body" in selector or "html" in selector:
            assert "font-size: 1rem" not in match.group(2), (
                "html に font-size: 1rem があると文字サイズ切り替えが効かない"
            )


def test_tap_targets_are_large_enough():
    """指で押す対象を 44px 以上にする。"""
    css = read("css", "app.css")
    button = re.search(r"\nbutton\s*\{([^}]*)\}", css)
    assert button and "min-height: 48px" in button.group(1)
    assert "min-height: 48px" in css


def test_motion_and_contrast_preferences_are_respected():
    css = read("css", "app.css")
    assert "prefers-reduced-motion" in css, "動きを減らす設定に対応していない"
    assert "prefers-contrast" in css, "コントラストを上げる設定に対応していない"


def test_focus_is_visible():
    css = read("css", "app.css")
    assert ":focus-visible" in css
    assert re.search(r":focus-visible\s*\{[^}]*outline:", css)


def test_creation_token_field_is_not_hidden_in_a_details():
    """合言葉の欄を折りたたみに隠さないこと。

    隠していたせいで「部屋が作れない」という問い合わせが実際に起きた。
    """
    html = read("index.html")
    assert 'id="create-token-block"' in html
    block_start = html.index('id="create-token-block"')
    before = html[:block_start]
    # 直前の <details> が閉じられていること (= 折りたたみの中にいない)
    assert before.count("<details>") == before.count("</details>"), (
        "合言葉の欄が <details> の中にある"
    )
    source = strip_comments(read("js", "home.js"))
    assert "requires_creation_token" in source, "サーバ設定を見ていない"
