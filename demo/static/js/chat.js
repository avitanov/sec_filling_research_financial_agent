// Ask page: a multi-turn chat answered on demand, with persistent history.
//
// A conversation holds many turns. Each turn is answered by the pipeline while
// short phase words and the pipeline diagram show what is happening; the full
// step-by-step detail is recorded into a collapsed audit trail. A conversation
// scopes to the company the latest answer resolved to: a follow-up that names no
// company reuses it ("and the year before?"), while one that names a company
// re-scopes the thread, so a single chat can compare several companies. Every
// follow-up carries the earlier turns as context so references still resolve.
//
// Answered turns are persisted server-side, so old chats appear in the sidebar
// after a reload and can be reopened and continued. Reopened turns are rebuilt
// from a compact record (question, answer, evidence, cost); the live audit trail
// exists only while a turn is being answered.

import {
  activityFor,
  activityLine,
  answerCard,
  chip,
  diagramStates,
  el,
  failureCard,
  icon,
  laneFor,
  notice,
  pipelineDiagram,
  revealText,
  runStatsRow,
  stageActivity,
  stepCard,
  streamEvents,
} from "./render.js";

const dom = {
  meta: document.getElementById("shell-meta"),
  banner: document.getElementById("live-banner"),
  newChat: document.getElementById("new-chat"),
  historySection: document.getElementById("history-section"),
  clearAll: document.getElementById("clear-all"),
  history: document.getElementById("history"),
  historyStore: document.getElementById("history-store"),
  scroll: document.getElementById("thread-scroll"),
  empty: document.getElementById("empty-state"),
  emptyDiagram: document.getElementById("empty-diagram"),
  emptyHints: document.getElementById("empty-hints"),
  conversation: document.getElementById("conversation"),
  composer: document.getElementById("composer"),
  input: document.getElementById("question"),
  companies: document.getElementById("companies"),
  send: document.getElementById("send"),
  hint: document.getElementById("composer-hint"),
  suggestions: document.getElementById("suggestions"),
  suggestionList: document.getElementById("suggestions-list"),
};

const state = {
  recommendations: [],
  companies: [],
  health: { live_available: false, reason: "" },
  conversations: [],
  active: null,
  streaming: false,
};

const newId = () =>
  crypto.randomUUID?.() ?? `conv-${Date.now()}-${Math.random().toString(16).slice(2)}`;

/* ---------- page state ---------- */

function setBusy(busy) {
  state.streaming = busy;
  dom.send.disabled = busy || !state.health.live_available;
  dom.input.disabled = busy;
  dom.newChat.disabled = busy;
  document.querySelectorAll(".suggestion").forEach((suggestion) => {
    suggestion.disabled = busy || !state.health.live_available;
  });
}

function scrollToEnd() {
  dom.scroll.scrollTo({ top: dom.scroll.scrollHeight, behavior: "smooth" });
}

function renderBanner() {
  dom.banner.replaceChildren();
  if (state.health.live_available) return;
  dom.banner.appendChild(
    notice(
      `Questions cannot be answered right now. ${state.health.reason}`,
      "notice--warn",
    ),
  );
}

function renderSuggestions() {
  dom.suggestionList.replaceChildren();
  state.recommendations.forEach((recommendation) => {
    const button = el("button", "suggestion");
    button.type = "button";
    button.disabled = !state.health.live_available;
    const head = el("div", "suggestion__head");
    head.appendChild(el("span", "suggestion__company", recommendation.company));
    head.appendChild(el("span", "suggestion__tag", recommendation.category_label));
    button.appendChild(head);
    button.appendChild(el("p", "suggestion__text", recommendation.question));
    const foot = el("span", "suggestion__cta");
    foot.appendChild(el("span", null, "Start a chat"));
    foot.appendChild(icon("answer", 13));
    button.appendChild(foot);
    // A recommendation names its own company, so it opens a fresh conversation.
    button.addEventListener("click", () => ask(recommendation.question, { fresh: true }));
    dom.suggestionList.appendChild(button);
  });
}

