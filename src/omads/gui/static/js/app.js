import { appState, el, esc, formatMsg, getClientSessionId } from "./shared.js";
import {
  addSystem,
  lock,
  logEvent,
  renderChatEvent,
  switchLogTab,
  toggleLiveLog,
  unlock,
} from "./chat_ui.js";
import {
  applyAutoReviewEnabled,
  applyBuilderAgent,
  applyReviewPipeline,
  applyTheme,
  browseTo,
  closeDiffViewer,
  closeLanModal,
  closeSettings,
  copyLanUrl,
  loadDiffViewer,
  loadSettings,
  openDiffViewerFromSettings,
  openLanModal,
  openSettings,
  pickFolder,
  saveSettings,
  switchTab,
  syncReviewReviewerOptions,
  toggleTheme,
} from "./settings_ui.js";
import {
  closeNewProject,
  createProject,
  loadProjects,
  openNewProject,
  pickNewProjectFolder,
} from "./projects_ui.js";
import {
  closeGitHub,
  closeGitOps,
  handleGitHubWsEvent,
  loadGitHubStatus,
  openGitHub,
  openGitOps,
} from "./github_ui.js";

function handle(msg) {
  if (msg.type === "server_restart") {
    addSystem(msg.text || "Server is restarting…");
    return;
  }
  if (msg.type === "github_connected" || msg.type === "github_disconnected") {
    handleGitHubWsEvent(msg);
    return;
  }
  logEvent(msg);
  renderChatEvent(msg);
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const clientSessionId = encodeURIComponent(getClientSessionId());
  appState.ws = new WebSocket(`${protocol}://${location.host}/ws?client_session_id=${clientSessionId}`);
  appState.ws.onopen = () => {
    appState.reconnectDelay = 2000;
    el("connBadge").textContent = "Connected";
    el("connBadge").classList.add("on");
    loadSettings();
    loadProjects();
    loadGitHubStatus();
  };
  appState.ws.onclose = () => {
    el("connBadge").textContent = "Disconnected";
    el("connBadge").classList.remove("on");
    if (appState.busy) {
      addSystem("Connection to the server was lost. The UI was unlocked and an automatic reconnect is in progress.");
      unlock();
    }
    setTimeout(connect, appState.reconnectDelay);
    appState.reconnectDelay = Math.min(appState.reconnectDelay * 2, 30000);
  };
  appState.ws.onmessage = (event) => handle(JSON.parse(event.data));
}

export function openReviewDialog() {
  el("reviewModal").classList.add("open");
  toggleCustomScope();
  toggleCustomFocus();
}

export function closeReviewDialog() {
  el("reviewModal").classList.remove("open");
}

export function toggleCustomScope() {
  const show = el("reviewScope").value === "custom";
  el("customScopeGroup").style.display = show ? "block" : "none";
}

export function toggleCustomFocus() {
  const show = el("reviewFocus").value === "custom";
  el("customFocusGroup").style.display = show ? "block" : "none";
}

export function startReview() {
  if (!appState.ws) return;
  const scope = el("reviewScope").value;
  const focus = el("reviewFocus").value;
  const customScope = el("reviewCustomScope") ? el("reviewCustomScope").value : "";
  const customFocus = el("reviewCustomFocus") ? el("reviewCustomFocus").value.trim() : "";
  if (focus === "custom" && !customFocus) {
    alert("Please describe what the manual review should focus on.");
    return;
  }

  closeReviewDialog();

  const focusMap = {
    all: "Everything",
    security: "Security",
    bugs: "Bugs & logic",
    performance: "Performance",
    custom: customFocus || "Custom instructions",
  };
  const scopeMap = {
    project: "Whole project",
    last_task: "Last task",
    custom: customScope || "Custom selection",
  };
  const div = document.createElement("div");
  div.className = "msg msg-user";
  div.innerHTML = formatMsg(`Start review — Scope: ${scopeMap[scope]}, Focus: ${focusMap[focus]}`);
  el("stream").appendChild(div);
  appState.ws.send(JSON.stringify({ type: "review", scope, focus, custom_scope: customScope, custom_focus: customFocus }));
  lock();
}

export function applyFixes() {
  if (!appState.ws) return;
  const div = document.createElement("div");
  div.className = "msg msg-user";
  div.innerHTML = formatMsg("Apply fixes");
  el("stream").appendChild(div);
  appState.ws.send(JSON.stringify({ type: "apply_fixes" }));
  lock();
}

export function send() {
  if (appState.busy) return;
  const input = el("input");
  const text = input.value.trim();
  if (!text || !appState.ws) return;

  const div = document.createElement("div");
  div.className = "msg msg-user";
  div.innerHTML = formatMsg(text);
  el("stream").appendChild(div);

  appState.ws.send(JSON.stringify({ type: "chat", text }));
  input.value = "";
  input.style.height = "auto";
  lock();
}

export function stop() {
  if (appState.ws) appState.ws.send(JSON.stringify({ type: "stop" }));
}

