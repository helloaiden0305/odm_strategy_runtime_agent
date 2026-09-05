const $ = (sel) => document.querySelector(sel);
const api = async (url, opts) => (await fetch(url, opts)).json();
const post = (url, body, options = {}) =>
  api(url, { method: "POST", headers: { "Content-Type": "application/json" },
             body: body ? JSON.stringify(body) : undefined, ...options });
const put = (url, body) =>
  api(url, { method: "PUT", headers: { "Content-Type": "application/json" },
             body: JSON.stringify(body) });
const del = (url) => api(url, { method: "DELETE" });

const SESSION = "demo";
const MODE = Object.freeze({ VALIDATE: "validate", DEPOSIT: "deposit" });
let activeMode = MODE.VALIDATE;
let activeRun = null;
let sessionResetting = false;
let runCounter = 0;
// 策略样本来源:分开管理专家教学 / 专家纠错 / 工单复盘
const SOURCE_LABEL = { taught: "专家教学", refine: "专家纠错", ticket: "工单复盘" };
const srcLabel = (s) => SOURCE_LABEL[s] || "专家教学";
const QUICK = [
  "蓝牙耳机偶现连接失败,应该怎么排查?",
  "刷机失败一直卡在 20%,可能是什么原因?",
  "有没有类似 ANR 的历史缺陷案例?",
  "稳定性测试压测 2 小时后 App 卡死,下一步查什么?",
  "蓝牙连接失败但日志不完整,帮我升级测试专家。",
];

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- Tab 切换 ----------
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("#tab-" + t.dataset.tab).classList.add("active");
    if (t.dataset.tab === "review") loadReview();
    if (t.dataset.tab === "samples") loadSamples();
    refreshMetrics();
  });
});

// ---------- 策略模式切换 ----------
function clearConversation() {
  $("#messages").innerHTML = "";
  renderTrace([]);
}

function renderRunControls() {
  const running = Boolean(activeRun);
  $("#send").disabled = running || sessionResetting;
  $("#input").disabled = running || sessionResetting;
  $("#reset").disabled = sessionResetting;
  $("#reset").textContent = running ? "中止并重置" : "重置";
}

function createRunId() {
  runCounter += 1;
  return globalThis.crypto?.randomUUID?.() || `run-${Date.now()}-${runCounter}`;
}

async function cancelActiveRun(reason) {
  const run = activeRun;
  if (!run) return false;
  activeRun = null;
  run.controller.abort();
  clearConversation();
  renderRunControls();
  try {
    await post("/api/chat/cancel", {
      session_id: SESSION,
      run_id: run.runId,
      reason,
    });
  } catch (error) {
    // 浏览器中止旧请求后,取消通知失败不应阻断用户开始下一轮操作。
    console.warn("策略运行取消通知失败", error);
  }
  return true;
}

async function resetChat(reason = "reset") {
  sessionResetting = true;
  renderRunControls();
  await cancelActiveRun(reason);
  try {
    await post("/api/session/reset?session_id=" + SESSION);
  } finally {
    clearConversation();
    sessionResetting = false;
    renderRunControls();
  }
}

function isDepositMode() {
  return activeMode === MODE.DEPOSIT;
}

function renderMode() {
  const deposit = isDepositMode();
  document.querySelectorAll(".mode-button").forEach((button) => {
    const selected = button.dataset.mode === activeMode;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  $("#teach-box").classList.toggle("show", deposit);
  $("#ask-box").classList.toggle("hidden", deposit);
  $("#messages").classList.toggle("hidden", deposit);
  $("#tab-chat").classList.toggle("deposit-mode", deposit);
  $("#mode-hint").textContent = deposit
    ? "策略沉淀:录入专家确认的排查经验,形成可复用策略,供后续同类问题验证与召回。"
    : "策略验证:输入 ODM 问题,观察策略如何被召回,并结合 SOP、缺陷案例和专家升级完成决策。";
}

async function selectMode(mode) {
  if (![MODE.VALIDATE, MODE.DEPOSIT].includes(mode) || mode === activeMode) return;
  await resetChat("mode_switch");
  activeMode = mode;
  renderMode();
  if (isDepositMode()) loadTaught();
}

document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => selectMode(button.dataset.mode));
});

