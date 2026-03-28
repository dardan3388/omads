import { appState, el, esc } from "./shared.js";
import { loadProjects } from "./projects_ui.js";

// ─── State ───────────────────────────────────────────────────────

let ghAuth = { authenticated: false, username: "", has_client_id: false };
let ghRepos = [];
let _devicePollTimer = null;

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
    showMainView();
  } else {
    showAuthView();
  }
}

export function closeGitHub() {
  el("githubModal").classList.remove("open");
  _stopDevicePoll();
}

// ─── Auth View: Device Flow + PAT fallback ──────────────────────

function showAuthView() {
  const body = el("githubModalBody");
  const hasClientId = ghAuth.has_client_id;

  body.innerHTML = `
    <div class="gh-auth-start">
      <p style="margin:0 0 16px;color:var(--text-dim)">Connect your GitHub account to browse, clone, and push repos directly from OMADS.</p>

      ${hasClientId ? `
        <button class="btn-primary" id="ghDeviceFlowBtn" style="width:100%;margin-bottom:12px">
          Connect with GitHub
        </button>
        <div id="ghDeviceFlowArea" style="display:none"></div>
        <div style="margin-top:12px;text-align:center">
          <button class="btn-link" id="ghShowPatBtn" style="font-size:12px;color:var(--text-dim);background:none;border:none;cursor:pointer;text-decoration:underline">
            Or use a Personal Access Token instead
          </button>
        </div>
      ` : ""}

      <div id="ghPatArea" style="display:${hasClientId ? "none" : "block"}">
        <div style="margin-bottom:12px">
          <label style="font-size:13px;color:var(--text-dim);display:block;margin-bottom:4px">GitHub Personal Access Token:</label>
          <input type="password" id="ghTokenInput" class="input" placeholder="ghp_xxxxxxxxxxxx" style="width:100%;font-family:monospace">
        </div>
        <button class="btn-primary" id="ghConnectBtn" style="width:100%">Connect</button>
        <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border)">
          <p style="margin:0;font-size:13px;color:var(--text-dim)"><strong>How to get a token (1 min):</strong></p>
          <ol style="margin:6px 0 0;padding-left:20px;font-size:13px;color:var(--text-dim);line-height:1.7">
            <li>Open <a href="https://github.com/settings/tokens?type=beta" target="_blank" style="color:var(--cyan)">github.com/settings/tokens</a></li>
            <li>Click "Generate new token"</li>
            <li>Give it a name (e.g. "OMADS"), select your repos</li>
            <li>Under "Permissions" enable <strong>Contents</strong> (read & write)</li>
            <li>Copy the token and paste it above</li>
          </ol>
        </div>
      </div>
      <div id="ghAuthStatus" style="margin-top:8px"></div>
    </div>
  `;

  if (hasClientId) {
    el("ghDeviceFlowBtn").onclick = startDeviceFlow;
    el("ghShowPatBtn").onclick = () => {
      el("ghPatArea").style.display = "block";
      el("ghShowPatBtn").style.display = "none";
    };
  }

  const connectBtn = el("ghConnectBtn");
  if (connectBtn) {
    connectBtn.onclick = connectWithToken;
    el("ghTokenInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter") connectWithToken();
    });
    if (!hasClientId) setTimeout(() => el("ghTokenInput")?.focus(), 100);
  }
}

async function startDeviceFlow() {
  const btn = el("ghDeviceFlowBtn");
  btn.disabled = true;
  btn.textContent = "Starting...";
  el("ghAuthStatus").innerHTML = "";

  try {
    const res = await fetch("/api/github/auth/device/start", { method: "POST" });
    const data = await res.json();

    if (data.error) {
      el("ghAuthStatus").innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      btn.disabled = false;
      btn.textContent = "Connect with GitHub";
      return;
    }

    // Show the device code UI
    const area = el("ghDeviceFlowArea");
    area.style.display = "block";
    btn.style.display = "none";
    el("ghPatArea").style.display = "none";
    const showPatBtn = el("ghShowPatBtn");
    if (showPatBtn) showPatBtn.style.display = "none";

    area.innerHTML = `
      <div style="text-align:center;padding:16px 0">
        <p style="margin:0 0 12px;color:var(--text-dim)">Enter this code on GitHub:</p>
        <div id="ghUserCode" style="font-size:32px;font-weight:bold;font-family:monospace;letter-spacing:4px;padding:12px;background:var(--surface);border-radius:8px;cursor:pointer;user-select:all" title="Click to copy">
          ${esc(data.user_code)}
        </div>
        <p style="margin:12px 0 0;font-size:13px;color:var(--text-dim)">Code expires in ${Math.floor(data.expires_in / 60)} minutes</p>
        <a href="${esc(data.verification_uri)}" target="_blank" class="btn-primary" style="display:inline-block;margin-top:16px;text-decoration:none;padding:10px 24px">
          Open GitHub
        </a>
        <p style="margin:16px 0 0;font-size:13px;color:var(--text-dim)" id="ghPollStatus">Waiting for authorization...</p>
      </div>
    `;

    // Copy code on click
    el("ghUserCode").onclick = () => {
      navigator.clipboard?.writeText(data.user_code);
      el("ghUserCode").title = "Copied!";
      setTimeout(() => { el("ghUserCode").title = "Click to copy"; }, 2000);
    };

    // Start polling
    _startDevicePoll(data.device_code, data.interval);
  } catch (err) {
    el("ghAuthStatus").innerHTML = `<div class="gh-error">Failed: ${esc(String(err))}</div>`;
    btn.disabled = false;
    btn.textContent = "Connect with GitHub";
  }
}

