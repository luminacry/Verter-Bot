//! NDJSON request loop for `verter-sandbox serve`.

use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use verter_sandbox_protocol::{
    ClientMessage, ErrorCode, PROTOCOL_VERSION, PlatformInfo, ServerMessage,
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
        let reply = match serde_json::from_str::<ClientMessage>(&line) {
            Ok(ClientMessage::Shutdown) => break,
            Ok(ClientMessage::Hello {
                protocol_version, ..
            }) if protocol_version != PROTOCOL_VERSION => error(
                ErrorCode::UnsupportedProtocolVersion,
                format!(
                    "client speaks protocol {protocol_version}, runner speaks {PROTOCOL_VERSION}"
                ),
            ),
            Ok(ClientMessage::Hello { .. }) => {
                ready = true;
                ServerMessage::HelloAck {
                    server_version: env!("CARGO_PKG_VERSION").to_owned(),
                    protocol_version: PROTOCOL_VERSION,
                    platform: platform.clone(),
                }
            }
            Ok(_) if !ready => error(ErrorCode::HandshakeRequired, "send hello first".to_owned()),
            Ok(ClientMessage::Ping { nonce }) => ServerMessage::Pong { nonce },
            Err(err) => error(ErrorCode::InvalidMessage, format!("invalid message: {err}")),
        };
        let mut encoded = serde_json::to_vec(&reply)?;
        encoded.push(b'\n');
        output.write_all(&encoded).await?;
        output.flush().await?;
    }
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
}
