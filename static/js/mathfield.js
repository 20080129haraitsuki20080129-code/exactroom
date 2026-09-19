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

const MODE_KEY = "exactroom.input-mode";

function readSavedMode() {
  try {
    const saved = window.localStorage.getItem(MODE_KEY);
    return saved === "tex" || saved === "editor" ? saved : null;
  } catch {
    return null; // プライベートブラウズなどで読めないことがある
  }
}

function saveMode(mode) {
  try {
    window.localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* 保存できなくても動作に影響しない */
  }
}

/** textarea のカーソル位置に文字列を差し込む。 */
function insertIntoTextarea(node, tex) {
  const start = node.selectionStart ?? node.value.length;
  const end = node.selectionEnd ?? node.value.length;
  node.value = node.value.slice(0, start) + tex + node.value.slice(end);
  // {} で終わる記号は、括弧の中にカーソルを置くと続けて書きやすい
  const inside = tex.endsWith("{}") ? 1 : 0;
  node.selectionStart = node.selectionEnd = start + tex.length - inside;
  node.focus();
  node.dispatchEvent(new Event("input"));
}

function createTexArea(placeholder) {
  const textarea = document.createElement("textarea");
  textarea.className = "mono";
  textarea.rows = 3;
  textarea.setAttribute("aria-label", "数式入力 (TeX)");
  textarea.placeholder = placeholder || "例: \\frac{1}{2}";
  textarea.autocapitalize = "off";
  textarea.autocomplete = "off";
  textarea.spellcheck = false;
  return textarea;
}

/**
 * 数式入力欄を作る。
 *
 * ボタンで組み立てる数式エディタ (MathLive) と、TeX を直接書く入力欄の
 * 両方を用意し、いつでも切り替えられるようにする。切り替えた時点の内容は
 * そのまま引き継がれる。編集中の欄が常に唯一の正 (source of truth) なので、
 * 打っている最中に MathLive が書式を整えて入力と喧嘩することはない。
 *
 * 返り値: { getValue(), setValue(v), focus(), insert(tex), isRich, mode }
 */
export async function createMathInput(container, { placeholder = "", onChange } = {}) {
  container.textContent = "";
  const rich = await ensureMathLive();
  const textarea = createTexArea(placeholder);

  if (!rich) {
    // MathLive が読めない環境では TeX 入力だけを出す
    container.appendChild(textarea);
    const note = document.createElement("p");
    note.className = "tex-fallback-note";
    note.textContent =
      "数式エディタを読み込めなかったため、TeX を直接入力するモードになっています。";
    container.appendChild(note);
    if (onChange) textarea.addEventListener("input", () => onChange(textarea.value));
    return {
      element: textarea,
      richElement: null,
      isRich: false,
      mode: "tex",
      getValue: () => textarea.value.trim(),
      setValue: (value) => {
        textarea.value = value || "";
      },
      focus: () => textarea.focus(),
      insert: (tex) => insertIntoTextarea(textarea, tex),
    };
  }

  const field = document.createElement("math-field");
  field.setAttribute("aria-label", "数式入力");
  // 外部リンクやスクリプトを誘発する操作を無効化する
  field.setAttribute("math-virtual-keyboard-policy", "auto");
  field.setAttribute("smart-fence", "true");
  field.setAttribute("smart-superscript", "true");
  if (placeholder) field.setAttribute("placeholder", placeholder);

  const switcher = document.createElement("div");
  switcher.className = "input-mode-switch";
  switcher.setAttribute("role", "group");
  switcher.setAttribute("aria-label", "解答の入力方法");

  const editorPane = document.createElement("div");
  editorPane.className = "input-pane";
  editorPane.appendChild(field);

  const texPane = document.createElement("div");
  texPane.className = "input-pane";
  texPane.appendChild(textarea);
  const texHint = document.createElement("p");
  texHint.className = "small muted";
  texHint.textContent = "例: \\frac{1}{2}、x^2、\\sqrt{2}、90^\\circ、π や √2 もそのまま使えます。";
  texPane.appendChild(texHint);

  let mode = readSavedMode() || "editor";

  const makeButton = (value, label) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mode-button";
    button.textContent = label;
    button.dataset.mode = value;
    button.addEventListener("click", () => setMode(value, true));
    switcher.appendChild(button);
    return button;
  };
  const editorButton = makeButton("editor", "数式エディタ");
  const texButton = makeButton("tex", "TeX で入力");

  function currentValue() {
    return mode === "editor" ? field.value || "" : textarea.value;
  }

  function setMode(next, moveFocus) {
    if (next !== "editor" && next !== "tex") return;
    // 切り替え前の内容を引き継ぐ
    const carried = currentValue();
    mode = next;
    if (mode === "editor") {
      field.value = carried;
    } else {
      textarea.value = carried;
    }
    editorPane.hidden = mode !== "editor";
    texPane.hidden = mode !== "tex";
    editorButton.setAttribute("aria-pressed", String(mode === "editor"));
    texButton.setAttribute("aria-pressed", String(mode === "tex"));
    saveMode(mode);
    if (moveFocus) {
      (mode === "editor" ? field : textarea).focus();
    }
    if (onChange) onChange(carried);
  }

  container.appendChild(switcher);
  container.appendChild(editorPane);
  container.appendChild(texPane);

  if (onChange) {
    field.addEventListener("input", () => {
      if (mode === "editor") onChange(field.value || "");
    });
    textarea.addEventListener("input", () => {
      if (mode === "tex") onChange(textarea.value);
    });
  }

  setMode(mode, false);

  return {
    get element() {
      return mode === "editor" ? field : textarea;
    },
    //: 仮想キーボードの制御用。モードに関係なく math-field を指す。
    richElement: field,
    get isRich() {
      return mode === "editor";
    },
    get mode() {
      return mode;
    },
    setMode: (next) => setMode(next, true),
    getValue: () => currentValue().trim(),
    setValue: (value) => {
      field.value = value || "";
      textarea.value = value || "";
    },
    focus: () => (mode === "editor" ? field : textarea).focus(),
    insert: (tex) => {
      if (mode === "editor" && field.executeCommand) {
        field.executeCommand(["insert", tex]);
        field.focus();
        if (onChange) onChange(field.value || "");
      } else {
        insertIntoTextarea(textarea, tex);
      }
    },
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
    ["\\pm", "±"],
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
    button.addEventListener("click", () => input.insert(tex));
    container.appendChild(button);
  }
}
