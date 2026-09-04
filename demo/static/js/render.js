// Shared renderers for the story and chat pages.
//
// Numbers arrive from the backend as decimal strings so Decimal precision is
// preserved; render them verbatim and never parse before comparing.

const STAGES = {
  user: { label: "Question", agent: "Analyst" },
  routing: { label: "Routing", agent: "Router" },
  research: { label: "Research", agent: "Research agent" },
  math: { label: "Calculation", agent: "Calculation agent" },
  verifier: { label: "Verification", agent: "Verifier" },
  final: { label: "Answer", agent: "Answer" },
};

const LANE_NOTES = {
  routing: "Reads what data exists, then picks the company",
  research: "Finds evidence in the frozen filing",
  math: "Computes with an exact calculator",
  verifier: "Checks the work before release",
  final: "Released to the user",
  user: "",
};

const ICONS = {
  question:
    "M8 1.4a6.6 6.6 0 1 0 0 13.2A6.6 6.6 0 0 0 8 1.4Zm0 10.9a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8Zm1.2-3.6c-.5.3-.5.4-.5.8H7.3c0-1.1.4-1.6 1.1-2 .5-.3.7-.5.7-.9 0-.5-.4-.8-1-.8s-1 .4-1 1H5.7c0-1.3 1-2.2 2.4-2.2 1.4 0 2.4.8 2.4 2 0 .9-.5 1.5-1.3 2.1Z",
  model:
    "M8 1.3 2.6 4v4.4c0 3.2 2.2 5.5 5.4 6.6 3.2-1.1 5.4-3.4 5.4-6.6V4L8 1.3Zm0 3.2a1.6 1.6 0 0 1 1.6 1.6c0 .6-.3 1.1-.8 1.4v1.1a.8.8 0 0 1-1.6 0V7.5a1.6 1.6 0 0 1 .8-3Z",
  tool: "M10.5 1.8c-1.9 0-3.4 1.5-3.4 3.4 0 .4.1.8.2 1.2l-4.8 4.8 1.9 1.9 4.8-4.8c.4.1.8.2 1.3.2 1.9 0 3.4-1.5 3.4-3.4 0-.5-.1-1-.3-1.4l-2.1 2.1-1.6-1.6 2.1-2.1c-.4-.2-.9-.3-1.5-.3Z",
  result:
    "M2.2 8.5 3.6 7.1l2.7 2.7 6-6L13.8 5.2l-7.5 7.5L2.2 8.5Z",
  verify:
    "M8 1.3 2.8 3.2v4.5c0 3.2 2.1 6 5.2 7.1 3.1-1.1 5.2-3.9 5.2-7.1V3.2L8 1.3Zm-.7 9.6L5 8.6l1.1-1.1 1.2 1.2 3.1-3.1L11.5 6.7l-4.2 4.2Z",
  answer:
    "M2.5 3.4c0-.8.6-1.4 1.4-1.4h8.2c.8 0 1.4.6 1.4 1.4v6.2c0 .8-.6 1.4-1.4 1.4H6.6l-3.2 2.7a.5.5 0 0 1-.9-.4V3.4Z",
  alert: "M8 1.6 15.2 14H.8L8 1.6Zm-.85 4.3v4.2h1.7V5.9h-1.7Zm0 5.4V13h1.7v-1.7h-1.7Z",
  info: "M8 1.4a6.6 6.6 0 1 0 0 13.2A6.6 6.6 0 0 0 8 1.4ZM7.15 4.4h1.7v1.7h-1.7V4.4Zm0 2.9h1.7v4.7h-1.7V7.3Z",
  stage: "M2.6 7.2h10.8v1.6H2.6V7.2Z",
  retry:
    "M8 2.6V.9L4.5 3.3 8 5.7V4.2a3.8 3.8 0 1 1-3.8 3.8H2.6a5.4 5.4 0 1 0 5.4-5.4Z",
  check: "M2.2 8.5 3.6 7.1l2.7 2.7 6-6L13.8 5.2l-7.5 7.5L2.2 8.5Z",
  cross:
    "M12.3 4.8 11.2 3.7 8 6.9 4.8 3.7 3.7 4.8 6.9 8l-3.2 3.2 1.1 1.1L8 9.1l3.2 3.2 1.1-1.1L9.1 8l3.2-3.2Z",
  dash: "M3.4 7.2h9.2v1.6H3.4V7.2Z",
  spark:
    "M8 1.5l1.7 4.2 4.5.3-3.5 2.9 1.1 4.4L8 10.9l-3.8 2.4 1.1-4.4L1.8 6l4.5-.3L8 1.5Z",
  trash:
    "M6.4 1.5a1 1 0 0 0-1 1V3H3a.8.8 0 0 0 0 1.6h.3l.6 8.1a1.6 1.6 0 0 0 1.6 1.5h5a1.6 1.6 0 0 0 1.6-1.5l.6-8.1H13A.8.8 0 0 0 13 3h-2.4v-.5a1 1 0 0 0-1-1H6.4Zm.6 1.5h2V3H7v-.5Zm-.7 3.1.3 6h1L7.6 6.1H6.3Zm3.4 0L9.4 12h1l.3-6H9.7Z",
};

