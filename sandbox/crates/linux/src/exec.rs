//! Run a command in bubblewrap or Landlock. Never falls through to the host.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, ExitStatus, Stdio};
use std::sync::mpsc;
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

/// Where the sandboxed command's stdout and stderr go.
pub enum OutputCapture<'a> {
    /// Inherit the caller's stdio. Used by CLI `exec`.
    Inherit,
    /// Pipe both streams and invoke the callback for each read. stdin is closed.
    ///
    /// The bool is `true` for stderr.
    Piped(&'a mut dyn FnMut(bool, &[u8])),
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
pub fn execute(
    helper: &Path,
    request: &ExecRequest,
    capture: OutputCapture<'_>,
) -> Result<ExecResult> {
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
    let piped = matches!(capture, OutputCapture::Piped(_));
    let mut child = match backend {
        Backend::Bubblewrap => {
            let bwrap = crate::bubblewrap::find_bwrap()
                .context("bubblewrap was available during probe but is gone")?;
            crate::bubblewrap::spawn(&bwrap, helper, &opts, piped)?
        }
        Backend::Landlock => spawn_landlock(helper, &opts, piped)?,
    };
    let kill_group = matches!(backend, Backend::Landlock);
    let (status, timed_out) = match capture {
        OutputCapture::Inherit => wait_child(&mut child, request.timeout, kill_group)?,
        OutputCapture::Piped(on_chunk) => {
            wait_captured(&mut child, request.timeout, kill_group, on_chunk)?
        }
    };
    Ok(ExecResult {
        backend,
        status,
        timed_out,
    })
}

fn spawn_landlock(helper: &Path, opts: &ApplyOptions, piped: bool) -> Result<Child> {
    let mut command = std::process::Command::new(helper);
    command
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .args(crate::apply::helper_args(opts));
    apply_stdio(&mut command, piped);
    command.spawn().context("spawn landlock helper")
}

pub(crate) fn apply_stdio(command: &mut std::process::Command, piped: bool) {
    if piped {
        command
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
    }
}

fn wait_captured(
    child: &mut Child,
    timeout: Duration,
    kill_group: bool,
    on_chunk: &mut dyn FnMut(bool, &[u8]),
) -> Result<(ExitStatus, bool)> {
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let (tx, rx) = mpsc::channel();
    thread::scope(|scope| {
        spawn_reader(scope, stdout, false, tx.clone());
        spawn_reader(scope, stderr, true, tx);
        let started = Instant::now();
        loop {
            while let Ok((is_err, chunk)) = rx.try_recv() {
                on_chunk(is_err, &chunk);
            }
            if let Some(status) = child.try_wait().context("wait for sandbox")? {
                drain_output(&rx, on_chunk);
                return Ok((status, false));
            }
            if started.elapsed() >= timeout {
                terminate(child, kill_group);
                let status = child.wait().context("reap sandbox")?;
                drain_output(&rx, on_chunk);
                return Ok((status, true));
            }
            thread::sleep(Duration::from_millis(2));
        }
    })
}

fn drain_output(rx: &mpsc::Receiver<(bool, Vec<u8>)>, on_chunk: &mut dyn FnMut(bool, &[u8])) {
    while let Ok((is_err, chunk)) = rx.recv() {
        on_chunk(is_err, &chunk);
    }
}

fn spawn_reader<'scope, R>(
    scope: &'scope thread::Scope<'scope, '_>,
    stream: Option<R>,
    is_err: bool,
    tx: mpsc::Sender<(bool, Vec<u8>)>,
) where
    R: Read + Send + 'scope,
{
    let Some(mut stream) = stream else {
        return;
    };
    scope.spawn(move || {
        let mut buf = [0_u8; 8192];
        loop {
            match stream.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if tx.send((is_err, buf[..n].to_vec())).is_err() {
                        break;
                    }
                }
            }
        }
    });
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
