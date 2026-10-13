//! Security subset for the Windows sandbox prototype.
//!
//! `AppContainer` tests run without administrator rights. Creating the offline and online
//! users is gated on `VERTER_SANDBOX_ADMIN=1` so the ordinary Windows check job does not
//! mutate local accounts. That job still runs the `AppContainer` suite.

#![cfg(windows)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::Mutex;
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_verter-sandbox");

static LOCK: Mutex<()> = Mutex::new(());

#[derive(Debug)]
struct Run {
    status: ExitStatus,
    stdout: String,
    stderr: String,
}

struct RemoveIsolatedUsers;

impl Drop for RemoveIsolatedUsers {
    fn drop(&mut self) {
        let _ = Command::new(BIN).args(["setup", "--uninstall"]).output();
    }
}

#[test]
fn doctor_reports_windows_isolation() {
    let _guard = lock();
    let output = Command::new(BIN).arg("doctor").output().unwrap();
    assert!(output.status.success());
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(text.contains("auto:"), "{text}");
    assert!(text.contains("appcontainer:"), "{text}");
    assert!(
        text.contains("policy: exec refuses to run without isolation"),
        "{text}"
    );
    let json = Command::new(BIN)
        .args(["doctor", "--json"])
        .output()
        .unwrap();
    let report: serde_json::Value = serde_json::from_slice(&json.stdout).unwrap();
    let kinds: Vec<&str> = report["backends"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|item| item["kind"].as_str())
        .collect();
    assert_eq!(kinds, ["appcontainer", "isolated", "restricted"]);
}

#[test]
fn setup_without_install_asks_for_an_administrator() {
    let _guard = lock();
    let output = Command::new(BIN).arg("setup").output().unwrap();
    assert!(output.status.success());
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(text.contains("verter-sandbox setup --install"), "{text}");
}

#[test]
fn restricted_refuses_a_network_deny() {
    let _guard = lock();
    let workspace = temp_workspace("restricted");
    let result = run(
        limits("restricted", &workspace, 15, 512, 32, false),
        &[
            cmd_exe().as_os_str(),
            "/c".as_ref(),
            "echo should-not-run>out.txt".as_ref(),
        ],
    );
    assert!(!result.status.success(), "{}", result.stderr);
    assert!(
        result.stderr.contains("network deny") || result.stderr.contains("refusing"),
        "{}",
        result.stderr
    );
    assert!(!workspace.join("out.txt").exists());
}

#[test]
fn security_suite_under_appcontainer() {
    let _guard = lock();
    security_suite("appcontainer");
    compat_matrix("appcontainer");
}

