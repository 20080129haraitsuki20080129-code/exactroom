import { api, session, setText, showError, formatTime, texPreview, el } from "./api.js";
import { createMathInput, renderMath, buildSymbolPad } from "./mathfield.js";

const $ = (id) => document.getElementById(id);

const solver = session.getSolver();
if (!solver || !solver.token) {
  location.replace("index.html");
}

let problems = [];
let current = null;
let mathInput = null;
let mathInputPromise = null;

setText($("room-meta"), solver ? `${solver.title || "(名前なし)"} / ${solver.code}` : "");
setText($("user-meta"), solver ? solver.name : "");

if (solver && solver.recoveryCode) {
  const note = $("recovery-note");
  setText(
    note,
    `復帰コード: ${solver.recoveryCode} — 別の端末や再入室で同じ名前を使うときに必要になることがあります。控えておいてください。`
  );
  const saved = { ...solver };
  delete saved.recoveryCode;
  // 「控えた」と押されるまで消さない。時間切れで勝手に消すと、
  // 控えそこねた解答者が同じ名前で戻れなくなることがある。
  note.appendChild(document.createTextNode(" "));
  note.appendChild(
    el("button", {
      className: "small",
      text: "控えたので閉じる",
      attrs: { type: "button" },
      on: {
        click: () => {
          setText(note, "");
          session.setSolver(saved);
        },
      },
    })
  );
}

$("leave").addEventListener("click", () => {
  if (
    !confirm(
      "退出します。復帰コードを控えていない場合、同じ名前で戻れなくなることがあります。よろしいですか?"
    )
  ) {
    return;
  }
  session.clearSolver();
  location.href = "index.html";
});

function handleError(err) {
  if (err && err.status === 401) {
    session.clearSolver();
    showError($("global-error"), "セッションの有効期限が切れました。もう一度参加してください。");
    setTimeout(() => location.replace("index.html"), 1500);
    return;
  }
  showError($("global-error"), (err && err.message) || "エラーが発生しました。");
}

function hintText(hints) {
  if (!hints) return "";
  const parts = [];
  parts.push(hints.euler_e ? "e は自然対数の底" : "e は普通の変数");
  parts.push(hints.imaginary_i ? "i は虚数単位" : "i は普通の変数");
  parts.push(hints.log_base === "10" ? "底なしの log は常用対数 (底 10)" : "底なしの log は自然対数");
  if (hints.assume_positive) parts.push("変数は正の実数として扱う");
  else if (hints.assume_real) parts.push("変数は実数として扱う");
  if (hints.ordered_list) parts.push("カンマ区切りの答えは順序も一致させる");
  return `入力の解釈: ${parts.join(" / ")}`;
}

function renderProblemList() {
  const list = $("problem-list");
  list.textContent = "";
  $("problem-empty").hidden = problems.length > 0;
  for (const problem of problems) {
    const label = el("span", { text: problem.title || `問題 ${problem.order_index}` });
    const sub = el("span", {
      className: "item-sub",
      text: texPreview(problem.statement_latex, 40),
    });
    const button = el(
      "button",
      {
        className: "item",
        attrs: {
          type: "button",
          "aria-current": String(current && current.id === problem.id),
        },
        on: { click: () => selectProblem(problem.id) },
      },
      [label, el("br"), sub]
    );
    list.appendChild(el("li", {}, [button]));
  }
}

async function selectProblem(problemId) {
  current = problems.find((p) => p.id === problemId) || null;
  renderProblemList();
  if (!current) return;

  $("empty-card").hidden = true;
  $("problem-card").hidden = false;
  setText($("problem-title"), current.title || `問題 ${current.order_index}`);
  setText($("problem-note"), current.statement_note || "");
  setText($("parse-hints"), hintText(current.parse_hints));
  $("result-area").textContent = "";
  await renderMath($("problem-statement"), current.statement_latex);

  // 問題を素早く切り替えると createMathInput が二重に走り、
  // 提出時に古い方の入力欄を読んでしまうことがあった。
  // 生成中の Promise を共有して 1 つだけ作る。
  if (!mathInput) {
    if (!mathInputPromise) {
      mathInputPromise = createMathInput($("answer-input"), {
        placeholder: "ここに解答を入力",
        onChange: (value) => setText($("tex-echo"), value),
      }).then((input) => {
        mathInput = input;
        buildSymbolPad($("symbol-pad"), input);
        setupVirtualKeyboardSpacing(input);
        return input;
      });
    }
    await mathInputPromise;
  }
  mathInput.setValue("");
  setText($("tex-echo"), "");
  await loadHistory();
}