// 策略沉淀模式里展示已沉淀清单,避免重复录入
async function loadTaught() {
  const list = await api("/api/playbook");
  $("#taught-count").textContent = list.length;
  const box = $("#taught-list");
  box.innerHTML = list.length ? "" : '<div class="empty">还没有策略样本。</div>';
  list.forEach((k) => {
    const el = document.createElement("div");
    el.className = "taught-item";
    el.innerHTML = `<div class="tq">${escapeHtml(k.question)}</div>` +
      (k.note ? `<div class="tn">策略原因:${escapeHtml(k.note)}</div>` : "");
    box.appendChild(el);
  });
}

// ---------- 聊天 ----------
function addMessage(text, who, handoff) {
  const div = document.createElement("div");
  div.className = "msg " + who + (handoff ? " handoff" : "");
  div.textContent = text;
  $("#messages").appendChild(div);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

// AI 回复(带「纠错 / 固化」能力)。question=触发这条回复的问题。
function addBotMessage(text, opts = {}) {
  const { handoff = false, question = null, revised = false, feedback = "" } = opts;
  const wrap = document.createElement("div");
  wrap.className = "msg bot" + (handoff ? " handoff" : "");

  if (revised) {
    const badge = document.createElement("div");
    badge.className = "revised-badge";
    badge.textContent = "↻ 已按专家意见修正";
    wrap.appendChild(badge);
  }
  const content = document.createElement("div");
  content.textContent = text;
  wrap.appendChild(content);

  if (!handoff && question) {
    const bar = document.createElement("div");
    bar.className = "msg-actions";

    const refineBtn = document.createElement("button");
    refineBtn.className = "mini-btn";
    refineBtn.textContent = "纠错";
    refineBtn.onclick = () => openRefineBox(wrap, question, text);
    bar.appendChild(refineBtn);

    if (revised) {
      const commitBtn = document.createElement("button");
      commitBtn.className = "mini-btn ok";
      commitBtn.textContent = "固化为策略";
      commitBtn.onclick = async () => {
        commitBtn.disabled = true;
        commitBtn.textContent = "固化中…";
        await post("/api/refine/commit", { question, answer: text, feedback });
        commitBtn.textContent = "已固化 ✓ 已并入总纲";
        refreshMetrics();
        if (isDepositMode()) loadTaught();
      };
      bar.appendChild(commitBtn);
    }
    wrap.appendChild(bar);
  }

  $("#messages").appendChild(wrap);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function openRefineBox(wrap, question, reply) {
  if (wrap.querySelector(".refine-box")) return;
  const box = document.createElement("div");
  box.className = "refine-box";
  const ta = document.createElement("textarea");
  ta.placeholder = "说明这条判断哪里不对、该补什么证据…例如:先查 bt_stack 日志,不能直接判硬件";
  const btns = document.createElement("div");
  btns.className = "refine-btns";
  const submit = document.createElement("button");
  submit.className = "mini-btn ok";
  submit.textContent = "提交纠错";
  const cancel = document.createElement("button");
  cancel.className = "mini-btn";
  cancel.textContent = "取消";
  cancel.onclick = () => box.remove();
  submit.onclick = async () => {
    const fb = ta.value.trim();
    if (!fb) return;
    submit.disabled = true;
    submit.textContent = "AI 修正中…";
    const r = await post("/api/refine",
      { question, reply, feedback: fb, session_id: SESSION });
    box.remove();
    addBotMessage(r.reply, { question, revised: true, feedback: fb });
  };
  btns.appendChild(submit);
  btns.appendChild(cancel);
  box.appendChild(ta);
  box.appendChild(btns);
  wrap.appendChild(box);
  ta.focus();
}

function parseTicketContext(text) {
  const data = {};
  String(text || "").split(/\n+/).forEach((line) => {
    const idx = line.indexOf(":");
    if (idx <= 0) return;
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    if (key && value) data[key] = value;
  });
  return data;
}

function renderTicketContext(t) {
  const ctx = parseTicketContext(t.context);
  const rows = [
    ["升级原因", ctx["升级原因"]],
    ["已尝试", ctx["已尝试"]],
    ["缺失证据", ctx["缺失证据"]],
  ].filter(([, value]) => value);
  if (!rows.length && !t.context) return "";
  const body = rows.length
    ? rows.map(([label, value]) =>
        `<div class="ticket-context-row"><span>${label}</span>${escapeHtml(value)}</div>`).join("")
    : `<div class="ticket-context-row"><span>上下文</span>${escapeHtml(t.context)}</div>`;
  return `<div class="ticket-context">${body}</div>`;
}

// 把一条内部 message 渲染成便于调试阅读的摘要行
function fmtMessage(m) {
  const role = m.role || "?";
  if (role === "assistant" && Array.isArray(m.tool_calls) && m.tool_calls.length) {
    const calls = m.tool_calls
      .map((c) => `${c.name}(${JSON.stringify(c.input || {})})`)
      .join(", ");
    const th = m.content ? ` 「${m.content}」` : "";
    return `[assistant → 调用]${th} ${calls}`;
  }
  if (role === "tool") {
    return `[tool:${m.name}] ${JSON.stringify(m.result)}`;
  }
  return `[${role}] ${m.content || ""}`;
}

function renderTrace(trace) {
  const box = $("#trace");
  box.innerHTML = "";
  if (!trace || !trace.length) {
    box.innerHTML = '<div class="trace-empty">暂无轨迹</div>';
    return;
  }
  const labels = {
    llm_call: "🧠 LLM 调用(上下文)",
    llm_response: "🧠 LLM 决策",
    think: "💭 思考",
    tool_call: "调用工具",
    tool_result: "工具结果",
    final: "最终回复",
  };
  trace.forEach((s) => {
    const el = document.createElement("div");
    let cls = "tstep " + s.type;
    if (s.type === "tool_result" && s.is_error) cls += " error";
    el.className = cls;

    let inner = `<div class="k">第${s.step}步 · ${labels[s.type] || s.type}${s.tool ? " · " + s.tool : ""}</div>`;

    // ① LLM 调用:展示可用工具 + 可折叠的完整上下文(喂给模型的 messages)
    if (s.type === "llm_call") {
      if (s.available_tools) {
        inner += `<div class="sub">可用工具:${escapeHtml(s.available_tools.join(", "))}</div>`;
      }
      const msgs = s.messages || [];
      const lines = msgs.map((m) => escapeHtml(fmtMessage(m))).join("\n");
      inner += `<details><summary>查看完整上下文(${msgs.length} 条消息)</summary><pre>${lines}</pre></details>`;
    }

    // ② LLM 决策:展示模型这一步决定"调工具"还是"直接回复"
    if (s.type === "llm_response") {
      if (s.decision === "tool_call" && s.tool_calls && s.tool_calls.length) {
        const names = s.tool_calls.map((c) => c.name).join(", ");
        inner += `<div class="sub">决策:调用工具 → ${escapeHtml(names)}${s.tool_calls.length > 1 ? "(并行)" : ""}</div>`;
        inner += `<pre>tool_calls ${escapeHtml(JSON.stringify(s.tool_calls))}</pre>`;
      } else {
        inner += `<div class="sub">决策:直接回复(结束循环)</div>`;
        if (s.content) inner += `<pre>${escapeHtml(s.content)}</pre>`;
      }
    }

    if (s.type !== "llm_call" && s.type !== "llm_response" && s.content) {
      inner += `<div>${escapeHtml(s.content)}</div>`;
    }
    if (s.input) inner += `<pre>入参 ${escapeHtml(JSON.stringify(s.input))}</pre>`;
    if (s.output) {
      inner += `<pre>出参 ${escapeHtml(JSON.stringify(s.output))}</pre>`;
      if (s.is_error) inner += `<div class="err-hint">工具返回错误 → Agent 将据此重新决策</div>`;
    }
    el.innerHTML = inner;
    box.appendChild(el);
  });
}

async function send() {
  if (sessionResetting) return;
  if (activeRun) await resetChat("superseded");
  const input = $("#input");
  const text = input.value.trim();
  if (!text) return;
  const run = { runId: createRunId(), controller: new AbortController() };
  activeRun = run;
  renderRunControls();
  input.value = "";
  addMessage(text, "user");
  renderTrace([{ step: 0, type: "think", content: "Agent 正在规划排查路径……" }]);
  try {
    const res = await post("/api/chat", {
      message: text,
      session_id: SESSION,
      run_id: run.runId,
    }, { signal: run.controller.signal });
    if (activeRun?.runId !== run.runId || res.cancelled) return;
    addBotMessage(res.reply, { handoff: res.handoff, question: text });
    renderTrace(res.trace);
    refreshMetrics();
  } catch (error) {
    if (error.name !== "AbortError" && activeRun?.runId === run.runId) {
      console.error("策略验证请求失败", error);
    }
  } finally {
    if (activeRun?.runId === run.runId) {
      activeRun = null;
      renderRunControls();
    }
  }
}

$("#send").addEventListener("click", send);
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
$("#reset").addEventListener("click", () => resetChat("reset"));

// ---------- 策略沉淀提交 ----------
$("#teach-btn").addEventListener("click", async () => {
  const question = $("#t-question").value.trim();
  const answer = $("#t-answer").value.trim();
  const note = $("#t-note").value.trim();
  if (!question || !answer) return alert("请至少填写「问题现象」和「建议排查路径」");
  await post("/api/teach", { question, answer, note });
  addMessage(`已沉淀策略样本:「${question}」`, "bot");
  $("#t-question").value = ""; $("#t-answer").value = ""; $("#t-note").value = "";
  refreshMetrics();
  loadTaught();
});

// 快捷问句
const quickBox = $("#quick");
QUICK.forEach((q) => {
  const b = document.createElement("button");
  b.textContent = q;
  b.onclick = () => {
    if (isDepositMode()) { $("#t-question").value = q; return; }
    $("#input").value = q; send();
  };
  quickBox.appendChild(b);
});

// ---------- 指标 ----------
async function refreshMetrics() {
  const m = await api("/api/metrics");
  $("#m-kb").textContent = m.kb_count;
  $("#m-chats").textContent = m.total_chats;
  $("#m-handoff").textContent = (m.handoff_rate * 100).toFixed(0) + "%";
  $("#m-hit").textContent = (m.kb_hit_rate * 100).toFixed(0) + "%";
}

// ---------- AI 归纳的排查策略总纲 ----------
$("#summary-btn").addEventListener("click", async () => {
  $("#sum-text").value = "";
  $("#sum-text").placeholder = "AI 正在归纳排查策略总纲……";
  $("#sum-status").textContent = "";
  $("#sum-modal").classList.add("show");
  const r = await api("/api/playbook/summary");
  $("#sum-text").value = r.summary || "";
});
$("#sum-save").addEventListener("click", async () => {
  await put("/api/playbook/summary", { text: $("#sum-text").value });
  $("#sum-status").textContent = "已保存 ✓";
  setTimeout(() => ($("#sum-status").textContent = ""), 1500);
});
$("#sum-regen").addEventListener("click", async () => {
  $("#sum-status").textContent = "AI 正在合并归纳(保留你的改写)……";
  const r = await post("/api/playbook/summary/regenerate");
  $("#sum-text").value = r.summary || "";
  $("#sum-status").textContent = "已重新归纳 ✓";
  setTimeout(() => ($("#sum-status").textContent = ""), 1500);
});
$("#sum-close").addEventListener("click", () => $("#sum-modal").classList.remove("show"));
$("#sum-modal").addEventListener("click", (e) => {
  if (e.target.id === "sum-modal") $("#sum-modal").classList.remove("show");
});

// ---------- 完整系统提示词(只读) ----------
$("#prompt-btn").addEventListener("click", async () => {
  $("#prompt-text").textContent = "加载中……";
  $("#prompt-modal").classList.add("show");
  const r = await api("/api/settings/system-prompt");
  $("#prompt-text").textContent = r.prompt || "(无内容)";
});
$("#prompt-close").addEventListener("click", () => $("#prompt-modal").classList.remove("show"));
$("#prompt-modal").addEventListener("click", (e) => {
  if (e.target.id === "prompt-modal") $("#prompt-modal").classList.remove("show");
});

// ---------- AI 业务设定 ----------
async function loadDirective() {
  const r = await api("/api/settings/directive");
  $("#directive").value = r.directive || "";
}
$("#save-directive").addEventListener("click", async () => {
  const btn = $("#save-directive");
  await put("/api/settings/directive", { text: $("#directive").value });
  const old = btn.textContent;
  btn.textContent = "已保存 ✓";
  setTimeout(() => (btn.textContent = old), 1500);
});

// ---------- 后台:工单 + 策略样本库管理 ----------
async function loadReview() {
  loadDirective();
  // 工单
  const tickets = await api("/api/tickets?status=open");
  const tbox = $("#tickets");
  tbox.innerHTML = tickets.length ? "" : '<div class="empty">暂无待专家复盘的问题。</div>';
  tickets.forEach((t) => {
    const c = document.createElement("div");
    c.className = "card";
    const ctx = parseTicketContext(t.context);
    const phenomenon = ctx["问题现象"] || t.question;
    c.innerHTML = `
      <div class="q">问题现象:${escapeHtml(phenomenon)}</div>
      <div class="meta">工单 #${t.id} · ${t.created_at}</div>
      ${renderTicketContext(t)}
      <textarea placeholder="建议排查路径…"></textarea>
      <textarea placeholder="策略原因 / 触发条件(可选)…"></textarea>
      <div class="actions"><button class="btn-primary">复盘并入库</button></div>`;
    const [ans, note] = c.querySelectorAll("textarea");
    c.querySelector("button").onclick = async () => {
      if (!ans.value.trim()) return alert("请先填写建议排查路径");
      await post(`/api/tickets/${t.id}/teach`, { answer: ans.value.trim(), note: note.value.trim() });
      loadReview(); refreshMetrics();
    };
    tbox.appendChild(c);
  });

}

async function loadSamples() {
  const samples = await api("/api/playbook");
  const counts = { taught: 0, refine: 0, ticket: 0 };
  samples.forEach((s) => {
    const source = s.source || "taught";
    if (Object.prototype.hasOwnProperty.call(counts, source)) counts[source] += 1;
  });
  $("#sample-total").textContent = samples.length;
  $("#sample-taught-total").textContent = counts.taught;
  $("#sample-refine-total").textContent = counts.refine;
  $("#sample-ticket-total").textContent = counts.ticket;

  const groups = {
    taught: { box: $("#samples-taught"), cnt: $("#cnt-taught") },
    refine: { box: $("#samples-refine"), cnt: $("#cnt-refine") },
    ticket: { box: $("#samples-ticket"), cnt: $("#cnt-ticket") },
  };
  const emptyHint = {
    taught: '<div class="empty">还没有专家教学样本。去「策略沉淀」录入几条。</div>',
    refine: '<div class="empty">还没有专家纠错记录。在策略验证页对回复点「纠错」并「固化为策略」即可产生。</div>',
    ticket: '<div class="empty">还没有工单复盘样本。</div>',
  };
  Object.entries(groups).forEach(([key, g]) => {
    const items = samples.filter((s) => (s.source || "taught") === key);
    g.cnt.textContent = counts[key];
    g.box.innerHTML = items.length ? "" : emptyHint[key];
    items.forEach((s) => g.box.appendChild(renderSampleCard(s)));
  });
}

// 渲染一张可编辑/删除的策略样本卡片
function renderSampleCard(s) {
  const c = document.createElement("div");
  c.className = "card";
  c.innerHTML = `
    <input class="s-q" value="${escapeHtml(s.question)}" />
    <textarea class="s-a">${escapeHtml(s.answer)}</textarea>
    <textarea class="s-n" placeholder="策略原因 / 触发条件">${escapeHtml(s.note || "")}</textarea>
    <div class="meta">#${s.id} · ${srcLabel(s.source)} · ${escapeHtml(s.created_at || "")}</div>
    <div class="actions">
      <button class="btn-ok">保存修改</button>
      <button class="btn-reject">删除</button>
    </div>`;
  const [save, remove] = c.querySelectorAll("button");
  save.onclick = async () => {
    save.disabled = true;
    const res = await put(`/api/playbook/${s.id}`, {
      question: c.querySelector(".s-q").value.trim(),
      answer: c.querySelector(".s-a").value.trim(),
      note: c.querySelector(".s-n").value.trim(),
    });
    save.disabled = false;
    if (res && res.ok) {
      save.textContent = "已保存 ✓";
      setTimeout(() => { save.textContent = "保存修改"; }, 1500);
    } else {
      alert("保存失败,请重试");
    }
  };
  remove.onclick = async () => {
    if (!confirm("确定删除这条策略样本?")) return;
    await del(`/api/playbook/${s.id}`); loadReview(); refreshMetrics();
  };
  return c;
}

refreshMetrics();
