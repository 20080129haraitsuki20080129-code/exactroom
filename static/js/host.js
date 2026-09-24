import {
  api,
  session,
  setText,
  showError,
  formatTime,
  el,
  setupTextSize,
  verdictText,
  verdictBadge,
} from "./api.js";
import { renderMath } from "./mathfield.js";

const $ = (id) => document.getElementById(id);

setupTextSize();

const host = session.getHost();
if (!host || !host.token) {
  location.replace("index.html#host");
}

let problems = [];
let participants = [];
let editingId = null;
let autoTimer = null;

setText($("room-meta"), host ? host.title || "(名前なし)" : "");
setText($("room-code"), host ? host.code : "");

// 同一オリジン配信でも、静的ホスティング配下 (/static/host.html など) でも
// 正しいリンクになるよう、現在のページからの相対で組み立てる。
const joinLink = host
  ? new URL(`index.html?code=${encodeURIComponent(host.code)}`, location.href).href
  : "";
$("join-link").value = joinLink;

$("copy-link").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(joinLink);
    flashOk("参加リンクをコピーしました。");
  } catch {
    $("join-link").select();
    flashOk("コピーできませんでした。リンクを選択してコピーしてください。");
  }
});

$("logout").addEventListener("click", () => {
  session.clearHost();
  location.href = "index.html";
});

function flashOk(message) {
  setText($("global-ok"), message);
  setTimeout(() => setText($("global-ok"), ""), 4000);
}

function handleError(err) {
  if (err && err.status === 401) {
    session.clearHost();
    showError($("global-error"), "セッションの有効期限が切れました。ログインし直してください。");
    setTimeout(() => location.replace("index.html#host"), 1500);
    return;
  }
  showError($("global-error"), (err && err.message) || "エラーが発生しました。");
}

/* ---------------- タブ ---------------- */
const TABS = ["problems", "submissions", "participants", "settings"];
function selectTab(name) {
  for (const key of TABS) {
    $(`tab-${key}`).setAttribute("aria-selected", String(key === name));
    $(`panel-${key}`).hidden = key !== name;
  }
  if (name === "submissions") loadSubmissions();
  if (name === "participants") loadParticipants();
  if (name === "settings") loadSettings();
}
for (const key of TABS) $(`tab-${key}`).addEventListener("click", () => selectTab(key));

/* ---------------- 問題 ---------------- */
function readOptions() {
  return {
    euler_e: $("o-euler").checked,
    imaginary_i: $("o-imag").checked,
    assume_real: $("o-real").checked,
    assume_positive: $("o-positive").checked,
    log_base: $("o-log").value,
  };
}

function writeOptions(options) {
  const o = options || {};
  $("o-euler").checked = o.euler_e !== false;
  $("o-imag").checked = o.imaginary_i !== false;
  $("o-real").checked = o.assume_real !== false;
  $("o-positive").checked = Boolean(o.assume_positive);
  $("o-log").value = o.log_base === "10" ? "10" : "e";
}

function resetEditor() {
  editingId = null;
  setText($("editor-title"), "新しい問題");
  $("p-title").value = "";
  $("p-statement").value = "";
  $("p-note").value = "";
  $("p-answer").value = "";
  $("p-published").checked = true;
  $("o-ordered").checked = false;
  writeOptions({});
  $("delete-problem").hidden = true;
  $("selftest-area").hidden = true;
  $("selftest-result").textContent = "";
  setText($("check-result"), "");
  $("p-statement-preview").textContent = "";
  $("p-answer-preview").textContent = "";
  showError($("problem-error"), "");
  renderProblemList();
}

function renderProblemList() {
  const list = $("problem-list");
  list.textContent = "";
  $("problem-empty").hidden = problems.length > 0;
  for (const problem of problems) {
    const title = el("span", { text: problem.title || `問題 ${problem.order_index}` });
    const badge = problem.is_published ? "" : " [非公開]";
    const sub = el("span", {
      className: "item-sub",
      text: `提出 ${problem.submission_count} / AC ${problem.ac_count}${badge}`,
    });
    list.appendChild(
      el("li", {}, [
        el(
          "button",
          {
            className: "item",
            attrs: { type: "button", "aria-current": String(editingId === problem.id) },
            on: { click: () => editProblem(problem.id) },
          },
          [title, el("br"), sub]
        ),
      ])
    );
  }
}

