use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, State, WindowEvent};

#[derive(Default)]
struct ServerState {
    child: Mutex<Option<Child>>,
    port: Mutex<Option<u16>>,
}

fn project_root() -> PathBuf {
    if let Ok(value) = std::env::var("SAMSUNG_RECEIPT_PROJECT_ROOT") {
        let path = PathBuf::from(value);
        if path.is_dir() {
            return path;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn free_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("无法分配本地服务端口：{error}"))?
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("读取本地服务端口失败：{error}"))
}

fn packaged_server(app: &AppHandle) -> Result<Option<PathBuf>, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("读取应用资源目录失败：{error}"))?;
    let executable = if cfg!(target_os = "windows") {
        "SamsungReceipt.exe"
    } else {
        "SamsungReceipt"
    };
    let candidates = [
        resource_dir.join("SamsungReceipt").join(executable),
        resource_dir.join("resources").join("SamsungReceipt").join(executable),
        // Older installers preserved the parent component as Tauri's `_up_` directory.
        resource_dir.join("_up_").join("resources").join("SamsungReceipt").join(executable),
        resource_dir.join(executable),
        resource_dir.join("server").join(executable),
    ];
    Ok(candidates.into_iter().find(|path| path.is_file()))
}

fn server_command(app: &AppHandle, port: u16) -> Result<(Command, PathBuf), String> {
    let root = project_root();
    let mut command;
    let current_dir;

    if !cfg!(debug_assertions) {
        if let Some(server) = packaged_server(app)? {
            current_dir = server
                .parent()
                .map(Path::to_path_buf)
                .unwrap_or_else(|| root.clone());
            command = Command::new(&server);
        } else {
            return Err("安装包中没有找到 SamsungReceipt 服务程序。请重新构建安装包。".to_string());
        }
    } else {
        let python = std::env::var_os("SAMSUNG_RECEIPT_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if cfg!(target_os = "windows") {
                    PathBuf::from("python")
                } else {
                    PathBuf::from("python3")
                }
            });
        current_dir = root.clone();
        command = Command::new(python);
        command.arg(root.join("app.py"));
    }

    command
        .current_dir(&current_dir)
        .env("APP_HOST", "127.0.0.1")
        .env("APP_PORT", port.to_string())
        .env("FLASK_DEBUG", "0")
        .stdin(Stdio::null());

    if !cfg!(debug_assertions) {
        if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA") {
            let data_dir = PathBuf::from(local_app_data).join("SamsungReceipt");
            command
                .env("SAMSUNG_RECEIPT_DATA_DIR", &data_dir)
                .env("PADDLE_MODEL_HOME", data_dir.join("models"));
        }
    }

    if cfg!(debug_assertions) {
        command.stdout(Stdio::inherit()).stderr(Stdio::inherit());
    } else {
        command.stdout(Stdio::null()).stderr(Stdio::null());
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    Ok((command, current_dir))
}

fn server_ready(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(350)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.write_all(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
    let mut response = [0_u8; 128];
    let size = stream.read(&mut response).unwrap_or(0);
    let head = String::from_utf8_lossy(&response[..size]);
    head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
}

fn stop_child(state: &ServerState) {
    if let Ok(mut child) = state.child.lock() {
        if let Some(mut process) = child.take() {
            let _ = process.kill();
            let _ = process.wait();
        }
    }
    if let Ok(mut port) = state.port.lock() {
        *port = None;
    }
}

#[tauri::command]
fn start_server(app: AppHandle, state: State<'_, ServerState>) -> Result<String, String> {
    if let (Ok(child), Ok(port)) = (state.child.lock(), state.port.lock()) {
        if child.is_some() {
            if let Some(port) = *port {
                return Ok(format!("http://127.0.0.1:{port}/"));
            }
        }
    }

    let port = free_port()?;
    let (mut command, _) = server_command(&app, port)?;
    let child = command
        .spawn()
        .map_err(|error| format!("启动本地识别服务失败：{error}"))?;

    if let Ok(mut slot) = state.child.lock() {
        *slot = Some(child);
    }
    if let Ok(mut slot) = state.port.lock() {
        *slot = Some(port);
    }

    let deadline = Instant::now() + Duration::from_secs(60);
    while Instant::now() < deadline {
        if server_ready(port) {
            return Ok(format!("http://127.0.0.1:{port}/"));
        }
        if let Ok(mut slot) = state.child.lock() {
            if let Some(process) = slot.as_mut() {
                if process.try_wait().map_err(|error| error.to_string())?.is_some() {
                    drop(slot);
                    stop_child(&state);
                    return Err("本地识别服务提前退出，请查看日志或检查 Python/OCR 依赖。".to_string());
                }
            }
        }
        thread::sleep(Duration::from_millis(250));
    }

    stop_child(&state);
    Err("本地识别服务在 60 秒内没有准备完成。".to_string())
}

#[tauri::command]
fn stop_server(state: State<'_, ServerState>) {
    stop_child(&state);
}

pub fn run() {
    tauri::Builder::default()
        .manage(ServerState::default())
        .invoke_handler(tauri::generate_handler![start_server, stop_server])
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                let state = window.app_handle().state::<ServerState>();
                stop_child(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("运行三星回单核验台桌面端失败");
}
