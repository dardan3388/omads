import { appState, el, esc, formatMsg, shortPath, agentClass, scrollDown } from "./shared.js";
import {
  applyAutoReviewEnabled,
  applyBuilderAgent,
  applyReviewPipeline,
  applyTheme,
} from "./settings_ui.js";

export function lock() {
  appState.busy = true;
  el("btnSend").style.display = "none";
  el("btnStop").style.display = "";
  const reviewBtn = el("btnReview");
  if (reviewBtn) reviewBtn.disabled = true;
}

export function unlock() {
  appState.busy = false;
  el("btnStop").style.display = "none";
  el("btnSend").style.display = "";
  const reviewBtn = el("btnReview");
  if (reviewBtn) reviewBtn.disabled = false;
}

export function addAgent(agent, text) {
  if (!text || !text.trim()) return;
  const stream = el("stream");
  const div = document.createElement("div");
  div.className = "msg msg-agent";
  const cls = agentClass(agent);
  div.innerHTML = `<span class="agent-name ${cls}">${esc(agent)}</span>${formatMsg(text)}`;
  stream.appendChild(div);
  scrollDown();
}

export function addToolBlock(agent, tool, desc, detail) {
  if (!tool && !desc) return;
  const stream = el("stream");
  const div = document.createElement("div");
  div.className = "tool-block";

  const icons = {
    Read: "📖",
    Edit: "✏️",
    Write: "📝",
    Bash: "⚡",
    Glob: "🔍",
    Grep: "🔎",
    Agent: "🤖",
    Skill: "🎯",
    TodoWrite: "📋",
    WebFetch: "🌐",
    WebSearch: "🔍",
    NotebookEdit: "📓",
    ToolSearch: "🔧",
  };
  const icon = icons[tool] || "🔧";
  const displayDesc = desc || tool || "...";

  div.innerHTML = `
    <div class="tool-header">
      <span class="tool-icon">${icon}</span>
      <span class="tool-name">${esc(tool)}</span>
      <span class="tool-desc">${esc(displayDesc)}</span>
    </div>
  `;
  stream.appendChild(div);
  scrollDown();
}

export function addStatus(agent, text) {
  const stream = el("stream");
  const div = document.createElement("div");
  div.className = "msg msg-agent";
  const cls = agentClass(agent);
  div.innerHTML = `<span class="agent-name ${cls}">${esc(agent)}</span><em style="color:var(--text-dim)">${esc(text)}</em>`;
  stream.appendChild(div);
  scrollDown();
}

export function addSystem(text) {
  if (!text || !text.trim()) return;
  const div = document.createElement("div");
  div.className = "msg-system";
  div.textContent = text;
  el("stream").appendChild(div);
  scrollDown();
}

function buildAcceptSubtitle(filesChanged, findings) {
  const parts = [];
  if (filesChanged > 0) parts.push(`${filesChanged} file${filesChanged > 1 ? "s" : ""} changed`);
  if (findings.length === 0) parts.push("All checks passed");
  else parts.push(`${findings.length} finding${findings.length > 1 ? "s" : ""}`);
  return parts.join(" · ");
}

function buildRejectSubtitle(findings, conformance, summary) {
  const issues = findings.filter((finding) => finding.severity === "high" || finding.severity === "medium");
  const total = issues.length + conformance.length;
  if (total > 0) return `${total} issue${total > 1 ? "s" : ""} found`;
  if (summary) return summary.substring(0, 80);
  return "The task could not be completed";
}

function buildEscalateSubtitle(findings) {
  const security = findings.filter((finding) => finding.type === "security_finding");
  const scope = findings.filter((finding) => finding.type === "scope_violation");
  if (security.length > 0) return "Security-sensitive change · review recommended";
  if (scope.length > 0) return "Change outside the expected scope";
  return "Result requires manual review";
}

