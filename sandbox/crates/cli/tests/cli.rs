use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

const BIN: &str = env!("CARGO_BIN_EXE_verter-sandbox");

#[test]
fn version_flag_prints_package_version() {
    let output = Command::new(BIN).arg("--version").output().unwrap();
    assert!(output.status.success());
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert_eq!(
        stdout.trim(),
        format!("verter-sandbox {}", env!("CARGO_PKG_VERSION"))
    );
}

#[test]
fn schema_command_prints_committed_schema() {
    let output = Command::new(BIN).arg("schema").output().unwrap();
    assert!(output.status.success());
    let committed = include_str!("../../../schema/protocol.schema.json").replace("\r\n", "\n");
    assert_eq!(String::from_utf8(output.stdout).unwrap(), committed);
}

#[test]
fn doctor_json_reports_host_platform() {
    let output = Command::new(BIN)
        .args(["doctor", "--json"])
        .output()
        .unwrap();
    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["os"], std::env::consts::OS);
    assert!(report["backends"].is_array());
}

#[test]
fn serve_speaks_ndjson_over_stdio() {
    let mut child = Command::new(BIN)
        .arg("serve")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());

    writeln!(
        stdin,
        r#"{{"type":"hello","client":"test","protocol_version":2}}"#
    )
    .unwrap();
    let mut line = String::new();
    stdout.read_line(&mut line).unwrap();
    let ack: serde_json::Value = serde_json::from_str(&line).unwrap();
    assert_eq!(ack["type"], "hello_ack");
    assert_eq!(ack["platform"]["os"], std::env::consts::OS);

    writeln!(stdin, r#"{{"type":"ping","nonce":9}}"#).unwrap();
    line.clear();
    stdout.read_line(&mut line).unwrap();
    assert_eq!(line.trim(), r#"{"type":"pong","nonce":9}"#);

    writeln!(stdin, r#"{{"type":"shutdown"}}"#).unwrap();
    assert!(child.wait().unwrap().success());
}

#[cfg(any(target_os = "linux", windows))]
#[test]
fn serve_exec_echo() {
    use std::fs;

    let workspace = std::env::temp_dir().join(format!(
        "verter-sandbox-serve-echo-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    fs::create_dir_all(&workspace).unwrap();

    let mut child = Command::new(BIN)
        .arg("serve")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());

    writeln!(
        stdin,
        r#"{{"type":"hello","client":"test","protocol_version":2}}"#
    )
    .unwrap();
    let mut line = String::new();
    stdout.read_line(&mut line).unwrap();
    let ack: serde_json::Value = serde_json::from_str(&line).unwrap();
    assert_eq!(ack["type"], "hello_ack");
    assert_eq!(ack["protocol_version"], 2);

    let command = if cfg!(windows) {
        serde_json::json!(["cmd", "/c", "echo ok"])
    } else {
        serde_json::json!(["/usr/bin/echo", "ok"])
    };
    let exec = serde_json::json!({
        "type": "exec",
        "id": 1,
        "command": command,
        "workspace": workspace,
        "backend": "auto",
        "network": false,
        "timeout_ms": 15_000,
        "memory_bytes": 256 * 1024 * 1024,
        "nproc": 64,
    });
    writeln!(stdin, "{exec}").unwrap();

    let mut stdout_b64 = String::new();
    let mut exit = None;
    for _ in 0..32 {
        line.clear();
        stdout.read_line(&mut line).unwrap();
        assert!(!line.is_empty(), "serve closed stdout before exec_exit");
        let msg: serde_json::Value = serde_json::from_str(&line).unwrap();
        match msg["type"].as_str() {
            Some("exec_stdout") => {
                stdout_b64.push_str(msg["chunk_b64"].as_str().unwrap_or(""));
            }
            Some("exec_stderr") => {}
            Some("exec_exit") => {
                exit = Some(msg);
                break;
            }
            Some("error") => {
                panic!("exec refused: {msg}");
            }
            other => {
                panic!("unexpected serve message {other:?}: {msg}");
            }
        }
    }
    let exit = exit.expect("missing exec_exit");
    assert_eq!(exit["id"], 1);
    assert_eq!(exit["exit_code"], 0);
    assert_eq!(exit["timed_out"], serde_json::Value::Bool(false));
    let backend = exit["backend"].as_str().unwrap_or("");
    if cfg!(windows) {
        assert!(
            matches!(backend, "appcontainer" | "isolated"),
            "backend {backend}"
        );
    } else {
        assert!(
            matches!(backend, "bubblewrap" | "landlock"),
            "backend {backend}"
        );
    }
    assert!(!stdout_b64.is_empty(), "no stdout chunks");

    writeln!(stdin, r#"{{"type":"shutdown"}}"#).unwrap();
    assert!(child.wait().unwrap().success());
}
