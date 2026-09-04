// Walkthrough page: play one worked example in order, then show its measurement.

import {
  answerCard,
  chip,
  diagramStates,
  el,
  failureCard,
  icon,
  kpiRow,
  laneFor,
  outcomePanels,
  pipelineDiagram,
  stepCard,
} from "./render.js";

const STEP_DELAY_MS = 1600;

const dom = {
  meta: document.getElementById("shell-meta"),
  examples: document.getElementById("example-list"),
  scope: document.getElementById("scope-note"),
  badges: document.getElementById("brief-badges"),
  title: document.getElementById("brief-title"),
  why: document.getElementById("brief-why"),
  question: document.getElementById("brief-question"),
  diagram: document.getElementById("diagram"),
  kpis: document.getElementById("kpis"),
  trace: document.getElementById("trace"),
  outcome: document.getElementById("outcome"),
  progress: document.getElementById("progress"),
  play: document.getElementById("play"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
  reveal: document.getElementById("reveal"),
  restart: document.getElementById("restart"),
};

const state = {
  examples: [],
  active: 0,
  steps: [],
  shown: 0,
  playing: false,
  timer: null,
  lane: null,
  laneStage: null,
};

function buildSteps(example) {
  const timeline = example.events.filter((event) => event.type === "timeline");
  const answer = example.events.find((event) => event.type === "answer") || null;
  const failure = example.events.find((event) => event.type === "run_failed") || null;
  const evaluation = example.events.find((event) => event.type === "evaluation");
  const steps = timeline.map((event) => ({
    stage: event.stage || "research",
    render: () => stepCard(event),
  }));
  if (answer) steps.push({ stage: "final", render: () => answerCard(answer) });
  if (failure) steps.push({ stage: "final", render: () => failureCard(failure) });
  return { steps, answer, evaluation };
}

function paintDiagram() {
  const seen = state.steps.slice(0, state.shown).map((step) => step.stage);
  const states = diagramStates(seen, {
    finished: state.shown >= state.steps.length,
  });
  dom.diagram.replaceChildren(
    pipelineDiagram(states, { neutral: state.shown === 0 }),
  );
}

function appendStep(step, { scroll }) {
  if (state.laneStage !== step.stage || state.lane === null) {
    state.lane = laneFor(step.stage);
    state.laneStage = step.stage;
    dom.trace.appendChild(state.lane);
  }
  const card = step.render();
  state.lane.querySelector(".lane__steps").appendChild(card);
  if (scroll) card.scrollIntoView({ behavior: "smooth", block: "center" });
}

function updateControls() {
  const total = state.steps.length;
  dom.progress.textContent = `${Math.min(state.shown, total)} / ${total}`;
  dom.prev.disabled = state.shown === 0;
  dom.next.disabled = state.shown >= total;
  dom.reveal.disabled = state.shown >= total;
  dom.play.textContent = state.playing
    ? "Pause"
    : state.shown >= total
      ? "Play again"
      : state.shown > 0
        ? "Resume"
        : "Play walkthrough";
}

function showNext({ scroll = true } = {}) {
  if (state.shown >= state.steps.length) {
    pause();
    return false;
  }
  appendStep(state.steps[state.shown], { scroll });
  state.shown += 1;
  if (state.shown === state.steps.length) {
    dom.outcome.replaceChildren(
      outcomePanels(state.answer, state.evaluation),
    );
  }
  paintDiagram();
  updateControls();
  return true;
}

function rebuild(count, { scroll = false } = {}) {
  dom.trace.replaceChildren();
  dom.outcome.replaceChildren();
  state.shown = 0;
  state.lane = null;
  state.laneStage = null;
  for (let index = 0; index < count; index += 1) showNext({ scroll });
  paintDiagram();
  updateControls();
}

function pause() {
  state.playing = false;
  if (state.timer) window.clearTimeout(state.timer);
  state.timer = null;
  updateControls();
}

function play() {
  if (state.shown >= state.steps.length) rebuild(0);
  state.playing = true;
  updateControls();
  const tick = () => {
    if (!state.playing) return;
    if (!showNext()) return;
    state.timer = window.setTimeout(tick, STEP_DELAY_MS);
  };
  tick();
}

function select(index) {
  pause();
  state.active = index;
  const example = state.examples[index];
  const built = buildSteps(example);
  state.steps = built.steps;
  state.answer = built.answer;
  state.evaluation = built.evaluation;
  renderRail();
  dom.title.textContent = example.title;
  dom.why.textContent = example.why_selected;
  dom.question.textContent = example.question;
  dom.badges.replaceChildren(
    chip(example.category_label, "chip--primary"),
    chip(`${built.steps.length} recorded steps`, "chip--muted"),
  );
  dom.kpis.replaceChildren(kpiRow(state.evaluation));
  rebuild(0);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderRail() {
  dom.examples.replaceChildren();
  state.examples.forEach((example, index) => {
    const item = el("li");
    const button = el("button", "rail__item");
    button.type = "button";
    button.setAttribute("aria-pressed", String(index === state.active));
    button.appendChild(el("span", "rail__index", String(index + 1)));
    const text = el("span", "rail__text");
    text.appendChild(el("strong", null, example.title));
    text.appendChild(el("span", null, example.category_label));
    button.appendChild(text);
    button.addEventListener("click", () => select(index));
    item.appendChild(button);
    dom.examples.appendChild(item);
  });
}

dom.play.addEventListener("click", () => (state.playing ? pause() : play()));
dom.next.addEventListener("click", () => {
  pause();
  showNext();
});
dom.prev.addEventListener("click", () => {
  pause();
  if (state.shown > 0) rebuild(state.shown - 1);
});
dom.reveal.addEventListener("click", () => {
  pause();
  rebuild(state.steps.length);
});
dom.restart.addEventListener("click", () => {
  pause();
  rebuild(0);
});

document.addEventListener("keydown", (event) => {
  if (event.target !== document.body) return;
  if (event.key === "ArrowRight") {
    pause();
    showNext();
  } else if (event.key === "ArrowLeft") {
    pause();
    if (state.shown > 0) rebuild(state.shown - 1);
  } else if (event.key === " ") {
    event.preventDefault();
    if (state.playing) pause();
    else play();
  }
});

async function load() {
  const response = await fetch("/api/story");
  if (!response.ok) {
    dom.title.textContent = "The worked examples could not be loaded.";
    return;
  }
  const document_ = await response.json();
  const model = el("span", "shell__chip");
  model.appendChild(icon("model", 12));
  model.appendChild(el("strong", null, document_.model_label));
  const variant = el("span", "shell__chip");
  variant.appendChild(el("span", null, "Pipeline"));
  variant.appendChild(el("strong", null, document_.pipeline_label));
  dom.meta.replaceChildren(variant, model);
  dom.scope.textContent = `${document_.content_scope} ${document_.metrics_scope}`;
  dom.scope.title = document_.selection_reason;
  state.examples = document_.candidates;
  select(0);
}

load();
