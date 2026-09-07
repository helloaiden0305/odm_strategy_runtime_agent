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
let summaryPreviewPending = false;
let noticeTimer = null;
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

function showNotice(message) {
  const notice = $("#app-notice");
  notice.textContent = message;
  notice.classList.add("show");
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => notice.classList.remove("show"), 3400);
}

// ---------- Tab 切换 ----------
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("#tab-" + t.dataset.tab).classList.add("active");
    if (t.dataset.tab === "review") loadDirective();
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
        const result = await post("/api/refine/commit", { question, answer: text, feedback });
        commitBtn.textContent = "已固化至样本库 ✓";
        showNotice(result.message || "已保存至策略样本库，可用于后续策略总纲归纳。");
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

function tracePhase(item) {
  if (item.type === "run_lifecycle") {
    return ["completed", "cancelled", "forced_handoff"].includes(item.status)
      ? { key: "finish", label: "收尾校验" }
      : { key: "plan", label: "规划" };
  }
  if (["planning", "plan_created", "plan_invalid"].includes(item.type)) {
    return { key: "plan", label: "规划" };
  }
  if (item.type === "plan_guard") {
    return { key: "plan_guard", label: "计划校验" };
  }
  if (["plan_state", "llm_call", "llm_response", "think", "tool_call", "tool_result", "decision_guard", "loop_guard"].includes(item.type)) {
    return { key: "execute", label: "受控执行" };
  }
  if (item.type === "replan") {
    const count = item.replan_count || 1;
    return { key: `replan-${count}`, label: `重规划（第 ${count} 次）` };
  }
  if (["final_guard", "guard_forced_finish", "final", "cancelled"].includes(item.type)) {
    return { key: "finish", label: "收尾校验" };
  }
  return null;
}

function appendTracePhase(box, phase) {
  const header = document.createElement("div");
  header.className = "trace-phase";
  header.innerHTML = `<span>阶段</span><strong>${escapeHtml(phase.label)}</strong>`;
  box.appendChild(header);
}

