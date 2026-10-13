//! Security subset for the Linux sandbox prototype.
//!
//! The suite runs under `auto` (bubblewrap when a user namespace works, otherwise Landlock).
//! When the other backend is also available it runs again. When bubblewrap cannot isolate,
//! requesting it must fail without running the command.

#![cfg(target_os = "linux")]

use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Output, Stdio};
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

#[test]
fn doctor_reports_auto_and_refuses_without_isolation() {
    let _guard = lock();
    let output = Command::new(BIN).arg("doctor").output().unwrap();
    assert!(output.status.success());
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(text.contains("auto:"), "{text}");
    assert!(
        text.contains("policy: exec refuses to run without isolation"),
        "{text}"
    );
    assert!(text.contains("fallback:"), "{text}");
}

#[test]
fn setup_does_not_require_sudo_when_apparmor_is_inactive() {
    let _guard = lock();
    let output = Command::new(BIN).arg("setup").output().unwrap();
    let text = String::from_utf8(output.stdout).unwrap();
    let lsm = fs::read_to_string("/sys/kernel/security/lsm").unwrap_or_default();
    if lsm.split(',').any(|name| name.trim() == "apparmor") {
        assert!(output.status.success());
        assert!(
            text.contains("sudo verter-sandbox setup --install"),
            "{text}"
        );
    } else {
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(text.contains("not the active LSM"), "{text}");
    }
}

#[test]
fn security_suite_under_auto_and_the_other_backend() {
    let _guard = lock();
    let auto = echo(backend_args("auto"));
    let selected = isolation_backend(&auto);
    security_suite(selected);
    let other = if selected == "bubblewrap" {
        "landlock"
    } else {
        "bubblewrap"
    };
    if other == "bubblewrap" && !bubblewrap_available() {
        let command = limits("bubblewrap", &temp_workspace("refuse"), 5, 256, 256);
        let refused = run(command, &["/usr/bin/echo", "should-not-run"]);
        assert!(!refused.status.success(), "{}", refused.stderr);
        assert!(
            refused.stderr.contains("refusing") || refused.stderr.contains("unsandboxed"),
            "{}",
            refused.stderr
        );
        assert!(!refused.stdout.contains("should-not-run"));
        return;
    }
    security_suite(other);
}

fn security_suite(backend: &str) {
    escape_write_is_blocked(backend);
    sensitive_read_is_blocked(backend);
    outbound_network_is_blocked(backend);
    fork_bomb_is_capped(backend);
    memory_bomb_is_blocked(backend);
    timeout_kills_the_tree(backend);
    parent_env_is_cleared(backend);
    echo_overhead_is_within_budget(backend);
}

fn escape_write_is_blocked(backend: &str) {
    let workspace = temp_workspace("write");
    let outside = temp_workspace("outside");
    let secret = outside.join("secret");
    fs::write(&secret, "original").unwrap();
    let script =
        "open('inside.txt','w').write('ok')\nopen(__import__('sys').argv[1],'w').write('pwned')\n";
    let result = run(
        limits(backend, &workspace, 10, 256, 256),
        &["/usr/bin/python3", "-c", script, secret.to_str().unwrap()],
    );
    assert_eq!(
        fs::read_to_string(workspace.join("inside.txt")).unwrap(),
        "ok",
        "stdout {} stderr {}",
        result.stdout,
        result.stderr
    );
    assert_eq!(
        fs::read_to_string(&secret).unwrap(),
        "original",
        "stdout {} stderr {}",
        result.stdout,
        result.stderr
    );
}

fn sensitive_read_is_blocked(backend: &str) {
    let workspace = temp_workspace("read");
    let secret = temp_workspace("secret-dir").join("config.yaml");
    fs::write(&secret, "TOP-SECRET-VALUE").unwrap();
    let script = "print(open(__import__('sys').argv[1]).read())";
    let result = run(
        limits(backend, &workspace, 10, 256, 256),
        &["/usr/bin/python3", "-c", script, secret.to_str().unwrap()],
    );
    assert!(
        !result.stdout.contains("TOP-SECRET-VALUE"),
        "stdout {} stderr {}",
        result.stdout,
        result.stderr
    );
    assert!(
        !result.status.success(),
        "read succeeded: {}",
        result.stdout
    );
}

