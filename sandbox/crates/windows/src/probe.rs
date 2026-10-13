//! What this Windows host can actually isolate.

use crate::policy::{Backend, Caps, RequestedBackend, choose};

/// Doctor-facing description of this host.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostReport {
    /// Capabilities used by [`choose`](crate::policy::choose).
    pub caps: Caps,
    /// Backend `auto` will use when network is denied.
    pub auto_backend: Option<Backend>,
    /// Human line such as `auto: appcontainer`.
    pub auto_line: String,
    /// `AppContainer` availability.
    pub appcontainer_line: String,
    /// Offline sandbox user.
    pub isolated_offline_line: String,
    /// Online sandbox user.
    pub isolated_online_line: String,
    /// States that exec will not run without isolation.
    pub policy_line: String,
}

/// Probe once per process.
#[must_use]
pub fn host_report() -> &'static HostReport {
    static REPORT: std::sync::OnceLock<HostReport> = std::sync::OnceLock::new();
    REPORT.get_or_init(probe)
}

fn probe() -> HostReport {
    let caps = Caps {
        appcontainer: crate::appcontainer::profile_sid().is_ok(),
        restricted: crate::restricted::available(),
        isolated_offline: crate::isolated::offline_ready(),
        isolated_online: crate::isolated::online_ready(),
    };
    let auto_backend = choose(RequestedBackend::Auto, caps, false).ok();
    let auto_line = match auto_backend {
        Some(backend) => format!("auto: {}", backend.as_str()),
        None => "auto: none".to_owned(),
    };
    HostReport {
        caps,
        auto_backend,
        auto_line,
        appcontainer_line: if caps.appcontainer {
            "appcontainer: available".to_owned()
        } else {
            "appcontainer: unavailable".to_owned()
        },
        isolated_offline_line: if caps.isolated_offline {
            "isolated offline: initialized".to_owned()
        } else {
            "isolated offline: not initialized (administrator: verter-sandbox setup --install)"
                .to_owned()
        },
        isolated_online_line: if caps.isolated_online {
            "isolated online: initialized".to_owned()
        } else {
            "isolated online: not initialized".to_owned()
        },
        policy_line: "policy: exec refuses to run without isolation".to_owned(),
    }
}
