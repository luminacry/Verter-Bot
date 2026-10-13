//! `verter-sandbox`: isolated command runner used by Verter-Bot agent tools.

mod platform;
mod serve;

use std::process::ExitCode;

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
}

fn main() -> ExitCode {
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
    }
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
}