function renderTrace(trace) {
  const box = $("#trace");
  box.innerHTML = "";
  if (!trace || !trace.length) {
    box.innerHTML = '<div class="trace-empty">暂无轨迹</div>';
    return;
  }
  const labels = {
    run_lifecycle: "运行状态",
    llm_call: "🧠 LLM 调用(上下文)",
    llm_response: "🧠 LLM 决策",
    think: "💭 思考",
    tool_call: "调用工具",
    tool_result: "工具结果",
    final: "最终回复",
    plan_created: "执行计划",
    planning: "正在规划",
    plan_invalid: "计划降级",
    plan_guard: "计划校验",
    plan_state: "步骤状态",
    replan: "重规划",
    final_guard: "收尾校验",
    guard_forced_finish: "受控兜底",
    decision_guard: "决策校验",
    loop_guard: "循环护栏",
    cancelled: "运行已中止",
  };
  const runId = trace.find((item) => item.run_id)?.run_id;
  if (runId) {
    const meta = document.createElement("div");
    meta.className = "trace-run-meta";
    meta.innerHTML = `<span>本次运行</span><code title="${escapeHtml(runId)}">${escapeHtml(runId)}</code>`;
    box.appendChild(meta);
  }
  let activePhase = null;
  trace.forEach((s) => {
    const phase = tracePhase(s);
    // Final Guard 放行后记录的“结论步骤完成”属于收尾，而不是新的执行轮次。
    const keepFinishPhase = activePhase === "finish" && s.type === "plan_state";
    if (phase && phase.key !== activePhase && !keepFinishPhase) {
      appendTracePhase(box, phase);
      activePhase = phase.key;
    }
    const el = document.createElement("div");
    let cls = "tstep " + s.type;
    if (s.type === "tool_result" && s.is_error) cls += " error";
    el.className = cls;

    const turn = Number.isInteger(s.turn) ? s.turn : 0;
    let inner = `<div class="k">第${turn}回合 · ${labels[s.type] || s.type}${s.tool ? " · " + s.tool : ""}</div>`;

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

    if (s.type === "plan_created" || s.type === "replan") {
      const plan = s.plan || {};
      if (plan.goal) inner += `<div class="sub">目标:${escapeHtml(plan.goal)}</div>`;
      if (plan.decision_reason) inner += `<div class="sub">决策摘要:${escapeHtml(plan.decision_reason)}</div>`;
      if (Array.isArray(plan.evidence_gap) && plan.evidence_gap.length) {
        inner += `<div class="sub">待补证据:${escapeHtml(plan.evidence_gap.join("、"))}</div>`;
      }
      if (Array.isArray(plan.steps)) {
        const steps = plan.steps.map((item) =>
          `${item.id} [${item.status}]：${item.goal}`).join("\n");
        inner += `<details><summary>查看计划步骤(${plan.steps.length} 项)</summary><pre>${escapeHtml(steps)}</pre></details>`;
      }
    }

    if (s.type === "plan_invalid" && s.error) {
      inner += `<div class="err-hint">降级原因:${escapeHtml(s.error)}</div>`;
    }

    if (s.type === "plan_guard" || s.type === "final_guard") {
      inner += `<div class="sub">${s.ok ? "校验通过" : "校验未通过"}</div>`;
      if (Array.isArray(s.reasons) && s.reasons.length) {
        inner += `<pre>${escapeHtml(s.reasons.join("\n"))}</pre>`;
      }
    }

    if (s.type === "plan_state") {
      inner += `<div class="sub">${escapeHtml(s.plan_step || "步骤")} → ${escapeHtml(s.status || "unknown")}</div>`;
    }

    if (s.type === "run_lifecycle") {
      inner += `<div class="sub">状态:${escapeHtml(s.status || "unknown")}</div>`;
    }

    if (s.type === "loop_guard") {
      const guardReasons = {
        tool_call_budget_exhausted: "工具调用预算已用尽",
        repeated_tool_cycle_without_new_evidence: "重复链路未产生新证据",
        cycle_information_gain: "重复链路获得新证据，继续执行",
        tool_circuit_open: "工具处于熔断冷却期",
        tool_circuit_opened: "工具连续异常，熔断已打开",
        tool_circuit_reopened: "半开试探失败，熔断重新打开",
        tool_circuit_recovered: "半开试探成功，工具已恢复",
      };
      inner += `<div class="sub">原因:${escapeHtml(guardReasons[s.reason] || s.reason || "运行时护栏")}</div>`;
      const details = [];
      if (s.pattern_length) details.push(`循环长度=${s.pattern_length}`);
      if (s.repeat_count) details.push(`重复次数=${s.repeat_count}`);
      if (s.information_gain !== undefined) details.push(`信息增量=${s.information_gain}`);
      if (s.tool_call_count !== undefined) details.push(`已执行工具=${s.tool_call_count}`);
      if (s.max_tool_calls_per_run !== undefined) details.push(`工具预算=${s.max_tool_calls_per_run}`);
      if (s.consecutive_failures !== undefined) details.push(`连续失败=${s.consecutive_failures}`);
      if (s.retry_after_seconds) details.push(`预计恢复=${s.retry_after_seconds} 秒`);
      if (s.action) details.push(`后续=${s.action}`);
      if (details.length) inner += `<div class="sub">${escapeHtml(details.join(" · "))}</div>`;
    }

    if (s.type === "decision_guard") {
      inner += `<div class="sub">原因:模型决策不符合 Turn Contract</div>`;
      if (s.action) inner += `<div class="sub">后续:${escapeHtml(s.action)}</div>`;
    }

    if (s.plan_step && s.type !== "plan_state") {
      inner += `<div class="sub">计划步骤:${escapeHtml(s.plan_step)}</div>`;
    }

    if (s.type !== "llm_call" && s.type !== "llm_response" && s.content) {
      inner += `<div>${escapeHtml(s.content)}</div>`;
    }
    if (s.input) inner += `<pre>入参 ${escapeHtml(JSON.stringify(s.input))}</pre>`;
    if (s.output) {
      inner += `<pre>出参 ${escapeHtml(JSON.stringify(s.output))}</pre>`;
      if (s.is_error) inner += `<div class="err-hint">工具返回错误 → Agent 将据此重新决策</div>`;
    }
    const governance = [
      ["工具存在", s.tool_exists],
      ["计划允许", s.plan_allowed],
      ["入参有效", s.input_valid],
      ["结果成功", s.result_ok],
      ["结果数量", s.result_count],
    ].filter(([, value]) => value !== undefined && value !== null);
    if (governance.length) {
      inner += `<div class="sub">治理:${governance.map(([key, value]) =>
        `${key}=${escapeHtml(String(value))}`).join(" · ")}</div>`;
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
  renderTrace([{ step: 0, type: "planning", content: "Agent 正在生成执行计划……" }]);
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
  const result = await post("/api/teach", { question, answer, note });
  addMessage(`已沉淀策略样本:「${question}」`, "bot");
  showNotice(result.message || "已保存至策略样本库，可用于后续策略总纲归纳。");
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
function renderSummaryEvidence(stats = {}) {
  const bySource = stats.by_source || {};
  const total = Number(stats.total || 0);
  if (!total) {
    $("#sum-evidence").textContent = "归纳依据：暂无策略样本。先沉淀专家经验后，再生成初始候选。";
    return;
  }
  $("#sum-evidence").textContent =
    `归纳依据：当前策略样本库 ${total} 条，其中专家教学 ${bySource.taught || 0} 条 · ` +
    `专家纠错 ${bySource.refine || 0} 条 · 工单复盘 ${bySource.ticket || 0} 条。`;
}

function renderSummaryState(result, pending = summaryPreviewPending) {
  if (pending) {
    $("#sum-state").textContent = "待专家保存";
    return;
  }
  if (result.merge_status === "empty") {
    $("#sum-state").textContent = "尚未生成";
    return;
  }
  $("#sum-state").textContent =
    result.summary_status === "expert_confirmed" ? "专家已确认" : "AI 草稿";
}

function setSummaryPreview(message, pending) {
  const preview = $("#sum-preview");
  preview.hidden = !message;
  preview.textContent = message || "";
  summaryPreviewPending = pending;
}

function closeSummaryModal() {
  summaryPreviewPending = false;
  $("#sum-modal").classList.remove("show");
  $("#sum-preview").hidden = true;
  $("#sum-preview").textContent = "";
}

$("#summary-btn").addEventListener("click", async () => {
  $("#sum-text").value = "";
  $("#sum-text").placeholder = "点击左侧「重新归纳」生成候选总纲";
  $("#sum-status").textContent = "";
  setSummaryPreview("", false);
  $("#sum-modal").classList.add("show");
  const r = await api("/api/playbook/summary");
  $("#sum-text").value = r.summary || "";
  renderSummaryEvidence(r.sample_stats);
  renderSummaryState(r);
  $("#sum-status").textContent = r.message || "";
});
$("#sum-save").addEventListener("click", async () => {
  if (!$("#sum-text").value.trim()) return alert("请先重新归纳或填写总纲内容");
  $("#sum-save").disabled = true;
  const r = await put("/api/playbook/summary", { text: $("#sum-text").value });
  $("#sum-save").disabled = false;
  setSummaryPreview("", false);
  renderSummaryState({ ...r, merge_status: "existing" }, false);
  $("#sum-status").textContent = "已保存为专家确认版本，后续策略验证将使用该总纲。";
  showNotice("已保存为专家确认总纲，后续策略验证将使用该版本。");
});
$("#sum-regen").addEventListener("click", async () => {
  const button = $("#sum-regen");
  button.disabled = true;
  button.textContent = "归纳中…";
  $("#sum-status").textContent = "正在根据当前策略样本生成候选版本…";
  try {
    const r = await post("/api/playbook/summary/preview");
    $("#sum-text").value = r.summary || "";
    renderSummaryEvidence(r.sample_stats);
    setSummaryPreview(r.message, Boolean(r.pending_save));
    renderSummaryState(r);
    $("#sum-status").textContent = r.pending_save
      ? "候选版本尚未生效，请确认或修改后保存。"
      : (r.message || "");
  } finally {
    button.disabled = false;
    button.textContent = "↻ 重新归纳";
  }
});
$("#sum-text").addEventListener("input", () => {
  if (!$("#sum-text").value.trim()) return;
  if (!summaryPreviewPending) {
    setSummaryPreview("编辑内容尚未保存，不会影响后续策略验证。", true);
    renderSummaryState({ summary_status: "draft" }, true);
  }
});
$("#sum-close").addEventListener("click", closeSummaryModal);
$("#sum-modal").addEventListener("click", (e) => {
  if (e.target.id === "sum-modal") closeSummaryModal();
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

// ---------- 策略样本库：待沉淀工单 + 已沉淀样本 ----------
async function loadPendingTickets() {
  const tickets = await api("/api/tickets?status=open");
  const tbox = $("#pending-tickets");
  $("#cnt-pending-ticket").textContent = tickets.length;
  tbox.innerHTML = tickets.length ? "" : '<div class="empty">暂无待沉淀的复盘工单。</div>';
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
      const result = await post(`/api/tickets/${t.id}/teach`, { answer: ans.value.trim(), note: note.value.trim() });
      showNotice(result.message || "已保存至策略样本库，可用于后续策略总纲归纳。");
      loadSamples(); refreshMetrics();
    };
    tbox.appendChild(c);
  });

}

async function loadSamples() {
  const [samples, stats] = await Promise.all([
    api("/api/playbook"),
    api("/api/playbook/stats"),
  ]);
  const counts = { taught: 0, refine: 0, ticket: 0, ...(stats.by_source || {}) };
  $("#sample-total").textContent = stats.total || 0;
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
  await loadPendingTickets();
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
      <button class="btn-ok" disabled>已保存</button>
      <button class="btn-reject">删除</button>
    </div>`;
  const [save, remove] = c.querySelectorAll("button");
  const fields = c.querySelectorAll(".s-q, .s-a, .s-n");
  let dirty = false;
  const markDirty = () => {
    if (dirty) return;
    dirty = true;
    save.disabled = false;
    save.textContent = "保存修改";
  };
  fields.forEach((field) => field.addEventListener("input", markDirty));

  save.onclick = async () => {
    if (!dirty) return;
    save.disabled = true;
    const res = await put(`/api/playbook/${s.id}`, {
      question: c.querySelector(".s-q").value.trim(),
      answer: c.querySelector(".s-a").value.trim(),
      note: c.querySelector(".s-n").value.trim(),
    });
    if (res && res.ok) {
      dirty = false;
      save.textContent = "已保存";
      showNotice("已更新策略样本库；下次重新归纳时会纳入候选总纲。");
    } else {
      save.disabled = false;
      alert("保存失败,请重试");
    }
  };
  remove.onclick = async () => {
    if (!confirm("确定删除这条策略样本?")) return;
    await del(`/api/playbook/${s.id}`);
    showNotice("已从策略样本库删除；后续归纳将不再使用该样本。");
    loadSamples(); refreshMetrics();
  };
  return c;
}

refreshMetrics();