const KIND_ICONS = {
  user_message: "question",
  model_action: "model",
  tool_call: "tool",
  tool_result: "result",
  verification: "verify",
  assistant_answer: "answer",
  answer: "answer",
  run_failed: "alert",
  stage_started: "stage",
  stage_finished: "stage",
  retry: "retry",
};

/* ---------- tiny DOM helpers ---------- */

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null && text !== "") node.textContent = text;
  return node;
}

export function icon(name, size = 15) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "currentColor");
  path.setAttribute("d", ICONS[name] || ICONS.info);
  svg.appendChild(path);
  return svg;
}

export function chip(text, variant, iconName) {
  const node = el("span", variant ? `chip ${variant}` : "chip");
  if (iconName) node.appendChild(icon(iconName, 12));
  node.appendChild(el("span", null, text));
  return node;
}

export function notice(message, variant) {
  const node = el("p", variant ? `notice ${variant}` : "notice");
  node.appendChild(icon(variant === "notice--bad" ? "alert" : "info"));
  node.appendChild(el("span", null, message));
  return node;
}

/* ---------- formatting ---------- */

function seconds(milliseconds, digits = 2) {
  if (milliseconds === null || milliseconds === undefined) return null;
  return `${(Number(milliseconds) / 1000).toFixed(digits)} s`;
}

function integer(value) {
  return value === null || value === undefined
    ? null
    : Number(value).toLocaleString("en-US");
}

function usd(value) {
  if (value === null || value === undefined || value === "") return null;
  return `$${Number(value).toFixed(4)}`;
}

function ratio(value) {
  return value === null || value === undefined ? null : Number(value).toFixed(2);
}

function flag(value) {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "not applicable";
}

function humanise(name) {
  return name.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase());
}

/* ---------- progressive reveal ---------- */

const reducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

// Reveal already-received text a word at a time, the way a chat client does.
// The pipeline returns its answer as one structured object, so this is a
// presentation reveal, not token-level streaming.
export function revealText(node, text, { msPerWord = 26, onTick } = {}) {
  if (reducedMotion()) {
    node.textContent = text;
    return Promise.resolve();
  }
  const words = String(text).split(/(\s+)/);
  let index = 0;
  return new Promise((resolve) => {
    const step = () => {
      const batch = Math.max(1, Math.round(words.length / 90));
      node.textContent += words.slice(index, index + batch).join("");
      index += batch;
      if (onTick) onTick();
      if (index >= words.length) {
        resolve();
        return;
      }
      window.setTimeout(step, msPerWord);
    };
    step();
  });
}

/* ---------- activity status ---------- */

// Short words only. The full detail lives in the collapsed audit trail.
const ACTIVITY = {
  routing: "Finding the company",
  research: "Searching information",
  math: "Calculating",
  verifier: "Verifying",
  final: "Writing answer",
};

const TOOL_ACTIVITY = {
  list_available_data: "Checking which filings exist",
  search_filing: "Searching the filing",
  lookup_fact: "Looking up figures",
  calculate: "Calculating",
};

// The one-line "what is happening now" indicator.
export function activityLine() {
  const line = el("p", "activity");
  line.appendChild(el("span", "activity__spinner"));
  line.appendChild(el("span", "activity__text", "Starting"));
  return line;
}

export function activityFor(event) {
  if (event.kind === "tool_call") {
    const call = (event.calls || [])[0] || {};
    return TOOL_ACTIVITY[call.tool] || "Working";
  }
  if (event.kind === "verification") return "Verifying";
  if (event.kind === "tool_result") return "Reading results";
  return ACTIVITY[event.stage] || "Working";
}

