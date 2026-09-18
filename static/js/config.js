/**
 * フロントエンドの設定。
 *
 * フロントを API と別のホスト (GitHub Pages / Netlify / Cloudflare Pages など)
 * に置く場合は、ここに API のオリジンを書く。
 * 例) window.EXACTROOM_API_BASE = "https://exactroom.example.com";
 *
 * 同じサーバから配信する場合は空文字のままでよい。
 */
window.EXACTROOM_API_BASE = "";

/**
 * 数式エディタ (MathLive) と数式表示 (KaTeX) の読み込み元。
 * 上から順に試し、すべて失敗したらプレーンな TeX 入力欄に自動で切り替わる。
 * CDN を変えたい場合はここだけ書き換えればよい。
 */
window.EXACTROOM_CDN = {
  mathlive: [
    "https://cdn.jsdelivr.net/npm/mathlive@0.110.0/mathlive.min.mjs",
    "https://unpkg.com/mathlive@0.110.0/mathlive.min.mjs",
    "https://cdn.jsdelivr.net/npm/mathlive@0.104.2/dist/mathlive.min.mjs",
  ],
  katexJs: [
    "https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js",
    "https://unpkg.com/katex@0.16.22/dist/katex.min.js",
  ],
  katexCss: [
    "https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css",
    "https://unpkg.com/katex@0.16.22/dist/katex.min.css",
  ],
};
