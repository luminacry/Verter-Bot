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
        r#"{{"type":"hello","client":"test","protocol_version":1}}"#
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