export function stageActivity(stage) {
  return ACTIVITY[stage] || "Working";
}

/* ---------- pipeline diagram ---------- */

const PIPELINE_NODES = [
  { id: "user", x: 8, label: "Question", sub: "routed to a company", stage: "user" },
  {
    id: "research",
    x: 138,
    label: "Research",
    sub: "search · lookup",
    stage: "research",
  },
  { id: "math", x: 268, label: "Calculation", sub: "calculate", stage: "math" },
  { id: "verifier", x: 398, label: "Verification", sub: "recheck", stage: "verifier" },
  { id: "final", x: 528, label: "Answer", sub: "evidence-backed", stage: "final" },
];
const NODE_W = 106;
const NODE_H = 48;
const NODE_Y = 26;

function svgEl(tag, attributes) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes || {}).forEach(([name, value]) =>
    node.setAttribute(name, String(value)),
  );
  return node;
}

// Map the stages a run has reached onto node states. `reached` is the ordered
// list of stages seen so far; the last one is "active" until the run finishes,
// earlier ones are "done", and the answer node lights up once it is finished.
// Routing has no node of its own: it is the work behind the Question node.
const STAGE_NODE = { routing: "user" };

export function diagramStates(stages, { finished = false } = {}) {
  const reached = stages.map((stage) => STAGE_NODE[stage] || stage);
  const current = reached[reached.length - 1];
  const states = {};
  PIPELINE_NODES.forEach(({ id }) => {
    if (!finished && id === current) states[id] = "active";
    else states[id] = reached.includes(id) ? "done" : "idle";
  });
  if (finished) states.final = "done";
  return states;
}

// A linear process flow with one optional repair loop. `states` maps a node id
// to "idle" | "active" | "done"; omit it for the neutral explanatory view.
export function pipelineDiagram(states = {}, { neutral = false } = {}) {
  const figure = el("figure", "card pipeline");
  figure.style.margin = "0";
  const svg = svgEl("svg", {
    viewBox: "0 0 642 128",
    role: "img",
    "aria-labelledby": "pipeline-title pipeline-desc",
  });
  const title = svgEl("title", { id: "pipeline-title" });
  title.textContent = "How one question is answered";
  const desc = svgEl("desc", { id: "pipeline-desc" });
  desc.textContent =
    "A question is scoped to one company, a research stage gathers evidence from " +
    "the frozen filing, a calculation stage computes with an exact calculator, a " +
    "verification stage rechecks the work and may send it back once for repair, " +
    "and only verified information reaches the answer.";
  svg.append(title, desc);

  const marker = svgEl("marker", {
    id: "pipeline-arrow",
    viewBox: "0 0 8 8",
    refX: 6,
    refY: 4,
    markerWidth: 5,
    markerHeight: 5,
    orient: "auto-start-reverse",
  });
  marker.appendChild(
    svgEl("path", { d: "M0.5 0.8 L6.5 4 L0.5 7.2 z", fill: "var(--line-2)" }),
  );
  svg.appendChild(svgEl("defs")).appendChild(marker);

  PIPELINE_NODES.slice(0, -1).forEach((node, index) => {
    const from = node.x + NODE_W;
    const to = PIPELINE_NODES[index + 1].x;
    svg.appendChild(
      svgEl("path", {
        class: "pipeline__link",
        "marker-end": "url(#pipeline-arrow)",
        d: `M${from} ${NODE_Y + NODE_H / 2} H${to - 3}`,
      }),
    );
  });

  // the single repair loop: verification -> calculation
  svg.appendChild(
    svgEl("path", {
      class: "pipeline__link pipeline__link--optional",
      "marker-end": "url(#pipeline-arrow)",
      d: `M${398 + NODE_W / 2} ${NODE_Y + NODE_H} V104 H${268 + NODE_W / 2} V${NODE_Y + NODE_H + 3}`,
    }),
  );
  const loopLabel = svgEl("text", {
    class: "pipeline__linklabel",
    x: (398 + 268) / 2 + NODE_W / 2,
    y: 118,
    "text-anchor": "middle",
  });
  loopLabel.textContent = "at most one repair request";
  svg.appendChild(loopLabel);

  PIPELINE_NODES.forEach((node) => {
    const state = neutral ? "neutral" : states[node.id] || "idle";
    const group = svgEl("g", { class: "pipeline__node", "data-state": state });
    group.setAttribute(
      "style",
      `--node-color: var(--stage-${node.stage}); --node-tint: color-mix(in srgb, var(--stage-${node.stage}) 8%, white)`,
    );
    group.appendChild(
      svgEl("rect", {
        x: node.x,
        y: NODE_Y,
        width: NODE_W,
        height: NODE_H,
        rx: 9,
      }),
    );
    const label = svgEl("text", {
      class: "pipeline__label",
      x: node.x + NODE_W / 2,
      y: NODE_Y + 20,
      "text-anchor": "middle",
    });
    label.textContent = node.label;
    const sub = svgEl("text", {
      class: "pipeline__sub",
      x: node.x + NODE_W / 2,
      y: NODE_Y + 35,
      "text-anchor": "middle",
    });
    sub.textContent = node.sub;
    group.append(label, sub);
    if (state === "active") {
      group.appendChild(
        svgEl("circle", {
          class: "pipeline__pulse",
          cx: node.x + NODE_W - 11,
          cy: NODE_Y + 11,
          r: 3.5,
        }),
      );
    }
    if (state === "done") {
      const tick = svgEl("path", {
        d: "M0 3 L2.6 5.6 L7 1",
        fill: "none",
        stroke: `var(--stage-${node.stage})`,
        "stroke-width": 1.8,
        "stroke-linecap": "round",
        transform: `translate(${node.x + NODE_W - 17} ${NODE_Y + 8})`,
      });
      group.appendChild(tick);
    }
    svg.appendChild(group);
  });

  figure.appendChild(svg);
  return figure;
}

