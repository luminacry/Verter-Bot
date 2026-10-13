//! Bubblewrap argv: namespaces, a read-only system root, and a writable workspace.

use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result, bail};

use crate::apply::{ApplyOptions, helper_args};

/// Spawn `bwrap` around the helper, which applies rlimits and seccomp.
///
/// # Errors
///
/// Returns an error when `bwrap` cannot be spawned.
pub fn spawn(
    bwrap: &Path,
    helper: &Path,
    opts: &ApplyOptions,
    piped: bool,
) -> Result<std::process::Child> {
    if !bwrap.is_file() {
        bail!("bubblewrap executable is missing");
    }
    let mut command = Command::new(bwrap);
    command
        .env_clear()
        .arg("--unshare-user")
        .arg("--unshare-pid")
        .arg("--unshare-ipc")
        .arg("--unshare-uts")
        .arg("--die-with-parent")
        .arg("--new-session");
    if !opts.network {
        command.arg("--unshare-net");
    }
    mount_readonly(&mut command);
    command
        .arg("--proc")
        .arg("/proc")
        .arg("--dev")
        .arg("/dev")
        .arg("--tmpfs")
        .arg("/tmp")
        .arg("--bind")
        .arg(&opts.workspace)
        .arg(&opts.workspace)
        .arg("--chdir")
        .arg(&opts.workspace)
        .arg("--ro-bind")
        .arg(helper)
        .arg("/sandbox-apply")
        .arg("--clearenv")
        .arg("--setenv")
        .arg("PATH")
        .arg("/usr/bin:/bin")
        .arg("--setenv")
        .arg("HOME")
        .arg(&opts.workspace)
        .arg("--setenv")
        .arg("TMPDIR")
        .arg(&opts.workspace);
    for (key, value) in &opts.env {
        command.arg("--setenv").arg(key).arg(value);
    }
    command.arg("--");
    let mut inner = opts.clone();
    inner.mode = crate::probe::Backend::Bubblewrap;
    command.arg("/sandbox-apply").args(helper_args(&inner));
    crate::exec::apply_stdio(&mut command, piped);
    command.spawn().context("spawn bubblewrap")
}

pub fn mount_readonly(command: &mut Command) {
    for path in ["/usr", "/etc", "/bin", "/lib", "/lib64"] {
        mount_one(command, path);
    }
}

fn mount_one(command: &mut Command, path: &str) {
    let host = Path::new(path);
    if host.is_symlink() {
        if let Ok(target) = host.read_link() {
            command.arg("--symlink").arg(target).arg(path);
        }
        return;
    }
    if host.exists() {
        command.arg("--ro-bind").arg(path).arg(path);
    }
}

/// Locate `bwrap` on `PATH`.
#[must_use]
pub fn find_bwrap() -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join("bwrap"))
        .find(|candidate| {
            candidate.is_file()
                && candidate.metadata().is_ok_and(|meta| {
                    std::os::unix::fs::PermissionsExt::mode(&meta.permissions()) & 0o111 != 0
                })
        })
}
