//! NDJSON request loop for `verter-sandbox serve`.

use std::time::Instant;

use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use verter_sandbox_protocol::{
    ClientMessage, ErrorCode, ExecBackend, PROTOCOL_VERSION, PlatformInfo, ServerMessage,
    encode_b64,
};

/// Serves one client until it sends `shutdown` or closes its input.
pub async fn serve<R, W>(input: R, mut output: W, platform: PlatformInfo) -> std::io::Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut lines = BufReader::new(input).lines();
    let mut ready = false;
    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<ClientMessage>(&line) {
            Ok(ClientMessage::Shutdown) => break,
            Ok(ClientMessage::Hello {
                protocol_version, ..
            }) if protocol_version != PROTOCOL_VERSION => {
                write_msg(
                    &mut output,
                    &error(
                        ErrorCode::UnsupportedProtocolVersion,
                        format!(
                            "client speaks protocol {protocol_version}, runner speaks {PROTOCOL_VERSION}"
                        ),
                    ),
                )
                .await?;
            }
            Ok(ClientMessage::Hello { .. }) => {
                ready = true;
                write_msg(
                    &mut output,
                    &ServerMessage::HelloAck {
                        server_version: env!("CARGO_PKG_VERSION").to_owned(),
                        protocol_version: PROTOCOL_VERSION,
                        platform: platform.clone(),
                    },
                )
                .await?;
            }
            Ok(_) if !ready => {
                write_msg(
                    &mut output,
                    &error(ErrorCode::HandshakeRequired, "send hello first".to_owned()),
                )
                .await?;
            }
            Ok(ClientMessage::Ping { nonce }) => {
                write_msg(&mut output, &ServerMessage::Pong { nonce }).await?;
            }
            Ok(ClientMessage::Exec {
                id,
                command,
                workspace,
                backend,
                network,
                timeout_ms,
                memory_bytes,
                nproc,
            }) => {
                handle_exec(
                    &mut output,
                    ExecArgs {
                        id,
                        command,
                        workspace,
                        backend,
                        network,
                        timeout_ms,
                        memory_bytes,
                        nproc,
                    },
                )
                .await?;
            }
            Err(err) => {
                write_msg(
                    &mut output,
                    &error(ErrorCode::InvalidMessage, format!("invalid message: {err}")),
                )
                .await?;
            }
        }
    }
    output.flush().await
}

struct ExecArgs {
    id: u64,
    command: Vec<String>,
    workspace: String,
    backend: ExecBackend,
    network: bool,
    timeout_ms: u64,
    memory_bytes: u64,
    nproc: u64,
}

async fn handle_exec<W: AsyncWrite + Unpin>(output: &mut W, args: ExecArgs) -> std::io::Result<()> {
    if args.command.is_empty() {
        return write_msg(
            output,
            &error(ErrorCode::InvalidExec, "command is empty".to_owned()),
        )
        .await;
    }
    if args.timeout_ms == 0 {
        return write_msg(
            output,
            &error(
                ErrorCode::InvalidExec,
                "timeout must be greater than zero".to_owned(),
            ),
        )
        .await;
    }
    let id = args.id;
    let started = Instant::now();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<(bool, Vec<u8>)>();
    let join = tokio::task::spawn_blocking(move || run_isolated(args, tx));
    while let Some((is_err, bytes)) = rx.recv().await {
        if bytes.is_empty() {
            continue;
        }
        let chunk_b64 = encode_b64(&bytes);
        let msg = if is_err {
            ServerMessage::ExecStderr { id, chunk_b64 }
        } else {
            ServerMessage::ExecStdout { id, chunk_b64 }
        };
        write_msg(output, &msg).await?;
    }
    match join.await {
        Ok(Ok(outcome)) => {
            let wall_ms = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX);
            write_msg(
                output,
                &ServerMessage::ExecExit {
                    id,
                    exit_code: outcome.exit_code,
                    timed_out: outcome.timed_out,
                    backend: outcome.backend,
                    wall_ms,
                },
            )
            .await
        }
        Ok(Err((code, message))) => write_msg(output, &error(code, message)).await,
        Err(err) => {
            write_msg(
                output,
                &error(ErrorCode::InvalidExec, format!("exec task failed: {err}")),
            )
            .await
        }
    }
}

struct ExecOutcome {
    exit_code: i32,
    timed_out: bool,
    backend: verter_sandbox_protocol::BackendKind,
}

