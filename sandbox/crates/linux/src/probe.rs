//! Host capability probe. Cached for the life of the process.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::Duration;

use anyhow::{Context, Result, bail};

/// Isolation backend that can actually run a command.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    /// User, pid, mount, and network namespaces via `bwrap`.
    Bubblewrap,
    /// Landlock, seccomp, and rlimits without namespaces.
    Landlock,
}

impl Backend {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Bubblewrap => "bubblewrap",
            Self::Landlock => "landlock",
        }
    }
}

/// Backend requested by `verter-sandbox exec --backend`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RequestedBackend {
    /// Bubblewrap when a user namespace works, otherwise Landlock.
    Auto,
    /// Require bubblewrap.
    Bubblewrap,
    /// Require Landlock.
    Landlock,
}

impl RequestedBackend {
    /// Parse a CLI value.
    ///
    /// # Errors
    ///
    /// Returns an error when `value` is not `auto`, `bubblewrap`, or `landlock`.
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "auto" => Ok(Self::Auto),
            "bubblewrap" | "bwrap" => Ok(Self::Bubblewrap),
            "landlock" => Ok(Self::Landlock),
            other => bail!("unknown backend {other}"),
        }
    }
}

/// Doctor-facing description of this host.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostReport {
    /// True when `bwrap` can create a user namespace.
    pub bubblewrap_available: bool,
    /// Path when bubblewrap works. Otherwise the reason, including any Landlock fallback.
    pub bubblewrap_detail: String,
    /// Backend `auto` will use. `None` means exec must refuse.
    pub auto_backend: Option<Backend>,
    /// True when a Landlock ruleset can be enforced.
    pub landlock_available: bool,
    /// Enforcement status, or the reason Landlock cannot isolate.
    pub landlock_detail: String,
    /// Human line such as `auto: landlock`.
    pub auto_line: String,
    /// Human line describing the Landlock fallback.
    pub fallback_line: String,
    /// States that exec will not run without isolation.
    pub policy_line: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Capability {
    bwrap_path: Option<PathBuf>,
    userns: bool,
    userns_error: Option<String>,
    landlock: LandlockState,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum LandlockState {
    Enforced,
    Partial,
    Unavailable(String),
}

const POLICY: &str = "policy: exec refuses to run without isolation";

/// Probe once and reuse the result.
#[must_use]
pub fn host_report() -> &'static HostReport {
    static REPORT: OnceLock<HostReport> = OnceLock::new();
    REPORT.get_or_init(|| {
        let helper = std::env::current_exe().unwrap_or_default();
        report_from(probe_capability(&helper))
    })
}

/// Choose a backend or refuse.
///
/// # Errors
///
/// Returns an error when the requested backend cannot isolate the command.
pub fn select_backend(requested: RequestedBackend, report: &HostReport) -> Result<Backend> {
    match requested {
        RequestedBackend::Bubblewrap if report.bubblewrap_available => Ok(Backend::Bubblewrap),
        RequestedBackend::Bubblewrap => {
            bail!("bubblewrap cannot create a user namespace; refusing to run unsandboxed")
        }
        RequestedBackend::Landlock if report.landlock_available => Ok(Backend::Landlock),
        RequestedBackend::Landlock => {
            bail!("landlock is not enforced; refusing to run unsandboxed")
        }
        RequestedBackend::Auto => report
            .auto_backend
            .context("refusing to run without isolation"),
    }
}

fn report_from(cap: Capability) -> HostReport {
    let landlock_ready = matches!(
        cap.landlock,
        LandlockState::Enforced | LandlockState::Partial
    );
    let auto_backend = if cap.userns {
        Some(Backend::Bubblewrap)
    } else if landlock_ready {
        Some(Backend::Landlock)
    } else {
        None
    };
    let bubblewrap_detail = if cap.userns {
        cap.bwrap_path
            .map_or_else(|| "bwrap".to_owned(), |path| path.display().to_string())
    } else if let Some(path) = &cap.bwrap_path {
        let reason = cap
            .userns_error
            .as_deref()
            .unwrap_or("user namespace unavailable");
        let fallback = landlock_phrase(&cap.landlock);
        format!(
            "{} cannot create a user namespace ({reason}); {fallback}",
            path.display()
        )
    } else {
        format!(
            "bwrap not found in PATH; {}",
            landlock_phrase(&cap.landlock)
        )
    };
    let auto_line = match auto_backend {
        Some(backend) => format!("auto: {}", backend.as_str()),
        None => "auto: none".to_owned(),
    };
    HostReport {
        bubblewrap_available: cap.userns,
        bubblewrap_detail,
        auto_backend,
        landlock_available: landlock_ready,
        landlock_detail: landlock_phrase(&cap.landlock),
        auto_line,
        fallback_line: format!("fallback: {}", landlock_phrase(&cap.landlock)),
        policy_line: POLICY.to_owned(),
    }
}

fn landlock_phrase(state: &LandlockState) -> String {
    match state {
        LandlockState::Enforced => "landlock enforced".to_owned(),
        LandlockState::Partial => "landlock partially enforced".to_owned(),
        LandlockState::Unavailable(reason) => format!("landlock unavailable ({reason})"),
    }
}

