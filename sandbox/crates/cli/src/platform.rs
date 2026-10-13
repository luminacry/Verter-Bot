//! Host platform and isolation backend detection.

use verter_sandbox_protocol::{BackendStatus, PlatformInfo};

pub fn detect() -> PlatformInfo {
    PlatformInfo {
        os: std::env::consts::OS.to_owned(),
        arch: std::env::consts::ARCH.to_owned(),
        backends: backends(),
    }
}

#[cfg(target_os = "linux")]
fn backends() -> Vec<BackendStatus> {
    use verter_sandbox_protocol::BackendKind;

    let bwrap = find_executable("bwrap");
    vec![BackendStatus {
        kind: BackendKind::Bubblewrap,
        available: bwrap.is_some(),
        detail: bwrap.map_or_else(
            || "bwrap not found in PATH".to_owned(),
            |path| path.display().to_string(),
        ),
    }]
}

#[cfg(not(target_os = "linux"))]
fn backends() -> Vec<BackendStatus> {
    Vec::new()
}

#[cfg(target_os = "linux")]
fn find_executable(name: &str) -> Option<std::path::PathBuf> {
    use std::os::unix::fs::PermissionsExt;

    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|candidate| {
            candidate
                .metadata()
                .is_ok_and(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
        })
}