/** MathLive の仮想キーボードの高さぶん、下に余白を作る。 */
function setupVirtualKeyboardSpacing(input) {
  const keyboard = window.mathVirtualKeyboard;
  if (!keyboard || !input.isRich) return;
  const apply = () => {
    const height = keyboard.boundingRect ? keyboard.boundingRect.height : 0;
    document.body.style.paddingBottom = height ? `${Math.round(height) + 16}px` : "";
    if (height && document.activeElement === input.element) {
      $("submit").scrollIntoView({ block: "nearest" });
    }
  };
  try {
    keyboard.addEventListener("geometrychange", apply);
  } catch {
    /* 対応していない版では何もしない */
  }
  input.element.addEventListener("blur", () => {
    document.body.style.paddingBottom = "";
  });
}

function verdictLabel(verdict) {
  if (verdict === "AC") return "AC (正解)";
  if (verdict === "WA") return "WA (不正解)";
  return "判定保留";
}

function showResult(result) {
  const area = $("result-area");
  area.textContent = "";
  const banner = el("div", { className: `banner ${result.verdict}` });
  banner.appendChild(el("div", { className: "headline", text: verdictLabel(result.verdict) }));
  banner.appendChild(el("div", { className: "small", text: result.message || "" }));
  if (result.remaining_submissions !== null && result.remaining_submissions !== undefined) {
    banner.appendChild(
      el("div", { className: "small muted", text: `この問題の残り提出回数: ${result.remaining_submissions}` })
    );
  }
  area.appendChild(banner);
}

async function loadHistory() {
  if (!current) return;
  try {
    const rows = await api.get(
      `/api/solve/submissions?problem_id=${encodeURIComponent(current.id)}&limit=50`,
      solver.token
    );
    const body = $("history-body");
    body.textContent = "";
    if (!rows.length) {
      body.appendChild(
        el("tr", {}, [el("td", { className: "muted small", attrs: { colspan: "3" }, text: "まだ提出がありません。" })])
      );
      return;
    }
    for (const row of rows) {
      body.appendChild(
        el("tr", {}, [
          el("td", { className: "small nowrap", text: formatTime(row.created_at) }),
          el("td", {}, [el("span", { className: `verdict ${row.verdict}`, text: row.verdict === "PENDING" ? "保留" : row.verdict })]),
          el("td", { className: "tex small", text: row.answer_latex }),
        ])
      );
    }
  } catch (err) {
    handleError(err);
  }
}

$("submit").addEventListener("click", async () => {
  if (!current || !mathInput) return;
  const answer = mathInput.getValue();
  showError($("global-error"), "");
  if (!answer) {
    showError($("global-error"), "解答を入力してください。");
    return;
  }
  $("submit").disabled = true;
  setText($("submit-status"), "判定中…");
  try {
    const result = await api.post(
      "/api/solve/submissions",
      { problem_id: current.id, answer_latex: answer },
      solver.token
    );
    showResult(result);
    await loadHistory();
  } catch (err) {
    handleError(err);
  } finally {
    $("submit").disabled = false;
    setText($("submit-status"), "");
  }
});

$("clear").addEventListener("click", () => {
  if (mathInput) {
    mathInput.setValue("");
    setText($("tex-echo"), "");
    mathInput.focus();
  }
});

$("reload").addEventListener("click", () => loadProblems());

async function loadProblems() {
  try {
    problems = await api.get("/api/solve/problems", solver.token);
    renderProblemList();
    if (current) {
      const still = problems.find((p) => p.id === current.id);
      if (!still) {
        current = null;
        $("problem-card").hidden = true;
        $("empty-card").hidden = false;
      }
    }
    if (!current && problems.length === 1) await selectProblem(problems[0].id);
  } catch (err) {
    handleError(err);
  }
}

if (solver && solver.token) {
  loadProblems();
  // タブが見えていないときはポーリングしない (無駄な通信と電池消費を避ける)
  setInterval(() => {
    if (document.visibilityState === "visible") loadProblems();
  }, 30000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadProblems();
  });
}
