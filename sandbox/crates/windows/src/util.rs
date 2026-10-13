//! Small Win32 helpers shared by the backends.

use std::ffi::{OsStr, OsString};
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use std::ptr;

use anyhow::Error;
use windows_sys::Win32::Foundation::{
    GetLastError, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE, SetHandleInformation,
};
use windows_sys::Win32::System::Console::{
    GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
};
use windows_sys::Win32::System::Threading::STARTUPINFOW;

/// `FILE_ALL_ACCESS` from `winnt.h`.
pub const FILE_ALL_ACCESS: u32 = 0x001F_01FF;

/// `SE_GROUP_ENABLED` from `winnt.h`.
pub const SE_GROUP_ENABLED: u32 = 0x0000_0004;

pub fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}

pub fn wide_os(text: &OsStr) -> Vec<u16> {
    text.encode_wide().chain(Some(0)).collect()
}

pub fn last_error(action: &str) -> Error {
    // SAFETY: `GetLastError` reads this thread's last-error slot and has no other precondition.
    let code = unsafe { GetLastError() };
    anyhow::anyhow!("{action} failed: Win32 error {code}")
}

pub fn command_line(args: &[OsString]) -> Vec<u16> {
    let mut line = String::new();
    for (index, arg) in args.iter().enumerate() {
        if index > 0 {
            line.push(' ');
        }
        line.push_str(&quote_arg(arg));
    }
    wide(&line)
}

fn quote_arg(arg: &OsStr) -> String {
    let text = arg.to_string_lossy();
    if !text.is_empty() && !text.chars().any(|ch| matches!(ch, ' ' | '\t' | '"')) {
        return text.into_owned();
    }
    let mut out = String::from("\"");
    let mut slashes = 0;
    for ch in text.chars() {
        if ch == '\\' {
            slashes += 1;
            continue;
        }
        if ch == '"' {
            out.push_str(&"\\".repeat(slashes * 2 + 1));
            out.push('"');
            slashes = 0;
            continue;
        }
        out.push_str(&"\\".repeat(slashes));
        slashes = 0;
        out.push(ch);
    }
    out.push_str(&"\\".repeat(slashes * 2));
    out.push('"');
    out
}

pub fn environment_block(workspace: &Path) -> Vec<u16> {
    let system_root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_owned());
    let path = format!("{system_root}\\System32;{system_root}");
    let workspace = workspace.display().to_string();
    let entries = [
        format!("PATH={path}"),
        "PATHEXT=.COM;.EXE;.BAT;.CMD".to_owned(),
        format!("SystemRoot={system_root}"),
        format!("TEMP={workspace}"),
        format!("TMP={workspace}"),
        format!("USERPROFILE={workspace}"),
    ];
    let mut block = Vec::new();
    for entry in &entries {
        block.extend(entry.encode_utf16());
        block.push(0);
    }
    block.push(0);
    block
}

pub fn startup_with_stdio() -> STARTUPINFOW {
    let (input, output, error) = std_handles();
    let mut startup = STARTUPINFOW {
        cb: u32::try_from(size_of::<STARTUPINFOW>()).unwrap_or(0),
        ..STARTUPINFOW::default()
    };
    if usable(input) && usable(output) && usable(error) {
        startup.dwFlags |= windows_sys::Win32::System::Threading::STARTF_USESTDHANDLES;
        startup.hStdInput = input;
        startup.hStdOutput = output;
        startup.hStdError = error;
    }
    startup
}

fn std_handles() -> (HANDLE, HANDLE, HANDLE) {
    // SAFETY: `GetStdHandle` returns the process standard handles. `SetHandleInformation`
    // only sets the inherit bit on those handles so `CreateProcessW` can pass them on.
    unsafe {
        let input = GetStdHandle(STD_INPUT_HANDLE);
        let output = GetStdHandle(STD_OUTPUT_HANDLE);
        let error = GetStdHandle(STD_ERROR_HANDLE);
        for handle in [input, output, error] {
            if usable(handle) {
                SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT);
            }
        }
        (input, output, error)
    }
}

fn usable(handle: HANDLE) -> bool {
    !handle.is_null() && handle != INVALID_HANDLE_VALUE
}

pub fn null<T>() -> *const T {
    ptr::null()
}

pub fn null_mut<T>() -> *mut T {
    ptr::null_mut()
}