function renderHistory() {
  dom.historySection.hidden = state.conversations.length === 0;
  renderStoreNote(state.conversations.length > 0);
  dom.history.replaceChildren();
  state.conversations.forEach((conversation) => {
    const item = el("li", "history-row");
    const button = el("button", "history-item");
    button.type = "button";
    button.setAttribute("aria-current", String(conversation === state.active));
    button.appendChild(el("span", "history-item__title", conversation.title));
    const meta = el("span", "history-item__meta");
    if (conversation.companyName) {
      meta.appendChild(chip(conversation.companyName, "chip--muted"));
    }
    const count = conversation.turnCount ?? conversation.turns.length;
    meta.appendChild(
      el("span", "history-item__count", `${count} ${count === 1 ? "turn" : "turns"}`),
    );
    button.appendChild(meta);
    button.addEventListener("click", () => openConversation(conversation));
    item.appendChild(button);
    const del = el("button", "history-row__delete");
    del.type = "button";
    del.title = "Delete chat";
    del.setAttribute("aria-label", `Delete chat: ${conversation.title}`);
    del.appendChild(icon("trash", 13));
    del.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteConversation(conversation);
    });
    item.appendChild(del);
    dom.history.appendChild(item);
  });
}

/* ---------- one turn view ---------- */

function buildTurn(question) {
  dom.empty.hidden = true;
  const turn = el("article", "turn");

  const ask = el("div", "turn__ask");
  ask.appendChild(el("p", "turn__question", question));
  turn.appendChild(ask);

  const response = el("div", "turn__response");
  const head = el("div", "turn__head");
  const avatar = el("span", "turn__avatar");
  avatar.appendChild(icon("model", 14));
  head.appendChild(avatar);
  const status = el("div", "turn__status");
  head.appendChild(status);
  response.appendChild(head);

  const diagramSlot = el("div", "turn__diagram");
  response.appendChild(diagramSlot);

  const activity = activityLine();
  response.appendChild(activity);

  const work = el("details", "work");
  const summary = el("summary");
  summary.appendChild(el("span", "work__caret"));
  const summaryText = el("span", null, "Audit trail");
  summary.appendChild(summaryText);
  work.appendChild(summary);
  const body = el("div", "work__body trace");
  work.appendChild(body);
  response.appendChild(work);

  turn.appendChild(response);
  dom.conversation.appendChild(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "start" });
  return { turn, response, status, diagramSlot, activity, work, summaryText, body };
}

function paintDiagram(slot, stages, finished) {
  slot.replaceChildren(pipelineDiagram(diagramStates(stages, { finished })));
}

function setActivity(view, text) {
  const node = view.activity.querySelector(".activity__text");
  if (node) node.textContent = text;
}

function closeActivity(view, stepCount) {
  view.activity.remove();
  view.summaryText.textContent = stepCount
    ? `Audit trail — ${stepCount} recorded steps`
    : "Audit trail";
}

function statusChips(view, event, { finished = false } = {}) {
  view.modeChip = chip(
    finished ? "Answered" : "Answering",
    finished ? "chip--ok" : "chip--primary",
    finished ? "check" : "model",
  );
  const chips = [view.modeChip];
  if (event.company) chips.push(chip(event.company, "chip--muted"));
  if (event.model_label) chips.push(chip(event.model_label, "chip--muted", "model"));
  view.status.replaceChildren(...chips);
}

function markAnswered(view) {
  if (!view.modeChip) return;
  view.modeChip.replaceChildren(icon("check", 12), el("span", null, "Answered"));
  view.modeChip.className = "chip chip--ok";
}

function wrap(card) {
  const list = el("ol", "trace");
  list.appendChild(card);
  return list;
}

// Build one lane per stage as steps stream in.
function stepRouter(view, stages) {
  let lane = null;
  let laneStage = null;
  return (event) => {
    const stage = event.stage || "research";
    if (!stages.includes(stage)) stages.push(stage);
    if (laneStage !== stage) {
      lane = laneFor(stage);
      laneStage = stage;
      view.body.appendChild(lane);
    }
    lane.querySelector(".lane__steps").appendChild(stepCard(event));
  };
}

/* ---------- context for follow-ups ---------- */

function turnAnswer(turn) {
  if (turn.persisted) return turn.answer || null;
  // A turn is either a filing answer or a conversational reply; both carry the
  // thread forward as context for follow-ups.
  const reply = turn.events?.find(
    (event) => event.type === "answer" || event.type === "reply",
  );
  return reply ? reply.text : null;
}

function historyFor(conversation) {
  return conversation.turns
    .map((turn) => ({ question: turn.question, answer: turnAnswer(turn) }))
    .filter((turn) => turn.answer);
}

/* ---------- running a live turn ---------- */

