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

    let report = verter_sandbox_linux::host_report();
    vec![BackendStatus {
        kind: BackendKind::Bubblewrap,
        available: report.bubblewrap_available,
        detail: report.bubblewrap_detail.clone(),
    }]
}

#[cfg(not(target_os = "linux"))]
fn backends() -> Vec<BackendStatus> {
    Vec::new()
}
