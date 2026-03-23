import { appState, el, esc, formatMsg, shortPath, agentClass, truncate, scrollDown } from "./shared.js";
import { addSystem, logEvent, renderChatEvent } from "./chat_ui.js";

export async function loadProjects() {
  try {
    const res = await fetch("/api/projects");
    appState.projects = await res.json();
    renderProjects();

    const settings = await (await fetch("/api/settings")).json();
    const repo = settings.target_repo || "";
    const active = appState.projects.find((project) => project.path === repo);
    if (active) {
      appState.activeProjectId = active.id;
      renderProjects();
      await loadHistoryIntoChat(active.id, active.name);
      await loadProjectLogs(active.id);
    }
  } catch (error) {
    console.error("Error while loading projects:", error);
  }
}

export function renderProjects() {
  const list = el("projectList");
  if (appState.projects.length === 0) {
    list.innerHTML = '<div class="history-empty">No project created yet.<br>Click "+ New" to get started.</div>';
    return;
  }

  const sorted = [...appState.projects].sort((a, b) => (b.last_used || "").localeCompare(a.last_used || ""));
  list.innerHTML = "";
  for (const project of sorted) {
    const div = document.createElement("div");
    div.className = `project-item${project.id === appState.activeProjectId ? " active" : ""}`;
    const parts = project.path.split("/");
    const shortP = parts.slice(-2).join("/");
    const lastUsed = project.last_used ? project.last_used.split(" ")[0] : "";
    div.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:start;">
        <div style="flex:1;min-width:0;">
          <div class="project-name">${esc(project.name)}</div>
          <div class="project-path" title="${esc(project.path)}">${esc(shortP)}</div>
          <div class="project-meta">Last used: ${lastUsed}</div>
        </div>
        <button class="btn-delete-project" data-id="${esc(project.id)}" data-name="${esc(project.name)}" title="Delete project" style="background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:14px;padding:2px 4px;opacity:0.4;transition:opacity .2s;" onmouseenter="this.style.opacity='1';this.style.color='#e74c3c'" onmouseleave="this.style.opacity='0.4';this.style.color='var(--text-dim)'">&times;</button>
      </div>
    `;
    const deleteBtn = div.querySelector(".btn-delete-project");
    if (deleteBtn) {
      deleteBtn.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteProject(deleteBtn.dataset.id, deleteBtn.dataset.name);
      });
    }
    div.onclick = () => switchProject(project.id);
    list.appendChild(div);
  }
}

export async function switchProject(projectId) {
  try {
    const res = await fetch("/api/projects/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: projectId }),
    });
    const data = await res.json();
    if (data.ok) {
      appState.activeProjectId = projectId;
      el("repoBadge").textContent = shortPath(data.project.path);
      renderProjects();
      el("stream").innerHTML = "";
      await loadHistoryIntoChat(projectId, data.project.name);
      await loadProjectLogs(projectId);
    }
  } catch {}
}

export async function deleteProject(projectId, projectName) {
  if (!confirm(`Delete project "${projectName}"?`)) return;
  try {
    await fetch(`/api/projects/${projectId}`, { method: "DELETE" });
    if (appState.activeProjectId === projectId) appState.activeProjectId = null;
    await loadProjects();
  } catch {}
}

export function openNewProject() {
  el("newProjectModal").classList.add("open");
  el("npName").value = "";
  el("npPath").value = "";
  browseToNp("~");
}

export function closeNewProject() {
  el("newProjectModal").classList.remove("open");
}

export async function browseToNp(path) {
  try {
    const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (data.error && path !== "~") {
      browseToNp("~");
      return;
    }
    appState.npFpPath = data.path;
    el("npFpPath").textContent = data.path;
    el("npPath").value = data.path;
    const list = el("npFpList");
    list.innerHTML = "";
    if (data.parent) {
      const li = document.createElement("li");
      li.className = "fp-item fp-up";
      li.innerHTML = "<span>..</span><span>Up</span>";
      li.onclick = () => browseToNp(data.parent);
      list.appendChild(li);
    }
    for (const dir of data.dirs) {
      const li = document.createElement("li");
      li.className = "fp-item";
      li.innerHTML = `<span>📁</span><span>${esc(dir.name)}</span>`;
      li.onclick = () => browseToNp(dir.path);
      list.appendChild(li);
    }
  } catch {}
}

export function pickNewProjectFolder() {
  el("npPath").value = appState.npFpPath;
  const btn = el("npFolderPicker").querySelector(".btn-pick");
  btn.textContent = "✓ Selected";
  setTimeout(() => {
    btn.textContent = "Select";
  }, 1200);
  if (!el("npName").value.trim()) {
    const parts = appState.npFpPath.split("/");
    el("npName").value = parts[parts.length - 1] || "";
  }
}

export async function createProject() {
  const name = el("npName").value.trim();
  const path = el("npPath").value.trim();
  if (!name) {
    el("npName").focus();
    return;
  }
  if (!path) return;

  const res = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, path }),
  });
  const data = await res.json();
  if (data.error) {
    alert(data.error);
    return;
  }
  closeNewProject();
  appState.activeProjectId = data.project.id;
  el("repoBadge").textContent = shortPath(data.project.path);
  await loadProjects();
  el("stream").innerHTML = `<div class="msg-system">New project: ${esc(name)}. What should I build?</div>`;
}

export async function loadHistoryIntoChat(projectId, projectName) {
  const stream = el("stream");
  stream.innerHTML = `<div class="msg-system">Project: ${esc(projectName)}</div>`;

  try {
    const timelineRes = await fetch(`/api/projects/${projectId}/timeline`);
    const timelineEntries = await timelineRes.json();

    if (Array.isArray(timelineEntries) && timelineEntries.length > 0) {
      const divider = document.createElement("div");
      divider.className = "msg-history-divider";
      divider.textContent = `History (${timelineEntries.length} events)`;
      stream.appendChild(divider);

      let lastDate = "";
      for (const entry of timelineEntries) {
        const time = entry.timestamp_display || entry.timestamp || "";
        const dateOnly = time.split(" ")[0] || time.split("T")[0] || "";
        if (dateOnly && dateOnly !== lastDate) {
          const dt = document.createElement("div");
          dt.className = "msg-timestamp";
          dt.textContent = dateOnly;
          stream.appendChild(dt);
          lastDate = dateOnly;
        }
        renderChatEvent(entry, { historical: true });
      }

      const now = document.createElement("div");
      now.className = "msg-history-divider";
      now.textContent = "Now";
      stream.appendChild(now);
      scrollDown();
      return;
    }

    const res = await fetch(`/api/projects/${projectId}/history`);
    const entries = await res.json();

    if (!entries || entries.length === 0) {
      addSystem("No history yet. Send your first task!");
      return;
    }

    const divider = document.createElement("div");
    divider.className = "msg-history-divider";
    divider.textContent = `History (${entries.length} entries)`;
    stream.appendChild(divider);

    let lastDate = "";
    for (const entry of entries) {
      const time = entry.timestamp || "";
      const dateOnly = time.split(" ")[0] || "";
      if (dateOnly && dateOnly !== lastDate) {
        const dt = document.createElement("div");
        dt.className = "msg-timestamp";
        dt.textContent = dateOnly;
        stream.appendChild(dt);
        lastDate = dateOnly;
      }

      if (entry.type === "user_input") {
        const div = document.createElement("div");
        div.className = "msg msg-user";
        div.innerHTML = `<span style="font-size:10px;color:var(--text-dim);display:block;margin-bottom:2px;">${esc(time)}</span>${formatMsg(entry.text)}`;
        stream.appendChild(div);
      } else if (entry.type === "task_result") {
        const decision = entry.decision || "reject";
        const score = entry.score ? `${(entry.score * 100).toFixed(0)}%` : "";
        const label = decision === "accept" ? "Done" : decision === "escalate" ? "Needs review" : "Failed";
        const div = document.createElement("div");
        div.className = `result-banner ${decision}`;
        div.innerHTML = `<span style="font-size:10px;color:inherit;opacity:0.7;display:block;margin-bottom:2px;">${esc(time)}</span>${label} ${score} — ${esc(truncate(entry.intent, 60))}`;
        if (entry.files_changed) {
          div.innerHTML += `<span style="font-size:11px;opacity:0.7;margin-left:8px;">(${entry.files_changed} file${entry.files_changed > 1 ? "s" : ""})</span>`;
        }
        stream.appendChild(div);
      } else if (entry.type === "claude_response" || entry.type === "builder_response") {
        const div = document.createElement("div");
        div.className = "msg msg-agent";
        let meta = "";
        if (entry.files_changed) meta += `${entry.files_changed} file${entry.files_changed > 1 ? "s" : ""} changed`;
        if (entry.duration_s) meta += `${meta ? " · " : ""}${entry.duration_s}s`;
        const historyAgent = entry.agent || "Claude Code";
        const historyClass = agentClass(historyAgent);
        div.innerHTML = `<span class="agent-name ${historyClass}">${esc(historyAgent)}</span>${formatMsg(entry.text)}${meta ? `<span style="font-size:10px;color:var(--text-dim);display:block;margin-top:4px;">${meta}</span>` : ""}`;
        stream.appendChild(div);
      } else if (entry.type === "chat") {
        const question = document.createElement("div");
        question.className = "msg msg-user";
        question.innerHTML = `<span style="font-size:10px;color:var(--text-dim);display:block;margin-bottom:2px;">${esc(time)}</span>${formatMsg(entry.question)}`;
        stream.appendChild(question);
        if (entry.answer) {
          const answer = document.createElement("div");
          answer.className = "msg msg-agent";
          answer.innerHTML = `<span class="agent-name claude">${esc(entry.model || "Chat")}</span>${formatMsg(entry.answer)}`;
          stream.appendChild(answer);
        }
      }
    }

    const now = document.createElement("div");
    now.className = "msg-history-divider";
    now.textContent = "Now";
    stream.appendChild(now);
    scrollDown();
  } catch (error) {
    console.error("Error while loading history:", error);
  }
}

export async function loadProjectLogs(projectId) {
  const container = el("livelogContent");
  container.innerHTML = '<div class="livelog-empty">Waiting for activity...</div>';
  try {
    let res = await fetch(`/api/projects/${projectId}/timeline`);
    const entries = await res.json();
    if (!entries || entries.length === 0) {
      res = await fetch(`/api/projects/${projectId}/logs`);
      const legacyEntries = await res.json();
      if (!legacyEntries || legacyEntries.length === 0) return;
      container.innerHTML = "";
      for (const msg of legacyEntries) {
        logEvent(msg, { historical: true });
      }
      container.scrollTop = container.scrollHeight;
      return;
    }
    container.innerHTML = "";
    for (const msg of entries) {
      logEvent(msg, { historical: true });
    }
    container.scrollTop = container.scrollHeight;
  } catch (error) {
    console.error("Error while loading logs:", error);
  }
}