function resultAction(action, param) {
  if (action === "new") {
    el("input").value = "";
    el("input").focus();
  } else if (action === "revise") {
    el("input").value = param || "";
    el("input").focus();
    el("input").style.height = "auto";
    el("input").style.height = `${Math.min(el("input").scrollHeight, 120)}px`;
  } else if (action === "approve") {
    if (appState.ws) appState.ws.send(JSON.stringify({ type: "escalation_response", task_id: param, action: "approve" }));
    addSystem("Approved — changes will be accepted.");
  } else if (action === "reject_escalation") {
    if (appState.ws) appState.ws.send(JSON.stringify({ type: "escalation_response", task_id: param, action: "reject" }));
    addSystem("Rejected — changes will be discarded.");
  }
}

function buildResultCard(msg) {
  const decision = msg.decision || "reject";
  const score = msg.score || 0;
  const intent = msg.intent || "";
  const summary = msg.summary || "";
  const filesChanged = msg.files_changed || 0;
  const findings = msg.findings || [];
  const conformance = msg.conformance_issues || [];

  const card = document.createElement("div");
  card.className = `result-card ${decision}`;

  const config = {
    accept: {
      icon: "✓",
      title: "Completed successfully",
      subtitle: buildAcceptSubtitle(filesChanged, findings),
      detailLabel: "Show details",
    },
    reject: {
      icon: "✗",
      title: "Not completed",
      subtitle: buildRejectSubtitle(findings, conformance, summary),
      detailLabel: "Show reasons",
    },
    escalate: {
      icon: "⚠",
      title: "Your decision is required",
      subtitle: buildEscalateSubtitle(findings),
      detailLabel: "Show reasons",
    },
  };
  const current = config[decision] || config.reject;

  const details = [];
  for (const finding of findings) {
    const dotColor = finding.severity === "high" ? "#ef4444" : finding.severity === "medium" ? "#f59e0b" : "#6b7280";
    details.push({ text: finding.text, color: dotColor });
  }
  for (const issue of conformance) {
    details.push({ text: issue, color: "#f59e0b" });
  }

  const detailId = `rc-detail-${Math.random().toString(36).slice(2, 8)}`;

  let html = `
    <div class="rc-header">
      <span class="rc-icon">${current.icon}</span>
      <div>
        <div class="rc-title">${esc(current.title)}</div>
        <div class="rc-subtitle">${esc(current.subtitle)}</div>
      </div>
    </div>
    <div class="rc-body">`;

  if (details.length > 0) {
    html += `<button class="rc-toggle" onclick="document.getElementById('${detailId}').classList.toggle('open'); this.textContent = this.textContent.startsWith('▶') ? '▼ ${esc(current.detailLabel)}' : '▶ ${esc(current.detailLabel)}'">▶ ${esc(current.detailLabel)}</button>`;
    html += `<div class="rc-details" id="${detailId}">`;
    for (const detail of details) {
      html += `<div class="rc-detail-item"><span class="rc-detail-dot" style="background:${detail.color}"></span><span>${esc(detail.text)}</span></div>`;
    }
    html += "</div>";
  } else if (summary && decision !== "accept") {
    html += `<div style="font-size:12px;opacity:0.7;margin-top:4px">${esc(summary)}</div>`;
  }

  html += `</div><div class="rc-actions">`;
  if (decision === "accept") {
    html += '<button class="rc-btn primary" data-action="new">New task</button>';
  } else if (decision === "reject") {
    html += `<button class="rc-btn primary" data-action="revise" data-param="${esc(intent)}">Revise &amp; retry</button>`;
  } else if (decision === "escalate") {
    html += `<button class="rc-btn primary" data-action="approve" data-param="${esc(msg.task_id)}">Approve</button>`;
    html += `<button class="rc-btn danger" data-action="reject_escalation" data-param="${esc(msg.task_id)}">Reject</button>`;
    html += `<button class="rc-btn" data-action="revise" data-param="${esc(intent)}">Request revision</button>`;
  }

  html += "</div>";
  card.innerHTML = html;
  for (const btn of card.querySelectorAll(".rc-btn[data-action]")) {
    btn.addEventListener("click", () => {
      resultAction(btn.dataset.action, btn.dataset.param);
    });
  }
  return card;
}

