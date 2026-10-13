//! Backend selection shared by every target so the refusal rules have unit tests.

use anyhow::{Result, bail};

/// Isolation backend that can actually run a command.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    /// Lowbox `AppContainer` plus a Job Object.
    AppContainer,
    /// Restricted token plus a Job Object. Network denial is not enforced.
    Restricted,
    /// Local user created by `setup`, plus a Job Object and a firewall rule.
    Isolated,
}

impl Backend {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::AppContainer => "appcontainer",
            Self::Restricted => "restricted",
            Self::Isolated => "isolated",
        }
    }
}

/// Backend requested on the command line.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RequestedBackend {
    /// Isolated user when `setup` has run, otherwise `AppContainer`.
    Auto,
    /// Require an `AppContainer`.
    AppContainer,
    /// Require a restricted token. Refuses when network access is denied.
    Restricted,
    /// Require the admin-initialized sandbox user.
    Isolated,
}

impl RequestedBackend {
    /// Parse `auto`, `appcontainer`, `restricted`, or `isolated`.
    ///
    /// # Errors
    ///
    /// Returns an error for any other value.
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "auto" => Ok(Self::Auto),
            "appcontainer" => Ok(Self::AppContainer),
            "restricted" => Ok(Self::Restricted),
            "isolated" => Ok(Self::Isolated),
            other => bail!("unknown backend {other}"),
        }
    }
}

/// Capabilities discovered on this host.
#[allow(clippy::struct_excessive_bools)]
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Caps {
    /// `CreateAppContainerProfile` succeeded or the profile already exists.
    pub appcontainer: bool,
    /// A restricted token can be created.
    pub restricted: bool,
    /// The offline sandbox user from `setup` is present.
    pub isolated_offline: bool,
    /// The online sandbox user from `setup` is present.
    pub isolated_online: bool,
}

/// Pick a backend or refuse.
///
/// # Errors
///
/// Returns an error when the request cannot be isolated. `auto` never returns success
/// without a real backend.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
pub fn choose(requested: RequestedBackend, caps: Caps, network: bool) -> Result<Backend> {
    match requested {
        RequestedBackend::Auto => {
            if network && caps.isolated_online || !network && caps.isolated_offline {
                Ok(Backend::Isolated)
            } else if caps.appcontainer {
                Ok(Backend::AppContainer)
            } else {
                bail!("refusing to run without isolation")
            }
        }
        RequestedBackend::AppContainer if caps.appcontainer => Ok(Backend::AppContainer),
        RequestedBackend::AppContainer => {
            bail!("AppContainer is unavailable; refusing to run unsandboxed")
        }
        RequestedBackend::Restricted if !network => {
            bail!("restricted token cannot enforce a network deny; refusing to run")
        }
        RequestedBackend::Restricted if caps.restricted => Ok(Backend::Restricted),
        RequestedBackend::Restricted => {
            bail!("restricted token is unavailable; refusing to run unsandboxed")
        }
        RequestedBackend::Isolated
            if network && caps.isolated_online || !network && caps.isolated_offline =>
        {
            Ok(Backend::Isolated)
        }
        RequestedBackend::Isolated => {
            bail!(
                "isolated user is not initialized; run verter-sandbox setup --install as administrator"
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Backend, Caps, RequestedBackend, choose};

    #[allow(clippy::fn_params_excessive_bools)]
    fn caps(app: bool, restricted: bool, offline: bool, online: bool) -> Caps {
        Caps {
            appcontainer: app,
            restricted,
            isolated_offline: offline,
            isolated_online: online,
        }
    }

    #[test]
    fn auto_prefers_the_initialized_user() {
        let host = caps(true, true, true, true);
        assert_eq!(
            choose(RequestedBackend::Auto, host, false).unwrap(),
            Backend::Isolated
        );
        assert_eq!(
            choose(RequestedBackend::Auto, host, true).unwrap(),
            Backend::Isolated
        );
    }

    #[test]
    fn auto_uses_appcontainer_and_refuses_when_nothing_works() {
        assert_eq!(
            choose(
                RequestedBackend::Auto,
                caps(true, true, false, false),
                false
            )
            .unwrap(),
            Backend::AppContainer
        );
        let err = choose(
            RequestedBackend::Auto,
            caps(false, true, false, false),
            false,
        )
        .unwrap_err();
        assert!(err.to_string().contains("without isolation"));
    }

    #[test]
    fn restricted_refuses_a_network_deny() {
        let host = caps(true, true, false, false);
        assert!(choose(RequestedBackend::Restricted, host, false).is_err());
        assert_eq!(
            choose(RequestedBackend::Restricted, host, true).unwrap(),
            Backend::Restricted
        );
    }
}
