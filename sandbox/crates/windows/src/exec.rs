//! Run a command in a Job Object. Never starts it until isolation is attached.

use std::ffi::OsString;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result, bail};

use crate::policy::{Backend, RequestedBackend, choose};
use crate::util::{AnonymousPipes, StartupStdio};

/// What `execute` should run.
#[derive(Debug, Clone)]
pub struct ExecRequest {
    /// Requested backend.
    pub backend: RequestedBackend,
    /// Program and arguments.
    pub command: Vec<OsString>,
    /// Directory that stays writable.
    pub workspace: PathBuf,
    /// When true, outbound network is requested.
    pub network: bool,
    /// Wall-clock limit. The job is terminated when it expires.
    pub timeout: Duration,
    /// Job memory limit in bytes.
    pub memory_bytes: u64,
    /// Active process limit inside the job.
    pub nproc: u64,
}

/// Where the sandboxed command's stdout and stderr go.
pub enum OutputCapture<'a> {
    /// Inherit the caller's stdio. Used by CLI `exec`.
    Inherit,
    /// Pipe both streams and invoke the callback for each read. stdin is EOF.
    ///
    /// The bool is `true` for stderr.
    Piped(&'a mut dyn FnMut(bool, &[u8])),
}

/// Result of a sandboxed command.
#[derive(Debug)]
pub struct ExecResult {
    /// Backend that isolated the command.
    pub backend: Backend,
    /// Process exit code, when one was collected.
    pub exit_code: Option<u32>,
    /// True when the wall-clock limit fired and the job was terminated.
    pub timed_out: bool,
}

/// Isolate and run `request.command`.
///
/// # Errors
///
/// Returns an error when no backend can isolate the command or the process cannot be created.
/// The command is not resumed in those cases.
pub fn execute(request: &ExecRequest, capture: OutputCapture<'_>) -> Result<ExecResult> {
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
    let backend = choose(request.backend, report.caps, request.network)?;
    let processes = u32::try_from(request.nproc.max(1)).unwrap_or(u32::MAX);
    let memory = usize::try_from(request.memory_bytes.max(1)).unwrap_or(usize::MAX);
    let job = crate::job::create(memory, processes)?;
    let mut pipes = if matches!(capture, OutputCapture::Piped(_)) {
        Some(AnonymousPipes::new()?)
    } else {
        None
    };
    let stdio = pipes
        .as_ref()
        .map_or_else(StartupStdio::inherit, |p| p.child);
    let spawned = match backend {
        Backend::AppContainer => {
            let sid = crate::appcontainer::profile_sid()?;
            crate::acl::grant(&workspace, sid.0)?;
            crate::appcontainer::spawn_suspended(
                &request.command,
                &workspace,
                request.network,
                sid.0,
                stdio,
            )?
        }
        Backend::Restricted => {
            crate::restricted::spawn_suspended(&request.command, &workspace, stdio)?
        }
        Backend::Isolated => {
            crate::isolated::spawn_suspended(&request.command, &workspace, request.network, stdio)?
        }
    };
    if let Some(pipes) = pipes.as_mut() {
        pipes.close_child_ends();
    }
    crate::job::assign_and_resume(&job, spawned.process, spawned.thread)?;
    let (exit_code, timed_out) = match (pipes.as_mut(), capture) {
        (_, OutputCapture::Inherit) => crate::job::wait(spawned.process, &job, request.timeout)?,
        (Some(pipes), OutputCapture::Piped(on_chunk)) => {
            let result = crate::job::wait_polling(spawned.process, &job, request.timeout, || {
                pipes.drain(on_chunk);
            })?;
            pipes.read_to_end(on_chunk);
            result
        }
        (None, OutputCapture::Piped(_)) => {
            bail!("piped stdio missing anonymous pipes")
        }
    };
    Ok(ExecResult {
        backend,
        exit_code,
        timed_out,
    })
}