/* ---------- KPI tiles ---------- */

// A stat tile, not a chart: one headline number with its unit and context.
function kpiTile({ label, value, unit, note, tone }) {
  const tile = el("div", "kpi");
  if (tone) tile.dataset.tone = tone;
  tile.appendChild(el("p", "kpi__label", label));
  const number = el("p", "kpi__value", value ?? "—");
  if (unit) number.appendChild(el("small", null, unit));
  tile.appendChild(number);
  if (note) tile.appendChild(el("p", "kpi__note", note));
  return tile;
}

export function kpiRow(evaluation) {
  const row = el("div", "kpis");
  const checks = Object.values(evaluation.deterministic);
  const applicable = checks.filter((state) => state !== null);
  const passed = applicable.filter((state) => state === true).length;
  const judge = evaluation.judge;
  const judgeScores = judge
    ? [
        judge.answer_correctness_grade,
        judge.workflow_correctness,
        judge.chronological_coherence,
        judge.workflow_efficiency,
        judge.auditability,
      ]
    : [];
  row.append(
    kpiTile({
      label: "Checks passed",
      value: `${passed}/${applicable.length}`,
      note: `${checks.length - applicable.length} not applicable`,
      tone: passed === applicable.length ? "ok" : "bad",
    }),
    kpiTile({
      label: "Latency",
      value: seconds(evaluation.performance.latency_ms),
      note: "end to end",
    }),
    kpiTile({
      label: "Cost",
      value: usd(evaluation.performance.cost_usd),
      note: `${integer(
        (evaluation.performance.input_tokens || 0) +
          (evaluation.performance.output_tokens || 0),
      )} tokens`,
    }),
    kpiTile({
      label: "Evidence cited",
      value: String(evaluation.evidence_count ?? "—"),
      note: "from the frozen filing",
    }),
    kpiTile({
      label: "Review score",
      value: judgeScores.length
        ? (judgeScores.reduce((total, score) => total + score, 0) / 5).toFixed(1)
        : "—",
      unit: judgeScores.length ? "/5" : "",
      note: "independent reviewer",
    }),
  );
  return row;
}

// A live answer has no gold reference, so it gets run statistics rather than the
// scored KPI row: what the run cost, not whether it was right.
export function runStatsRow({ cost, answer, stepCount }) {
  const tokens = cost?.token_usage?.totalTokens;
  const row = el("div", "kpis");
  row.append(
    kpiTile({
      label: "Latency",
      value: seconds(cost?.latency_ms),
      note: "end to end",
    }),
    kpiTile({
      label: "Cost",
      value: usd(cost?.cost_usd?.total),
      note: tokens ? `${integer(tokens)} tokens` : null,
    }),
    kpiTile({
      label: "Evidence cited",
      value: String(answer?.evidence_ids?.length ?? 0),
      note: "from the frozen filing",
    }),
    kpiTile({
      label: "Pipeline steps",
      value: String(stepCount),
      note: "observable actions",
    }),
  );
  return row;
}

