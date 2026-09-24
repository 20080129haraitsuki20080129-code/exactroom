const root = document.documentElement;
const body = document.body;
const drawerButton = document.getElementById("problem-drawer-toggle");
const sidebar = document.getElementById("problem-sidebar");
const backdrop = document.getElementById("drawer-backdrop");
const mobileLayout = window.matchMedia("(max-width: 767px)");

function setDrawer(open) {
  const visible = Boolean(open && mobileLayout.matches);
  body.classList.toggle("drawer-open", visible);
  drawerButton.setAttribute("aria-expanded", String(visible));
  if (visible) {
    const firstProblem = sidebar.querySelector(".item");
    if (firstProblem) firstProblem.focus();
  } else if (document.activeElement && sidebar.contains(document.activeElement)) {
    drawerButton.focus();
  }
}

drawerButton.addEventListener("click", () => setDrawer(!body.classList.contains("drawer-open")));
backdrop.addEventListener("click", () => setDrawer(false));
document.getElementById("problem-list").addEventListener("click", (event) => {
  if (event.target.closest(".item") && mobileLayout.matches) setDrawer(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && body.classList.contains("drawer-open")) {
    setDrawer(false);
    drawerButton.focus();
  }
});
mobileLayout.addEventListener("change", () => setDrawer(false));

const popovers = [...document.querySelectorAll(".topbar-popover")];
popovers.forEach((popover) => {
  popover.addEventListener("toggle", () => {
    if (popover.open) popovers.filter((item) => item !== popover).forEach((item) => { item.open = false; });
  });
});
document.addEventListener("click", (event) => {
  popovers.forEach((popover) => {
    if (popover.open && !popover.contains(event.target)) popover.open = false;
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const openPopover = popovers.find((popover) => popover.open);
  if (openPopover) {
    openPopover.open = false;
    openPopover.querySelector("summary").focus();
  }
});

const themeChoice = document.getElementById("theme-choice");
function applyTheme(value) {
  if (value === "system") {
    root.removeAttribute("data-theme");
    root.style.colorScheme = "light dark";
  } else {
    root.dataset.theme = value;
    root.style.colorScheme = value;
  }
}
let savedTheme = "system";
try {
  const candidate = localStorage.getItem("exactroom-theme");
  if (["system", "light", "dark"].includes(candidate)) savedTheme = candidate;
} catch { /* Storage can be disabled; the system theme remains available. */ }
themeChoice.value = savedTheme;
applyTheme(savedTheme);
themeChoice.addEventListener("change", () => {
  applyTheme(themeChoice.value);
  try { localStorage.setItem("exactroom-theme", themeChoice.value); } catch { /* Keep the choice for this page session. */ }
});

const historyBody = document.getElementById("history-body");
const historyCount = document.getElementById("history-count");
function updateHistoryCount() {
  const emptyRow = historyBody.querySelector('[colspan="3"]');
  const count = emptyRow ? 0 : historyBody.querySelectorAll("tr").length;
  historyCount.textContent = count ? String(count) : "";
}
new MutationObserver(updateHistoryCount).observe(historyBody, { childList: true, subtree: true });
updateHistoryCount();

window.addEventListener("keydown", (event) => {
  if (!(event.metaKey || event.ctrlKey) || event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
  const problem = document.getElementById("problem-card");
  const answer = document.getElementById("tex-echo").textContent.trim();
  const submit = document.getElementById("submit");
  if (problem.hidden || !answer || submit.disabled) return;
  event.preventDefault();
  submit.click();
});
