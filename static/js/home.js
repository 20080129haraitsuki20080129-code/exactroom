import { api, session, showError, setText } from "./api.js";

const $ = (id) => document.getElementById(id);

/* ---- タブ ---- */
function selectTab(name) {
  for (const key of ["solve", "host"]) {
    const isActive = key === name;
    $(`tab-${key}`).setAttribute("aria-selected", String(isActive));
    $(`panel-${key}`).hidden = !isActive;
  }
  try {
    history.replaceState(null, "", `#${name}`);
  } catch {
    /* ignore */
  }
}

$("tab-solve").addEventListener("click", () => selectTab("solve"));
$("tab-host").addEventListener("click", () => selectTab("host"));
if (location.hash === "#host") selectTab("host");

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
    location.href = "/solve";
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
    location.href = "/host";
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
  try {
    const result = await api.post("/api/rooms", {
      title: $("create-title").value.trim(),
      secret,
      creation_token: $("create-token").value.trim(),
    });
    session.setHost({ code: result.code, title: result.title, token: result.host_token });
    setText($("create-ok"), `部屋コード ${result.code} を作成しました。管理画面へ移動します…`);
    setTimeout(() => {
      location.href = "/host";
    }, 800);
  } catch (err) {
    showError($("create-error"), err.message);
  }
});