fn outbound_network_is_blocked(backend: &str) {
    assert!(
        host_can_connect(),
        "the host cannot open a TCP connection, so a sandbox failure would be invisible"
    );
    let script = concat!(
        "import socket,sys\n",
        "s=socket.socket();s.settimeout(3)\n",
        "try:\n",
        "    s.connect(('1.1.1.1',443))\n",
        "except OSError:\n",
        "    print('blocked'); sys.exit(0)\n",
        "print('connected'); sys.exit(1)\n",
    );
    let result = run(
        limits(backend, &temp_workspace("net"), 10, 256, 256),
        &["/usr/bin/python3", "-c", script],
    );
    assert!(
        result.stdout.contains("blocked"),
        "stdout {} stderr {}",
        result.stdout,
        result.stderr
    );
    assert!(!result.stdout.contains("connected"));
}

fn fork_bomb_is_capped(backend: &str) {
    let nproc = uid_threads() + 40;
    let script = concat!(
        "import os,sys\n",
        "n=0\n",
        "limit=500\n",
        "try:\n",
        "    while n<limit:\n",
        "        pid=os.fork()\n",
        "        if pid==0: os._exit(0)\n",
        "        n+=1\n",
        "except OSError:\n",
        "    print(n); sys.exit(0)\n",
        "print(n); sys.exit(1)\n",
    );
    let result = run(
        limits(backend, &temp_workspace("fork"), 15, 256, nproc),
        &["/usr/bin/python3", "-c", script],
    );
    let forks: u64 = result
        .stdout
        .trim()
        .lines()
        .next_back()
        .unwrap_or("0")
        .parse()
        .unwrap_or(0);
    assert!(
        result.status.success(),
        "stdout {} stderr {}",
        result.stdout,
        result.stderr
    );
    assert!(
        (1..400).contains(&forks),
        "forks={forks} nproc={nproc} stderr {}",
        result.stderr
    );
}

fn memory_bomb_is_blocked(backend: &str) {
    let script = concat!(
        "import sys\n",
        "try:\n",
        "    blob=bytearray(800*1024*1024)\n",
        "    print('allocated', len(blob)); sys.exit(1)\n",
        "except MemoryError:\n",
        "    print('blocked'); sys.exit(0)\n",
    );
    let result = run(
        limits(backend, &temp_workspace("mem"), 15, 128, 256),
        &["/usr/bin/python3", "-c", script],
    );
    assert!(
        result.stdout.contains("blocked"),
        "stdout {} stderr {}",
        result.stdout,
        result.stderr
    );
    assert!(!result.stdout.contains("allocated"));
}

fn timeout_kills_the_tree(backend: &str) {
    let workspace = temp_workspace("tree");
    let script = concat!(
        "import os,time\n",
        "if os.fork()==0:\n",
        "    if os.fork()==0:\n",
        "        while True:\n",
        "            handle=open('heartbeat','a')\n",
        "            handle.write('x')\n",
        "            handle.flush()\n",
        "            handle.close()\n",
        "            time.sleep(0.05)\n",
        "    os._exit(0)\n",
        "time.sleep(60)\n",
    );
    let result = run(
        limits(backend, &workspace, 2, 256, 256),
        &["/usr/bin/python3", "-c", script],
    );
    assert_eq!(result.status.code(), Some(124), "stderr {}", result.stderr);
    let heartbeat = workspace.join("heartbeat");
    assert!(
        heartbeat.exists(),
        "grandchild never wrote a heartbeat; stderr {}",
        result.stderr
    );
    let before = fs::metadata(&heartbeat).unwrap().len();
    std::thread::sleep(Duration::from_millis(400));
    let after = fs::metadata(&heartbeat).unwrap().len();
    assert_eq!(before, after, "grandchild kept writing after the timeout");
}

fn parent_env_is_cleared(backend: &str) {
    let script = "import os\nprint(os.environ.get('VERTER_SECRET','absent'))";
    let mut command = limits(backend, &temp_workspace("env"), 10, 256, 256);
    command.env("VERTER_SECRET", "hunter2");
    let result = run(command, &["/usr/bin/python3", "-c", script]);
    assert!(
        result.status.success(),
        "stdout {} stderr {}",
        result.stdout,
        result.stderr
    );
    assert_eq!(result.stdout.trim(), "absent", "stderr {}", result.stderr);
}

