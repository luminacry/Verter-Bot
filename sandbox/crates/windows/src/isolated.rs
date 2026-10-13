//! Admin-initialized local users. The offline user has an outbound firewall block.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{Context, Result, bail};
use windows_sys::Win32::Foundation::FALSE;
use windows_sys::Win32::System::Threading::{
    CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, CreateProcessWithLogonW,
    LOGON_WITH_PROFILE, PROCESS_INFORMATION,
};

use crate::appcontainer::Spawned;
use crate::util::{command_line, environment_block, last_error, null, startup_with_stdio};

pub const OFFLINE_USER: &str = "verter-sandbox-offline";
pub const ONLINE_USER: &str = "verter-sandbox-online";

/// What `setup` did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SetupOutcome {
    /// Users and the firewall rule were created.
    Installed,
    /// Users and the firewall rule were removed.
    Removed,
    /// The caller is not an administrator.
    NeedsAdmin,
}

/// Create both sandbox users and block outbound traffic for the offline user.
///
/// # Errors
///
/// Returns an error when the caller is not an administrator or a system command fails.
pub fn setup(install: bool) -> Result<SetupOutcome> {
    if !install {
        return Ok(SetupOutcome::NeedsAdmin);
    }
    if !is_admin() {
        bail!("Windows sandbox setup requires an administrator: verter-sandbox setup --install");
    }
    let dir = state_dir();
    fs::create_dir_all(&dir).with_context(|| format!("create {}", dir.display()))?;
    let offline_password = password_or_create(&dir.join("offline.password"))?;
    let online_password = password_or_create(&dir.join("online.password"))?;
    ensure_user(OFFLINE_USER, &offline_password)?;
    ensure_user(ONLINE_USER, &online_password)?;
    let _ = tool(
        "netsh",
        &[
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            "name=verter-sandbox-offline",
        ],
    );
    tool(
        "netsh",
        &[
            "advfirewall",
            "firewall",
            "add",
            "rule",
            "name=verter-sandbox-offline",
            "dir=out",
            "action=block",
            &format!("localuser={OFFLINE_USER}"),
        ],
    )?;
    Ok(SetupOutcome::Installed)
}

/// Remove the sandbox users, firewall rule, and password files.
///
/// # Errors
///
/// Returns an error when the caller is not an administrator or a delete command fails.
pub fn uninstall() -> Result<SetupOutcome> {
    if !is_admin() {
        bail!("removing the Windows sandbox users requires an administrator");
    }
    let _ = tool(
        "netsh",
        &[
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            "name=verter-sandbox-offline",
        ],
    );
    let _ = tool("net", &["user", OFFLINE_USER, "/delete"]);
    let _ = tool("net", &["user", ONLINE_USER, "/delete"]);
    let _ = fs::remove_dir_all(state_dir());
    Ok(SetupOutcome::Removed)
}

#[must_use]
pub fn offline_ready() -> bool {
    user_ready(OFFLINE_USER, "offline.password")
}

#[must_use]
pub fn online_ready() -> bool {
    user_ready(ONLINE_USER, "online.password")
}

/// Start `command` suspended as the sandbox user selected by `network`.
///
/// # Errors
///
/// Returns an error when the user is missing or logon fails.
pub fn spawn_suspended(
    command: &[std::ffi::OsString],
    workspace: &Path,
    network: bool,
) -> Result<Spawned> {
    let (user, password_file) = if network {
        (ONLINE_USER, "online.password")
    } else {
        (OFFLINE_USER, "offline.password")
    };
    let password = fs::read_to_string(state_dir().join(password_file))
        .with_context(|| format!("read {password_file}"))?;
    let password = password.trim().to_owned();
    grant_workspace(user, workspace)?;
    let mut cmdline = command_line(command);
    let env = environment_block(workspace);
    let cwd = crate::util::wide_os(workspace.as_os_str());
    let user_w = crate::util::wide(user);
    let domain = crate::util::wide(".");
    let password_w = crate::util::wide(&password);
    let startup = startup_with_stdio();
    let mut info = PROCESS_INFORMATION::default();
    let flags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW;
    // SAFETY: the wide strings and startup info are live, and the process stays suspended
    // until the caller assigns it to a job.
    let created = unsafe {
        CreateProcessWithLogonW(
            user_w.as_ptr(),
            domain.as_ptr(),
            password_w.as_ptr(),
            LOGON_WITH_PROFILE,
            null(),
            cmdline.as_mut_ptr(),
            flags,
            env.as_ptr().cast(),
            cwd.as_ptr(),
            &raw const startup,
            &raw mut info,
        )
    };
    if created == FALSE {
        bail!(last_error("CreateProcessWithLogonW"));
    }
    Ok(Spawned {
        process: info.hProcess,
        thread: info.hThread,
    })
}

fn user_ready(name: &str, file: &str) -> bool {
    state_dir().join(file).is_file() && user_exists(name)
}

fn user_exists(name: &str) -> bool {
    Command::new("net")
        .args(["user", name])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn ensure_user(name: &str, password: &str) -> Result<()> {
    if user_exists(name) {
        return tool("net", &["user", name, password]);
    }
    tool("net", &["user", name, password, "/add"])
}

fn grant_workspace(user: &str, workspace: &Path) -> Result<()> {
    tool(
        "icacls",
        &[
            &workspace.display().to_string(),
            "/grant",
            &format!("{user}:(OI)(CI)M"),
        ],
    )
}

fn password_or_create(path: &Path) -> Result<String> {
    if let Ok(existing) = fs::read_to_string(path) {
        let existing = existing.trim().to_owned();
        if !existing.is_empty() {
            return Ok(existing);
        }
    }
    let generated = new_password()?;
    fs::write(path, &generated).with_context(|| format!("write {}", path.display()))?;
    Ok(generated)
}

fn new_password() -> Result<String> {
    let output = Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-Command",
            "[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')",
        ])
        .output()
        .context("generate a sandbox password")?;
    if !output.status.success() {
        bail!("powershell could not generate a password");
    }
    let password = String::from_utf8(output.stdout).context("password was not utf-8")?;
    let password = format!("{}Aa1!", password.trim());
    if password.len() < 16 {
        bail!("generated password was too short");
    }
    Ok(password)
}

fn state_dir() -> PathBuf {
    PathBuf::from(r"C:\ProgramData\verter-sandbox")
}

pub fn is_admin() -> bool {
    Command::new("net")
        .arg("session")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn tool(program: &str, args: &[&str]) -> Result<()> {
    let output = Command::new(program)
        .args(args)
        .output()
        .with_context(|| format!("spawn {program}"))?;
    if output.status.success() {
        return Ok(());
    }
    // Arguments can include a generated password. Keep them out of the error text.
    bail!("{program} failed with status {}", output.status)
}
