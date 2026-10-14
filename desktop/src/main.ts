import { invoke } from "@tauri-apps/api/core";

const status = document.querySelector<HTMLParagraphElement>("#status");
const hint = document.querySelector<HTMLParagraphElement>("#hint");
const launch = document.querySelector<HTMLElement>(".launch");
const retry = document.querySelector<HTMLButtonElement>("#retry");
const details = document.querySelector<HTMLDetailsElement>("#error-details");
const errorMessage = document.querySelector<HTMLParagraphElement>("#error-message");
let starting = false;

function setStatus(state: string, message: string, description: string) {
  if (launch) launch.dataset.state = state;
  if (status) status.textContent = message;
  if (hint) hint.textContent = description;
}

async function start() {
  if (starting) return;
  if (!("__TAURI_INTERNALS__" in window)) {
    setStatus("preview", "启动页预览", "请打开桌面应用，开始回单核验");
    return;
  }
  starting = true;
  if (retry) retry.hidden = true;
  if (details) { details.hidden = true; details.open = false; }
  setStatus("loading", "正在准备工作空间", "正在启动识别服务，请稍候");
  try {
    const url = await invoke<string>("start_server");
    setStatus("ready", "工作空间已就绪", "正在打开工作台…");
    window.location.replace(url);
  } catch (error) {
    setStatus("error", "暂时无法启动", "识别服务未能就绪，请重试");
    if (errorMessage) errorMessage.textContent = String(error);
    if (details) details.hidden = false;
    if (retry) retry.hidden = false;
  } finally {
    starting = false;
  }
}

retry?.addEventListener("click", () => void start());
void start();