function _startDevicePoll(deviceCode, interval) {
  _stopDevicePoll();
  const pollInterval = Math.max(interval, 5) * 1000;

  _devicePollTimer = setInterval(async () => {
    try {
      const res = await fetch("/api/github/auth/device/poll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_code: deviceCode }),
      });
      const data = await res.json();

      if (data.status === "complete") {
        _stopDevicePoll();
        ghAuth = { authenticated: true, username: data.username || "", has_client_id: ghAuth.has_client_id };
        renderGitHubBadge();
        showMainView();
      } else if (data.status === "expired") {
        _stopDevicePoll();
        el("ghPollStatus").innerHTML = `<span style="color:var(--error)">Code expired — try again</span>`;
      } else if (data.status === "error") {
        _stopDevicePoll();
        el("ghPollStatus").innerHTML = `<span style="color:var(--error)">${esc(data.error)}</span>`;
      }
      // "pending" → keep polling
    } catch {
      // Network error — keep trying
    }
  }, pollInterval);
}

function _stopDevicePoll() {
  if (_devicePollTimer) {
    clearInterval(_devicePollTimer);
    _devicePollTimer = null;
  }
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

    ghAuth = { authenticated: true, username: data.username || "", has_client_id: ghAuth.has_client_id };
    renderGitHubBadge();
    showMainView();
  } catch (err) {
    el("ghAuthStatus").innerHTML = `<div class="gh-error">Connection failed: ${esc(String(err))}</div>`;
    btn.disabled = false;
    btn.textContent = "Connect";
  }
}

// ─── Main View (authenticated) ──────────────────────────────────

async function showMainView() {
  const body = el("githubModalBody");
  body.innerHTML = `
    <div class="gh-main-header" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
      <span style="color:var(--text-dim)">Signed in as <strong>${esc(ghAuth.username)}</strong></span>
      <button class="btn-sm btn-danger" id="ghDisconnect">Disconnect</button>
    </div>

    <div class="gh-actions" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px">
      <button class="btn-sm btn-primary" id="ghOpenRepoBtn" style="padding:12px">Open Repository</button>
      <button class="btn-sm" id="ghCreateRepoBtn" style="padding:12px">New Repository</button>
      <button class="btn-sm" id="ghReviewRepoBtn" style="padding:12px;grid-column:span 2">Review External Repo</button>
    </div>

    <div style="margin-bottom:8px">
      <input type="text" id="ghSearchInput" class="input" placeholder="Search your repositories..." style="width:100%">
    </div>
    <div class="gh-repo-list" id="ghRepoList">
      <div class="gh-loading">Loading repositories...</div>
    </div>
  `;

  el("ghDisconnect").onclick = disconnectGitHub;
  el("ghOpenRepoBtn").onclick = () => showOpenRepoDialog();
  el("ghCreateRepoBtn").onclick = () => showCreateRepoDialog();
  el("ghReviewRepoBtn").onclick = () => showReviewRepoDialog();

  let searchTimeout = null;
  el("ghSearchInput").addEventListener("input", (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => loadRepos(1, e.target.value.trim()), 300);
  });

  await loadRepos();
}

async function disconnectGitHub() {
  try {
    await fetch("/api/github/auth", { method: "DELETE" });
    ghAuth = { authenticated: false, username: "", has_client_id: ghAuth.has_client_id };
    renderGitHubBadge();
    showAuthView();
  } catch {}
}

// ─── Repo List with Search ──────────────────────────────────────