async function editProblem(problemId) {
  try {
    const detail = await api.get(`/api/host/problems/${problemId}`, host.token);
    editingId = detail.id;
    setText($("editor-title"), `問題を編集: ${detail.title || detail.id}`);
    $("p-title").value = detail.title || "";
    $("p-statement").value = detail.statement_latex || "";
    $("p-note").value = detail.statement_note || "";
    $("p-answer").value = detail.answer_latex || "";
    $("p-published").checked = Boolean(detail.is_published);
    $("o-ordered").checked = Boolean(detail.ordered_list);
    writeOptions(detail.parse_options);
    $("delete-problem").hidden = false;
    $("selftest-area").hidden = false;
    $("selftest-result").textContent = "";
    showError($("problem-error"), "");
    renderProblemList();
    await renderMath($("p-statement-preview"), detail.statement_latex);
    await renderMath($("p-answer-preview"), detail.answer_latex);
  } catch (err) {
    handleError(err);
  }
}

$("new-problem").addEventListener("click", resetEditor);
$("cancel-edit").addEventListener("click", resetEditor);
$("reload-problems").addEventListener("click", () => loadProblems());

let previewTimer = null;
function schedulePreview(sourceId, targetId) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    renderMath($(targetId), $(sourceId).value);
  }, 350);
}
$("p-statement").addEventListener("input", () => schedulePreview("p-statement", "p-statement-preview"));
$("p-answer").addEventListener("input", () => schedulePreview("p-answer", "p-answer-preview"));

$("check-answer").addEventListener("click", async () => {
  setText($("check-result"), "確認中…");
  try {
    const result = await api.post(
      "/api/host/answer-check",
      { answer_latex: $("p-answer").value.trim(), parse_options: readOptions() },
      host.token
    );
    if (result.ok) {
      const kindLabel = { expr: "式", rel: "関係式", set: "集合", list: "複数解" }[result.kind] || result.kind;
      setText($("check-result"), `OK — 種類: ${kindLabel} / 内部表現: ${result.normalized}`);
    } else {
      setText($("check-result"), `NG — ${result.message}`);
    }
  } catch (err) {
    setText($("check-result"), "");
    handleError(err);
  }
});

$("problem-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError($("problem-error"), "");
  const payload = {
    title: $("p-title").value.trim(),
    statement_latex: $("p-statement").value.trim(),
    statement_note: $("p-note").value.trim(),
    answer_latex: $("p-answer").value.trim(),
    parse_options: readOptions(),
    ordered_list: $("o-ordered").checked,
    is_published: $("p-published").checked,
  };
  if (!payload.answer_latex) {
    showError($("problem-error"), "模範解答を入力してください。");
    return;
  }
  $("save-problem").disabled = true;
  try {
    if (editingId) {
      await api.patch(`/api/host/problems/${editingId}`, payload, host.token);
      flashOk("問題を更新しました。");
    } else {
      const created = await api.post("/api/host/problems", payload, host.token);
      editingId = created.id;
      flashOk("問題を追加しました。");
    }
    await loadProblems();
    if (editingId) await editProblem(editingId);
  } catch (err) {
    showError($("problem-error"), (err && err.message) || "保存に失敗しました。");
  } finally {
    $("save-problem").disabled = false;
  }
});

$("delete-problem").addEventListener("click", async () => {
  if (!editingId) return;
  if (!confirm("この問題と、その提出履歴をすべて削除します。よろしいですか?")) return;
  try {
    await api.del(`/api/host/problems/${editingId}`, host.token);
    flashOk("問題を削除しました。");
    resetEditor();
    await loadProblems();
  } catch (err) {
    handleError(err);
  }
});