async function ask(question, { fresh = false } = {}) {
  if (state.streaming) return;
  if (!state.health.live_available) {
    renderBanner();
    return;
  }

  let conversation = state.active;
  if (fresh || !conversation) {
    conversation = {
      id: newId(),
      title: question,
      ticker: null,
      companyName: null,
      turns: [],
      loaded: true,
    };
    state.conversations.unshift(conversation);
    state.active = conversation;
    dom.conversation.replaceChildren();
    dom.empty.hidden = true;
  }

  const turn = { question, events: [] };
  const priorHistory = historyFor(conversation);
  const company = conversation.ticker;
  conversation.turns.push(turn);
  conversation.turnCount = conversation.turns.length;
  renderHistory();
  setBusy(true);

  const view = buildTurn(question);
  const stages = ["user"];
  const routeStep = stepRouter(view, stages);
  const pending = [];
  let answer = null;
  // The pipeline diagram implies the decomposed architecture is running, which
  // is only true once routing has actually resolved a company: paint it on
  // "run_started", not eagerly here, so a plain reply or routing failure never
  // flashes it.
  setActivity(view, "Working");

  const params = new URLSearchParams({ q: question, conversation_id: conversation.id });
  if (company) params.set("company", company);
  if (priorHistory.length) params.set("history", JSON.stringify(priorHistory));

  try {
    await streamEvents(`/api/ask?${params}`, (event) => {
      turn.events.push(event);
      if (event.type === "busy") {
        view.status.replaceChildren(chip("Busy", "chip--muted", "alert"));
        view.diagramSlot.remove();
        view.activity.remove();
        view.work.remove();
        view.response.appendChild(notice(event.message, "notice--warn"));
        return;
      }
      if (event.type === "connection_error") {
        // The router's own model call failed (no AWS Bedrock connection, an
        // expired credential, a throttle): say so plainly, in red, instead of
        // rendering it as an answer or a "which company?" reply.
        view.status.replaceChildren(chip("Error", "chip--bad", "alert"));
        view.diagramSlot.remove();
        view.activity.remove();
        view.work.remove();
        view.response.appendChild(notice(event.message, "notice--bad"));
        return;
      }
      if (event.type === "reply") {
        // A general/meta question answered conversationally, without the filing
        // pipeline: render it as a plain assistant message, no diagram, trace, or
        // "Working…" phase line — the reply is already here, just revealing.
        view.status.replaceChildren(chip("Assistant", "chip--muted", "model"));
        view.diagramSlot.remove();
        view.activity.remove();
        view.work.remove();
        const textNode = el("p", "compare__text caret");
        view.response.appendChild(textNode);
        pending.push(
          revealText(textNode, event.text, { onTick: scrollToEnd }).then(() => {
            textNode.classList.remove("caret");
          }),
        );
        return;
      }
      if (event.type === "run_started") {
        conversation.ticker = event.ticker;
        conversation.companyName = event.company;
        statusChips(view, event);
        paintDiagram(view.diagramSlot, stages, false);
        renderHistory();
        return;
      }
      if (event.type === "timeline") {
        routeStep(event);
        paintDiagram(view.diagramSlot, stages, false);
        setActivity(view, activityFor(event));
        return;
      }
      if (event.type === "answer") {
        answer = event;
        if (!stages.includes("final")) stages.push("final");
        paintDiagram(view.diagramSlot, stages, false);
        setActivity(view, stageActivity("final"));
        const card = answerCard(event, { deferText: true });
        view.response.appendChild(wrap(card));
        card.textNode.classList.add("caret");
        pending.push(
          revealText(card.textNode, event.text, { onTick: scrollToEnd }).then(() => {
            card.textNode.classList.remove("caret");
            card.detailNode.hidden = false;
          }),
        );
        return;
      }
      if (event.type === "run_failed") {
        view.response.appendChild(wrap(failureCard(event)));
        return;
      }
      if (event.type === "run_cost") {
        turn.cost = event;
      }
    });
    await Promise.all(pending);
    if (turn.cost) {
      view.response.appendChild(
        runStatsRow({
          cost: turn.cost,
          answer,
          stepCount: view.body.querySelectorAll(".step").length,
        }),
      );
    }
  } catch (error) {
    view.response.appendChild(notice(String(error.message || error), "notice--bad"));
  } finally {
    // The diagram was a live progress read; once finished the audit trail is the
    // record, so drop it to keep a multi-turn thread uncluttered.
    view.diagramSlot.remove();
    markAnswered(view);
    closeActivity(view, view.body.querySelectorAll(".step").length);
    setBusy(false);
    // Keep a turn that produced a reply the user should read — an answer, or the
    // "name a company" / "busy" guidance. Only a turn that showed nothing at all
    // is dropped (and the conversation with it, if it was its only turn), so the
    // thread stays uncluttered without ever swallowing a message silently.
    const answered = turn.events.some((event) => event.type === "run_started");
    const informed = turn.events.some(
      (event) =>
        event.type === "reply" ||
        event.type === "busy" ||
        event.type === "connection_error",
    );
    if (!answered && !informed) {
      conversation.turns = conversation.turns.filter((entry) => entry !== turn);
      conversation.turnCount = conversation.turns.length;
      view.turn.remove();
      if (conversation.turns.length === 0) {
        state.conversations = state.conversations.filter((entry) => entry !== conversation);
        state.active = null;
        dom.empty.hidden = dom.conversation.children.length > 0;
      }
    }
    renderHistory();
    scrollToEnd();
  }
}