fn echo_overhead_is_within_budget(backend: &str) {
    let workspace = temp_workspace("echo");
    let warmup = run(
        limits(backend, &workspace, 10, 256, 256),
        &["/usr/bin/echo", "ok"],
    );
    assert!(warmup.status.success(), "{}", warmup.stderr);
    let mut samples = Vec::new();
    for _ in 0..5 {
        let started = std::time::Instant::now();
        let result = run(
            limits(backend, &workspace, 10, 256, 256),
            &["/usr/bin/echo", "ok"],
        );
        assert!(result.status.success(), "{}", result.stderr);
        assert_eq!(result.stdout.trim(), "ok");
        samples.push(started.elapsed().as_secs_f64() * 1000.0);
    }
    samples.sort_by(f64::total_cmp);
    let median = samples[samples.len() / 2];
    eprintln!("echo overhead median via {backend}: {median:.2} ms");
    assert!(
        median < 50.0,
        "median {median:.2} ms exceeds the 50 ms budget"
    );
}

fn echo(command: Command) -> Run {
    run(command, &["/usr/bin/echo", "ok"])
}

fn isolation_backend(result: &Run) -> &'static str {
    assert!(result.status.success(), "stderr {}", result.stderr);
    if result.stderr.contains("isolation landlock") {
        "landlock"
    } else if result.stderr.contains("isolation bubblewrap") {
        "bubblewrap"
    } else {
        panic!("missing isolation line: {}", result.stderr);
    }
}

fn bubblewrap_available() -> bool {
    let output = Command::new(BIN)
        .args(["doctor", "--json"])
        .output()
        .unwrap();
    let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    report["backends"]
        .as_array()
        .and_then(|items| items.iter().find(|item| item["kind"] == "bubblewrap"))
        .and_then(|item| item["available"].as_bool())
        .unwrap_or(false)
}

fn limits(backend: &str, workspace: &Path, timeout: u64, memory_mb: u64, nproc: u64) -> Command {
    let mut command = Command::new(BIN);
    command
        .args(["exec", "--backend", backend, "--workspace"])
        .arg(workspace)
        .arg("--timeout")
        .arg(timeout.to_string())
        .arg("--memory-mb")
        .arg(memory_mb.to_string())
        .arg("--nproc")
        .arg(nproc.to_string())
        .args(["--cpu", "20", "--"])
        .env_remove("VERTER_SECRET")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command
}

fn backend_args(backend: &str) -> Command {
    limits(backend, &temp_workspace("which"), 10, 256, 256)
}

fn run(mut command: Command, args: &[&str]) -> Run {
    command.args(args);
    finish(&command.output().unwrap())
}

fn finish(output: &Output) -> Run {
    Run {
        status: output.status,
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

fn temp_workspace(label: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "verter-sandbox-{label}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    fs::create_dir_all(&path).unwrap();
    path
}

fn uid_threads() -> u64 {
    let uid = fs::metadata("/proc/self").map_or(0, |meta| meta.uid());
    let mut threads = 0_u64;
    for entry in fs::read_dir("/proc").unwrap().flatten() {
        let Some(text) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        if !text.bytes().all(|byte| byte.is_ascii_digit()) {
            continue;
        }
        let status = fs::read_to_string(entry.path().join("status")).unwrap_or_default();
        let Some(owner) = status.lines().find_map(|line| line.strip_prefix("Uid:\t")) else {
            continue;
        };
        if owner.split_whitespace().next() != Some(uid.to_string().as_str()) {
            continue;
        }
        let Some(count) = status
            .lines()
            .find_map(|line| line.strip_prefix("Threads:\t"))
        else {
            continue;
        };
        threads += count.trim().parse::<u64>().unwrap_or(1);
    }
    threads
}

fn host_can_connect() -> bool {
    Command::new("/usr/bin/python3")
        .args([
            "-c",
            "import socket; socket.create_connection(('1.1.1.1', 443), 3)",
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn lock() -> std::sync::MutexGuard<'static, ()> {
    LOCK.lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