async function loadRepos(page = 1, search = "") {
  try {
    const params = new URLSearchParams({ page, per_page: 30 });
    if (search) params.set("search", search);
    const res = await fetch(`/api/github/repos?${params}`);
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

// ─── Open Repo by URL/Name ──────────────────────────────────────

function showOpenRepoDialog() {
  const body = el("githubModalBody");
  body.innerHTML = `
    <div>
      <button class="btn-link" id="ghBackToMain" style="margin-bottom:12px;background:none;border:none;cursor:pointer;color:var(--cyan)">← Back</button>
      <h3 style="margin:0 0 12px">Open GitHub Repository</h3>
      <p style="margin:0 0 12px;color:var(--text-dim);font-size:13px">Enter a repository name (owner/repo) or paste a GitHub URL:</p>
      <input type="text" id="ghRepoInput" class="input" placeholder="owner/repo or https://github.com/..." style="width:100%;margin-bottom:12px">
      <button class="btn-sm btn-primary" id="ghOpenBtn" style="width:100%">Open</button>
      <div id="ghOpenStatus" style="margin-top:8px"></div>
    </div>
  `;
  el("ghBackToMain").onclick = showMainView;
  el("ghOpenBtn").onclick = openRepoByName;
  el("ghRepoInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") openRepoByName();
  });
  setTimeout(() => el("ghRepoInput")?.focus(), 100);
}

async function openRepoByName() {
  let input = el("ghRepoInput")?.value.trim();
  if (!input) return;

  // Extract owner/repo from URL if needed
  const urlMatch = input.match(/github\.com\/([^/]+\/[^/]+)/);
  if (urlMatch) input = urlMatch[1].replace(/\.git$/, "");

  const status = el("ghOpenStatus");
  status.innerHTML = `<div class="gh-loading">Looking up ${esc(input)}...</div>`;

  try {
    const res = await fetch(`/api/github/repos/info?full_name=${encodeURIComponent(input)}`);
    const data = await res.json();

    if (data.error) {
      status.innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      return;
    }

    showCloneDialog(data.full_name);
  } catch (err) {
    status.innerHTML = `<div class="gh-error">Failed: ${esc(String(err))}</div>`;
  }
}

// ─── Create Repo ────────────────────────────────────────────────

function showCreateRepoDialog() {
  const body = el("githubModalBody");
  body.innerHTML = `
    <div>
      <button class="btn-link" id="ghBackToMain" style="margin-bottom:12px;background:none;border:none;cursor:pointer;color:var(--cyan)">← Back</button>
      <h3 style="margin:0 0 16px">New GitHub Repository</h3>
      <div style="margin-bottom:12px">
        <label style="font-size:13px;color:var(--text-dim);display:block;margin-bottom:4px">Repository name</label>
        <input type="text" id="ghNewRepoName" class="input" placeholder="my-project" style="width:100%">
      </div>
      <div style="margin-bottom:12px">
        <label style="font-size:13px;color:var(--text-dim);display:block;margin-bottom:4px">Description (optional)</label>
        <input type="text" id="ghNewRepoDesc" class="input" placeholder="" style="width:100%">
      </div>
      <div style="margin-bottom:12px;display:flex;gap:12px;align-items:center">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
          <input type="radio" name="ghVisibility" value="public" checked> Public
        </label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
          <input type="radio" name="ghVisibility" value="private"> Private
        </label>
      </div>
      <div style="margin-bottom:12px;display:flex;gap:12px">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
          <input type="checkbox" id="ghNewRepoReadme" checked> Add README
        </label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
          <input type="checkbox" id="ghNewRepoGitignore"> .gitignore
        </label>
      </div>
      <div id="ghGitignoreSelect" style="display:none;margin-bottom:12px">
        <select id="ghGitignoreTemplate" class="input" style="width:100%">
          <option value="">None</option>
          <option value="Python">Python</option>
          <option value="Node">Node</option>
          <option value="Java">Java</option>
          <option value="Go">Go</option>
          <option value="Rust">Rust</option>
          <option value="C++">C++</option>
          <option value="Ruby">Ruby</option>
        </select>
      </div>
      <button class="btn-primary" id="ghCreateBtn" style="width:100%">Create & Clone</button>
      <div id="ghCreateStatus" style="margin-top:8px"></div>
    </div>
  `;
  el("ghBackToMain").onclick = showMainView;
  el("ghCreateBtn").onclick = doCreateRepo;
  el("ghNewRepoGitignore").onchange = (e) => {
    el("ghGitignoreSelect").style.display = e.target.checked ? "block" : "none";
  };
  el("ghNewRepoName").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doCreateRepo();
  });
  setTimeout(() => el("ghNewRepoName")?.focus(), 100);
}