$("selftest-run").addEventListener("click", async () => {
  if (!editingId) return;
  const candidate = $("selftest-input").value.trim();
  if (!candidate) return;
  const area = $("selftest-result");
  area.textContent = "判定中…";
  try {
    const result = await api.post(
      "/api/host/self-test",
      { problem_id: editingId, candidate_latex: candidate },
      host.token
    );
    area.textContent = "";
    const banner = el("div", { className: `banner ${result.verdict || "PENDING"}` });
    banner.appendChild(
      el("div", {
        className: "headline",
        text: verdictText(result.verdict, { long: true, status: result.status }),
      })
    );
    banner.appendChild(el("div", { className: "small", text: `根拠: ${result.reason} (${result.elapsed_ms} ms)` }));
    area.appendChild(banner);
  } catch (err) {
    area.textContent = "";
    handleError(err);
  }
});

async function loadProblems() {
  try {
    problems = await api.get("/api/host/problems", host.token);
    renderProblemList();
    const select = $("filter-problem");
    const chosen = select.value;
    select.textContent = "";
    select.appendChild(el("option", { attrs: { value: "" }, text: "すべての問題" }));
    for (const problem of problems) {
      select.appendChild(
        el("option", { attrs: { value: String(problem.id) }, text: problem.title || `問題 ${problem.order_index}` })
      );
    }
    select.value = chosen;
  } catch (err) {
    handleError(err);
  }
}

/* ---------------- 提出一覧 ---------------- */
const PAGE_SIZE = 200;
//: 表示中の提出 (新しい順)。「さらに古い提出を読む」で後ろに継ぎ足す。
let submissionRows = [];
//: これ以上古い提出が残っているか (最後に取得したページが満杯だったか)
let hasOlderSubmissions = false;

function buildQuery(beforeId) {
  const params = new URLSearchParams();
  const problemId = $("filter-problem").value;
  const verdict = $("filter-verdict").value;
  if (problemId) params.set("problem_id", problemId);
  if (verdict) params.set("verdict", verdict);
  if (beforeId) params.set("before_id", String(beforeId));
  params.set("limit", String(PAGE_SIZE));
  return params.toString();
}

async function loadSubmissions() {
  try {
    const rows = await api.get(`/api/host/submissions?${buildQuery(0)}`, host.token);
    submissionRows = rows;
    hasOlderSubmissions = rows.length === PAGE_SIZE;
    renderSubmissions();
  } catch (err) {
    handleError(err);
  }
}

async function loadOlderSubmissions() {
  if (!submissionRows.length) return;
  const oldestId = submissionRows[submissionRows.length - 1].id;
  $("load-older").disabled = true;
  try {
    const older = await api.get(
      `/api/host/submissions?${buildQuery(oldestId)}`,
      host.token
    );
    hasOlderSubmissions = older.length === PAGE_SIZE;
    if (!older.length) {
      flashOk("これ以上古い提出はありません。");
    } else {
      submissionRows = submissionRows.concat(older);
    }
    renderSubmissions();
  } catch (err) {
    handleError(err);
    $("load-older").disabled = false;
  }
}

function renderSubmissions() {
  const rows = submissionRows;
  const body = $("submission-body");
  body.textContent = "";
  $("load-older").disabled = !hasOlderSubmissions;

  const counts = { AC: 0, WA: 0, unresolved: 0 };
  for (const row of rows) {
    if (row.status === "judged" && (row.verdict === "AC" || row.verdict === "WA")) {
      counts[row.verdict] += 1;
    } else {
      counts.unresolved += 1;
    }
  }
  setText(
    $("submission-summary"),
    `${rows.length} 件${hasOlderSubmissions ? " (さらに古い提出あり)" : ""} — ` +
      `AC ${counts.AC} / WA ${counts.WA} / 未確定・処理エラー ${counts.unresolved}`
  );

  if (!rows.length) {
    body.appendChild(
      el("tr", {}, [
        el("td", {
          attrs: { colspan: "8" },
          className: "muted small",
          text: "提出はまだありません。",
        }),
      ])
    );
    return;
  }
  for (const row of rows) {
    body.appendChild(
      el("tr", {}, [
        el("td", { className: "small", text: String(row.id) }),
        el("td", { className: "small nowrap", text: formatTime(row.created_at) }),
        el("td", { className: "small", text: row.participant_name }),
        el("td", { className: "small", text: row.problem_title || `#${row.problem_id}` }),
        el("td", { className: "tex small", text: row.answer_latex }),
        el("td", {}, [verdictBadge(row.verdict, row.status)]),
        el("td", { className: "small muted", text: row.reason }),
        el("td", { className: "small muted nowrap", text: `${row.elapsed_ms} ms` }),
      ])
    );
  }
}

