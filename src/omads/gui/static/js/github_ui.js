import { appState, el, esc } from "./shared.js";
import { loadProjects } from "./projects_ui.js";

// ─── State ───────────────────────────────────────────────────────

let ghAuth = { authenticated: false, username: "" };
let ghRepos = [];

// ─── Auth status ─────────────────────────────────────────────────

export async function loadGitHubStatus() {
  try {
    const res = await fetch("/api/github/auth/status");
    ghAuth = await res.json();
    renderGitHubBadge();
  } catch {}
}

function renderGitHubBadge() {
  const badge = el("githubBadge");
  if (!badge) return;
  if (ghAuth.authenticated) {
    badge.textContent = `GitHub: ${ghAuth.username || "connected"}`;
    badge.classList.add("on");
  } else {
    badge.textContent = "GitHub";
    badge.classList.remove("on");
  }
}

// ─── GitHub Modal ────────────────────────────────────────────────

export function openGitHub() {
  el("githubModal").classList.add("open");
  if (ghAuth.authenticated) {
    showRepoView();
  } else {
    showAuthView();
  }
}

export function closeGitHub() {
  el("githubModal").classList.remove("open");
}

// ─── Auth View: simple token input ───────────────────────────────

function showAuthView() {
  const body = el("githubModalBody");
  body.innerHTML = `
    <div class="gh-auth-start">
      <p style="margin:0 0 16px;color:var(--text-dim)">Connect your GitHub account to browse, clone, and push repos directly from OMADS.</p>
      <div style="margin-bottom:12px">
        <label style="font-size:13px;color:var(--text-dim);display:block;margin-bottom:4px">GitHub Personal Access Token:</label>
        <input type="password" id="ghTokenInput" class="input" placeholder="ghp_xxxxxxxxxxxx" style="width:100%;font-family:monospace">
      </div>
      <button class="btn-primary" id="ghConnectBtn" style="width:100%">Connect</button>
      <div id="ghAuthStatus" style="margin-top:8px"></div>
      <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
        <p style="margin:0;font-size:13px;color:var(--text-dim)">
          <strong>How to get a token (1 min):</strong>
        </p>
        <ol style="margin:6px 0 0;padding-left:20px;font-size:13px;color:var(--text-dim);line-height:1.7">
          <li>Open <a href="https://github.com/settings/tokens?type=beta" target="_blank" style="color:var(--cyan)">github.com/settings/tokens</a></li>
          <li>Click "Generate new token"</li>
          <li>Give it a name (e.g. "OMADS"), select your repos</li>
          <li>Under "Permissions" enable <strong>Contents</strong> (read & write)</li>
          <li>Copy the token and paste it above</li>
        </ol>
      </div>
    </div>
  `;
  el("ghConnectBtn").onclick = connectWithToken;
  el("ghTokenInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") connectWithToken();
  });
  setTimeout(() => el("ghTokenInput")?.focus(), 100);
}