export function renderChatEvent(msg, { historical = false } = {}) {
  const stream = el("stream");
  switch (msg.type) {
    case "chat_response":
      addAgent(msg.agent || "OMADS", msg.text);
      if (!historical) unlock();
      break;
    case "task_start":
      addSystem("Task started");
      break;
    case "stream_text":
      if (msg.text && msg.text.trim()) addAgent(msg.agent, msg.text);
      break;
    case "stream_tool":
      if (msg.tool) addToolBlock(msg.agent, msg.tool, msg.description || "", msg.detail || "");
      break;
    case "agent_status":
      addStatus(msg.agent, msg.status);
      break;
    case "agent_activity":
      if (msg.activity === "finding") {
        addAgent(msg.agent, `Finding: ${msg.text}`);
      } else if (msg.activity === "analysis") {
        addToolBlock(msg.agent, "Analysis", "Code review result", msg.text);
      } else {
        addAgent(msg.agent, msg.text);
      }
      break;
    case "task_complete": {
      const card = buildResultCard(msg);
      stream.appendChild(card);
      scrollDown();
      if (!historical) unlock();
      break;
    }
    case "task_stopped":
      addSystem(msg.text);
      if (!historical) unlock();
      break;
    case "error":
      addSystem(msg.text || "Error");
      if (!historical) unlock();
      break;
    case "task_error":
      addAgent("System", msg.text);
      if (!historical) unlock();
      break;
    case "settings_updated":
      if (!historical && msg.settings) {
        if (msg.settings.target_repo) el("repoBadge").textContent = shortPath(msg.settings.target_repo);
        if (msg.settings.ui_theme) applyTheme(msg.settings.ui_theme);
        if (msg.settings.builder_agent) applyBuilderAgent(msg.settings.builder_agent);
        if (msg.settings.review_first_reviewer || msg.settings.review_second_reviewer) {
          applyReviewPipeline(
            msg.settings.review_first_reviewer || appState.reviewFirstReviewer,
            msg.settings.review_second_reviewer || appState.reviewSecondReviewer,
          );
        }
        if (msg.settings.auto_review !== undefined) applyAutoReviewEnabled(msg.settings.auto_review);
      }
      break;
    case "unlock":
      if (!historical) unlock();
      break;
    case "review_fixes_available":
      if (historical) {
        addSystem(msg.text || "Fix suggestions were available for this review.");
      } else {
        const fixDiv = document.createElement("div");
        fixDiv.className = "msg msg-system";
        fixDiv.innerHTML = `
          <div style="padding:12px;background:var(--bg);border:1px solid var(--blue);border-radius:8px;margin:8px 0;">
            <div style="margin-bottom:10px;color:var(--text);">${formatMsg(msg.text)}</div>
            <div style="display:flex;gap:8px;"></div>
          </div>`;
        const actions = fixDiv.querySelector("div div:last-child");
        const applyBtn = document.createElement("button");
        applyBtn.textContent = "Apply fixes";
        applyBtn.style.cssText = "background:var(--green);color:white;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:bold;";
        applyBtn.addEventListener("click", () => window.applyFixes?.());
        const dismissBtn = document.createElement("button");
        dismissBtn.textContent = "No thanks";
        dismissBtn.style.cssText = "background:var(--bg2);color:var(--muted);border:1px solid var(--border);padding:8px 16px;border-radius:6px;cursor:pointer;";
        dismissBtn.addEventListener("click", () => fixDiv.remove());
        actions.appendChild(applyBtn);
        actions.appendChild(dismissBtn);
        stream.appendChild(fixDiv);
        scrollDown();
      }
      break;
    case "system":
      el("repoBadge").textContent = shortPath(msg.text);
      break;
    case "user_input": {
      const div = document.createElement("div");
      div.className = "msg msg-user";
      if (historical && msg.timestamp_display) {
        div.innerHTML = `<span style="font-size:10px;color:var(--text-dim);display:block;margin-bottom:2px;">${esc(msg.timestamp_display)}</span>${formatMsg(msg.text || "")}`;
      } else {
        div.innerHTML = formatMsg(msg.text || "");
      }
      stream.appendChild(div);
      scrollDown();
      break;
    }
  }
}

export function toggleLiveLog() {
  const panel = el("livelogPanel");
  const btn = el("btnLogToggle");
  panel.classList.toggle("open");
  btn.classList.toggle("active");
}

