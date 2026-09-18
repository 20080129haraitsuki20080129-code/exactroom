/**
 * 数式入力 (MathLive) と数式表示 (KaTeX) のローダ。
 *
 * * CDN が読めない環境では自動的にプレーンな TeX 入力欄 / 生の TeX 表示に
 *   フォールバックする (特定のサービスに依存しないための保険)。
 * * 読み込み元は static/js/config.js で差し替えられる。
 */

const CDN = window.EXACTROOM_CDN || {};

let mathliveState = null; // null=未試行, "ok", "failed"
let katexState = null;

async function loadModule(urls) {
  for (const url of urls || []) {
    try {
      return await import(/* webpackIgnore: true */ url);
    } catch {
      /* 次の候補へ */
    }
  }
  return null;
}

function loadScript(url) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.async = true;
    script.crossOrigin = "anonymous";
    script.onload = () => resolve(true);
    script.onerror = () => reject(new Error("load failed"));
    document.head.appendChild(script);
  });
}

function loadStylesheet(url) {
  return new Promise((resolve) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    link.crossOrigin = "anonymous";
    link.onload = () => resolve(true);
    link.onerror = () => resolve(false);
    document.head.appendChild(link);
  });
}

/** MathLive を読み込む。成功なら true。 */
export async function ensureMathLive() {
  if (mathliveState === "ok") return true;
  if (mathliveState === "failed") return false;
  if (window.customElements && window.customElements.get("math-field")) {
    mathliveState = "ok";
    return true;
  }
  const module = await loadModule(CDN.mathlive);
  const ok = Boolean(
    module && window.customElements && window.customElements.get("math-field")
  );
  mathliveState = ok ? "ok" : "failed";
  return ok;
}

/** KaTeX を読み込む。成功なら true。 */
export async function ensureKatex() {
  if (katexState === "ok") return true;
  if (katexState === "failed") return false;
  if (window.katex) {
    katexState = "ok";
    return true;
  }
  for (const url of CDN.katexCss || []) {
    if (await loadStylesheet(url)) break;
  }
  for (const url of CDN.katexJs || []) {
    try {
      await loadScript(url);
      if (window.katex) break;
    } catch {
      /* 次の候補へ */
    }
  }
  katexState = window.katex ? "ok" : "failed";
  return katexState === "ok";
}

/**
 * TeX を要素に描画する。KaTeX が使えない場合は生の TeX を表示する。
 * ユーザ入力を innerHTML で扱わないため XSS の心配がない。
 */
export async function renderMath(element, tex, { displayMode = true } = {}) {
  if (!element) return;
  const source = (tex || "").trim();
  element.textContent = "";
  if (!source) return;
  const ok = await ensureKatex();
  if (!ok) {
    const code = document.createElement("code");
    code.className = "mono";
    code.textContent = source;
    element.appendChild(code);
    return;
  }
  try {
    window.katex.render(source, element, {
      displayMode,
      throwOnError: false,
      trust: false,
      strict: "ignore",
      maxSize: 20,
      maxExpand: 200,
      errorColor: "#c02626",
    });
  } catch {
    const code = document.createElement("code");
    code.className = "mono";
    code.textContent = source;
    element.appendChild(code);
  }
}

/**
 * 数式入力欄を作る。
 *
 * 返り値: { element, getValue(), setValue(v), focus(), isRich }
 */
export async function createMathInput(container, { placeholder = "", onChange } = {}) {
  container.textContent = "";
  const rich = await ensureMathLive();

  if (rich) {
    const field = document.createElement("math-field");
    field.setAttribute("aria-label", "数式入力");
    // 外部リンクやスクリプトを誘発する操作を無効化する
    field.setAttribute("math-virtual-keyboard-policy", "auto");
    field.setAttribute("smart-fence", "true");
    field.setAttribute("smart-superscript", "true");
    if (placeholder) field.setAttribute("placeholder", placeholder);
    container.appendChild(field);
    if (onChange) field.addEventListener("input", () => onChange(field.value || ""));
    return {
      element: field,
      isRich: true,
      getValue: () => (field.value || "").trim(),
      setValue: (value) => {
        field.value = value || "";
      },
      focus: () => field.focus(),
    };
  }

  const textarea = document.createElement("textarea");
  textarea.className = "mono";
  textarea.rows = 3;
  textarea.setAttribute("aria-label", "数式入力 (TeX)");
  textarea.placeholder = placeholder || "例: \\frac{1}{2}";
  textarea.autocapitalize = "off";
  textarea.autocomplete = "off";
  textarea.spellcheck = false;
  container.appendChild(textarea);

  const note = document.createElement("p");
  note.className = "tex-fallback-note";
  note.textContent =
    "数式エディタを読み込めなかったため、TeX を直接入力するモードになっています。";
  container.appendChild(note);

  if (onChange) textarea.addEventListener("input", () => onChange(textarea.value));
  return {
    element: textarea,
    isRich: false,
    getValue: () => textarea.value.trim(),
    setValue: (value) => {
      textarea.value = value || "";
    },
    focus: () => textarea.focus(),
  };
}

/** よく使う記号のボタンを作る。 */
export function buildSymbolPad(container, input) {
  const symbols = [
    ["\\frac{}{}", "分数"],
    ["\\sqrt{}", "√"],
    ["^{}", "冪"],
    ["_{}", "添字"],
    ["\\pi", "π"],
    ["\\theta", "θ"],
    ["\\le", "≦"],
    ["\\ge", "≧"],
    ["\\{\\}", "集合"],
    ["\\sin", "sin"],
    ["\\cos", "cos"],
    ["\\log_{}", "log"],
  ];
  container.textContent = "";
  for (const [tex, label] of symbols) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "small";
    button.textContent = label;
    button.addEventListener("click", () => {
      if (input.isRich && input.element.executeCommand) {
        input.element.executeCommand(["insert", tex]);
        input.element.focus();
      } else {
        const node = input.element;
        const start = node.selectionStart ?? node.value.length;
        const end = node.selectionEnd ?? node.value.length;
        node.value = node.value.slice(0, start) + tex + node.value.slice(end);
        node.selectionStart = node.selectionEnd = start + tex.length;
        node.focus();
        node.dispatchEvent(new Event("input"));
      }
    });
    container.appendChild(button);
  }
}