fn run_isolated(
    args: ExecArgs,
    tx: tokio::sync::mpsc::UnboundedSender<(bool, Vec<u8>)>,
) -> Result<ExecOutcome, (ErrorCode, String)> {
    #[cfg(target_os = "linux")]
    {
        run_linux(args, tx)
    }
    #[cfg(windows)]
    {
        run_windows(args, tx)
    }
    #[cfg(not(any(target_os = "linux", windows)))]
    {
        let _ = (args, tx);
        Err((
            ErrorCode::IsolationUnavailable,
            "exec is not available on this platform".to_owned(),
        ))
    }
}

#[cfg(target_os = "linux")]
fn run_linux(
    args: ExecArgs,
    tx: tokio::sync::mpsc::UnboundedSender<(bool, Vec<u8>)>,
) -> Result<ExecOutcome, (ErrorCode, String)> {
    use std::ffi::OsString;
    use std::path::PathBuf;
    use std::time::Duration;

    use verter_sandbox_linux::{ExecRequest, OutputCapture, RequestedBackend};

    let requested = match args.backend {
        ExecBackend::Auto => RequestedBackend::Auto,
        ExecBackend::Bubblewrap => RequestedBackend::Bubblewrap,
        ExecBackend::Landlock => RequestedBackend::Landlock,
        other => {
            return Err((
                ErrorCode::InvalidExec,
                format!("{} is not a Linux backend", other.as_str()),
            ));
        }
    };
    let helper = std::env::current_exe()
        .map_err(|err| (ErrorCode::InvalidExec, format!("current executable: {err}")))?;
    let timeout = Duration::from_millis(args.timeout_ms);
    let request = ExecRequest {
        backend: requested,
        command: args.command.into_iter().map(OsString::from).collect(),
        workspace: PathBuf::from(args.workspace),
        network: args.network,
        timeout,
        memory_bytes: args.memory_bytes,
        nproc: args.nproc,
        cpu_seconds: timeout.as_secs().max(1),
        env: Vec::new(),
    };
    let mut send = move |is_err: bool, bytes: &[u8]| {
        let _ = tx.send((is_err, bytes.to_vec()));
    };
    match verter_sandbox_linux::execute(&helper, &request, OutputCapture::Piped(&mut send)) {
        Ok(result) => {
            let exit_code = if result.timed_out {
                124
            } else {
                result.status.code().unwrap_or(128)
            };
            Ok(ExecOutcome {
                exit_code,
                timed_out: result.timed_out,
                backend: match result.backend {
                    verter_sandbox_linux::Backend::Bubblewrap => {
                        verter_sandbox_protocol::BackendKind::Bubblewrap
                    }
                    verter_sandbox_linux::Backend::Landlock => {
                        verter_sandbox_protocol::BackendKind::Landlock
                    }
                },
            })
        }
        Err(err) => Err(classify_exec_error(&err.to_string())),
    }
}

#[cfg(windows)]
fn run_windows(
    args: ExecArgs,
    tx: tokio::sync::mpsc::UnboundedSender<(bool, Vec<u8>)>,
) -> Result<ExecOutcome, (ErrorCode, String)> {
    use std::ffi::OsString;
    use std::path::PathBuf;
    use std::time::Duration;

    use verter_sandbox_windows::{ExecRequest, OutputCapture, RequestedBackend};

    let requested = match args.backend {
        ExecBackend::Auto => RequestedBackend::Auto,
        ExecBackend::AppContainer => RequestedBackend::AppContainer,
        ExecBackend::Isolated => RequestedBackend::Isolated,
        ExecBackend::Restricted => RequestedBackend::Restricted,
        other => {
            return Err((
                ErrorCode::InvalidExec,
                format!("{} is not a Windows backend", other.as_str()),
            ));
        }
    };
    let request = ExecRequest {
        backend: requested,
        command: args.command.into_iter().map(OsString::from).collect(),
        workspace: PathBuf::from(args.workspace),
        network: args.network,
        timeout: Duration::from_millis(args.timeout_ms),
        memory_bytes: args.memory_bytes,
        nproc: args.nproc,
    };
    let mut send = move |is_err: bool, bytes: &[u8]| {
        let _ = tx.send((is_err, bytes.to_vec()));
    };
    match verter_sandbox_windows::execute(&request, OutputCapture::Piped(&mut send)) {
        Ok(result) => {
            let exit_code = if result.timed_out {
                124
            } else {
                result
                    .exit_code
                    .and_then(|code| i32::try_from(code).ok())
                    .unwrap_or(128)
            };
            Ok(ExecOutcome {
                exit_code,
                timed_out: result.timed_out,
                backend: match result.backend {
                    verter_sandbox_windows::Backend::AppContainer => {
                        verter_sandbox_protocol::BackendKind::AppContainer
                    }
                    verter_sandbox_windows::Backend::Isolated => {
                        verter_sandbox_protocol::BackendKind::Isolated
                    }
                    verter_sandbox_windows::Backend::Restricted => {
                        verter_sandbox_protocol::BackendKind::Restricted
                    }
                },
            })
        }
        Err(err) => Err(classify_exec_error(&err.to_string())),
    }
}