/* ---------- replaying turns ---------- */

// A live turn kept its full event stream in memory, so rebuild it fully.
function replayLiveTurn(turn) {
  const view = buildTurn(turn.question);
  const stages = ["user"];
  const routeStep = stepRouter(view, stages);
  let answer = null;
  turn.events.forEach((event) => {
    if (event.type === "run_started") {
      statusChips(view, event, { finished: true });
      return;
    }
    if (event.type === "reply") {
      view.status.replaceChildren(chip("Assistant", "chip--muted", "model"));
      view.work.remove();
      view.response.appendChild(el("p", "compare__text", event.text));
      return;
    }
    if (event.type === "connection_error") {
      view.status.replaceChildren(chip("Error", "chip--bad", "alert"));
      view.work.remove();
      view.response.appendChild(notice(event.message, "notice--bad"));
      return;
    }
    if (event.type === "timeline") {
      routeStep(event);
      return;
    }
    if (event.type === "answer") {
      answer = event;
      if (!stages.includes("final")) stages.push("final");
      view.response.appendChild(wrap(answerCard(event)));
      return;
    }
    if (event.type === "run_failed") {
      view.response.appendChild(wrap(failureCard(event)));
      return;
    }
    if (event.type === "run_cost") {
      view.response.appendChild(
        runStatsRow({
          cost: event,
          answer,
          stepCount: view.body.querySelectorAll(".step").length,
        }),
      );
    }
  });
  view.diagramSlot.remove();
  markAnswered(view);
  closeActivity(view, view.body.querySelectorAll(".step").length);
}

// A persisted turn was reopened from storage: rebuild from its compact record.
// The step-by-step audit trail is not stored, only the outcome.
function replayPersistedTurn(turn) {
  const view = buildTurn(turn.question);
  view.diagramSlot.remove();
  view.activity.remove();
  view.work.remove();
  if (turn.kind === "reply") {
    // A conversational turn: a plain assistant message, no answer card.
    view.status.replaceChildren(chip("Assistant", "chip--muted", "model"));
    view.response.appendChild(el("p", "compare__text", turn.answer || ""));
    return;
  }
  const answered = turn.status === "answered" && turn.answer_payload;
  const chips = [
    answered
      ? chip("Answered", "chip--ok", "check")
      : chip("No answer", "chip--bad", "alert"),
  ];
  if (turn.company) chips.push(chip(turn.company, "chip--muted"));
  view.status.replaceChildren(...chips);
  if (answered) {
    view.response.appendChild(wrap(answerCard(turn.answer_payload)));
    view.response.appendChild(
      runStatsRow({
        cost: turn.cost,
        answer: turn.answer_payload,
        stepCount: turn.step_count,
      }),
    );
  } else if (turn.failure) {
    view.response.appendChild(wrap(failureCard(turn.failure)));
  }
}

function renderTurns(conversation) {
  dom.conversation.replaceChildren();
  dom.empty.hidden = true;
  conversation.turns.forEach((turn) =>
    turn.persisted ? replayPersistedTurn(turn) : replayLiveTurn(turn),
  );
}

async function openConversation(conversation) {
  if (state.streaming || conversation === state.active) return;
  if (!conversation.loaded) {
    try {
      const response = await fetch(`/api/conversation/${encodeURIComponent(conversation.id)}`);
      const data = await response.json();
      conversation.turns = (data.turns || []).map((turn) => ({ ...turn, persisted: true }));
      conversation.loaded = true;
    } catch {
      conversation.turns = [];
      conversation.loaded = true;
    }
  }
  state.active = conversation;
  renderTurns(conversation);
  renderHistory();
  scrollToEnd();
}

function startNew() {
  if (state.streaming) return;
  state.active = null;
  dom.conversation.replaceChildren();
  dom.empty.hidden = false;
  dom.input.value = "";
  dom.input.style.height = "auto";
  renderHistory();
  dom.input.focus();
}

/* ---------- deleting chats ---------- */

// Remove from the sidebar right away, then delete server-side. Best-effort: a
// failed delete just means it reappears on the next reload.
function deleteConversation(conversation) {
  if (state.streaming) return;
  state.conversations = state.conversations.filter((entry) => entry !== conversation);
  if (state.active === conversation) startNew();
  renderHistory();
  fetch(`/api/conversation/${encodeURIComponent(conversation.id)}`, {
    method: "DELETE",
  }).catch(() => {});
}

