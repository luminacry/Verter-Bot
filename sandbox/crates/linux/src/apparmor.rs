//! Optional `AppArmor` profile that permits bubblewrap's user namespace.

use std::fs;
use std::path::Path;
use std::process::Command;

use anyhow::{Context, Result, bail};

const PROFILE_PATH: &str = "/etc/apparmor.d/bwrap";

/// Profile text installed by `verter-sandbox setup --install`.
pub const PROFILE: &str = r"abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,

  include if exists <local/bwrap>
}
";

/// What `setup` did on this host.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApparmorSetup {
    /// `AppArmor` is not the active LSM, so no profile is required.
    NotActive,
    /// `AppArmor` is active. The profile was printed and not installed.
    NeedsSudo,
    /// The profile was written and loaded.
    Installed,
}

/// Report or install the bubblewrap `AppArmor` profile.
///
/// # Errors
///
/// Returns an error when `--install` is requested without root, or when loading the profile fails.
pub fn setup(install: bool) -> Result<ApparmorSetup> {
    if !apparmor_active() {
        return Ok(ApparmorSetup::NotActive);
    }
    if !install {
        return Ok(ApparmorSetup::NeedsSudo);
    }
    if !is_root() {
        bail!("AppArmor is active; re-run as root: sudo verter-sandbox setup --install");
    }
    fs::write(PROFILE_PATH, PROFILE).with_context(|| format!("write {PROFILE_PATH}"))?;
    let status = Command::new("apparmor_parser")
        .args(["-r", PROFILE_PATH])
        .status()
        .context("run apparmor_parser")?;
    if !status.success() {
        bail!("apparmor_parser failed to load {PROFILE_PATH}");
    }
    Ok(ApparmorSetup::Installed)
}

fn apparmor_active() -> bool {
    fs::read_to_string("/sys/kernel/security/lsm")
        .is_ok_and(|lsm| lsm.split(',').any(|name| name.trim() == "apparmor"))
}

fn is_root() -> bool {
    Path::new("/proc/self").metadata().is_ok_and(|meta| {
        use std::os::unix::fs::MetadataExt;
        meta.uid() == 0
    })
}

#[cfg(test)]
mod tests {
    use super::PROFILE;

    #[test]
    fn profile_allows_userns_for_bwrap() {
        assert!(PROFILE.contains("profile bwrap /usr/bin/bwrap"));
        assert!(PROFILE.contains("userns"));
    }
}
