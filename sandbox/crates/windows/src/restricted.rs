//! Restricted token. Privileges are removed; outbound network is not blocked.

use std::ptr;

use anyhow::{Result, bail};
use windows_sys::Win32::Foundation::{CloseHandle, FALSE, HANDLE};
use windows_sys::Win32::Security::{
    CreateRestrictedToken, DISABLE_MAX_PRIVILEGE, TOKEN_ASSIGN_PRIMARY, TOKEN_DUPLICATE,
    TOKEN_QUERY,
};
use windows_sys::Win32::System::Threading::{
    CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, CreateProcessWithTokenW,
    GetCurrentProcess, OpenProcessToken, PROCESS_INFORMATION, STARTUPINFOW,
};

use crate::appcontainer::Spawned;
use crate::util::{StartupStdio, command_line, environment_block, last_error, null};

struct Token(HANDLE);

impl Drop for Token {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: the handle was opened or created for this token and is closed once.
            unsafe {
                CloseHandle(self.0);
            }
            self.0 = ptr::null_mut();
        }
    }
}

/// Create a restricted token from the current process.
///
/// # Errors
///
/// Returns an error when the token cannot be opened or restricted.
fn restricted_token() -> Result<Token> {
    let mut current = ptr::null_mut();
    let access = TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY;
    // SAFETY: `GetCurrentProcess` is the pseudo-handle and `current` is a live out pointer.
    let opened = unsafe { OpenProcessToken(GetCurrentProcess(), access, &raw mut current) };
    if opened == FALSE {
        bail!(last_error("OpenProcessToken"));
    }
    let current = Token(current);
    let mut restricted = ptr::null_mut();
    // SAFETY: `current.0` is open. Empty SID and privilege lists with `DISABLE_MAX_PRIVILEGE`
    // drop privileges without adding a restricting SID.
    let created = unsafe {
        CreateRestrictedToken(
            current.0,
            DISABLE_MAX_PRIVILEGE,
            0,
            null(),
            0,
            null(),
            0,
            null(),
            &raw mut restricted,
        )
    };
    if created == FALSE {
        bail!(last_error("CreateRestrictedToken"));
    }
    Ok(Token(restricted))
}

/// Start `command` suspended with a restricted token.
///
/// # Errors
///
/// Returns an error when the token or process cannot be created.
pub fn spawn_suspended(
    command: &[std::ffi::OsString],
    workspace: &std::path::Path,
    stdio: StartupStdio,
) -> Result<Spawned> {
    let token = restricted_token()?;
    let app = crate::util::application_name(command);
    let mut cmdline = command_line(command);
    let env = environment_block(workspace);
    let cwd = crate::util::wide_os(crate::util::native_path(workspace).as_os_str());
    let mut startup = STARTUPINFOW {
        cb: u32::try_from(size_of::<STARTUPINFOW>()).unwrap_or(0),
        ..STARTUPINFOW::default()
    };
    stdio.apply_to(&mut startup);
    let mut info = PROCESS_INFORMATION::default();
    let flags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW;
    // SAFETY: `token.0` is open and the command, environment, directory, and startup info are live.
    let created = unsafe {
        CreateProcessWithTokenW(
            token.0,
            0,
            app.as_ptr(),
            cmdline.as_mut_ptr(),
            flags,
            env.as_ptr().cast(),
            cwd.as_ptr(),
            &raw const startup,
            &raw mut info,
        )
    };
    if created == FALSE {
        bail!(last_error("CreateProcessWithTokenW"));
    }
    Ok(Spawned {
        process: info.hProcess,
        thread: info.hThread,
    })
}

/// Probe whether a restricted token can be created.
#[must_use]
pub fn available() -> bool {
    restricted_token().is_ok()
}