export function onKey(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
  if (event.key === "Escape" && appState.busy) {
    event.preventDefault();
    stop();
  }
  setTimeout(() => {
    event.target.style.height = "auto";
    event.target.style.height = `${Math.min(event.target.scrollHeight, 120)}px`;
  }, 0);
}

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const health = await res.json();
    const banner = el("onboardBanner");

    const allOk = health.claude?.installed && health.codex?.installed && health.claude?.authenticated !== false;
    if (allOk) {
      banner.className = "onboard-banner";
      banner.innerHTML = `<div class="onboard-ok">Claude Code CLI ${health.claude.version ? `(${esc(health.claude.version)})` : ""} + Codex CLI ${health.codex.version ? `(${esc(health.codex.version)})` : ""} — ready.</div>`;
      banner.style.display = "";
      setTimeout(() => {
        banner.style.display = "none";
      }, 5000);
      return;
    }

    let html = "<h3>Setup required</h3>";
    html += '<div class="cli-status">';

    if (health.claude?.installed) {
      html += '<div class="cli-card ok"><div class="cli-name ok">Claude Code CLI</div>';
      html += `<div class="cli-ver">${esc(health.claude.version || "")} &mdash; ${esc(health.claude.path || "")}</div>`;
      if (health.claude.authenticated === false) {
        html += '<div class="cli-hint">Not authenticated. Run <code>claude</code> once in a terminal and sign in.</div>';
      }
      html += "</div>";
    } else {
      html += '<div class="cli-card missing"><div class="cli-name missing">Claude Code CLI</div>';
      html += '<div class="cli-hint">Not found. Install with:</div>';
      html += '<div class="install-cmd">npm install -g @anthropic-ai/claude-code</div>';
      html += '<div class="cli-ver" style="margin-top:6px">Requirement: <code>Node.js 18+</code> &mdash; <a href="https://nodejs.org" target="_blank" style="color:var(--cyan)">nodejs.org</a></div>';
      html += '<div class="cli-ver">Then run <code>claude</code> once in a terminal and sign in with your Claude Pro / Max / Team subscription.</div>';
      html += "</div>";
    }

    if (health.codex?.installed) {
      html += '<div class="cli-card ok"><div class="cli-name ok">Codex CLI</div>';
      html += `<div class="cli-ver">${esc(health.codex.version || "")} &mdash; ${esc(health.codex.path || "")}</div>`;
      html += "</div>";
    } else {
      html += '<div class="cli-card missing"><div class="cli-name missing">Codex CLI</div>';
      html += '<div class="cli-hint">Not found. Install with:</div>';
      html += '<div class="install-cmd">npm install -g @openai/codex</div>';
      html += '<div class="cli-ver" style="margin-top:6px">Requirement: <code>Node.js 22+</code> &mdash; <a href="https://nodejs.org" target="_blank" style="color:var(--cyan)">nodejs.org</a></div>';
      html += '<div class="cli-ver">Then run <code>codex</code> once in a terminal and sign in with your ChatGPT Plus / Pro subscription.</div>';
      html += "</div>";
    }

    html += "</div>";
    if (!health.claude?.installed || !health.codex?.installed) {
      html += '<div class="cli-ver" style="margin-top:8px">After installation, restart the OMADS server or reload the page.</div>';
    }
    html += '<button class="dismiss-btn" onclick="el(\'onboardBanner\').style.display=\'none\'">Got it, dismiss</button>';

    banner.className = "onboard-banner";
    banner.innerHTML = html;
    banner.style.display = "";
  } catch {}
}

function exposeGlobals() {
  window.el = el;
  window.openSettings = openSettings;
  window.closeSettings = closeSettings;
  window.switchTab = switchTab;
  window.toggleTheme = toggleTheme;
  window.pickFolder = pickFolder;
  window.saveSettings = saveSettings;
  window.openDiffViewerFromSettings = openDiffViewerFromSettings;
  window.closeDiffViewer = closeDiffViewer;
  window.loadDiffViewer = loadDiffViewer;
  window.syncReviewReviewerOptions = syncReviewReviewerOptions;
  window.openLanModal = openLanModal;
  window.closeLanModal = closeLanModal;
  window.copyLanUrl = copyLanUrl;
  window.toggleLiveLog = toggleLiveLog;
  window.switchLogTab = switchLogTab;
  window.openNewProject = openNewProject;
  window.closeNewProject = closeNewProject;
  window.pickNewProjectFolder = pickNewProjectFolder;
  window.createProject = createProject;
  window.openReviewDialog = openReviewDialog;
  window.closeReviewDialog = closeReviewDialog;
  window.toggleCustomScope = toggleCustomScope;
  window.toggleCustomFocus = toggleCustomFocus;
  window.startReview = startReview;
  window.send = send;
  window.stop = stop;
  window.onKey = onKey;
  window.applyFixes = applyFixes;
  window.browseTo = browseTo;
  window.openGitHub = openGitHub;
  window.closeGitHub = closeGitHub;
  window.openGitOps = openGitOps;
  window.closeGitOps = closeGitOps;
  window.toggleMobileSidebar = toggleMobileSidebar;
  window.closeMobileSidebar = closeMobileSidebar;
}

const MOBILE_MEDIA = "(max-width: 1024px)";

function closeMobileSidebar() {
  const sidebar = el("sidebar");
  const overlay = el("sidebarOverlay");
  if (!sidebar || !overlay) return;
  sidebar.classList.remove("mobile-open");
  overlay.classList.remove("open");
}

function toggleMobileSidebar(forceOpen) {
  const sidebar = el("sidebar");
  const overlay = el("sidebarOverlay");
  if (!sidebar || !overlay) return;

  if (!window.matchMedia(MOBILE_MEDIA).matches) {
    closeMobileSidebar();
    return;
  }

  const open = typeof forceOpen === "boolean"
    ? forceOpen
    : !sidebar.classList.contains("mobile-open");

  sidebar.classList.toggle("mobile-open", open);
  overlay.classList.toggle("open", open);
}

function bindMobileSidebar() {
  const sync = () => {
    if (!window.matchMedia(MOBILE_MEDIA).matches) closeMobileSidebar();
  };
  window.addEventListener("resize", sync);
  window.addEventListener("orientationchange", () => setTimeout(sync, 0));
}

function init() {
  exposeGlobals();
  bindMobileSidebar();
  connect();
  checkHealth();
  el("input").focus();
}

init();
