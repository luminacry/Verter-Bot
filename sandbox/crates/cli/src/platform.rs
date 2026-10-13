//! Host platform and isolation backend detection.

use serde::Serialize;
use verter_sandbox_protocol::{BackendKind, BackendStatus, PlatformInfo};

/// Machine-readable `verter-sandbox doctor --json` document.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DoctorDocument {
    /// Operating system, as in Rust's `std::env::consts::OS`.
    pub os: String,
    /// CPU architecture.
    pub arch: String,
    /// Isolation backends known on this platform.
    pub backends: Vec<BackendStatus>,
    /// Backend `auto` will use, or `none` when exec must refuse.
    pub auto: String,
    /// States that exec will not run without isolation.
    pub policy: String,
}

/// Platform info plus the effective `auto` backend for the dashboard.
#[must_use]
pub fn doctor_document() -> DoctorDocument {
    let info = detect();
    DoctorDocument {
        os: info.os,
        arch: info.arch,
        backends: info.backends,
        auto: auto_name(),
        policy: "exec refuses to run without isolation".to_owned(),
    }
}

fn auto_name() -> String {
    #[cfg(target_os = "linux")]
    {
        match verter_sandbox_linux::host_report().auto_backend {
            Some(backend) => backend.as_str().to_owned(),
            None => "none".to_owned(),
        }
    }
    #[cfg(windows)]
    {
        match verter_sandbox_windows::host_report().auto_backend {
            Some(backend) => backend.as_str().to_owned(),
            None => "none".to_owned(),
        }
    }
    #[cfg(not(any(target_os = "linux", windows)))]
    {
        "none".to_owned()
    }
}

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
