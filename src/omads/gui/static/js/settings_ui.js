import { appState, el, esc, shortPath } from "./shared.js";

export function switchTab(tabId) {
  document.querySelectorAll(".tab-content").forEach((tab) => tab.classList.remove("active"));
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
  el(tabId).classList.add("active");
  const tabs = document.querySelectorAll(".tab");
  const map = { tabProject: 0, tabInterface: 1, tabReview: 2, tabClaude: 3, tabCodex: 4 };
  if (map[tabId] !== undefined) tabs[map[tabId]].classList.add("active");
}

export function applyTheme(theme) {
  appState.uiTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = appState.uiTheme;
  const btn = el("btnThemeToggle");
  if (btn) btn.textContent = `Theme: ${appState.uiTheme === "light" ? "Light" : "Dark"}`;
  const select = el("sTheme");
  if (select) select.value = appState.uiTheme;
}

export function applyBuilderAgent(builderAgent) {
  appState.builderAgent = builderAgent === "codex" ? "codex" : "claude";
  const badge = el("builderBadge");
  if (badge) badge.textContent = `Builder: ${appState.builderAgent === "codex" ? "Codex" : "Claude"}`;
  const select = el("sBuilder");
  if (select) select.value = appState.builderAgent;
}

export function syncSelectedRepo(path) {
  const repoPath = path || "";
  const repoInput = el("sRepo");
  if (repoInput) repoInput.value = repoPath;
  const repoBadge = el("repoBadge");
  if (repoBadge) repoBadge.textContent = shortPath(repoPath || "No project");
}

export function reviewerLabel(agent) {
  return agent === "codex" ? "Codex" : "Claude Code";
}

export function applyReviewPipeline(firstReviewer, secondReviewer) {
  appState.reviewFirstReviewer = firstReviewer === "codex" ? "codex" : "claude";
  appState.reviewSecondReviewer = secondReviewer === "claude" || secondReviewer === "codex"
    ? secondReviewer
    : (appState.reviewFirstReviewer === "claude" ? "codex" : "claude");
  if (appState.reviewSecondReviewer === appState.reviewFirstReviewer) {
    appState.reviewSecondReviewer = appState.reviewFirstReviewer === "claude" ? "codex" : "claude";
  }
  if (el("sReviewFirst")) el("sReviewFirst").value = appState.reviewFirstReviewer;
  if (el("sReviewSecond")) el("sReviewSecond").value = appState.reviewSecondReviewer;
  if (el("sReviewThird")) {
    el("sReviewThird").value = `${reviewerLabel(appState.reviewFirstReviewer)} (same as Reviewer 1)`;
  }
  const reviewBtn = el("btnReview");
  if (reviewBtn) {
    reviewBtn.title = `Start the configured manual review pipeline (${reviewerLabel(appState.reviewFirstReviewer)} -> ${reviewerLabel(appState.reviewSecondReviewer)} -> ${reviewerLabel(appState.reviewFirstReviewer)})`;
  }
}

export function syncReviewReviewerOptions(changed) {
  const first = el("sReviewFirst").value;
  const second = el("sReviewSecond").value;
  if (first === second) {
    if (changed === "first") {
      el("sReviewSecond").value = first === "claude" ? "codex" : "claude";
    } else {
      el("sReviewFirst").value = second === "claude" ? "codex" : "claude";
    }
  }
  applyReviewPipeline(el("sReviewFirst").value, el("sReviewSecond").value);
}

export function applyAutoReviewEnabled(enabled) {
  const badge = el("autoReviewBadge");
  if (!badge) return;
  const isEnabled = enabled !== false;
  badge.textContent = `Auto Review: ${isEnabled ? "On" : "Off"}`;
  badge.title = isEnabled
    ? "Automatic post-change review is enabled"
    : "Automatic post-change review is disabled";
  badge.classList.toggle("on", isEnabled);
}

export async function persistTheme(theme) {
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ui_theme: theme }),
  });
}

export async function toggleTheme() {
  const nextTheme = appState.uiTheme === "light" ? "dark" : "light";
  applyTheme(nextTheme);
  try {
    await persistTheme(nextTheme);
  } catch {}
}

export async function openSettings() {
  await loadSettings();
  el("settingsModal").classList.add("open");
  switchTab("tabProject");
  browseTo(el("sRepo").value || "~");
}

export function closeSettings() {
  el("settingsModal").classList.remove("open");
}

export async function browseTo(path) {
  try {
    const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (data.error && path !== "~") {
      browseTo("~");
      return;
    }

    appState.fpPath = data.path;
    el("fpPath").textContent = data.path;
    const list = el("fpList");
    list.innerHTML = "";

    if (data.parent) {
      const li = document.createElement("li");
      li.className = "fp-item fp-up";
      li.innerHTML = "<span>..</span><span>Up</span>";
      li.onclick = () => browseTo(data.parent);
      list.appendChild(li);
    }
    for (const dir of data.dirs) {
      const li = document.createElement("li");
      li.className = "fp-item";
      li.innerHTML = `<span>📁</span><span>${esc(dir.name)}</span>`;
      li.onclick = () => browseTo(dir.path);
      list.appendChild(li);
    }
  } catch {}
}

export function pickFolder() {
  el("sRepo").value = appState.fpPath;
  const btn = document.querySelector(".btn-pick");
  btn.textContent = "✓ Selected";
  setTimeout(() => {
    btn.textContent = "Select";
  }, 1200);
}

