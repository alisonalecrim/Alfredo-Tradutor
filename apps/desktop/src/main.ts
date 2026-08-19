import { invoke } from "@tauri-apps/api/core";

type Device = {
  index: number;
  name: string;
  label?: string;
  kind?: string;
  kind_label?: string;
  technical?: boolean;
  recommended_for?: string[];
  max_input_channels: number;
  max_output_channels: number;
};

type Suggestions = {
  line_a?: {
    input_device?: number | null;
    output_device?: number | null;
    summary?: string;
  };
  line_b?: {
    input_device?: number | null;
    output_device?: number | null;
    summary?: string;
  };
};

type DevicesResponse = {
  error?: string;
  default_input?: number | null;
  default_output?: number | null;
  inputs: Device[];
  outputs: Device[];
  inputs_simple?: Device[];
  outputs_simple?: Device[];
  suggestions?: Suggestions;
  guide?: { line_a?: string; line_b?: string };
};

type StatusResponse = {
  running: boolean;
  mode?: string | null;
  line_a?: Record<string, unknown>;
  line_b?: Record<string, unknown>;
  captions?: { line: string; source: string; translation: string }[];
  error?: string | null;
};

const $ = <T extends HTMLElement>(id: string) =>
  document.getElementById(id) as T;

let pollTimer: number | null = null;
let lastDevices: DevicesResponse | null = null;

async function engine<T>(method: string, path: string, body?: unknown): Promise<T> {
  return invoke<T>("engine_request", { method, path, body: body ?? null });
}

function deviceLabel(d: Device, role: string): string {
  const base = d.label || d.name;
  const kind = d.kind_label ? ` · ${d.kind_label}` : "";
  const rec = d.recommended_for?.includes(role) ? " ★ recomendado" : "";
  return `${base}${kind}${rec}`;
}

function fillSelect(
  el: HTMLSelectElement,
  devices: Device[],
  role: string,
  preferred?: number | null,
) {
  const previous = el.value;
  el.innerHTML = "";
  if (!devices.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Nenhum dispositivo encontrado";
    el.appendChild(opt);
    return;
  }

  const sorted = [...devices].sort((a, b) => {
    const ar = a.recommended_for?.includes(role) ? 0 : 1;
    const br = b.recommended_for?.includes(role) ? 0 : 1;
    return ar - br;
  });

  for (const d of sorted) {
    const opt = document.createElement("option");
    opt.value = String(d.index);
    opt.textContent = deviceLabel(d, role);
    if (d.recommended_for?.includes(role)) opt.dataset.recommended = "1";
    el.appendChild(opt);
  }

  if (preferred != null && devices.some((d) => d.index === preferred)) {
    el.value = String(preferred);
  } else if (previous && devices.some((d) => String(d.index) === previous)) {
    el.value = previous;
  }
  updatePickHint(el.id);
}

function findDevice(list: Device[], index: number | null): Device | undefined {
  if (index == null) return undefined;
  return list.find((d) => d.index === index);
}

function updatePickHint(selectId: string) {
  const map: Record<string, string> = {
    "a-input": "a-input-hint",
    "a-output": "a-output-hint",
    "b-input": "b-input-hint",
    "b-output": "b-output-hint",
  };
  const hintId = map[selectId];
  if (!hintId || !lastDevices) return;

  const el = $<HTMLSelectElement>(selectId);
  const idx = el.value === "" ? null : Number(el.value);
  const pool = selectId.endsWith("input") ? lastDevices.inputs : lastDevices.outputs;
  const d = findDevice(pool, idx);
  const hint = $(hintId);
  if (!d) {
    hint.textContent = "";
    return;
  }

  const tips: Record<string, string> = {
    system_loopback: "Certo para pegar o áudio do Meet/Zoom/navegador.",
    microphone: "Certo para a sua voz.",
    speakers: "Alto-falantes ou saída padrão do PC.",
    headphones: "Bom para você ouvir a tradução.",
    hdmi: "Saída da TV/monitor — só use se for isso mesmo.",
    duplex_default: "Padrão do sistema — costuma funcionar.",
  };
  hint.textContent = tips[d.kind ?? ""] ?? d.name;
}

function applySuggestions() {
  if (!lastDevices?.suggestions) return;
  const s = lastDevices.suggestions;
  if (s.line_a?.input_device != null) $<HTMLSelectElement>("a-input").value = String(s.line_a.input_device);
  if (s.line_a?.output_device != null) $<HTMLSelectElement>("a-output").value = String(s.line_a.output_device);
  if (s.line_b?.input_device != null) $<HTMLSelectElement>("b-input").value = String(s.line_b.input_device);
  if (s.line_b?.output_device != null) $<HTMLSelectElement>("b-output").value = String(s.line_b.output_device);
  for (const id of ["a-input", "a-output", "b-input", "b-output"]) updatePickHint(id);
}

function populateFromCache() {
  if (!lastDevices) return;
  const showTech = $<HTMLInputElement>("show-technical").checked;
  const inputs = showTech ? lastDevices.inputs : (lastDevices.inputs_simple ?? lastDevices.inputs);
  const outputs = showTech ? lastDevices.outputs : (lastDevices.outputs_simple ?? lastDevices.outputs);
  const sug = lastDevices.suggestions;

  fillSelect($<HTMLSelectElement>("a-input"), inputs, "a_input", sug?.line_a?.input_device);
  fillSelect($<HTMLSelectElement>("a-output"), outputs, "a_output", sug?.line_a?.output_device);
  fillSelect($<HTMLSelectElement>("b-input"), inputs, "b_input", sug?.line_b?.input_device);
  fillSelect($<HTMLSelectElement>("b-output"), outputs, "b_output", sug?.line_b?.output_device);

  if (lastDevices.guide?.line_a) $("a-guide").textContent = lastDevices.guide.line_a;
  if (lastDevices.guide?.line_b) $("b-guide").textContent = lastDevices.guide.line_b;
}

