import { invoke } from "@tauri-apps/api/core";
import "./styles.css";

const status = document.querySelector<HTMLParagraphElement>("#status");

function setStatus(message: string) {
  if (status) status.textContent = message;
}

async function start() {
  if (!("__TAURI_INTERNALS__" in window)) {
    setStatus("请通过 Tauri 桌面命令启动，浏览器预览不会启动本地识别服务。");
    return;
  }
  try {
    const url = await invoke<string>("start_server");
    setStatus("正在打开工作台…");
    window.location.replace(url);
  } catch (error) {
    setStatus(`本地服务启动失败：${String(error)}`);
    document.querySelector(".spinner")?.remove();
  }
}

void start();
