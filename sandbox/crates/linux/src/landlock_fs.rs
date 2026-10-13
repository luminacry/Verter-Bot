//! Landlock filesystem policy: system directories read-only, workspace read-write.

use std::fs::OpenOptions;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

use anyhow::{Context, Result, bail};
use landlock::{
    ABI, Access, AccessFs, BitFlags, PathBeneath, RestrictionStatus, Ruleset, RulesetAttr,
    RulesetCreated, RulesetCreatedAttr, RulesetStatus,
};

/// ABI this prototype requests. Best-effort mode downgrades on older kernels.
const ABI_REQUESTED: ABI = ABI::V3;

/// Restrict the calling thread.
///
/// # Errors
///
/// Returns an error when Landlock cannot be enforced. The caller must not exec.
pub fn enforce(workspace: &Path) -> Result<RestrictionStatus> {
    let read = AccessFs::from_read(ABI_REQUESTED);
    let all = AccessFs::from_all(ABI_REQUESTED);
    let mut ruleset = Ruleset::default()
        .handle_access(all)
        .context("landlock handle access")?
        .create()
        .context("landlock create ruleset")?
        .add_rules(landlock::path_beneath_rules(read_only_roots(), read))
        .context("landlock read-only rules")?;
    ruleset = add_symlink_roots(ruleset, read)?;
    let status = ruleset
        .add_rules(landlock::path_beneath_rules([workspace], all))
        .context("landlock workspace rule")?
        .restrict_self()
        .context("landlock restrict_self")?;
    if matches!(status.ruleset, RulesetStatus::NotEnforced) {
        bail!("landlock is not enforced on this kernel");
    }
    Ok(status)
}

/// Probe whether a ruleset can be enforced, without execing a user command.
///
/// # Errors
///
/// Returns an error when the probe ruleset cannot be created.
pub fn probe() -> Result<RulesetStatus> {
    let read = AccessFs::from_read(ABI_REQUESTED);
    let all = AccessFs::from_all(ABI_REQUESTED);
    let status = Ruleset::default()
        .handle_access(all)
        .context("landlock handle access")?
        .create()
        .context("landlock create ruleset")?
        .add_rules(landlock::path_beneath_rules(["/usr"], read))
        .context("landlock probe rule")?
        .restrict_self()
        .context("landlock restrict_self")?;
    Ok(status.ruleset)
}

fn read_only_roots() -> Vec<&'static str> {
    ["/usr", "/etc", "/dev", "/proc"]
        .into_iter()
        .filter(|path| Path::new(path).exists())
        .collect()
}

/// Attach a rule to symlink inodes such as `/lib64` so the dynamic linker path can be opened.
///
/// `path_beneath_rules` follows symlinks and would only allow the target under `/usr`.
fn add_symlink_roots(
    mut ruleset: RulesetCreated,
    _directory_access: BitFlags<AccessFs>,
) -> Result<RulesetCreated> {
    let link_access = AccessFs::ReadFile | AccessFs::Execute;
    for path in ["/bin", "/lib", "/lib64"] {
        let candidate = Path::new(path);
        if candidate.is_symlink() {
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(nix::libc::O_PATH | nix::libc::O_NOFOLLOW | nix::libc::O_CLOEXEC)
                .open(path)
                .with_context(|| format!("open {path} without following the symlink"))?;
            ruleset = ruleset
                .add_rule(PathBeneath::new(file, link_access))
                .with_context(|| format!("landlock rule for symlink {path}"))?;
        } else if candidate.exists() {
            ruleset = ruleset
                .add_rules(landlock::path_beneath_rules([path], link_access))
                .with_context(|| format!("landlock rule for {path}"))?;
        }
    }
    Ok(ruleset)
}