async function refreshDevices() {
  const data = await engine<DevicesResponse>("GET", "/devices");
  if (data.error) throw new Error(data.error);
  lastDevices = data;
  populateFromCache();
}

async function checkHealth() {
  const dot = $("engine-dot");
  const label = $("engine-label");
  try {
    const h = await engine<{ status: string; session_running: boolean; mode?: string | null }>("GET", "/health");
    dot.className = "dot ok";
    label.textContent = h.session_running
      ? `Motor OK · sessão ativa (${h.mode ?? "?"})`
      : "Motor OK · aguardando sessão";
    return true;
  } catch (e) {
    dot.className = "dot err";
    label.textContent = "Motor offline — rode: bash scripts/run-engine.sh";
    console.error(e);
    return false;
  }
}

function linePayload(prefix: "a" | "b") {
  const enabled = $<HTMLInputElement>(`${prefix}-enabled`).checked;
  const input = $<HTMLSelectElement>(`${prefix}-input`).value;
  const output = $<HTMLSelectElement>(`${prefix}-output`).value;
  return {
    enabled,
    input_device: input === "" ? null : Number(input),
    output_device: output === "" ? null : Number(output),
    source_lang: $<HTMLSelectElement>(`${prefix}-from`).value,
    target_lang: $<HTMLSelectElement>(`${prefix}-to`).value,
    label: prefix.toUpperCase(),
  };
}

function showError(msg: string | null) {
  const el = $("error");
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
}

function lineStatusText(line: Record<string, unknown>, fallback: string): string {
  const error = String(line.error ?? "").trim();
  if (error) return `ERRO · ${error}`;
  const warning = String(line.warning ?? "").trim();
  if (warning) return `ATENÇÃO · ${warning}`;

  const status = String(line.status ?? fallback);
  const total = Number(line.latency_ms ?? 0);
  const stt = Number(line.stt_ms ?? 0);
  const tr = Number(line.translation_ms ?? 0);
  const tts = Number(line.tts_ms ?? 0);
  const dropped = Number(line.dropped_segments ?? 0);
  if (total > 0) {
    const droppedText = dropped > 0 ? ` · perdas ${dropped}` : "";
    return `${status} · ${total} ms (STT ${stt} / Trad ${tr} / TTS ${tts})${droppedText}`;
  }
  return status;
}

function applyStatus(s: StatusResponse) {
  const a = (s.line_a ?? {}) as Record<string, unknown>;
  const b = (s.line_b ?? {}) as Record<string, unknown>;

  const aLevel = Number(a.level ?? 0);
  const bLevel = Number(b.level ?? 0);
  $("a-level").style.width = `${Math.min(100, aLevel * 100)}%`;
  $("b-level").style.width = `${Math.min(100, bLevel * 100)}%`;
  $("a-status").textContent = lineStatusText(a, s.running ? "…" : "parado");
  $("b-status").textContent = lineStatusText(b, s.running ? "…" : "parado");

  const box = $("captions");
  const captions = s.captions ?? [];
  box.innerHTML = captions
    .slice()
    .reverse()
    .map(
      (c) =>
        `<div class="caption"><div class="meta">Linha ${c.line}</div><div>${escapeHtml(c.source)}</div><div><strong>${escapeHtml(c.translation)}</strong></div></div>`,
    )
    .join("");

  $("btn-start").toggleAttribute("disabled", s.running);
  $("btn-stop").toggleAttribute("disabled", !s.running);

  const lineErrors = [a.error, b.error].filter(Boolean).map(String);
  if (s.error) showError(s.error);
  else if (lineErrors.length) showError(lineErrors.join(" | "));
  else showError(null);
}

function escapeHtml(s: string) {
  return s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

async function startSession() {
  showError(null);
  const body = {
    mode: $<HTMLSelectElement>("mode").value,
    line_a: linePayload("a"),
    line_b: linePayload("b"),
  };
  try {
    const s = await engine<StatusResponse>("POST", "/session/start", body);
    applyStatus(s);
    startPolling();
  } catch (e) {
    showError(String(e));
  }
}

async function stopSession() {
  try {
    const s = await engine<StatusResponse>("POST", "/session/stop");
    applyStatus(s);
  } catch (e) {
    showError(String(e));
  }
}

function startPolling() {
  if (pollTimer != null) window.clearInterval(pollTimer);
  pollTimer = window.setInterval(async () => {
    try {
      const s = await engine<StatusResponse>("GET", "/status");
      applyStatus(s);
      if (!s.running && pollTimer != null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    } catch {
      /* ignore transient */
    }
  }, 400);
}

window.addEventListener("DOMContentLoaded", async () => {
  $("btn-refresh").addEventListener("click", async () => {
    try {
      await refreshDevices();
      showError(null);
    } catch (e) {
      showError(String(e));
    }
  });
  $("btn-suggest").addEventListener("click", () => {
    applySuggestions();
    showError(null);
  });
  $("show-technical").addEventListener("change", () => populateFromCache());
  for (const id of ["a-input", "a-output", "b-input", "b-output"]) {
    $<HTMLSelectElement>(id).addEventListener("change", () => updatePickHint(id));
  }
  $("btn-start").addEventListener("click", () => void startSession());
  $("btn-stop").addEventListener("click", () => void stopSession());

  const ok = await checkHealth();
  if (ok) {
    try {
      await refreshDevices();
    } catch (e) {
      showError(String(e));
    }
  }
  window.setInterval(() => void checkHealth(), 3000);
});
