//! Resource limits applied in the sandboxed process before exec.

use anyhow::{Context, Result};
use nix::sys::resource::{Resource, setrlimit};

use crate::apply::ApplyOptions;

/// Install address-space, process-count, CPU, and core-dump limits.
///
/// # Errors
///
/// Returns an error if `setrlimit` fails. The caller must not exec the user command.
pub fn apply(opts: &ApplyOptions) -> Result<()> {
    setrlimit(Resource::RLIMIT_AS, opts.memory_bytes, opts.memory_bytes)
        .context("set RLIMIT_AS")?;
    setrlimit(Resource::RLIMIT_NPROC, opts.nproc, opts.nproc).context("set RLIMIT_NPROC")?;
    setrlimit(Resource::RLIMIT_CPU, opts.cpu_seconds, opts.cpu_seconds)
        .context("set RLIMIT_CPU")?;
    setrlimit(Resource::RLIMIT_CORE, 0, 0).context("set RLIMIT_CORE")?;
    Ok(())
}
