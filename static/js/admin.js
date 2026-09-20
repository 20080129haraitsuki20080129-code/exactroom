/**
 * 管理画面 (運営者専用)。
 *
 * 部屋コードを忘れた、という問い合わせに答えるための画面。
 * 模範解答も答案の中身も、サーバ側が返さないのでここには出てこない。
 */

import {
  ApiError,
  api,
  el,
  formatTime,
  setText,
  setupTextSize,
  showError,
} from "./api.js";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "exactroom.admin";

let token = null;
let rooms = [];

function loadToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // プライベートブラウズ等では保持できない
  }
}

function saveToken(value) {
  try {
    if (value === null) sessionStorage.removeItem(TOKEN_KEY);
    else sessionStorage.setItem(TOKEN_KEY, value);
  } catch {
    /* 保存できなくてもその場では使える */
  }
}

function handleError(err) {
  if (err instanceof ApiError && err.status === 401) {
    // 期限切れ。もう一度パスワードから。
    token = null;
    saveToken(null);
    showLogin();
    showError($("global-error"), "セッションが切れました。もう一度入力してください。");
    return;
  }
  showError($("global-error"), err instanceof Error ? err.message : String(err));
}

function showLogin() {
  $("login-card").hidden = false;
  $("rooms-card").hidden = true;
  $("detail-card").hidden = true;
  $("logout").hidden = true;
}

function showRooms() {
  $("login-card").hidden = true;
  $("rooms-card").hidden = false;
  $("logout").hidden = false;
}

/* ---------------- 部屋の一覧 ---------------- */

function matches(room, needle) {
  if (!needle) return true;
  const target = `${room.code} ${room.title}`.toLowerCase();
  return target.includes(needle.toLowerCase());
}

function renderRooms() {
  const needle = $("filter").value.trim();
  const shown = rooms.filter((room) => matches(room, needle));
  const body = $("rooms-body");
  body.textContent = "";

  for (const room of shown) {
    const openMark = room.is_open ? "○ 開" : "✕ 閉";
    const problems = room.published_problem_count === room.problem_count
      ? String(room.problem_count)
      : `${room.problem_count} (公開 ${room.published_problem_count})`;
    body.appendChild(
      el("tr", {}, [
        el("td", {}, [el("code", { className: "mono", text: room.code })]),
        el("td", { text: room.title || "(名前なし)" }),
        el("td", { text: openMark }),
        el("td", { text: problems }),
        el("td", { text: String(room.participant_count) }),
        el("td", { text: String(room.submission_count) }),
        el("td", { className: "small", text: formatTime(room.created_at) }),
        el("td", {}, [
          el("button", {
            className: "small",
            text: "中身",
            attrs: { type: "button" },
            on: { click: () => openDetail(room.code) },
          }),
        ]),
      ])
    );
  }

  const total = rooms.length;
  setText(
    $("rooms-summary"),
    needle
      ? `${total} 部屋のうち ${shown.length} 件を表示中`
      : `${total} 部屋`
  );
}

async function loadRooms() {
  try {
    rooms = await api.get("/api/admin/rooms", token);
    showRooms();
    renderRooms();
    showError($("global-error"), "");
  } catch (err) {
    handleError(err);
  }
}

/* ---------------- 部屋の中身 ---------------- */

async function openDetail(code) {
  try {
    const detail = await api.get(`/api/admin/rooms/${encodeURIComponent(code)}`, token);
    setText($("detail-title"), `${detail.code} — ${detail.title || "(名前なし)"}`);
    setText(
      $("detail-meta"),
      [
        detail.is_open ? "○ 開いています" : "✕ 閉じています",
        detail.allow_new_participants ? "新規参加を受付中" : "新規参加は締切",
        `作成 ${formatTime(detail.created_at)}`,
      ].join(" / ")
    );

    const problems = $("problems-body");
    problems.textContent = "";
    $("problems-empty").hidden = detail.problems.length > 0;
    for (const p of detail.problems) {
      problems.appendChild(
        el("tr", {}, [
          el("td", { text: String(p.order_index) }),
          el("td", { text: p.title || "(無題)" }),
          el("td", { text: p.is_published ? "○ 公開中" : "✕ 非公開" }),
          el("td", { text: String(p.submission_count) }),
          el("td", { className: "small", text: formatTime(p.created_at) }),
        ])
      );
    }

    const people = $("participants-body");
    people.textContent = "";
    $("participants-empty").hidden = detail.participants.length > 0;
    for (const person of detail.participants) {
      people.appendChild(
        el("tr", {}, [
          el("td", { text: person.display_name }),
          el("td", { text: String(person.submission_count) }),
          el("td", { className: "small", text: formatTime(person.created_at) }),
          el("td", { className: "small", text: formatTime(person.last_seen_at) }),
        ])
      );
    }

    $("detail-card").hidden = false;
    $("detail-card").scrollIntoView({ block: "start" });
  } catch (err) {
    handleError(err);
  }
}

/* ---------------- 起動 ---------------- */

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const secret = $("admin-secret").value;
  if (!secret) return;
  $("login-button").disabled = true;
  showError($("global-error"), "");
  try {
    const result = await api.post("/api/admin/login", { secret });
    token = result.admin_token;
    saveToken(token);
    $("admin-secret").value = "";
    await loadRooms();
  } catch (err) {
    showError(
      $("global-error"),
      err instanceof Error ? err.message : String(err)
    );
  } finally {
    $("login-button").disabled = false;
  }
});

$("logout").addEventListener("click", () => {
  token = null;
  saveToken(null);
  rooms = [];
  showLogin();
});

$("reload").addEventListener("click", () => loadRooms());
$("filter").addEventListener("input", () => renderRooms());
$("close-detail").addEventListener("click", () => {
  $("detail-card").hidden = true;
});

setupTextSize();

token = loadToken();
if (token) {
  loadRooms();
} else {
  showLogin();
}