$("reload-submissions").addEventListener("click", loadSubmissions);
$("load-older").addEventListener("click", loadOlderSubmissions);
$("filter-problem").addEventListener("change", loadSubmissions);
$("filter-verdict").addEventListener("change", loadSubmissions);

function setAutoRefresh(enabled) {
  if (autoTimer) clearInterval(autoTimer);
  autoTimer = null;
  if (enabled) autoTimer = setInterval(() => {
    if (!$("panel-submissions").hidden && document.visibilityState === "visible") {
      loadSubmissions();
    }
  }, 10000);
}
$("auto-refresh").addEventListener("change", (event) => setAutoRefresh(event.target.checked));
setAutoRefresh(true);

$("download-csv").addEventListener("click", async () => {
  try {
    const response = await fetch(`${api.base}/api/host/submissions.csv`, {
      headers: { Authorization: `Bearer ${host.token}` },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`ダウンロードに失敗しました (${response.status})`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `exactroom_${host.code}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    handleError(err);
  }
});

/* ---------------- 参加者 ---------------- */
async function loadParticipants() {
  try {
    participants = await api.get("/api/host/participants", host.token);
    const body = $("participant-body");
    body.textContent = "";
    if (!participants.length) {
      body.appendChild(
        el("tr", {}, [el("td", { attrs: { colspan: "7" }, className: "muted small", text: "参加者はまだいません。" })])
      );
      return;
    }
    for (const person of participants) {
      body.appendChild(
        el("tr", {}, [
          el("td", { className: "small", text: String(person.id) }),
          el("td", { text: person.display_name }),
          el("td", { className: "small", text: String(person.submission_count) }),
          el("td", { className: "small", text: String(person.ac_count) }),
          el("td", { className: "small nowrap", text: formatTime(person.created_at) }),
          el("td", { className: "small nowrap", text: formatTime(person.last_seen_at) }),
          el("td", {}, [
            el("button", {
              className: "small",
              text: "復帰コード再発行",
              attrs: { type: "button" },
              on: { click: () => resetRecoveryCode(person) },
            }),
          ]),
        ])
      );
    }
  } catch (err) {
    handleError(err);
  }
}
async function resetRecoveryCode(person) {
  if (
    !confirm(
      `${person.display_name} さんの復帰コードを再発行します。古いコードは使えなくなります。よろしいですか?`
    )
  ) {
    return;
  }
  try {
    const result = await api.post(
      `/api/host/participants/${person.id}/recovery-code`,
      {},
      host.token
    );
    flashOk(`${result.display_name} さんの新しい復帰コード: ${result.recovery_code}`);
  } catch (err) {
    handleError(err);
  }
}

$("reload-participants").addEventListener("click", loadParticipants);

/* ---------------- 設定 ---------------- */
async function loadSettings() {
  try {
    const settings = await api.get("/api/host/settings", host.token);
    $("s-title").value = settings.title || "";
    $("s-open").checked = Boolean(settings.is_open);
    $("s-new-participants").checked = Boolean(settings.allow_new_participants);
    $("s-rejoin").value = settings.rejoin_policy || "open";
    $("s-max").value = settings.max_submissions_per_problem;
    $("s-cooldown").value = settings.submission_cooldown_sec;
  } catch (err) {
    handleError(err);
  }
}

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError($("settings-error"), "");
  try {
    const updated = await api.patch(
      "/api/host/settings",
      {
        title: $("s-title").value.trim(),
        is_open: $("s-open").checked,
        allow_new_participants: $("s-new-participants").checked,
        rejoin_policy: $("s-rejoin").value,
        max_submissions_per_problem: Number($("s-max").value || 0),
        submission_cooldown_sec: Number($("s-cooldown").value || 0),
      },
      host.token
    );
    session.setHost({ ...host, title: updated.title });
    setText($("room-meta"), updated.title || "(名前なし)");
    flashOk("設定を保存しました。");
  } catch (err) {
    showError($("settings-error"), (err && err.message) || "保存に失敗しました。");
  }
});

/* ---------------- 起動 ---------------- */
if (host && host.token) {
  loadProblems();
  resetEditor();
}