async function connectWithToken() {
  const token = el("ghTokenInput")?.value.trim();
  if (!token) {
    el("ghAuthStatus").innerHTML = `<div class="gh-error">Please paste your token</div>`;
    return;
  }

  const btn = el("ghConnectBtn");
  btn.disabled = true;
  btn.textContent = "Connecting...";
  el("ghAuthStatus").innerHTML = "";

  try {
    const res = await fetch("/api/github/auth/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await res.json();

    if (data.error) {
      el("ghAuthStatus").innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      btn.disabled = false;
      btn.textContent = "Connect";
      return;
    }

    ghAuth = { authenticated: true, username: data.username || "" };
    renderGitHubBadge();
    showRepoView();
  } catch (err) {
    el("ghAuthStatus").innerHTML = `<div class="gh-error">Connection failed: ${esc(String(err))}</div>`;
    btn.disabled = false;
    btn.textContent = "Connect";
  }
}

// ─── Repo Browser View ───────────────────────────────────────────

async function showRepoView() {
  const body = el("githubModalBody");
  body.innerHTML = `
    <div class="gh-repo-header">
      <span style="color:var(--text-dim)">Signed in as <strong>${esc(ghAuth.username)}</strong></span>
      <button class="btn-sm btn-danger" id="ghDisconnect">Disconnect</button>
    </div>
    <div class="gh-repo-list" id="ghRepoList">
      <div class="gh-loading">Loading repositories...</div>
    </div>
  `;
  el("ghDisconnect").onclick = disconnectGitHub;
  await loadRepos();
}

async function disconnectGitHub() {
  try {
    await fetch("/api/github/auth", { method: "DELETE" });
    ghAuth = { authenticated: false, username: "" };
    renderGitHubBadge();
    showAuthView();
  } catch {}
}

async function loadRepos(page = 1) {
  try {
    const res = await fetch(`/api/github/repos?page=${page}&per_page=30`);
    const data = await res.json();

    if (data.error) {
      el("ghRepoList").innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      return;
    }

    ghRepos = data.repos || [];
    renderRepoList();
  } catch {
    el("ghRepoList").innerHTML = `<div class="gh-error">Failed to load repos</div>`;
  }
}

function renderRepoList() {
  const list = el("ghRepoList");
  if (ghRepos.length === 0) {
    list.innerHTML = `<div class="gh-loading">No repositories found.</div>`;
    return;
  }

  list.innerHTML = "";
  for (const repo of ghRepos) {
    const div = document.createElement("div");
    div.className = "gh-repo-item";
    const langBadge = repo.language ? `<span class="gh-lang">${esc(repo.language)}</span>` : "";
    const privBadge = repo.private ? `<span class="gh-private">private</span>` : "";
    div.innerHTML = `
      <div class="gh-repo-info">
        <div class="gh-repo-name">${esc(repo.full_name)} ${privBadge} ${langBadge}</div>
        <div class="gh-repo-desc">${esc(repo.description || "No description")}</div>
      </div>
      <div class="gh-repo-actions">
        <button class="btn-sm btn-primary gh-clone-btn" data-repo="${esc(repo.full_name)}">Clone & Open</button>
      </div>
    `;
    div.querySelector(".gh-clone-btn").onclick = () => showCloneDialog(repo.full_name);
    list.appendChild(div);
  }
}

// ─── Clone Dialog ────────────────────────────────────────────────

function showCloneDialog(fullName) {
  const repoName = fullName.split("/").pop();
  const defaultDir = `${appState.npFpPath || "~"}/${repoName}`;

  const body = el("githubModalBody");
  body.innerHTML = `
    <div class="gh-clone-dialog">
      <h3 style="margin:0 0 12px">Clone ${esc(fullName)}</h3>
      <label style="font-size:13px;color:var(--text-dim);display:block;margin-bottom:4px">Target directory:</label>
      <input type="text" id="ghCloneDir" class="input" value="${esc(defaultDir)}" style="width:100%;margin-bottom:12px">
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-sm" id="ghCloneBack">Back</button>
        <button class="btn-sm btn-primary" id="ghCloneStart">Clone</button>
      </div>
      <div id="ghCloneStatus" style="margin-top:12px"></div>
    </div>
  `;
  el("ghCloneBack").onclick = showRepoView;
  el("ghCloneStart").onclick = () => doClone(fullName);
}

async function doClone(fullName) {
  const targetDir = el("ghCloneDir").value.trim();
  if (!targetDir) return;

  const status = el("ghCloneStatus");
  status.innerHTML = `<div class="gh-loading">Cloning ${esc(fullName)}...</div>`;
  el("ghCloneStart").disabled = true;

  try {
    const res = await fetch("/api/github/clone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: fullName, target_dir: targetDir }),
    });
    const data = await res.json();

    if (data.error) {
      status.innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      el("ghCloneStart").disabled = false;
      return;
    }

    status.innerHTML = `<div class="gh-success">Cloned and registered as project!</div>`;
    await loadProjects();
    setTimeout(() => closeGitHub(), 1500);
  } catch (err) {
    status.innerHTML = `<div class="gh-error">Clone failed: ${esc(String(err))}</div>`;
    el("ghCloneStart").disabled = false;
  }
}

