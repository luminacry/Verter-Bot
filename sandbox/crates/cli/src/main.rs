//! `verter-sandbox`: isolated command runner used by Verter-Bot agent tools.

mod platform;
mod serve;

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;
#[cfg(target_os = "linux")]
use std::time::Duration;

use clap::{Parser, Subcommand};
use verter_sandbox_protocol::PROTOCOL_VERSION;

#[derive(Parser)]
#[command(
    name = "verter-sandbox",
    version,
    about = "Sandbox runner for Verter-Bot agent tools"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Speak the NDJSON protocol on stdin and stdout.
    Serve,
    /// Report the host platform and isolation backends.
    Doctor {
        /// Print the report as JSON.
        #[arg(long)]
        json: bool,
    },
    /// Print the protocol JSON Schema.
    Schema,
    /// Run a command inside an isolation backend.
    Exec {
        /// `auto`, `bubblewrap`, or `landlock`.
        #[arg(long, default_value = "auto")]
        backend: String,
        /// Writable workspace. Defaults to the current directory.
        #[arg(long)]
        workspace: Option<PathBuf>,
        /// Allow outbound network.
        #[arg(long)]
        network: bool,
        /// Wall-clock timeout in seconds.
        #[arg(long, default_value_t = 30)]
        timeout: u64,
        /// Address-space limit in megabytes.
        #[arg(long, default_value_t = 512)]
        memory_mb: u64,
        /// `RLIMIT_NPROC` for the sandboxed command.
        #[arg(long, default_value_t = 256)]
        nproc: u64,
        /// CPU-time limit in seconds.
        #[arg(long, default_value_t = 30)]
        cpu: u64,
        /// Command and arguments.
        #[arg(trailing_var_arg = true, allow_hyphen_values = true, required = true)]
        command: Vec<OsString>,
    },
    /// Install an optional `AppArmor` profile so bubblewrap can create user namespaces.
    Setup {
        /// Write and load the profile. Requires root when `AppArmor` is the active LSM.
        #[arg(long)]
        install: bool,
    },
}

fn main() -> ExitCode {
    let args: Vec<OsString> = std::env::args_os().collect();
    if let Some(code) = dispatch_helper(&args) {
        return code;
    }
    match Cli::parse().command {
        Command::Serve => run_serve(),
        Command::Doctor { json } => {
            doctor(json);
            ExitCode::SUCCESS
        }
        Command::Schema => {
            print!("{}", verter_sandbox_protocol::schema_json());
            ExitCode::SUCCESS
        }
        Command::Exec {
            backend,
            workspace,
            network,
            timeout,
            memory_mb,
            nproc,
            cpu,
            command,
        } => exec_command(
            &backend, workspace, network, timeout, memory_mb, nproc, cpu, command,
        ),
        Command::Setup { install } => setup_command(install),
    }
}

fn dispatch_helper(args: &[OsString]) -> Option<ExitCode> {
    let command = args.get(1)?;
    if command == "__probe-landlock" {
        return Some(probe_landlock());
    }
    if command == "__apply" {
        return Some(apply_helper(&args[2..]));
    }
    None
}

#[cfg(target_os = "linux")]
fn probe_landlock() -> ExitCode {
    verter_sandbox_linux::probe_landlock_main();
}

#[cfg(not(target_os = "linux"))]
fn probe_landlock() -> ExitCode {
    eprintln!("verter-sandbox: landlock probe is only available on Linux");
    ExitCode::FAILURE
}

#[cfg(target_os = "linux")]
fn apply_helper(args: &[OsString]) -> ExitCode {
    if let Err(err) = verter_sandbox_linux::apply_from_args(args) {
        eprintln!("verter-sandbox: {err}");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

#[cfg(not(target_os = "linux"))]
fn apply_helper(_args: &[OsString]) -> ExitCode {
    eprintln!("verter-sandbox: isolation apply is only available on Linux");
    ExitCode::FAILURE
}

fn run_serve() -> ExitCode {
    let runtime = match tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(err) => {
            eprintln!("verter-sandbox: cannot start runtime: {err}");
            return ExitCode::FAILURE;
        }
    };
    let result = runtime.block_on(serve::serve(
        tokio::io::stdin(),
        tokio::io::stdout(),
        platform::detect(),
    ));
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("verter-sandbox: {err}");
            ExitCode::FAILURE
        }
    }
}

