// Launch Windows release builds without an extra console window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    samsung_receipt_desktop_lib::run();
}