async function doCreateRepo() {
  const name = el("ghNewRepoName")?.value.trim();
  if (!name) {
    el("ghCreateStatus").innerHTML = `<div class="gh-error">Please enter a repository name</div>`;
    return;
  }

  const isPrivate = document.querySelector('input[name="ghVisibility"][value="private"]')?.checked || false;
  const description = el("ghNewRepoDesc")?.value.trim() || "";
  const autoInit = el("ghNewRepoReadme")?.checked ?? true;
  const gitignore = el("ghNewRepoGitignore")?.checked ? (el("ghGitignoreTemplate")?.value || "") : "";

  const btn = el("ghCreateBtn");
  btn.disabled = true;
  btn.textContent = "Creating...";
  el("ghCreateStatus").innerHTML = "";

  try {
    const res = await fetch("/api/github/repos/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        private: isPrivate,
        description,
        auto_init: autoInit,
        gitignore_template: gitignore,
      }),
    });
    const data = await res.json();

    if (data.error) {
      el("ghCreateStatus").innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      btn.disabled = false;
      btn.textContent = "Create & Clone";
      return;
    }

    el("ghCreateStatus").innerHTML = `<div class="gh-success">Repository created!</div>`;

    // Auto-proceed to clone dialog
    setTimeout(() => showCloneDialog(data.full_name), 800);
  } catch (err) {
    el("ghCreateStatus").innerHTML = `<div class="gh-error">Failed: ${esc(String(err))}</div>`;
    btn.disabled = false;
    btn.textContent = "Create & Clone";
  }
}

// ─── Review External Repo ───────────────────────────────────────

function showReviewRepoDialog() {
  const body = el("githubModalBody");
  body.innerHTML = `
    <div>
      <button class="btn-link" id="ghBackToMain" style="margin-bottom:12px;background:none;border:none;cursor:pointer;color:var(--cyan)">← Back</button>
      <h3 style="margin:0 0 12px">Review GitHub Repository</h3>
      <p style="margin:0 0 12px;color:var(--text-dim);font-size:13px">Enter any public repository to review (owner/repo or URL):</p>
      <input type="text" id="ghReviewInput" class="input" placeholder="owner/repo or https://github.com/..." style="width:100%;margin-bottom:12px">
      <button class="btn-sm btn-primary" id="ghReviewLookupBtn" style="width:100%">Look Up</button>
      <div id="ghReviewInfo" style="margin-top:12px"></div>
    </div>
  `;
  el("ghBackToMain").onclick = showMainView;
  el("ghReviewLookupBtn").onclick = lookupReviewRepo;
  el("ghReviewInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") lookupReviewRepo();
  });
  setTimeout(() => el("ghReviewInput")?.focus(), 100);
}

async function lookupReviewRepo() {
  let input = el("ghReviewInput")?.value.trim();
  if (!input) return;

  const urlMatch = input.match(/github\.com\/([^/]+\/[^/]+)/);
  if (urlMatch) input = urlMatch[1].replace(/\.git$/, "");

  const info = el("ghReviewInfo");
  info.innerHTML = `<div class="gh-loading">Looking up ${esc(input)}...</div>`;

  try {
    const res = await fetch(`/api/github/repos/info?full_name=${encodeURIComponent(input)}`);
    const data = await res.json();

    if (data.error) {
      info.innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      return;
    }

    const langBadge = data.language ? `<span class="gh-lang">${esc(data.language)}</span>` : "";
    const privBadge = data.private ? `<span class="gh-private">private</span>` : "";
    const desc = data.description ? `<p style="margin:4px 0 0;color:var(--text-dim);font-size:13px">${esc(data.description)}</p>` : "";

    info.innerHTML = `
      <div style="padding:12px;background:var(--surface);border-radius:8px;margin-bottom:12px">
        <div class="gh-repo-name">${esc(data.full_name)} ${privBadge} ${langBadge}</div>
        ${desc}
        <div style="margin-top:8px;font-size:12px;color:var(--text-dim)">Owner: ${esc(data.owner)} · Branch: ${esc(data.default_branch)}</div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn-sm btn-primary" id="ghReviewCloneBtn" style="flex:1">Clone & Review</button>
        <button class="btn-sm" id="ghReviewOpenBtn" style="flex:1">Clone & Open as Project</button>
      </div>
    `;

    el("ghReviewCloneBtn").onclick = () => {
      const tmpDir = `/tmp/omads-review/${data.full_name.replace("/", "-")}`;
      doCloneAndReview(data.full_name, tmpDir);
    };
    el("ghReviewOpenBtn").onclick = () => showCloneDialog(data.full_name);
  } catch (err) {
    info.innerHTML = `<div class="gh-error">Failed: ${esc(String(err))}</div>`;
  }
}