fn doctor(json: bool) {
    let info = platform::detect();
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&info).expect("platform info serializes")
        );
        return;
    }
    println!(
        "verter-sandbox {} (protocol {PROTOCOL_VERSION})",
        env!("CARGO_PKG_VERSION")
    );
    println!("os: {}", info.os);
    println!("arch: {}", info.arch);
    if info.backends.is_empty() {
        println!("isolation backends: none available on this platform yet");
    }
    for backend in &info.backends {
        let state = if backend.available {
            "available"
        } else {
            "unavailable"
        };
        let kind = serde_json::to_value(backend.kind).expect("backend kind serializes");
        println!(
            "backend {}: {state} ({})",
            kind.as_str().unwrap_or("unknown"),
            backend.detail
        );
    }
    #[cfg(target_os = "linux")]
    {
        let report = verter_sandbox_linux::host_report();
        println!("{}", report.auto_line);
        println!("{}", report.fallback_line);
        println!("{}", report.policy_line);
    }
}

#[cfg(target_os = "linux")]
#[allow(clippy::too_many_arguments)]
fn exec_command(
    backend: &str,
    workspace: Option<PathBuf>,
    network: bool,
    timeout: u64,
    memory_mb: u64,
    nproc: u64,
    cpu: u64,
    command: Vec<OsString>,
) -> ExitCode {
    let requested = match verter_sandbox_linux::RequestedBackend::parse(backend) {
        Ok(requested) => requested,
        Err(err) => {
            eprintln!("verter-sandbox: {err}");
            return ExitCode::FAILURE;
        }
    };
    let Some(memory_bytes) = memory_mb.checked_mul(1024 * 1024) else {
        eprintln!("verter-sandbox: memory limit overflows");
        return ExitCode::FAILURE;
    };
    let workspace = workspace.unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
    let helper = match std::env::current_exe() {
        Ok(path) => path,
        Err(err) => {
            eprintln!("verter-sandbox: current executable: {err}");
            return ExitCode::FAILURE;
        }
    };
    let request = verter_sandbox_linux::ExecRequest {
        backend: requested,
        command,
        workspace,
        network,
        timeout: Duration::from_secs(timeout),
        memory_bytes,
        nproc,
        cpu_seconds: cpu,
        env: Vec::new(),
    };
    match verter_sandbox_linux::execute(&helper, &request) {
        Ok(result) => {
            eprintln!("verter-sandbox: isolation {}", result.backend.as_str());
            if result.timed_out {
                eprintln!(
                    "verter-sandbox: timed out after {timeout}s; killed the sandbox process tree"
                );
                return ExitCode::from(124);
            }
            exit_code(result.status.code())
        }
        Err(err) => {
            eprintln!("verter-sandbox: {err}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(not(target_os = "linux"))]
#[allow(clippy::too_many_arguments)]
fn exec_command(
    _backend: &str,
    _workspace: Option<PathBuf>,
    _network: bool,
    _timeout: u64,
    _memory_mb: u64,
    _nproc: u64,
    _cpu: u64,
    _command: Vec<OsString>,
) -> ExitCode {
    eprintln!("verter-sandbox: exec is not available on this platform yet");
    ExitCode::FAILURE
}

#[cfg(target_os = "linux")]
fn exit_code(code: Option<i32>) -> ExitCode {
    let Some(code) = code else {
        return ExitCode::from(128);
    };
    u8::try_from(code).map_or(ExitCode::FAILURE, ExitCode::from)
}

#[cfg(target_os = "linux")]
fn setup_command(install: bool) -> ExitCode {
    match verter_sandbox_linux::setup(install) {
        Ok(verter_sandbox_linux::ApparmorSetup::NotActive) => {
            println!(
                "AppArmor is not the active LSM on this host. bubblewrap uses user namespaces directly; no profile was installed."
            );
            ExitCode::SUCCESS
        }
        Ok(verter_sandbox_linux::ApparmorSetup::NeedsSudo) => {
            println!("AppArmor is active. Installing a bwrap profile requires one sudo.");
            println!("Re-run: sudo verter-sandbox setup --install");
            println!("{}", verter_sandbox_linux::PROFILE);
            ExitCode::SUCCESS
        }
        Ok(verter_sandbox_linux::ApparmorSetup::Installed) => {
            println!("Installed the bubblewrap AppArmor profile at /etc/apparmor.d/bwrap");
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("verter-sandbox: {err}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(not(target_os = "linux"))]
fn setup_command(_install: bool) -> ExitCode {
    eprintln!("verter-sandbox: AppArmor setup is only available on Linux");
    ExitCode::FAILURE
}
