//! Linux isolation: bubblewrap namespaces, with Landlock and seccomp as the fallback.
//!
//! `auto` never runs a command on the host. If neither backend can isolate, exec fails.
//! On non-Linux targets this crate is empty so `cargo test --workspace` still builds on Windows.

#[cfg(target_os = "linux")]
mod apparmor;
#[cfg(target_os = "linux")]
mod apply;
#[cfg(target_os = "linux")]
mod bubblewrap;
#[cfg(target_os = "linux")]
mod exec;
#[cfg(target_os = "linux")]
mod landlock_fs;
#[cfg(target_os = "linux")]
mod limits;
#[cfg(target_os = "linux")]
mod probe;
#[cfg(target_os = "linux")]
mod seccomp;

#[cfg(target_os = "linux")]
pub use apparmor::{ApparmorSetup, PROFILE, setup};
#[cfg(target_os = "linux")]
pub use apply::apply_from_args;
#[cfg(target_os = "linux")]
pub use exec::{ExecRequest, ExecResult, OutputCapture, execute};
#[cfg(target_os = "linux")]
pub use probe::{Backend, HostReport, RequestedBackend, host_report, probe_landlock_main};