/* ---------- trace ---------- */

function argumentText(args) {
  return Object.entries(args || {})
    .map(([name, value]) => `${name}: ${JSON.stringify(value)}`)
    .join("  ");
}

function callList(event) {
  const wrap = el("div", "calls");
  (event.calls || []).forEach((call) => {
    const row = el("div", "call");
    row.appendChild(el("span", "call__name", call.tool));
    row.appendChild(el("span", "call__args", argumentText(call.arguments)));
    wrap.appendChild(row);
  });
  return wrap;
}

function resultPill(value) {
  const pill = el("div", "pill");
  pill.appendChild(el("span", null, "result"));
  pill.appendChild(el("strong", null, String(value)));
  return pill;
}

function evidenceRow(ids) {
  const row = el("div", "chip-row");
  row.style.marginTop = "var(--s2)";
  ids.filter(Boolean).forEach((id) => row.appendChild(chip(id, "chip--num")));
  return row;
}

function dataTable(rows, columns) {
  // Drop any column no row actually has data for, rather than rendering it as
  // a header over a column of dashes.
  const visible = columns.filter(([key]) =>
    rows.some((row) => row[key] !== null && row[key] !== undefined && row[key] !== ""),
  );
  const table = el("table", "data");
  const headRow = el("tr");
  visible.forEach(([, label, numeric]) =>
    headRow.appendChild(el("th", numeric ? "n" : null, label)),
  );
  table.appendChild(el("thead")).appendChild(headRow);
  const body = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr");
    if (row.matched === false) tr.dataset.tone = "bad";
    visible.forEach(([key, , numeric, render]) => {
      const cell = el("td", numeric ? "n" : null);
      const value = row[key];
      if (render) {
        cell.appendChild(render(row));
      } else {
        cell.textContent =
          value === null || value === undefined || value === "" ? "—" : String(value);
      }
      tr.appendChild(cell);
    });
    body.appendChild(tr);
  });
  table.appendChild(body);
  return table;
}

export function stepCard(event) {
  const stage = event.stage || "research";
  const item = el("li", "step");
  if (event.kind === "user_message") item.classList.add("step--user");
  if (event.kind === "run_failed") item.classList.add("step--failed");
  if (event.included_in_accepted_calculations === false) {
    item.classList.add("step--discarded");
  }
  item.style.setProperty("--stage-color", `var(--stage-${stage}, var(--primary))`);

  const badge = el("span", "step__icon");
  badge.appendChild(icon(KIND_ICONS[event.kind] || "stage"));
  item.appendChild(badge);

  const body = el("div", "step__body");
  const meta = el("div", "step__meta");
  if (event.duration_ms) meta.appendChild(chip(seconds(event.duration_ms), "chip--num"));
  if (event.attempt > 1) meta.appendChild(chip(`retry ${event.attempt}`, "chip--warn"));
  if (event.included_in_accepted_calculations === false) {
    meta.appendChild(chip("discarded", "chip--muted"));
  }
  if (meta.children.length) body.appendChild(meta);

  body.appendChild(el("p", "step__title", event.title || defaultTitle(event, stage)));
  if (event.summary) body.appendChild(el("p", "step__summary", event.summary));
  if (event.kind === "tool_call") body.appendChild(callList(event));
  if (event.selected_evidence?.length) body.appendChild(evidenceRow(event.selected_evidence));
  if (event.results?.length) {
    body.appendChild(
      dataTable(event.results, [
        ["label", "Fact", false],
        ["period", "Period", false],
        ["value", "Value", true],
        ["unit", "Unit", false],
        ["evidence_id", "Source", false],
      ]),
    );
  }
  if (event.selected_value) {
    body.appendChild(
      dataTable([event.selected_value], [
        ["value", "Value", true],
        ["unit", "Unit", false],
        ["period", "Period", false],
      ]),
    );
  }
  if (event.result !== undefined && event.result !== null) {
    body.appendChild(resultPill(event.result));
  }
  if (event.kind === "verification") {
    const verdict = el(
      "span",
      `verdict ${event.approved ? "verdict--yes" : "verdict--no"}`,
    );
    verdict.appendChild(icon(event.approved ? "check" : "cross", 13));
    verdict.appendChild(
      el("span", null, event.approved ? "Approved for release" : "Sent back"),
    );
    body.appendChild(verdict);
  }
  item.appendChild(body);
  return item;
}