fn classify_exec_error(message: &str) -> (ErrorCode, String) {
    let lower = message.to_ascii_lowercase();
    let code = if lower.contains("workspace") {
        ErrorCode::InvalidWorkspace
    } else if lower.contains("without isolation")
        || lower.contains("unsandboxed")
        || lower.contains("not initialized")
        || lower.contains("unavailable")
        || lower.contains("only available on")
        || lower.contains("cannot enforce")
    {
        ErrorCode::IsolationUnavailable
    } else {
        ErrorCode::InvalidExec
    };
    (code, message.to_owned())
}

async fn write_msg<W: AsyncWrite + Unpin>(
    output: &mut W,
    msg: &ServerMessage,
) -> std::io::Result<()> {
    let mut encoded = serde_json::to_vec(msg)?;
    encoded.push(b'\n');
    output.write_all(&encoded).await?;
    output.flush().await
}

fn error(code: ErrorCode, message: String) -> ServerMessage {
    ServerMessage::Error { code, message }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn platform() -> PlatformInfo {
        PlatformInfo {
            os: "test".into(),
            arch: "test".into(),
            backends: Vec::new(),
        }
    }

    async fn run(input: &str) -> Vec<ServerMessage> {
        let mut output = Vec::new();
        serve(input.as_bytes(), &mut output, platform())
            .await
            .unwrap();
        String::from_utf8(output)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    fn hello(version: u32) -> String {
        format!(r#"{{"type":"hello","client":"test","protocol_version":{version}}}"#)
    }

    #[tokio::test]
    async fn handshake_then_ping_then_shutdown() {
        let input = format!(
            "{}\n{}\n{}\n{}\n",
            hello(PROTOCOL_VERSION),
            r#"{"type":"ping","nonce":42}"#,
            r#"{"type":"shutdown"}"#,
            r#"{"type":"ping","nonce":7}"#,
        );
        let replies = run(&input).await;
        assert_eq!(replies.len(), 2, "messages after shutdown must be ignored");
        assert!(matches!(
            &replies[0],
            ServerMessage::HelloAck { protocol_version, .. } if *protocol_version == PROTOCOL_VERSION
        ));
        assert_eq!(replies[1], ServerMessage::Pong { nonce: 42 });
    }

    #[tokio::test]
    async fn ping_before_hello_is_rejected() {
        let replies = run("{\"type\":\"ping\",\"nonce\":1}\n").await;
        assert!(matches!(
            &replies[0],
            ServerMessage::Error {
                code: ErrorCode::HandshakeRequired,
                ..
            }
        ));
    }

    #[tokio::test]
    async fn unsupported_version_and_garbage_are_reported() {
        let input = format!("{}\nnot json\n\n", hello(PROTOCOL_VERSION + 1));
        let replies = run(&input).await;
        assert_eq!(replies.len(), 2);
        assert!(matches!(
            &replies[0],
            ServerMessage::Error {
                code: ErrorCode::UnsupportedProtocolVersion,
                ..
            }
        ));
        assert!(matches!(
            &replies[1],
            ServerMessage::Error {
                code: ErrorCode::InvalidMessage,
                ..
            }
        ));
    }

    #[tokio::test]
    async fn empty_exec_is_invalid_without_running() {
        let input = format!(
            "{}\n{}\n",
            hello(PROTOCOL_VERSION),
            r#"{"type":"exec","id":1,"command":[],"workspace":"/tmp","backend":"auto","network":false,"timeout_ms":1000,"memory_bytes":1,"nproc":1}"#,
        );
        let replies = run(&input).await;
        assert_eq!(replies.len(), 2);
        assert!(matches!(replies[0], ServerMessage::HelloAck { .. }));
        assert!(matches!(
            &replies[1],
            ServerMessage::Error {
                code: ErrorCode::InvalidExec,
                message
            } if message.contains("empty")
        ));
    }
}
