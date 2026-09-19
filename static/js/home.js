import { api, session, showError, setText, setupTextSize } from "./api.js";

const $ = (id) => document.getElementById(id);

setupTextSize();

/* ---- 役割の選択 ---- */
function selectRole(name) {
  for (const key of ["solve", "host"]) {
    const isActive = key === name;
    $(`role-${key}`).setAttribute("aria-pressed", String(isActive));
    $(`panel-${key}`).hidden = !isActive;
  }
  try {
    history.replaceState(null, "", `#${name}`);
  } catch {
    /* ignore */
  }
}

$("role-solve").addEventListener("click", () => selectRole("solve"));
$("role-host").addEventListener("click", () => selectRole("host"));
if (location.hash === "#host") selectRole("host");

/* ---- サーバの設定に画面を合わせる ---- */
/* 合言葉が要るサーバなのに入力欄を隠していると、
   403 が返ってきた理由が利用者に分からない。 */
async function applyServerConfig() {
  let config = null;
  try {
    config = await api.get("/api/rooms/-/config");
  } catch {
    return; // 取れなくても、入力欄を出しておけば手入力はできる
  }
  if (config.requires_creation_token) {
    $("create-token-block").hidden = false;
    $("create-token").required = true;
    setText($("create-token-required"), "(必須)");
    setText(
      $("create-token-hint"),
      "このサーバでは、部屋を作るのに合言葉が必要です。管理者に聞いてください。"
    );
  }
  if (!config.allow_room_creation) {
    $("create-form").hidden = true;
    setText(
      $("create-disabled"),
      "このサーバでは新しい部屋を作れません。すでにある部屋の出題者としてログインしてください。"
    );
  }
  if (config.min_secret_chars) {
    $("create-secret").minLength = config.min_secret_chars;
  }
}

applyServerConfig();

/* URL の ?code= を自動入力 (出題者が配る参加リンク用) */
const params = new URLSearchParams(location.search);
const presetCode = (params.get("code") || "").toUpperCase();
if (presetCode) {
  $("join-code").value = presetCode;
  $("host-code").value = presetCode;
}

function normalizeCode(value) {
  return (value || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
}

/* ---- 参加 ---- */
$("join-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("join-submit");
  showError($("join-error"), "");
  const code = normalizeCode($("join-code").value);
  const name = $("join-name").value.trim();
  if (!code || !name) {
    showError($("join-error"), "部屋コードと名前を入力してください。");
    return;
  }
  button.disabled = true;
  try {
    const result = await api.post(`/api/rooms/${encodeURIComponent(code)}/join`, {
      display_name: name,
      recovery_code: $("join-recovery").value.trim(),
    });
    session.setSolver({
      code: result.code,
      title: result.title,
      token: result.token,
      name: result.display_name,
      participantId: result.participant_id,
      recoveryCode: result.recovery_code || null,
    });
    location.href = "solve.html";
  } catch (err) {
    showError($("join-error"), err.message);
  } finally {
    button.disabled = false;
  }
});

/* ---- 出題者ログイン ---- */
$("host-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError($("host-error"), "");
  const code = normalizeCode($("host-code").value);
  try {
    const result = await api.post(
      `/api/rooms/${encodeURIComponent(code)}/host/login`,
      { secret: $("host-secret").value }
    );
    session.setHost({ code: result.code, title: result.title, token: result.host_token });
    location.href = "host.html";
  } catch (err) {
    showError($("host-error"), err.message);
  }
});

/* ---- 部屋作成 ---- */
$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError($("create-error"), "");
  setText($("create-ok"), "");
  const secret = $("create-secret").value;
  if (secret.length < 8) {
    showError($("create-error"), "秘密キーは 8 文字以上にしてください。");
    return;
  }
  const tokenBlock = $("create-token-block");
  if (!tokenBlock.hidden && !$("create-token").value.trim()) {
    showError($("create-error"), "部屋作成の合言葉を入力してください。");
    $("create-token").focus();
    return;
  }
  try {
    const result = await api.post("/api/rooms", {
      title: $("create-title").value.trim(),
      secret,
      code: normalizeCode($("create-code").value),
      creation_token: $("create-token").value.trim(),
    });
    session.setHost({ code: result.code, title: result.title, token: result.host_token });
    setText($("create-ok"), `部屋コード ${result.code} を作成しました。管理画面へ移動します…`);
    setTimeout(() => {
      location.href = "host.html";
    }, 800);
  } catch (err) {
    showError($("create-error"), err.message);
  }
});
