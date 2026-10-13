//! Windows isolation prototypes: `AppContainer`, restricted token, and an admin-initialized user.
//!
//! `auto` never runs a command on the host. On non-Windows targets execution returns an error.

mod policy;

#[cfg(windows)]
mod acl;
#[cfg(windows)]
mod appcontainer;
#[cfg(windows)]
mod exec;
#[cfg(windows)]
mod isolated;
#[cfg(windows)]
mod job;
#[cfg(windows)]
mod probe;
#[cfg(windows)]
mod restricted;
#[cfg(windows)]
mod util;

#[cfg(windows)]
pub use exec::{ExecRequest, ExecResult, OutputCapture, execute};
#[cfg(windows)]
pub use isolated::{SetupOutcome, setup, uninstall};
#[cfg(windows)]
pub use probe::{HostReport, host_report};

#[cfg(not(windows))]
mod unsupported;

#[cfg(not(windows))]
pub use unsupported::{
    ExecRequest, ExecResult, HostReport, OutputCapture, SetupOutcome, execute, host_report, setup,
    uninstall,
};

pub use policy::{Backend, RequestedBackend};
