//! Run a command in bubblewrap or Landlock. Never falls through to the host.

use std::path::{Path, PathBuf};
use std::process::{Child, ExitStatus};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};

use crate::apply::ApplyOptions;
use crate::probe::{Backend, RequestedBackend, select_backend};

/// What `execute` should run.
#[derive(Debug, Clone)]
pub struct ExecRequest {
    /// Requested backend. `auto` never selects an unsandboxed run.
    pub backend: RequestedBackend,
    /// Program and arguments.
    pub command: Vec<std::ffi::OsString>,
    /// Directory that stays writable inside the sandbox.
    pub workspace: PathBuf,
    /// When true, outbound network is left open.
    pub network: bool,
    /// Wall-clock limit. The process tree is killed when it expires.
    pub timeout: Duration,
    /// `RLIMIT_AS` in bytes.
    pub memory_bytes: u64,
    /// `RLIMIT_NPROC`.
    pub nproc: u64,
    /// `RLIMIT_CPU` in seconds.
    pub cpu_seconds: u64,
    /// Extra environment variables copied into the sandbox.
    pub env: Vec<(std::ffi::OsString, std::ffi::OsString)>,
}

/// Result of a sandboxed command.
#[derive(Debug)]
pub struct ExecResult {
    /// Backend that actually isolated the command.
    pub backend: Backend,
    /// Child exit status. After a timeout this is the status from the kill.
    pub status: ExitStatus,
    /// True when the wall-clock limit fired and the tree was killed.
    pub timed_out: bool,
}

/// Isolate and run `request.command`.
///
/// # Errors
///
/// Returns an error when no backend can isolate the command, the workspace is unusable,
/// or the sandbox process cannot be spawned. The user command is not started in those cases.
pub fn execute(helper: &Path, request: &ExecRequest) -> Result<ExecResult> {
    if request.command.is_empty() {
        bail!("command is empty");
    }
    if request.timeout.is_zero() {
        bail!("timeout must be greater than zero");
    }
    let workspace = request
        .workspace
        .canonicalize()
        .with_context(|| format!("workspace {}", request.workspace.display()))?;
    if !workspace.is_dir() {
        bail!("workspace {} is not a directory", workspace.display());
    }
    let report = crate::probe::host_report();
    let backend = select_backend(request.backend, report)?;
    let opts = ApplyOptions {
        mode: backend,
        workspace,
        network: request.network,
        memory_bytes: request.memory_bytes,
        nproc: request.nproc,
        cpu_seconds: request.cpu_seconds,
        env: request.env.clone(),
        command: request.command.clone(),
    };
    let mut child = match backend {
        Backend::Bubblewrap => {
            let bwrap = crate::bubblewrap::find_bwrap()
                .context("bubblewrap was available during probe but is gone")?;
            crate::bubblewrap::spawn(&bwrap, helper, &opts)?
        }
        Backend::Landlock => spawn_landlock(helper, &opts)?,
    };
    let kill_group = matches!(backend, Backend::Landlock);
    let (status, timed_out) = wait_child(&mut child, request.timeout, kill_group)?;
    Ok(ExecResult {
        backend,
        status,
        timed_out,
    })
}

fn spawn_landlock(helper: &Path, opts: &ApplyOptions) -> Result<Child> {
    let mut command = std::process::Command::new(helper);
    command
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .args(crate::apply::helper_args(opts));
    command.spawn().context("spawn landlock helper")
}

/// Wait until `child` exits or `timeout` elapses, then kill it.
///
/// # Errors
///
/// Returns an error if waiting on the child fails.
pub fn wait_child(
    child: &mut Child,
    timeout: Duration,
    kill_group: bool,
) -> Result<(ExitStatus, bool)> {
    let started = Instant::now();
    loop {
        if let Some(status) = child.try_wait().context("wait for sandbox")? {
            return Ok((status, false));
        }
        if started.elapsed() >= timeout {
            terminate(child, kill_group);
            let status = child.wait().context("reap sandbox")?;
            return Ok((status, true));
        }
        thread::sleep(Duration::from_millis(2));
    }
}

fn terminate(child: &mut Child, kill_group: bool) {
    if kill_group {
        if let Ok(raw) = i32::try_from(child.id()) {
            let pid = nix::unistd::Pid::from_raw(raw);
            let _ = nix::sys::signal::killpg(pid, nix::sys::signal::Signal::SIGKILL);
        }
    }
    let _ = child.kill();
}
