//! Host platform and isolation backend detection.

use verter_sandbox_protocol::{BackendKind, BackendStatus, PlatformInfo};

pub fn detect() -> PlatformInfo {
    PlatformInfo {
        os: std::env::consts::OS.to_owned(),
        arch: std::env::consts::ARCH.to_owned(),
        backends: backends(),
    }
}

#[cfg(target_os = "linux")]
fn backends() -> Vec<BackendStatus> {
    let report = verter_sandbox_linux::host_report();
    vec![
        BackendStatus {
            kind: BackendKind::Bubblewrap,
            available: report.bubblewrap_available,
            detail: report.bubblewrap_detail.clone(),
        },
        BackendStatus {
            kind: BackendKind::Landlock,
            available: report.landlock_available,
            detail: report.landlock_detail.clone(),
        },
    ]
}

#[cfg(windows)]
fn backends() -> Vec<BackendStatus> {
    let report = verter_sandbox_windows::host_report();
    vec![
        BackendStatus {
            kind: BackendKind::AppContainer,
            available: report.caps.appcontainer,
            detail: report.appcontainer_line.clone(),
        },
        BackendStatus {
            kind: BackendKind::Isolated,
            available: report.caps.isolated_offline || report.caps.isolated_online,
            detail: format!(
                "{}; {}",
                report.isolated_offline_line, report.isolated_online_line
            ),
        },
        BackendStatus {
            kind: BackendKind::Restricted,
            available: report.caps.restricted,
            detail: if report.caps.restricted {
                "restricted token available".to_owned()
            } else {
                "restricted token unavailable".to_owned()
            },
        },
    ]
}

#[cfg(not(any(target_os = "linux", windows)))]
fn backends() -> Vec<BackendStatus> {
    Vec::new()
}