#[test]
fn security_suite_under_isolated_user_when_admin() {
    let _guard = lock();
    if std::env::var("VERTER_SANDBOX_ADMIN").ok().as_deref() != Some("1") {
        eprintln!("skip isolated suite: VERTER_SANDBOX_ADMIN is not 1");
        return;
    }
    let _cleanup = RemoveIsolatedUsers;
    let output = Command::new(BIN)
        .args(["setup", "--install"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "stdout {}\nstderr {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    security_suite("isolated");
    compat_matrix("isolated");
}

fn security_suite(backend: &str) {
    escape_write_is_blocked(backend);
    sensitive_read_is_blocked(backend);
    outbound_network_is_blocked(backend);
    process_limit_blocks_a_child(backend);
    memory_bomb_is_blocked(backend);
    timeout_kills_the_tree(backend);
    parent_env_is_cleared(backend);
    echo_overhead_is_within_budget(backend);
}

fn escape_write_is_blocked(backend: &str) {
    let parent = temp_workspace("parent-write");
    tighten(&parent);
    let workspace = parent.join("ws");
    fs::create_dir_all(&workspace).unwrap();
    let secret = parent.join("secret.txt");
    fs::write(&secret, "original").unwrap();
    write_bat(
        &workspace,
        "escape.bat",
        "@echo off\r\necho ok>inside.txt\r\necho pwned>..\\secret.txt\r\n",
    );
    let result = run_bat(backend, &workspace, "escape.bat", 20, 512, 32);
    assert!(
        fs::read_to_string(workspace.join("inside.txt"))
            .unwrap_or_default()
            .contains("ok"),
        "stdout {} stderr {}",
        result.stdout,
        result.stderr
    );
    assert_eq!(
        fs::read_to_string(&secret).unwrap(),
        "original",
        "escape write changed the file outside the workspace\nstderr {}",
        result.stderr
    );
}

fn sensitive_read_is_blocked(backend: &str) {
    let parent = temp_workspace("parent-read");
    tighten(&parent);
    let workspace = parent.join("ws");
    fs::create_dir_all(&workspace).unwrap();
    fs::write(parent.join("secret.txt"), "TOP-SECRET-VALUE").unwrap();
    write_bat(
        &workspace,
        "read.bat",
        "@echo off\r\ntype ..\\secret.txt >stolen.txt\r\n",
    );
    let result = run_bat(backend, &workspace, "read.bat", 20, 512, 32);
    let stolen = fs::read_to_string(workspace.join("stolen.txt")).unwrap_or_default();
    assert!(
        !stolen.contains("TOP-SECRET-VALUE"),
        "sandbox read the secret outside the workspace\n{stolen}\nstderr {}",
        result.stderr
    );
    assert!(
        !result.stdout.contains("TOP-SECRET-VALUE"),
        "{}",
        result.stdout
    );
}

fn outbound_network_is_blocked(backend: &str) {
    if !host_can_reach_cloudflare() {
        eprintln!("skip network deny: this host cannot reach 1.1.1.1:443");
        return;
    }
    let workspace = temp_workspace("net");
    let curl = system_root().join("System32").join("curl.exe");
    let result = run(
        limits(backend, &workspace, 20, 512, 32, false),
        &[
            curl.as_os_str(),
            "-sS".as_ref(),
            "--connect-timeout".as_ref(),
            "4".as_ref(),
            "--max-time".as_ref(),
            "8".as_ref(),
            "-o".as_ref(),
            "NUL".as_ref(),
            "https://1.1.1.1/".as_ref(),
        ],
    );
    assert!(
        !result.status.success(),
        "outbound network succeeded under {backend}: stderr {}",
        result.stderr
    );
}

fn process_limit_blocks_a_child(backend: &str) {
    let allowed = temp_workspace("fork-ok");
    write_fork_bats(&allowed);
    let opened = run_bat(backend, &allowed, "fork.bat", 20, 512, 32);
    assert!(opened.status.success(), "stderr {}", opened.stderr);
    assert!(
        allowed.join("child.txt").is_file(),
        "fork script did not start a child when the limit allows it\nstderr {}",
        opened.stderr
    );

    let blocked = temp_workspace("fork-block");
    write_fork_bats(&blocked);
    let result = run_bat(backend, &blocked, "fork.bat", 20, 512, 1);
    assert!(result.status.success(), "stderr {}", result.stderr);
    assert!(
        fs::read_to_string(blocked.join("parent.txt"))
            .unwrap_or_default()
            .contains("parent-done"),
        "stderr {}",
        result.stderr
    );
    assert!(
        !blocked.join("child.txt").exists(),
        "active process limit allowed a child to start"
    );
}

fn write_fork_bats(workspace: &Path) {
    write_bat(
        workspace,
        "child.bat",
        "@echo off\r\necho child>child.txt\r\n",
    );
    write_bat(
        workspace,
        "fork.bat",
        "@echo off\r\nstart /b /wait \"\" cmd /c child.bat\r\necho parent-done>parent.txt\r\n",
    );
}

fn memory_bomb_is_blocked(backend: &str) {
    let workspace = temp_workspace("mem");
    let powershell = system_root()
        .join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe");
    let small = run(
        limits(backend, &workspace, 30, 1024, 32, false),
        &[
            powershell.as_os_str(),
            "-NoProfile".as_ref(),
            "-Command".as_ref(),
            "Set-Content -Path small.txt -Value ok".as_ref(),
        ],
    );
    if !fs::read_to_string(workspace.join("small.txt"))
        .unwrap_or_default()
        .contains("ok")
    {
        eprintln!(
            "memory bomb: powershell did not start under {backend} (exit {:?}); skipping the 800MB allocation. stderr {}",
            small.status.code(),
            small.stderr
        );
        return;
    }
    let bomb = run(
        limits(backend, &workspace, 20, 256, 32, false),
        &[
            powershell.as_os_str(),
            "-NoProfile".as_ref(),
            "-Command".as_ref(),
            "$b = New-Object byte[] (800MB); Set-Content -Path bomb.txt -Value allocated".as_ref(),
        ],
    );
    let allocated = fs::read_to_string(workspace.join("bomb.txt")).unwrap_or_default();
    assert!(
        !allocated.contains("allocated"),
        "800MB allocation succeeded under a 256MB job limit\nstderr {}",
        bomb.stderr
    );
}

fn timeout_kills_the_tree(backend: &str) {
    let workspace = temp_workspace("timeout");
    write_bat(
        &workspace,
        "sleep.bat",
        "@echo off\r\n:loop\r\necho tick>>ticks.txt\r\nfor /L %%j in (1,1,200000) do rem\r\ngoto loop\r\n",
    );
    let started = std::time::Instant::now();
    let result = run_bat(backend, &workspace, "sleep.bat", 3, 512, 8);
    assert_eq!(result.status.code(), Some(124), "stderr {}", result.stderr);
    assert!(
        started.elapsed() < Duration::from_secs(20),
        "timeout did not return"
    );
    let ticks = workspace.join("ticks.txt");
    let first = tick_count(&ticks);
    std::thread::sleep(Duration::from_millis(800));
    let second = tick_count(&ticks);
    assert_eq!(
        first, second,
        "process tree kept writing after the timeout ({first} then {second} ticks)"
    );
}

fn tick_count(path: &Path) -> usize {
    fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter(|line| line.contains("tick"))
        .count()
}

fn parent_env_is_cleared(backend: &str) {
    let workspace = temp_workspace("env");
    let host = std::env::var("COMPUTERNAME").unwrap_or_default();
    assert!(!host.is_empty(), "COMPUTERNAME is set on Windows");
    write_bat(
        &workspace,
        "env.bat",
        "@echo off\r\necho %COMPUTERNAME%>env.txt\r\n",
    );
    let result = run_bat(backend, &workspace, "env.bat", 20, 512, 32);
    let captured = fs::read_to_string(workspace.join("env.txt")).unwrap_or_default();
    assert!(
        !captured.contains(&host),
        "sandbox inherited COMPUTERNAME={host}\n{captured}\nstderr {}",
        result.stderr
    );
}

fn echo_overhead_is_within_budget(backend: &str) {
    let workspace = temp_workspace("echo");
    let warmup = run(
        limits(backend, &workspace, 15, 512, 32, false),
        &[cmd_exe().as_os_str(), "/c".as_ref(), "echo ok".as_ref()],
    );
    assert!(warmup.status.success(), "stderr {}", warmup.stderr);
    assert_eq!(warmup.stdout.trim(), "ok");
    let mut samples = Vec::new();
    for _ in 0..5 {
        let started = std::time::Instant::now();
        let result = run(
            limits(backend, &workspace, 15, 512, 32, false),
            &[cmd_exe().as_os_str(), "/c".as_ref(), "echo ok".as_ref()],
        );
        assert!(result.status.success(), "stderr {}", result.stderr);
        assert_eq!(result.stdout.trim(), "ok");
        samples.push(started.elapsed().as_secs_f64() * 1000.0);
    }
    samples.sort_by(f64::total_cmp);
    let median = samples[samples.len() / 2];
    eprintln!("echo overhead median via {backend}: {median:.2} ms");
    assert!(
        median < 200.0,
        "median {median:.2} ms exceeds the 200 ms budget"
    );
}

fn compat_matrix(backend: &str) {
    let cases = [
        ("cmd", Some(cmd_exe()), vec!["/c", "echo ok>compat.txt"]),
        (
            "powershell",
            Some(
                system_root()
                    .join("System32")
                    .join("WindowsPowerShell")
                    .join("v1.0")
                    .join("powershell.exe"),
            ),
            vec![
                "-NoProfile",
                "-Command",
                "Set-Content -Path compat.txt -Value ok",
            ],
        ),
        (
            "pwsh",
            which("pwsh.exe"),
            vec![
                "-NoProfile",
                "-Command",
                "Set-Content -Path compat.txt -Value ok",
            ],
        ),
        (
            "python",
            which("python.exe").or_else(|| which("python")),
            vec!["-c", "open('compat.txt','w').write('ok')"],
        ),
        (
            "git",
            which("git.exe").or_else(|| which("git")),
            vec!["--version"],
        ),
    ];
    for (name, program, args) in cases {
        let Some(program) = program else {
            eprintln!("compat {name}: not installed");
            continue;
        };
        let workspace = temp_workspace(&format!("compat-{name}"));
        let mut argv = vec![program.as_os_str()];
        argv.extend(args.iter().copied().map(std::ffi::OsStr::new));
        let result = run(limits(backend, &workspace, 40, 1024, 32, false), &argv);
        eprintln!(
            "compat {name}: exit {}",
            result
                .status
                .code()
                .map_or("-".to_owned(), |code| code.to_string())
        );
        if name == "cmd" {
            assert!(result.status.success(), "stderr {}", result.stderr);
            let text = fs::read_to_string(workspace.join("compat.txt")).unwrap_or_default();
            assert!(text.contains("ok"), "{text}");
        }
    }
}

fn run_bat(
    backend: &str,
    workspace: &Path,
    script: &str,
    timeout: u64,
    memory_mb: u64,
    nproc: u64,
) -> Run {
    run(
        limits(backend, workspace, timeout, memory_mb, nproc, false),
        &[cmd_exe().as_os_str(), "/c".as_ref(), script.as_ref()],
    )
}

fn limits(
    backend: &str,
    workspace: &Path,
    timeout: u64,
    memory_mb: u64,
    nproc: u64,
    network: bool,
) -> Command {
    let mut command = Command::new(BIN);
    command
        .args(["exec", "--backend", backend])
        .arg("--workspace")
        .arg(workspace)
        .arg("--timeout")
        .arg(timeout.to_string())
        .arg("--memory-mb")
        .arg(memory_mb.to_string())
        .arg("--nproc")
        .arg(nproc.to_string());
    if network {
        command.arg("--network");
    }
    command.arg("--");
    command
}

fn run(mut command: Command, args: &[&std::ffi::OsStr]) -> Run {
    let output = command
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .unwrap();
    Run {
        status: output.status,
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

fn write_bat(workspace: &Path, name: &str, body: &str) {
    fs::write(workspace.join(name), body).unwrap();
}

fn temp_workspace(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "verter-sandbox-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    let _ = fs::remove_dir_all(&path);
    fs::create_dir_all(&path).unwrap();
    path
}

fn tighten(path: &Path) {
    let user = Command::new("whoami").output().expect("whoami");
    let user = String::from_utf8_lossy(&user.stdout).trim().to_owned();
    let status = Command::new("icacls")
        .arg(path)
        .args(["/inheritance:r", "/grant:r"])
        .arg(format!("{user}:(OI)(CI)F"))
        .status()
        .expect("icacls");
    assert!(
        status.success(),
        "icacls could not tighten {}",
        path.display()
    );
}

fn host_can_reach_cloudflare() -> bool {
    let curl = system_root().join("System32").join("curl.exe");
    Command::new(curl)
        .args([
            "-sS",
            "--connect-timeout",
            "4",
            "--max-time",
            "8",
            "-o",
            "NUL",
            "https://1.1.1.1/",
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn system_root() -> PathBuf {
    PathBuf::from(std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_owned()))
}

fn cmd_exe() -> PathBuf {
    system_root().join("System32").join("cmd.exe")
}

fn which(name: &str) -> Option<PathBuf> {
    let output = Command::new("where.exe").arg(name).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8(output.stdout).ok()?;
    text.lines().next().map(PathBuf::from)
}

fn lock() -> std::sync::MutexGuard<'static, ()> {
    LOCK.lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
