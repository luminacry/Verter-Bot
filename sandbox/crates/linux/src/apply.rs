//! Inner process: apply limits and isolation, then replace this process with the command.

use std::ffi::{OsStr, OsString};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;

use anyhow::{Context, Result, bail};

use crate::probe::Backend;

/// Options parsed from the hidden `__apply` invocation.
#[derive(Debug, Clone)]
pub struct ApplyOptions {
    /// Backend that already isolated this process, or the one to apply now.
    pub mode: Backend,
    /// Writable directory.
    pub workspace: PathBuf,
    /// When false, seccomp rejects outbound sends.
    pub network: bool,
    /// `RLIMIT_AS` in bytes.
    pub memory_bytes: u64,
    /// `RLIMIT_NPROC`.
    pub nproc: u64,
    /// `RLIMIT_CPU` in seconds.
    pub cpu_seconds: u64,
    /// Extra environment variables allowed into the command.
    pub env: Vec<(OsString, OsString)>,
    /// Command and arguments. The first item is the program.
    pub command: Vec<OsString>,
}

/// Parse `__apply` arguments and exec the command.
///
/// # Errors
///
/// Returns an error if the arguments are invalid or isolation cannot be applied.
/// On success this function does not return.
pub fn apply_from_args(args: &[OsString]) -> Result<()> {
    let opts = parse_apply_args(args)?;
    apply_and_exec(&opts)
}

fn apply_and_exec(opts: &ApplyOptions) -> Result<()> {
    if opts.command.is_empty() {
        bail!("command is empty");
    }
    match opts.mode {
        Backend::Landlock => {
            nix::unistd::setsid().context("setsid")?;
            crate::limits::apply(opts)?;
            crate::landlock_fs::enforce(&opts.workspace)?;
            crate::seccomp::install(!opts.network)?;
        }
        Backend::Bubblewrap => {
            crate::limits::apply(opts)?;
            crate::seccomp::install(!opts.network)?;
        }
    }
    let err = exec_command(opts);
    Err(err).context("exec command")
}

fn exec_command(opts: &ApplyOptions) -> std::io::Error {
    let mut command = Command::new(&opts.command[0]);
    command
        .args(&opts.command[1..])
        .current_dir(&opts.workspace)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", &opts.workspace)
        .env("TMPDIR", &opts.workspace);
    for (key, value) in &opts.env {
        command.env(key, value);
    }
    command.exec()
}

pub fn helper_args(opts: &ApplyOptions) -> Vec<OsString> {
    let mut args = vec![
        OsString::from("__apply"),
        OsString::from("--mode"),
        OsString::from(opts.mode.as_str()),
        OsString::from("--workspace"),
        opts.workspace.as_os_str().to_owned(),
        OsString::from("--network"),
        OsString::from(if opts.network { "1" } else { "0" }),
        OsString::from("--memory"),
        OsString::from(opts.memory_bytes.to_string()),
        OsString::from("--nproc"),
        OsString::from(opts.nproc.to_string()),
        OsString::from("--cpu"),
        OsString::from(opts.cpu_seconds.to_string()),
    ];
    for (key, value) in &opts.env {
        args.push(OsString::from("--setenv"));
        let mut pair = key.clone();
        pair.push("=");
        pair.push(value);
        args.push(pair);
    }
    args.push(OsString::from("--"));
    args.extend(opts.command.iter().cloned());
    args
}

fn parse_apply_args(args: &[OsString]) -> Result<ApplyOptions> {
    let mut mode = None;
    let mut workspace = None;
    let mut network = None;
    let mut memory_bytes = None;
    let mut nproc = None;
    let mut cpu_seconds = None;
    let mut env = Vec::new();
    let mut command = Vec::new();
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        if arg == "--" {
            command.extend(args[index + 1..].iter().cloned());
            break;
        }
        let flag = arg.to_str().context("apply flag is not utf-8")?;
        index += 1;
        match flag {
            "--mode" => {
                let value = next_str(args, &mut index, flag)?;
                mode = Some(parse_mode(&value)?);
            }
            "--workspace" => {
                workspace = Some(PathBuf::from(next_os(args, &mut index, flag)?));
            }
            "--network" => {
                let value = next_str(args, &mut index, flag)?;
                network = Some(match value.as_str() {
                    "1" => true,
                    "0" => false,
                    other => bail!("--network must be 0 or 1, got {other}"),
                });
            }
            "--memory" => memory_bytes = Some(next_u64(args, &mut index, flag)?),
            "--nproc" => nproc = Some(next_u64(args, &mut index, flag)?),
            "--cpu" => cpu_seconds = Some(next_u64(args, &mut index, flag)?),
            "--setenv" => {
                let raw = next_os(args, &mut index, flag)?;
                let (key, value) = split_env(raw)?;
                env.push((key, value));
            }
            other => bail!("unknown apply argument {other}"),
        }
    }
    Ok(ApplyOptions {
        mode: mode.context("missing --mode")?,
        workspace: workspace.context("missing --workspace")?,
        network: network.context("missing --network")?,
        memory_bytes: memory_bytes.context("missing --memory")?,
        nproc: nproc.context("missing --nproc")?,
        cpu_seconds: cpu_seconds.context("missing --cpu")?,
        env,
        command,
    })
}

fn parse_mode(value: &str) -> Result<Backend> {
    match value {
        "bubblewrap" => Ok(Backend::Bubblewrap),
        "landlock" => Ok(Backend::Landlock),
        other => bail!("unknown apply mode {other}"),
    }
}

fn next_os<'a>(args: &'a [OsString], index: &mut usize, flag: &str) -> Result<&'a OsStr> {
    let value = args
        .get(*index)
        .with_context(|| format!("missing value for {flag}"))?;
    *index += 1;
    Ok(value)
}

fn next_str(args: &[OsString], index: &mut usize, flag: &str) -> Result<String> {
    let value = next_os(args, index, flag)?;
    value
        .to_str()
        .map(ToOwned::to_owned)
        .with_context(|| format!("{flag} value is not utf-8"))
}

fn next_u64(args: &[OsString], index: &mut usize, flag: &str) -> Result<u64> {
    let value = next_str(args, index, flag)?;
    value
        .parse()
        .with_context(|| format!("{flag} is not an integer"))
}

fn split_env(raw: &OsStr) -> Result<(OsString, OsString)> {
    let bytes = raw.as_bytes();
    let split_at = bytes
        .iter()
        .position(|byte| *byte == b'=')
        .context("--setenv requires KEY=VALUE")?;
    let key = OsStr::from_bytes(&bytes[..split_at]);
    if key.is_empty() {
        bail!("--setenv key is empty");
    }
    let value = OsStr::from_bytes(&bytes[split_at + 1..]);
    Ok((key.to_owned(), value.to_owned()))
}
