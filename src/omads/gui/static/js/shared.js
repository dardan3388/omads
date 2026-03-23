export const appState = {
  ws: null,
  busy: false,
  fpPath: "~",
  npFpPath: "~",
  logFilter: "all",
  uiTheme: "dark",
  builderAgent: "claude",
  reviewFirstReviewer: "claude",
  reviewSecondReviewer: "codex",
  reconnectDelay: 2000,
  projects: [],
  activeProjectId: null,
  savedSettings: {},
  timelineEntries: [],
  timelineHasMore: false,
  timelineNextBefore: null,
  timelineTotalCount: 0,
  currentProjectName: "",
  scrollRAF: 0,
};

export function el(id) {
  return document.getElementById(id);
}

export function scrollDown() {
  if (appState.scrollRAF) return;
  appState.scrollRAF = requestAnimationFrame(() => {
    const stream = el("stream");
    if (stream) {
      stream.scrollTop = stream.scrollHeight;
    }
    appState.scrollRAF = 0;
  });
}

export function esc(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

export function formatMsg(text) {
  let html = esc(text);
  html = html.replace(
    /```(\w*)\n([\s\S]*?)```/g,
    '<pre style="background:var(--bg);padding:8px 12px;border-radius:6px;overflow-x:auto;margin:6px 0;font-size:13px;border:1px solid var(--border)"><code>$2</code></pre>',
  );
  html = html.replace(
    /`([^`]+)`/g,
    '<code style="background:var(--bg);padding:1px 5px;border-radius:3px;font-size:13px">$1</code>',
  );
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  html = html.replace(/^### (.+)$/gm, '<strong style="font-size:14px">$1</strong>');
  html = html.replace(/^## (.+)$/gm, '<strong style="font-size:15px">$1</strong>');
  html = html.replace(/^# (.+)$/gm, '<strong style="font-size:16px">$1</strong>');
  html = html.replace(/^[\-\*] (.+)$/gm, "  • $1");
  html = html.replace(/^(\d+)\. (.+)$/gm, "  $1. $2");
  html = html.replace(/\n/g, "<br>");
  html = html.replace(/<\/pre><br>/g, "</pre>");
  html = html.replace(/<br><pre/g, "<pre");
  return html;
}

export function shortPath(path) {
  if (!path) return "?";
  const parts = path.replace(/\/$/, "").split("/");
  return parts.slice(-2).join("/");
}

export function agentClass(agent) {
  const lower = agent.toLowerCase();
  return lower.includes("claude")
    ? "claude"
    : lower.includes("codex") || lower.includes("breaker")
      ? "codex"
      : lower === "director"
        ? "system"
        : lower === "omads"
          ? "omads"
          : "system";
}

export function truncate(text, maxLength) {
  return text && text.length > maxLength ? `${text.substring(0, maxLength)}...` : (text || "");
}

export function fmtTimestamp(unixTs) {
  if (!unixTs) return "—";
  return new Date(unixTs * 1000).toLocaleString("en-US", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