export async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const settings = await res.json();
    appState.savedSettings = settings;
    syncSelectedRepo(settings.target_repo || "");
    el("sBuilder").value = settings.builder_agent || "claude";
    el("sReviewFirst").value = settings.review_first_reviewer || "claude";
    el("sReviewSecond").value = settings.review_second_reviewer || "codex";
    el("sPerms").value = settings.claude_permission_mode || "default";
    el("sClaude").value = settings.claude_model || "sonnet";
    el("sClaudeEffort").value = settings.claude_effort || "high";
    el("sCodexModel").value = settings.codex_model || "";
    el("sCodexReasoning").value = settings.codex_reasoning || "high";
    el("sCodexFast").value = settings.codex_fast ? "true" : "false";
    el("sAutoReview").value = settings.auto_review !== false ? "true" : "false";
    el("sTheme").value = settings.ui_theme || "dark";
    el("sLanAccess").value = settings.lan_access ? "true" : "false";
    const lanGroup = el("lanInfoGroup");
    if (lanGroup) lanGroup.style.display = settings.lan_access ? "block" : "none";
    applyTheme(settings.ui_theme || "dark");
    applyBuilderAgent(settings.builder_agent || "claude");
    applyReviewPipeline(settings.review_first_reviewer || "claude", settings.review_second_reviewer || "codex");
    applyAutoReviewEnabled(settings.auto_review !== false);
  } catch {}
}

export async function saveSettings() {
  const lanNow = el("sLanAccess").value === "true";
  const data = {
    target_repo: el("sRepo").value,
    builder_agent: el("sBuilder").value,
    review_first_reviewer: el("sReviewFirst").value,
    review_second_reviewer: el("sReviewSecond").value,
    claude_permission_mode: el("sPerms").value,
    claude_model: el("sClaude").value,
    claude_effort: el("sClaudeEffort").value,
    codex_model: el("sCodexModel").value,
    codex_reasoning: el("sCodexReasoning").value,
    codex_fast: el("sCodexFast").value === "true",
    auto_review: el("sAutoReview").value === "true",
    ui_theme: el("sTheme").value,
    lan_access: lanNow,
  };
  if (appState.ws && data.target_repo) {
    appState.ws.send(JSON.stringify({ type: "set_repo", path: data.target_repo }));
  }
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  applyTheme(data.ui_theme);
  applyBuilderAgent(data.builder_agent);
  applyReviewPipeline(data.review_first_reviewer, data.review_second_reviewer);
  applyAutoReviewEnabled(data.auto_review);
  syncSelectedRepo(data.target_repo);
  const lanGroup = el("lanInfoGroup");
  if (lanGroup) lanGroup.style.display = lanNow ? "block" : "none";
  closeSettings();
}

export function openDiffViewer() {
  el("diffModal").classList.add("open");
  loadDiffViewer();
}

export function openDiffViewerFromSettings() {
  closeSettings();
  openDiffViewer();
}

export function closeDiffViewer() {
  el("diffModal").classList.remove("open");
}

export async function loadDiffViewer() {
  const btn = el("btnDiffRefresh");
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Refreshing...";
  try {
    const res = await fetch("/api/diff");
    const data = await res.json();
    el("diffTitle").textContent = data.has_changes ? "Current working tree changes" : "Current working tree";
    el("diffSubtitle").textContent = data.repo || "No project selected";

    if (data.error) {
      el("diffStatus").textContent = data.error;
      el("diffContent").textContent = "No diff available.";
      return;
    }

    if (!data.has_changes) {
      el("diffStatus").textContent = "Working tree clean. No staged, unstaged, or untracked changes were reported.";
      el("diffContent").textContent = "No local changes detected.";
      return;
    }

    const statusLines = (data.status_lines || []).join("\n");
    const changedFiles = data.changed_files || [];
    let statusText = `${changedFiles.length} changed item(s)`;
    if (statusLines) statusText += `\n\n${statusLines}`;
    el("diffStatus").textContent = statusText;
    el("diffContent").textContent = data.diff || "Git reported changes, but no unified diff text was returned.";
  } catch {
    el("diffStatus").textContent = "The diff could not be loaded right now.";
    el("diffContent").textContent = "Please try again.";
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

// ─── LAN access modal ────────────────────────────────────────────

export async function openLanModal() {
  closeSettings();
  el("lanModal").classList.add("open");
  try {
    const res = await fetch("/api/network-info");
    const info = await res.json();
    const url = info.lan_url || `http://${info.lan_ip}:${info.port}`;
    el("lanUrlDisplay").textContent = url;
    if (info.qr_data_url) {
      el("lanQr").innerHTML = `<img src="${info.qr_data_url}" alt="QR Code" style="width:100%;max-width:290px;image-rendering:pixelated">`;
    } else {
      el("lanQr").innerHTML = "";
    }
  } catch {
    el("lanUrlDisplay").textContent = "Could not detect LAN address";
    el("lanQr").innerHTML = "";
  }
}

export function closeLanModal() {
  el("lanModal").classList.remove("open");
}

export function copyLanUrl() {
  const url = el("lanUrlDisplay").textContent;
  if (!url || url === "—") return;
  navigator.clipboard.writeText(url).then(() => {
    const btn = el("btnCopyLan");
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = "Copy URL"; }, 1500);
  }).catch(() => {});
}