function defaultTitle(event, stage) {
  const name = STAGES[stage]?.label || stage;
  if (event.kind === "stage_started") return `${name} begins`;
  if (event.kind === "stage_finished") return `${name} complete`;
  return name;
}

// A stage lane: one heading per stage, with that stage's steps beside it.
export function laneFor(stage) {
  const lane = el("div", "lane");
  lane.dataset.stage = stage;
  lane.style.setProperty("--lane-color", `var(--stage-${stage}, var(--primary))`);
  const head = el("div", "lane__head");
  const title = el("p", "lane__title");
  title.appendChild(icon(stage === "user" ? "question" : "model", 13));
  title.appendChild(el("span", null, STAGES[stage]?.label || stage));
  head.appendChild(title);
  if (LANE_NOTES[stage]) head.appendChild(el("p", "lane__note", LANE_NOTES[stage]));
  lane.appendChild(head);
  lane.appendChild(el("ol", "lane__steps"));
  return lane;
}

export function answerCard(answer, { deferText = false } = {}) {
  const item = el("li", "step step--answer");
  item.style.setProperty("--stage-color", "var(--stage-final)");
  const badge = el("span", "step__icon");
  badge.appendChild(icon("answer"));
  item.appendChild(badge);
  const body = el("div", "step__body");
  body.appendChild(el("p", "eyebrow", "Verified answer"));
  const text = el("p", "compare__text", deferText ? "" : answer.text);
  body.appendChild(text);
  const detail = el("div");
  if (deferText) detail.hidden = true;
  if (answer.values?.length) {
    detail.appendChild(
      dataTable(answer.values, [
        ["label", "Reported value", false],
        ["period", "Period", false],
        ["value", "Value", true],
        ["unit", "Unit", false],
      ]),
    );
  }
  if (answer.evidence_ids?.length) detail.appendChild(evidenceRow(answer.evidence_ids));
  body.appendChild(detail);
  item.appendChild(body);
  item.textNode = text;
  item.detailNode = detail;
  return item;
}

export function failureCard(failure) {
  const item = el("li", "step step--failed");
  item.style.setProperty("--stage-color", "var(--bad)");
  const badge = el("span", "step__icon");
  badge.appendChild(icon("alert"));
  item.appendChild(badge);
  const body = el("div", "step__body");
  body.appendChild(el("p", "eyebrow", "No answer released"));
  body.appendChild(
    el("p", "step__title", humanise(failure.failure_category || "unknown failure")),
  );
  if (failure.failure_message) {
    body.appendChild(el("p", "step__summary", failure.failure_message));
  }
  item.appendChild(body);
  return item;
}

/* ---------- outcome ---------- */

function sectionHead(title, description) {
  const head = el("div", "section__head");
  head.appendChild(el("h3", null, title));
  if (description) head.appendChild(el("p", null, description));
  return head;
}

function panel(title, aside) {
  const card = el("section", "card panel");
  const head = el("div", "panel__head");
  head.appendChild(el("h4", null, title));
  if (aside) head.appendChild(el("span", "kpi__note", aside));
  card.appendChild(head);
  return card;
}

function rowList(entries) {
  const list = el("dl", "rows");
  entries.forEach(([label, value]) => {
    const row = el("div", "row");
    row.appendChild(el("dt", null, label));
    row.appendChild(
      el("dd", null, value === null || value === undefined || value === "" ? "—" : String(value)),
    );
    list.appendChild(row);
  });
  return list;
}

function checkChip(name, state) {
  const label = humanise(name);
  if (state === true) return chip(label, "chip--ok", "check");
  if (state === false) return chip(label, "chip--bad", "cross");
  return chip(`${label} · n/a`, "chip--muted", "dash");
}

function meter(label, value, { warn = false, hint = "" } = {}) {
  const wrap = el("div", "meter");
  wrap.appendChild(el("span", "meter__label", label));
  wrap.appendChild(el("span", "meter__value", value === null ? "—" : ratio(value)));
  const track = el("div", "meter__track");
  const fill = el("div", warn ? "meter__fill meter__fill--warn" : "meter__fill");
  fill.style.width = `${Math.max(0, Math.min(1, value ?? 0)) * 100}%`;
  track.appendChild(fill);
  wrap.appendChild(track);
  if (hint) wrap.appendChild(el("span", "meter__hint", hint));
  return wrap;
}

