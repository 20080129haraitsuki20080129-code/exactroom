/** API 呼び出しとセッション管理。 */

const BASE = (window.EXACTROOM_API_BASE || "").replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code || "error";
  }
}

async function request(method, path, { body, token } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "omit",
      cache: "no-store",
    });
  } catch (err) {
    throw new ApiError("サーバに接続できません。通信環境を確認してください。", 0, "network");
  }

  if (response.status === 204) return null;

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const detail = (payload && payload.detail) || `エラーが発生しました (${response.status})`;
    throw new ApiError(detail, response.status, payload && payload.code);
  }
  return payload;
}

export const api = {
  get: (path, token) => request("GET", path, { token }),
  post: (path, body, token) => request("POST", path, { body, token }),
  patch: (path, body, token) => request("PATCH", path, { body, token }),
  del: (path, token) => request("DELETE", path, { token }),
  base: BASE,
};

/* ---------------- セッション ---------------- */

const SOLVER_KEY = "exactroom.solver";
const HOST_KEY = "exactroom.host";

function load(key) {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* プライベートブラウズ等で失敗しても動作は続ける */
  }
}

export const session = {
  getSolver: () => load(SOLVER_KEY),
  setSolver: (value) => save(SOLVER_KEY, value),
  clearSolver: () => save(SOLVER_KEY, null),
  getHost: () => load(HOST_KEY),
  setHost: (value) => save(HOST_KEY, value),
  clearHost: () => save(HOST_KEY, null),
};

/* ---------------- 表示ユーティリティ ---------------- */

/** テキストを安全に差し込む (innerHTML は使わない)。 */
export function setText(element, text) {
  if (!element) return;
  element.textContent = text == null ? "" : String(text);
}

export function showError(element, message) {
  setText(element, message || "");
}

export function formatTime(isoString) {
  if (!isoString) return "";
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return String(isoString);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

/** 一覧に出す短い要約用に、TeX の記号類を落として読みやすくする。 */
export function texPreview(tex, maxLength = 48) {
  let text = String(tex || "");
  text = text.replace(/\\text\s*\{([^}]*)\}/g, "$1");
  text = text.replace(/\\(?:left|right|displaystyle|,|;|:|!)/g, "");
  text = text.replace(/\\[a-zA-Z]+/g, " ");
  text = text.replace(/[{}$]/g, "");
  text = text.replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}

export function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text != null) node.textContent = String(options.text);
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) {
      if (value !== null && value !== undefined) node.setAttribute(key, String(value));
    }
  }
  if (options.on) {
    for (const [event, handler] of Object.entries(options.on)) {
      node.addEventListener(event, handler);
    }
  }
  for (const child of children) {
    if (child) node.appendChild(child);
  }
  return node;
}
