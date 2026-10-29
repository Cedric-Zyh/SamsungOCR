use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

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
        resource_dir
            .join("resources")
            .join("SamsungReceipt")
            .join(executable),
        // Older installers preserved the parent component as Tauri's `_up_` directory.
        resource_dir
            .join("_up_")
            .join("resources")
            .join("SamsungReceipt")
            .join(executable),
        resource_dir.join(executable),
        resource_dir.join("server").join(executable),
    ];
    Ok(candidates.into_iter().find(|path| path.is_file()))
}

fn configure_packaged_paths(
    command: &mut Command,
    desktop_executable: &Path,
    local_app_data: Option<&OsStr>,
) -> Result<(), String> {
    let install_dir = desktop_executable
        .parent()
        .ok_or_else(|| "无法确定软件安装目录。".to_string())?;
    let models = install_dir.join("models");
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let probe = models.join(format!(".write-check-{}-{nonce}", std::process::id()));
    let writable = (|| -> std::io::Result<()> {
        fs::create_dir_all(&models)?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&probe)?;
        file.write_all(b"model-cache")
    })();
    let _ = fs::remove_file(&probe);
    writable.map_err(|error| {
        format!(
            "模型目录无法写入：{}。请选择可写的安装位置或调整该文件夹权限。（{error}）",
            models.display()
        )
    })?;
    // PaddleX reads its cache root at import time. Override both names before
    // the service starts so all native/ONNX/orientation models share one root.
    command
        .env("SAMSUNG_RECEIPT_INSTALL_DIR", install_dir)
        .env("PADDLE_MODEL_HOME", &models)
        .env("PADDLE_PDX_CACHE_HOME", &models);
    if let Some(local_app_data) = local_app_data {
        command.env(
            "SAMSUNG_RECEIPT_DATA_DIR",
            PathBuf::from(local_app_data).join("SamsungReceipt"),
        );
    }
    Ok(())
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

    if !cfg!(debug_assertions) && cfg!(target_os = "windows") {
        let desktop_executable =
            std::env::current_exe().map_err(|error| format!("读取软件安装位置失败：{error}"))?;
        configure_packaged_paths(
            &mut command,
            &desktop_executable,
            std::env::var_os("LOCALAPPDATA").as_deref(),
        )?;
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
                if process
                    .try_wait()
                    .map_err(|error| error.to_string())?
                    .is_some()
                {
                    drop(slot);
                    stop_child(&state);
                    return Err(
                        "本地识别服务提前退出，请查看日志或检查 Python/OCR 依赖。".to_string()
                    );
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

#[cfg(test)]
mod tests {
    use super::*;

    fn temporary_root() -> PathBuf {
        std::env::temp_dir().join(format!(
            "samsung-model-path-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }

    #[test]
    fn all_models_follow_the_desktop_install_directory() {
        let root = temporary_root();
        let install = root.join("自选安装位置 with spaces");
        let local = root.join("local-data");
        let mut command = Command::new("unused");
        command.env("PADDLE_PDX_CACHE_HOME", "old-cache");
        configure_packaged_paths(
            &mut command,
            &install.join("三星回单核验台.exe"),
            Some(local.as_os_str()),
        )
        .unwrap();
        let env: std::collections::HashMap<_, _> = command.get_envs().collect();
        assert_eq!(
            env[OsStr::new("SAMSUNG_RECEIPT_INSTALL_DIR")],
            Some(install.as_os_str())
        );
        for key in ["PADDLE_MODEL_HOME", "PADDLE_PDX_CACHE_HOME"] {
            assert_eq!(
                env[OsStr::new(key)],
                Some(install.join("models").as_os_str())
            );
        }
        assert_eq!(
            env[OsStr::new("SAMSUNG_RECEIPT_DATA_DIR")],
            Some(local.join("SamsungReceipt").as_os_str())
        );
        assert_eq!(fs::read_dir(install.join("models")).unwrap().count(), 0);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_local_app_data_does_not_change_the_model_location() {
        let root = temporary_root();
        let mut command = Command::new("unused");
        configure_packaged_paths(&mut command, &root.join("app.exe"), None).unwrap();
        assert!(root.join("models").is_dir());
        assert!(!command
            .get_envs()
            .any(|(key, _)| key == "SAMSUNG_RECEIPT_DATA_DIR"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unusable_model_directory_reports_the_selected_path() {
        let root = temporary_root();
        fs::create_dir_all(&root).unwrap();
        let models = root.join("models");
        fs::write(&models, b"occupied").unwrap();
        let mut command = Command::new("unused");
        let error =
            configure_packaged_paths(&mut command, &root.join("app.exe"), None).unwrap_err();
        assert!(error.contains(&models.display().to_string()));
        assert_eq!(fs::read(&models).unwrap(), b"occupied");
        assert!(!command
            .get_envs()
            .any(|(key, _)| key == "PADDLE_MODEL_HOME"));
        fs::remove_dir_all(root).unwrap();
    }
}