function scoreRow(label, score) {
  const row = el("div", "score");
  row.appendChild(el("span", null, label));
  const dots = el("span", "score__dots");
  for (let index = 1; index <= 5; index += 1) {
    const dot = el("span", "score__dot");
    dot.dataset.on = String(index <= score);
    dots.appendChild(dot);
  }
  row.appendChild(dots);
  row.appendChild(el("span", "score__value", `${score}/5`));
  return row;
}

function verdictBanner(evaluation) {
  const clean = evaluation.issues.length === 0;
  const caveat = !clean && evaluation.user_facing_answer_matches_expected === true;
  const tone = clean ? "ok" : caveat ? "warn" : "bad";
  const banner = el("div", `verdict-banner verdict-banner--${tone}`);
  banner.appendChild(icon(clean ? "check" : caveat ? "info" : "alert", 17));
  const text = el("span");
  if (clean) {
    text.appendChild(el("strong", null, "Matches the verified answer. "));
    text.appendChild(
      el("span", null, "Every deterministic check passed for this question."),
    );
  } else if (caveat) {
    text.appendChild(el("strong", null, "Answer correct, scoring caveat. "));
    text.appendChild(el("span", null, evaluation.note || ""));
  } else {
    text.appendChild(el("strong", null, "Does not meet the verified answer. "));
    const failed = evaluation.issues.length;
    text.appendChild(
      el("span", null, `${failed} ${failed === 1 ? "check" : "checks"} failed for this question.`),
    );
  }
  banner.appendChild(text);
  return banner;
}

function comparisonSection(answer, evaluation) {
  const section = el("section", "section");
  section.appendChild(
    sectionHead(
      "Answer against the verified reference",
      "The reference answer and its values were confirmed by hand before the run.",
    ),
  );
  section.appendChild(verdictBanner(evaluation));
  const compare = el("div", "compare");
  const generated = el("article", "card compare__panel");
  generated.appendChild(el("p", "eyebrow", "Generated answer"));
  generated.appendChild(
    el("p", "compare__text", answer?.text || "No answer was released."),
  );
  const expected = el("article", "card compare__panel compare__panel--gold");
  expected.appendChild(el("p", "eyebrow", "Verified reference"));
  expected.appendChild(el("p", "compare__text", evaluation.expected_answer.text));
  compare.append(generated, expected);
  section.appendChild(compare);

  if (evaluation.value_comparison.length) {
    const card = panel("Value-by-value comparison");
    card.classList.add("panel--wide");
    card.appendChild(
      dataTable(evaluation.value_comparison, [
        ["label", "Reported", false],
        ["period", "Period", false],
        ["value", "Reported value", true],
        ["expected_value", "Reference value", true],
        [
          "matched",
          "Match",
          false,
          (row) =>
            row.matched
              ? chip("match", "chip--ok", "check")
              : chip(row.missing ? "missing" : "extra", "chip--bad", "cross"),
        ],
      ]),
    );
    section.appendChild(card);
  }
  return section;
}

function findingsSection(evaluation) {
  const section = el("section", "section");
  section.appendChild(
    sectionHead("Findings", "Produced by deterministic checks, not by a model."),
  );
  if (!evaluation.issues.length) {
    const box = el("div", "finding finding--clean");
    const label = el("p", "finding__label");
    label.appendChild(icon("check", 14));
    label.appendChild(el("span", null, "No issues found"));
    box.appendChild(label);
    box.appendChild(
      el(
        "p",
        "finding__hint",
        "Values, evidence, periods, formula, formatting, and the audit trail all held.",
      ),
    );
    section.appendChild(box);
    return section;
  }
  evaluation.issues.forEach((issue) => {
    const box = el("div", "finding");
    const label = el("p", "finding__label");
    label.appendChild(icon("cross", 14));
    label.appendChild(el("span", null, issue.label));
    box.appendChild(label);
    if (issue.hint) box.appendChild(el("p", "finding__hint", issue.hint));
    issue.detail
      .filter(Boolean)
      .forEach((detail) => box.appendChild(el("p", "finding__detail", detail)));
    section.appendChild(box);
  });
  return section;
}

