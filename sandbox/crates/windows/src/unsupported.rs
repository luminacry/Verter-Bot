//! Non-Windows builds keep the crate in the workspace without Win32 bindings.

use std::ffi::OsString;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Result, bail};

pub use crate::policy::{Backend, RequestedBackend};

/// Doctor-facing description. On this target nothing is available.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostReport {
    /// Backend `auto` would use.
    pub auto_backend: Option<Backend>,
    /// Human line such as `auto: none`.
    pub auto_line: String,
    /// Whether an `AppContainer` can be created.
    pub appcontainer_line: String,
    /// Offline sandbox user.
    pub isolated_offline_line: String,
    /// Online sandbox user.
    pub isolated_online_line: String,
    /// States that exec will not run without isolation.
    pub policy_line: String,
}

/// What `execute` should run.
#[derive(Debug, Clone)]
pub struct ExecRequest {
    /// Requested backend.
    pub backend: RequestedBackend,
    /// Program and arguments.
    pub command: Vec<OsString>,
    /// Writable directory.
    pub workspace: PathBuf,
    /// When true, outbound network is requested.
    pub network: bool,
    /// Wall-clock limit.
    pub timeout: Duration,
    /// Job memory limit in bytes.
    pub memory_bytes: u64,
    /// Active process limit.
    pub nproc: u64,
}

/// Result of a sandboxed command.
#[derive(Debug)]
pub struct ExecResult {
    /// Backend that isolated the command.
    pub backend: Backend,
    /// Process exit code, when one was collected.
    pub exit_code: Option<u32>,
    /// True when the wall-clock limit fired.
    pub timed_out: bool,
}

/// Where the sandboxed command's stdout and stderr go.
pub enum OutputCapture<'a> {
    /// Inherit the caller's stdio.
    Inherit,
    /// Pipe both streams. Unused on this target.
    Piped(&'a mut dyn FnMut(bool, &[u8])),
}

/// What `setup` did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SetupOutcome {
    /// Users and the firewall rule were created.
    Installed,
    /// Users and the firewall rule were removed.
    Removed,
    /// The caller is not an administrator.
    NeedsAdmin,
}

/// Always reports that Windows isolation is unavailable.
#[must_use]
pub fn host_report() -> HostReport {
    HostReport {
        auto_backend: None,
        auto_line: "auto: none".to_owned(),
        appcontainer_line: "appcontainer: unavailable on this operating system".to_owned(),
        isolated_offline_line: "isolated offline: unavailable on this operating system".to_owned(),
        isolated_online_line: "isolated online: unavailable on this operating system".to_owned(),
        policy_line: "policy: exec refuses to run without isolation".to_owned(),
    }
}

/// Refuse. This target cannot isolate a process.
///
/// # Errors
///
/// Always returns an error.
pub fn execute(_request: &ExecRequest, _capture: OutputCapture<'_>) -> Result<ExecResult> {
    bail!("Windows sandbox exec is only available on Windows")
}

/// Refuse. Setup mutates a Windows machine.
///
/// # Errors
///
/// Always returns an error.
pub fn setup(_install: bool) -> Result<SetupOutcome> {
    bail!("verter-sandbox setup for the Windows sandbox is only available on Windows")
}

/// Refuse.
///
/// # Errors
///
/// Always returns an error.
pub fn uninstall() -> Result<SetupOutcome> {
    bail!("verter-sandbox setup --uninstall is only available on Windows")
}