fn probe_capability(helper: &Path) -> Capability {
    let bwrap_path = crate::bubblewrap::find_bwrap();
    let (userns, userns_error) = match &bwrap_path {
        Some(path) => match probe_userns(path) {
            Ok(()) => (true, None),
            Err(err) => (false, Some(err)),
        },
        None => (false, None),
    };
    Capability {
        bwrap_path,
        userns,
        userns_error,
        landlock: probe_landlock(helper),
    }
}

fn probe_userns(bwrap: &Path) -> std::result::Result<(), String> {
    let mut command = Command::new(bwrap);
    command
        .args(["--unshare-user", "--unshare-net", "--die-with-parent"])
        .env_clear();
    crate::bubblewrap::mount_readonly(&mut command);
    let output = command
        .args(["--", "/usr/bin/echo", "ok"])
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|err| err.to_string())?;
    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let line = stderr.lines().next().unwrap_or("bwrap failed").trim();
        Err(line.to_owned())
    }
}

fn probe_landlock(helper: &Path) -> LandlockState {
    if !helper.is_file() {
        return LandlockState::Unavailable("helper missing".to_owned());
    }
    let mut child = match Command::new(helper)
        .arg("__probe-landlock")
        .env_clear()
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(err) => return LandlockState::Unavailable(err.to_string()),
    };
    let timed_out = match crate::exec::wait_child(&mut child, Duration::from_secs(3), false) {
        Ok((_, timed_out)) => timed_out,
        Err(err) => return LandlockState::Unavailable(err.to_string()),
    };
    if timed_out {
        return LandlockState::Unavailable("probe timed out".to_owned());
    }
    let mut stdout = String::new();
    if let Some(mut pipe) = child.stdout.take() {
        use std::io::Read;
        let _ = pipe.read_to_string(&mut stdout);
    }
    let text = stdout.trim();
    match text {
        "enforced" => LandlockState::Enforced,
        "partial" => LandlockState::Partial,
        other => LandlockState::Unavailable(if other.is_empty() {
            "probe produced no output".to_owned()
        } else {
            other.to_owned()
        }),
    }
}

/// Entry point for `__probe-landlock`. Prints one status word and exits.
pub fn probe_landlock_main() -> ! {
    let word = match crate::landlock_fs::probe() {
        Ok(landlock::RulesetStatus::FullyEnforced) => "enforced",
        Ok(landlock::RulesetStatus::PartiallyEnforced) => "partial",
        Ok(landlock::RulesetStatus::NotEnforced) => "unavailable not enforced",
        Err(err) => {
            eprintln!("verter-sandbox: landlock probe failed: {err}");
            println!("unavailable {err}");
            std::process::exit(0);
        }
    };
    println!("{word}");
    std::process::exit(0);
}

#[cfg(test)]
mod tests {
    use super::{
        Backend, Capability, LandlockState, RequestedBackend, report_from, select_backend,
    };

    fn cap(userns: bool, landlock: LandlockState) -> Capability {
        Capability {
            bwrap_path: Some("/usr/bin/bwrap".into()),
            userns,
            userns_error: if userns {
                None
            } else {
                Some("setting up uid map: Permission denied".to_owned())
            },
            landlock,
        }
    }

    #[test]
    fn auto_prefers_bubblewrap_and_keeps_the_path_detail() {
        let report = report_from(cap(true, LandlockState::Enforced));
        assert!(report.bubblewrap_available);
        assert_eq!(report.bubblewrap_detail, "/usr/bin/bwrap");
        assert_eq!(report.auto_backend, Some(Backend::Bubblewrap));
        assert_eq!(
            select_backend(RequestedBackend::Auto, &report).unwrap(),
            Backend::Bubblewrap
        );
        assert_eq!(
            select_backend(RequestedBackend::Landlock, &report).unwrap(),
            Backend::Landlock
        );
    }

    #[test]
    fn auto_falls_back_to_landlock_and_says_so() {
        let report = report_from(cap(false, LandlockState::Enforced));
        assert!(!report.bubblewrap_available);
        assert!(report.bubblewrap_detail.contains("uid map"));
        assert!(report.bubblewrap_detail.contains("landlock enforced"));
        assert_eq!(report.auto_backend, Some(Backend::Landlock));
        assert_eq!(
            select_backend(RequestedBackend::Auto, &report).unwrap(),
            Backend::Landlock
        );
        assert!(select_backend(RequestedBackend::Bubblewrap, &report).is_err());
    }

    #[test]
    fn auto_refuses_when_nothing_can_isolate() {
        let report = report_from(cap(false, LandlockState::Unavailable("no abi".to_owned())));
        assert_eq!(report.auto_backend, None);
        let err = select_backend(RequestedBackend::Auto, &report).unwrap_err();
        assert!(err.to_string().contains("without isolation"));
        assert!(select_backend(RequestedBackend::Landlock, &report).is_err());
    }
}