function metricsSection(evaluation) {
  const section = el("section", "section");
  section.appendChild(
    sectionHead("Full measurement", "Every metric recorded for this single question."),
  );

  const checks = panel("Deterministic checks");
  checks.classList.add("panel--wide");
  const chips = el("div", "chip-row");
  Object.entries(evaluation.deterministic).forEach(([name, state]) =>
    chips.appendChild(checkChip(name, state)),
  );
  checks.appendChild(chips);
  section.appendChild(checks);

  const grid = el("div", "metrics");
  const workflow = evaluation.workflow;
  const workflowPanel = panel("Workflow");
  workflowPanel.appendChild(
    rowList([
      ["Tools required", (workflow.required_tools || []).join(", ")],
      ["Tools used", (workflow.invoked_tools || []).join(", ")],
      ["Calculation attempts", workflow.math_attempt_count],
      ["Attempt accepted", workflow.accepted_math_attempt],
      ["Repair requested", flag(workflow.math_repair_attempted)],
    ]),
  );
  grid.appendChild(workflowPanel);

  const toolPanel = panel("Tool selection");
  toolPanel.appendChild(
    rowList([
      ["Precision", ratio(evaluation.tool_metrics.precision)],
      ["Recall", ratio(evaluation.tool_metrics.recall)],
      ["F1", ratio(evaluation.tool_metrics.f1)],
      ["Exact match", flag(evaluation.tool_metrics.exact_match)],
    ]),
  );
  grid.appendChild(toolPanel);

  const retrievalPanel = panel("Retrieval quality");
  if (!evaluation.retrieval_metrics.applicable) {
    retrievalPanel.appendChild(
      el("p", "finding__hint", evaluation.retrieval_metrics.reason),
    );
  } else {
    evaluation.retrieval_metrics.search_calls.forEach((call, index) => {
      const block = el("div", "search-call");
      block.appendChild(el("p", "search-call__query", `“${call.query}”`));
      block.appendChild(
        meter("Evidence found in top 5", call.evidence_recall_at_5, {
          hint: index === 0 ? "higher is better" : "",
        }),
      );
      block.appendChild(
        meter("Off-target results in top 5", call.irrelevant_retrieval_rate_at_5, {
          warn: true,
          hint: index === 0 ? "lower is better" : "",
        }),
      );
      retrievalPanel.appendChild(block);
    });
  }
  grid.appendChild(retrievalPanel);

  const performance = evaluation.performance;
  const performancePanel = panel("Performance and cost");
  performancePanel.appendChild(
    rowList([
      ["Latency", seconds(performance.latency_ms)],
      ["Input tokens", integer(performance.input_tokens)],
      ["Output tokens", integer(performance.output_tokens)],
      ["Estimated cost", usd(performance.cost_usd)],
      ["Provider retries", evaluation.reliability.retries],
      ["Schema repairs", evaluation.reliability.structured_output_repairs],
    ]),
  );
  grid.appendChild(performancePanel);

  if (evaluation.judge) {
    const judgePanel = panel("Independent review", evaluation.judge.model_label);
    const scores = el("div", "rows");
    scores.append(
      scoreRow("Answer correctness", evaluation.judge.answer_correctness_grade),
      scoreRow("Workflow correctness", evaluation.judge.workflow_correctness),
      scoreRow("Step order", evaluation.judge.chronological_coherence),
      scoreRow("Efficiency", evaluation.judge.workflow_efficiency),
      scoreRow("Auditability", evaluation.judge.auditability),
    );
    judgePanel.appendChild(scores);
    judgePanel.appendChild(el("p", "explanation", evaluation.judge.explanation));
    judgePanel.classList.add("panel--wide");
    grid.appendChild(judgePanel);
  }
  section.appendChild(grid);
  return section;
}

export function outcomePanels(answer, evaluation) {
  const fragment = document.createDocumentFragment();
  fragment.appendChild(comparisonSection(answer, evaluation));
  fragment.appendChild(findingsSection(evaluation));
  fragment.appendChild(metricsSection(evaluation));
  return fragment;
}

/* ---------- streaming ---------- */

const EVENT_NAMES = [
  "run_started",
  "timeline",
  "answer",
  "run_failed",
  "evaluation",
  "run_cost",
  "busy",
  "reply",
];

export function streamEvents(url, onEvent) {
  return new Promise((resolve, reject) => {
    const source = new EventSource(url);
    source.addEventListener("done", () => {
      source.close();
      resolve();
    });
    source.onerror = () => {
      source.close();
      reject(new Error("The connection to the pipeline stopped."));
    };
    EVENT_NAMES.forEach((name) =>
      source.addEventListener(name, (message) => onEvent(JSON.parse(message.data))),
    );
  });
}