async function doCloneAndReview(fullName, targetDir) {
  const info = el("ghReviewInfo");
  info.innerHTML = `<div class="gh-loading">Cloning ${esc(fullName)} for review...</div>`;

  try {
    const res = await fetch("/api/github/clone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: fullName, target_dir: targetDir }),
    });
    const data = await res.json();

    if (data.error) {
      info.innerHTML = `<div class="gh-error">${esc(data.error)}</div>`;
      return;
    }

    info.innerHTML = `<div class="gh-success">Cloned! Starting review...</div>`;
    await loadProjects();
    setTimeout(() => closeGitHub(), 1500);
  } catch (err) {
    info.innerHTML = `<div class="gh-error">Clone failed: ${esc(String(err))}</div>`;
  }
}

// ─── Clone Dialog ────────────────────────────────────────────────

function showCloneDialog(fullName) {
  const repoName = fullName.split("/").pop();
  const defaultDir = `${appState.npFpPath || "~"}/${repoName}`;

  const body = el("githubModalBody");
  body.innerHTML = `
    <div class="gh-clone-dialog">
      <button class="btn-link" id="ghBackToMain" style="margin-bottom:12px;background:none;border:none;cursor:pointer;color:var(--cyan)">← Back</button>
      <h3 style="margin:0 0 12px">Clone ${esc(fullName)}</h3>
      <label style="font-size:13px;color:var(--text-dim);display:block;margin-bottom:4px">Target directory:</label>
      <input type="text" id="ghCloneDir" class="input" value="${esc(defaultDir)}" style="width:100%;margin-bottom:12px">
      <button class="btn-sm btn-primary" id="ghCloneStart" style="width:100%">Clone & Open in OMADS</button>
      <div id="ghCloneStatus" style="margin-top:12px"></div>
    </div>
  `;
  el("ghBackToMain").onclick = showMainView;
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

let _currentGitRepoPath = null;

export function openGitOps(projectPath, projectName) {
  _currentGitRepoPath = projectPath;
  el("gitOpsModal").classList.add("open");
  el("gitOpsTitle").textContent = `Git: ${projectName}`;
  loadGitStatus(projectPath);
}

export function closeGitOps() {
  el("gitOpsModal").classList.remove("open");
  _currentGitRepoPath = null;
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

    const branch = data.branch || "main";
    const statusLines = (data.status_lines || []).map((l) => esc(l)).join("\n");
    const isClean = data.clean;
    const hasCommits = branch !== "(no commits)";

    body.innerHTML = `
      <div style="margin-bottom:12px;display:flex;align-items:center;gap:8px">
        <span style="font-size:13px;color:var(--text-dim)">Branch:</span>
        <strong style="font-size:13px">${esc(branch)}</strong>
      </div>
      ${isClean ? `<div class="gh-success">Working tree is clean</div>` : ""}
      ${!isClean ? `<pre class="gh-status-pre">${statusLines}</pre>` : ""}
      <div class="gh-ops-row">
        ${!isClean ? `
          <div class="gh-commit-row" style="display:flex;gap:8px;margin-bottom:8px">
            <input type="text" id="gitCommitMsg" class="input" placeholder="Commit message..." style="flex:1">
            <button class="btn-sm btn-primary" id="gitCommitBtn">Commit</button>
          </div>
        ` : ""}
        <div style="display:flex;gap:8px">
          <button class="btn-sm" id="gitPullBtn" ${!hasCommits ? "disabled title=\"No commits yet\"" : ""}>Pull</button>
          <button class="btn-sm btn-primary" id="gitPushBtn" ${!hasCommits ? "disabled title=\"Create a commit first\"" : ""}>Push</button>
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

    // Auto-refresh status after any operation
    setTimeout(() => loadGitStatus(repoPath), 800);
  } catch (err) {
    status.innerHTML = `<div class="gh-error">${esc(String(err))}</div>`;
  }
}

// ─── WebSocket event handlers ────────────────────────────────────

export function handleGitHubWsEvent(msg) {
  if (msg.type === "github_connected") {
    ghAuth = { authenticated: true, username: msg.username || "", has_client_id: ghAuth.has_client_id };
    renderGitHubBadge();
  } else if (msg.type === "github_disconnected") {
    ghAuth = { authenticated: false, username: "", has_client_id: ghAuth.has_client_id };
    renderGitHubBadge();
  }
}
