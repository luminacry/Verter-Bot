//! Seccomp filter: allow by default, reject ptrace, `io_uring`, and outbound sends.

use std::collections::BTreeMap;
use std::convert::TryInto;

use anyhow::{Context, Result, anyhow};
use seccompiler::{SeccompAction, SeccompFilter, TargetArch, apply_filter};

/// Install the filter in the calling thread.
///
/// # Errors
///
/// Returns an error if the filter cannot be compiled or installed. The caller must not exec.
pub fn install(deny_network: bool) -> Result<()> {
    let arch = TargetArch::try_from(std::env::consts::ARCH)
        .map_err(|err| anyhow!("seccomp architecture: {err}"))?;
    let mut rules: BTreeMap<i64, Vec<seccompiler::SeccompRule>> = BTreeMap::new();
    for number in denied_syscalls(deny_network) {
        rules.insert(number, Vec::new());
    }
    let filter = SeccompFilter::new(rules, SeccompAction::Allow, SeccompAction::Errno(1), arch)
        .context("build seccomp filter")?;
    let program = TryInto::<seccompiler::BpfProgram>::try_into(filter)
        .map_err(|err| anyhow!("compile seccomp bpf: {err}"))?;
    apply_filter(&program).context("apply seccomp filter")?;
    Ok(())
}

fn denied_syscalls(deny_network: bool) -> Vec<i64> {
    let mut syscalls = vec![
        nix::libc::SYS_ptrace,
        nix::libc::SYS_io_uring_setup,
        nix::libc::SYS_io_uring_enter,
        nix::libc::SYS_io_uring_register,
    ];
    if deny_network {
        syscalls.extend([
            nix::libc::SYS_connect,
            nix::libc::SYS_sendto,
            nix::libc::SYS_sendmsg,
            nix::libc::SYS_sendmmsg,
        ]);
    }
    syscalls
}