function deleteAllConversations() {
  if (state.streaming) return;
  state.conversations = [];
  startNew();
  fetch("/api/conversations", { method: "DELETE" }).catch(() => {});
}

// "Clear all" arms on the first click and confirms on the second, so a whole
// history is never wiped by a single stray click and no blocking dialog is used.
let clearArmed = false;
let clearTimer = null;

function resetClearAll() {
  clearArmed = false;
  dom.clearAll.textContent = "Clear all";
  dom.clearAll.classList.remove("sidebar__clear--armed");
  if (clearTimer) clearTimeout(clearTimer);
  clearTimer = null;
}

dom.clearAll.addEventListener("click", () => {
  if (state.streaming) return;
  if (!clearArmed) {
    clearArmed = true;
    dom.clearAll.textContent = "Delete all?";
    dom.clearAll.classList.add("sidebar__clear--armed");
    clearTimer = setTimeout(resetClearAll, 4000);
    return;
  }
  resetClearAll();
  deleteAllConversations();
});

/* ---------- events ---------- */

dom.composer.addEventListener("submit", (submit) => {
  submit.preventDefault();
  const question = dom.input.value.trim();
  if (!question) return;
  dom.input.value = "";
  dom.input.style.height = "auto";
  ask(question);
});

dom.input.addEventListener("input", () => {
  dom.input.style.height = "auto";
  dom.input.style.height = `${Math.min(dom.input.scrollHeight, 168)}px`;
});

dom.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    dom.composer.requestSubmit();
  }
});

dom.newChat.addEventListener("click", startNew);

/* ---------- persisted history ---------- */

function renderStoreNote(hasChats) {
  dom.historyStore.hidden = !hasChats;
  dom.historyStore.textContent = "Saved on this machine.";
}

async function loadConversations() {
  let fromServer = [];
  try {
    const response = await fetch("/api/conversations");
    const data = await response.json();
    fromServer = (data.conversations || []).map((summary) => ({
      id: summary.id,
      title: summary.title,
      ticker: summary.ticker,
      companyName: summary.company,
      turnCount: summary.turn_count,
      turns: [],
      loaded: false,
    }));
  } catch {
    fromServer = [];
  }
  // History loads asynchronously after the input is enabled, so a first chat can
  // be started before this resolves. Merge rather than replace: keep any
  // conversation already created locally (it holds live turns and may be active,
  // and it is not in the server list yet), and append the server ones it does not
  // already include. Replacing here would wipe that first chat from the sidebar.
  const localIds = new Set(state.conversations.map((conversation) => conversation.id));
  state.conversations = [
    ...state.conversations,
    ...fromServer.filter((conversation) => !localIds.has(conversation.id)),
  ];
  renderHistory();
}

/* ---------- boot ---------- */

async function load() {
  const [config, health] = await Promise.all([
    fetch("/api/recommendations").then((response) => response.json()),
    fetch("/api/health").then((response) => response.json()),
  ]);
  state.recommendations = config.recommendations;
  state.companies = config.companies;
  state.health = health;

  const pipelineChip = el("span", "shell__chip");
  pipelineChip.appendChild(el("span", null, "Pipeline"));
  pipelineChip.appendChild(el("strong", null, health.pipeline_label));
  const modelChip = el("span", "shell__chip");
  modelChip.appendChild(icon("model", 12));
  modelChip.appendChild(el("strong", null, health.model_label));
  const statusChip = el("span", "shell__chip");
  statusChip.appendChild(el("span", health.live_available ? "dot dot--ok" : "dot dot--off"));
  statusChip.appendChild(el("strong", null, health.live_available ? "Ready" : "Unavailable"));
  dom.meta.replaceChildren(pipelineChip, modelChip, statusChip);

  dom.companies.replaceChildren(
    ...state.companies.map((company) => el("li", null, company.name)),
  );
  dom.hint.textContent = health.live_available
    ? "Name a company in your first question, then keep asking follow-ups — "
      + "Enter to send, Shift+Enter for a new line."
    : "Answering is unavailable right now.";
  dom.emptyDiagram.replaceChildren(pipelineDiagram({}, { neutral: true }));
  dom.emptyHints.replaceChildren(
    chip("Frozen 10-K evidence", "chip--muted", "verify"),
    chip("Exact calculator", "chip--muted", "tool"),
    chip("Verified before release", "chip--muted", "check"),
  );
  renderSuggestions();
  renderBanner();
  setBusy(false);
  await loadConversations();
}

load();