export function switchLogTab(tab) {
  appState.logFilter = tab;
  document.querySelectorAll(".livelog-tab").forEach((node) => node.classList.remove("active"));
  if (tab === "all") el("logTabAll").classList.add("active");
  else if (tab === "claude") el("logTabClaude").classList.add("active");
  else el("logTabCodex").classList.add("active");

  const lines = el("livelogContent").querySelectorAll(".log-line");
  lines.forEach((line) => {
    if (tab === "all") {
      line.style.display = "";
      return;
    }
    const agent = line.dataset.agent || "";
    if (tab === "claude" && agent === "claude") line.style.display = "";
    else if (tab === "codex" && agent === "codex") line.style.display = "";
    else line.style.display = "none";
  });
}

export function logEvent(msg, { historical = false } = {}) {
  const logTypes = [
    "user_input",
    "task_start",
    "stream_text",
    "stream_tool",
    "agent_status",
    "agent_activity",
    "task_complete",
    "task_stopped",
    "task_error",
    "chat_response",
    "stream_thinking",
    "stream_result",
    "review_fixes_available",
  ];
  if (!logTypes.includes(msg.type)) return;

  const container = el("livelogContent");
  const empty = container.querySelector(".livelog-empty");
  if (empty) empty.remove();

  const now = new Date();
  const fallbackStamp = `${now.toLocaleDateString("en-US", { day: "2-digit", month: "2-digit" })} ${now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  const timestampText = msg.timestamp_display || msg.timestamp || fallbackStamp;

  const agent = msg.agent || "System";
  const agentCls = agentClass(agent);

  let typeLabel = msg.type.replace("stream_", "").replace("agent_", "").replace("task_", "");
  let msgText = "";
  let msgCls = "text";

  switch (msg.type) {
    case "task_start":
      msgText = `Task started: ${msg.intent || msg.task_id || ""}`;
      break;
    case "stream_text":
      msgText = msg.text || "";
      typeLabel = "text";
      break;
    case "stream_tool":
      msgText = `${msg.tool || "?"}: ${msg.description || ""}`;
      if (msg.detail) msgText += `\n${msg.detail}`;
      msgCls = "tool";
      typeLabel = "tool";
      break;
    case "agent_status":
      msgText = msg.status || "";
      typeLabel = "status";
      break;
    case "agent_activity":
      msgText = `[${msg.activity || "?"}] ${msg.text || ""}`;
      typeLabel = "activity";
      break;
    case "task_complete":
      msgText = `Decision: ${msg.decision} (Score: ${((msg.score || 0) * 100).toFixed(0)}%)`;
      msgCls = msg.decision === "accept" ? "text" : "error";
      typeLabel = "result";
      break;
    case "task_stopped":
      msgText = msg.text || "Stopped";
      msgCls = "error";
      typeLabel = "stop";
      break;
    case "task_error":
      msgText = msg.text || "Error";
      msgCls = "error";
      typeLabel = "error";
      break;
    case "chat_response":
      msgText = msg.text || "";
      typeLabel = "chat";
      break;
    case "user_input":
      msgText = msg.text || "";
      typeLabel = "input";
      break;
    case "stream_thinking":
      msgText = msg.text || "";
      msgCls = "thinking";
      typeLabel = "thinking";
      break;
    case "stream_result":
      msgText = msg.text || "";
      msgCls = msg.is_error ? "error" : "result";
      typeLabel = msg.is_error ? "error" : "result";
      break;
    case "review_fixes_available":
      msgText = msg.text || "";
      typeLabel = "review";
      break;
  }

  if (!msgText.trim()) return;

  const div = document.createElement("div");
  div.className = "log-line";
  div.dataset.agent = agentCls;
  div.innerHTML = `<span class="log-time">${esc(timestampText)}</span><span class="log-agent ${agentCls}">${esc(agent)}</span><span class="log-type">[${typeLabel}]</span><span class="log-msg ${msgCls}">${esc(msgText)}</span>`;

  if (appState.logFilter !== "all") {
    if (appState.logFilter === "claude" && agentCls !== "claude") div.style.display = "none";
    else if (appState.logFilter === "codex" && agentCls !== "codex") div.style.display = "none";
  }

  container.appendChild(div);
  if (!historical) container.scrollTop = container.scrollHeight;
}
