//! Small Win32 helpers shared by the backends.

use std::ffi::{OsStr, OsString};
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::ptr;

use anyhow::{Error, Result, bail};
use windows_sys::Win32::Foundation::{
    CloseHandle, FALSE, GetLastError, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE,
    SetHandleInformation, TRUE,
};
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::Storage::FileSystem::ReadFile;
use windows_sys::Win32::System::Console::{
    GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
};
use windows_sys::Win32::System::Pipes::{CreatePipe, PeekNamedPipe};
use windows_sys::Win32::System::Threading::{STARTF_USESTDHANDLES, STARTUPINFOW};

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
    let hint = match code {
        203 => " (ERROR_ENVVAR_NOT_FOUND)",
        _ => "",
    };
    anyhow::anyhow!("{action} failed: Win32 error {code}{hint}")
}

/// Absolute executable path as a wide string.
///
/// Relative names return `None` so `CreateProcess*` can search System32 / PATH / PATHEXT.
/// Passing `"cmd"` as `lpApplicationName` looks for a file literally named `cmd` in the
/// workspace and fails with `ERROR_FILE_NOT_FOUND`.
pub fn application_name(command: &[OsString]) -> Option<Vec<u16>> {
    let exe = command.first()?;
    Path::new(exe).is_absolute().then(|| wide_os(exe))
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

/// Strip the `\\?\` prefix so environment values and `CreateProcess` cwd stay Win32 paths.
#[must_use]
pub fn native_path(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    PathBuf::from(text.strip_prefix(r"\\?\").unwrap_or(text.as_ref()))
}

pub fn environment_block(workspace: &Path) -> Vec<u16> {
    let system_root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_owned());
    let system_drive = std::env::var("SystemDrive").unwrap_or_else(|_| {
        system_root
            .get(..2)
            .filter(|drive| drive.ends_with(':'))
            .unwrap_or("C:")
            .to_owned()
    });
    let path = format!("{system_root}\\System32;{system_root}");
    let workspace = native_path(workspace).to_string_lossy().into_owned();
    let home_path = workspace.get(2..).unwrap_or(workspace.as_str());
    let entries = [
        format!("ALLUSERSPROFILE={system_drive}\\ProgramData"),
        format!("APPDATA={workspace}"),
        format!("ComSpec={system_root}\\System32\\cmd.exe"),
        format!("HOMEDRIVE={system_drive}"),
        format!("HOMEPATH={home_path}"),
        format!("LOCALAPPDATA={workspace}"),
        "OS=Windows_NT".to_owned(),
        format!("PATH={path}"),
        "PATHEXT=.COM;.EXE;.BAT;.CMD".to_owned(),
        format!("ProgramData={system_drive}\\ProgramData"),
        format!("SystemDrive={system_drive}"),
        format!("SystemRoot={system_root}"),
        format!("TEMP={workspace}"),
        format!("TMP={workspace}"),
        "USERDOMAIN=SANDBOX".to_owned(),
        "USERNAME=sandbox".to_owned(),
        format!("USERPROFILE={workspace}"),
        format!("windir={system_root}"),
    ];
    let mut block = Vec::new();
    for entry in &entries {
        block.extend(entry.encode_utf16());
        block.push(0);
    }
    block.push(0);
    block
}

/// Standard handles to install on a suspended child via `STARTF_USESTDHANDLES`.
#[derive(Clone, Copy)]
pub struct StartupStdio {
    /// Child stdin.
    pub input: HANDLE,
    /// Child stdout.
    pub output: HANDLE,
    /// Child stderr.
    pub error: HANDLE,
}

impl StartupStdio {
    /// Inherit this process's standard handles (CLI `exec`).
    #[must_use]
    pub fn inherit() -> Self {
        let (input, output, error) = std_handles();
        Self {
            input,
            output,
            error,
        }
    }

    pub fn apply_to(self, startup: &mut STARTUPINFOW) {
        if usable(self.input) && usable(self.output) && usable(self.error) {
            startup.dwFlags |= STARTF_USESTDHANDLES;
            startup.hStdInput = self.input;
            startup.hStdOutput = self.output;
            startup.hStdError = self.error;
        }
    }
}

/// Anonymous pipes so a `serve` child cannot write onto the NDJSON stdout.
pub struct AnonymousPipes {
    /// Handles the child should inherit.
    pub child: StartupStdio,
    stdout_read: HANDLE,
    stderr_read: HANDLE,
}

impl AnonymousPipes {
    /// Create stdin (EOF), stdout, and stderr pipes.
    ///
    /// # Errors
    ///
    /// Returns an error when a pipe cannot be created.
    pub fn new() -> Result<Self> {
        let (stdin_read, stdin_write) = create_pipe(true, false)?;
        // Parent will not write to child stdin; close the write end so the child sees EOF.
        close_handle(stdin_write);
        let (stdout_read, stdout_write) = create_pipe(false, true)?;
        let (stderr_read, stderr_write) = create_pipe(false, true)?;
        Ok(Self {
            child: StartupStdio {
                input: stdin_read,
                output: stdout_write,
                error: stderr_write,
            },
            stdout_read,
            stderr_read,
        })
    }

    /// Close the parent's copies of the child's pipe ends after `CreateProcessW`.
    pub fn close_child_ends(&mut self) {
        close_handle(self.child.input);
        close_handle(self.child.output);
        close_handle(self.child.error);
        self.child.input = ptr::null_mut();
        self.child.output = ptr::null_mut();
        self.child.error = ptr::null_mut();
    }

    pub fn drain(&self, on_chunk: &mut dyn FnMut(bool, &[u8])) {
        drain_available(self.stdout_read, false, on_chunk);
        drain_available(self.stderr_read, true, on_chunk);
    }

    pub fn read_to_end(&self, on_chunk: &mut dyn FnMut(bool, &[u8])) {
        read_remaining(self.stdout_read, false, on_chunk);
        read_remaining(self.stderr_read, true, on_chunk);
    }
}

impl Drop for AnonymousPipes {
    fn drop(&mut self) {
        self.close_child_ends();
        close_handle(self.stdout_read);
        close_handle(self.stderr_read);
        self.stdout_read = ptr::null_mut();
        self.stderr_read = ptr::null_mut();
    }
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

fn create_pipe(inherit_read: bool, inherit_write: bool) -> Result<(HANDLE, HANDLE)> {
    let mut security = SECURITY_ATTRIBUTES {
        nLength: u32::try_from(size_of::<SECURITY_ATTRIBUTES>()).unwrap_or(0),
        lpSecurityDescriptor: ptr::null_mut(),
        bInheritHandle: TRUE,
    };
    let mut read = ptr::null_mut();
    let mut write = ptr::null_mut();
    // SAFETY: `read` and `write` are live out pointers; `security` is a valid attributes block.
    let created = unsafe { CreatePipe(&raw mut read, &raw mut write, &raw mut security, 0) };
    if created == FALSE {
        bail!(last_error("CreatePipe"));
    }
    if let Err(err) = set_inherit(read, inherit_read) {
        close_handle(read);
        close_handle(write);
        return Err(err);
    }
    if let Err(err) = set_inherit(write, inherit_write) {
        close_handle(read);
        close_handle(write);
        return Err(err);
    }
    Ok((read, write))
}

fn set_inherit(handle: HANDLE, inherit: bool) -> Result<()> {
    let mask = HANDLE_FLAG_INHERIT;
    let flags = if inherit { HANDLE_FLAG_INHERIT } else { 0 };
    // SAFETY: `handle` is an open pipe end from `CreatePipe`.
    let ok = unsafe { SetHandleInformation(handle, mask, flags) };
    if ok == FALSE {
        bail!(last_error("SetHandleInformation"));
    }
    Ok(())
}

fn close_handle(handle: HANDLE) {
    if usable(handle) {
        // SAFETY: `handle` is open and is closed once by the caller.
        unsafe {
            CloseHandle(handle);
        }
    }
}

fn drain_available(handle: HANDLE, is_err: bool, on_chunk: &mut dyn FnMut(bool, &[u8])) {
    if !usable(handle) {
        return;
    }
    loop {
        let mut available = 0_u32;
        // SAFETY: `handle` is the parent's non-inherited read end of an anonymous pipe.
        let peeked = unsafe {
            PeekNamedPipe(
                handle,
                ptr::null_mut(),
                0,
                ptr::null_mut(),
                &raw mut available,
                ptr::null_mut(),
            )
        };
        if peeked == FALSE || available == 0 {
            break;
        }
        let want = available.min(8192);
        let mut buf = vec![0_u8; usize::try_from(want).unwrap_or(8192)];
        let mut read = 0_u32;
        // SAFETY: `buf` has `want` bytes and `read` is a live DWORD out pointer.
        let ok = unsafe {
            ReadFile(
                handle,
                buf.as_mut_ptr().cast(),
                want,
                &raw mut read,
                ptr::null_mut(),
            )
        };
        if ok == FALSE || read == 0 {
            break;
        }
        let n = usize::try_from(read).unwrap_or(0);
        on_chunk(is_err, &buf[..n]);
    }
}

fn read_remaining(handle: HANDLE, is_err: bool, on_chunk: &mut dyn FnMut(bool, &[u8])) {
    if !usable(handle) {
        return;
    }
    let mut buf = [0_u8; 8192];
    loop {
        let mut read = 0_u32;
        // SAFETY: `handle` is the parent's read end; `buf` is a live 8 KiB buffer.
        let ok = unsafe {
            ReadFile(
                handle,
                buf.as_mut_ptr().cast(),
                8192,
                &raw mut read,
                ptr::null_mut(),
            )
        };
        if ok == FALSE || read == 0 {
            break;
        }
        let n = usize::try_from(read).unwrap_or(0);
        on_chunk(is_err, &buf[..n]);
    }
}

pub fn null<T>() -> *const T {
    ptr::null()
}

pub fn null_mut<T>() -> *mut T {
    ptr::null_mut()
}