// ─── Git Operations Modal ────────────────────────────────────────

export function openGitOps(projectPath, projectName) {
  el("gitOpsModal").classList.add("open");
  el("gitOpsTitle").textContent = `Git: ${projectName}`;
  loadGitStatus(projectPath);
}

export function closeGitOps() {
  el("gitOpsModal").classList.remove("open");
}

async function loadGitStatus(repoPath) {
  const body = el("gitOpsBody");
  body.innerHTML = `<div class="gh-loading">Loading status...</div>`;

  try {
    const res = await fetch("/api/github/git", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_path: repoPath, operation: "status" }),
    });
    const data = await res.json();

    if (data.error) {
      body.innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      return;
    }

    const statusLines = (data.status_lines || []).map((l) => esc(l)).join("\n");
    const cleanMsg = data.clean ? `<div class="gh-success">Working tree is clean</div>` : "";

    body.innerHTML = `
      ${cleanMsg}
      ${!data.clean ? `<pre class="gh-status-pre">${statusLines}</pre>` : ""}
      <div class="gh-ops-row">
        <div class="gh-commit-row" style="display:${data.clean ? "none" : "flex"};gap:8px;margin-bottom:8px">
          <input type="text" id="gitCommitMsg" class="input" placeholder="Commit message..." style="flex:1">
          <button class="btn-sm btn-primary" id="gitCommitBtn">Commit</button>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn-sm" id="gitPullBtn">Pull</button>
          <button class="btn-sm btn-primary" id="gitPushBtn">Push</button>
          <button class="btn-sm" id="gitRefreshBtn">Refresh</button>
        </div>
      </div>
      <div id="gitOpsStatus" style="margin-top:8px"></div>
    `;

    el("gitCommitBtn")?.addEventListener("click", () => runGitOp(repoPath, "commit"));
    el("gitPullBtn").addEventListener("click", () => runGitOp(repoPath, "pull"));
    el("gitPushBtn").addEventListener("click", () => runGitOp(repoPath, "push"));
    el("gitRefreshBtn").addEventListener("click", () => loadGitStatus(repoPath));
  } catch {
    body.innerHTML = `<div class="gh-error">Failed to load status</div>`;
  }
}

async function runGitOp(repoPath, operation) {
  const status = el("gitOpsStatus");
  const payload = { repo_path: repoPath, operation };

  if (operation === "commit") {
    const msg = el("gitCommitMsg")?.value.trim();
    if (!msg) {
      status.innerHTML = `<div class="gh-error">Please enter a commit message</div>`;
      return;
    }
    payload.message = msg;
  }

  status.innerHTML = `<div class="gh-loading">${esc(operation)}...</div>`;

  try {
    const res = await fetch("/api/github/git", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (data.error) {
      status.innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      return;
    }

    const output = data.output || data.message || `${operation} done`;
    status.innerHTML = `<div class="gh-success">${esc(output)}</div>`;

    if (operation === "commit") {
      setTimeout(() => loadGitStatus(repoPath), 800);
    }
  } catch (err) {
    status.innerHTML = `<div class="gh-error">${esc(String(err))}</div>`;
  }
}

// ─── WebSocket event handlers ────────────────────────────────────

export function handleGitHubWsEvent(msg) {
  if (msg.type === "github_connected") {
    ghAuth = { authenticated: true, username: msg.username || "" };
    renderGitHubBadge();
  } else if (msg.type === "github_disconnected") {
    ghAuth = { authenticated: false, username: "" };
    renderGitHubBadge();
  }
}
