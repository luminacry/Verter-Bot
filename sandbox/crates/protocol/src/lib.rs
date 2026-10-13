//! Wire protocol between the Verter-Bot core and `verter-sandbox serve`.
//!
//! Every message is a single JSON object on its own line (NDJSON), tagged by its
//! `type` field. The client must send [`ClientMessage::Hello`] before anything else.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Version of this protocol. Bump on any incompatible change.
pub const PROTOCOL_VERSION: u32 = 2;

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// Standard base64 (RFC 4648) with padding. Used for `exec` output chunks so NDJSON
/// is never split by binary bytes.
#[must_use]
pub fn encode_b64(input: &[u8]) -> String {
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let first = chunk[0];
        let second = chunk.get(1).copied().unwrap_or(0);
        let third = chunk.get(2).copied().unwrap_or(0);
        out.push(char::from(B64[(first >> 2) as usize]));
        out.push(char::from(
            B64[(((first & 0b11) << 4) | (second >> 4)) as usize],
        ));
        if chunk.len() > 1 {
            out.push(char::from(
                B64[(((second & 0b1111) << 2) | (third >> 6)) as usize],
            ));
        } else {
            out.push('=');
        }
        if chunk.len() > 2 {
            out.push(char::from(B64[(third & 0b11_1111) as usize]));
        } else {
            out.push('=');
        }
    }
    out
}

/// Messages sent by the Verter-Bot core to the runner.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMessage {
    /// Handshake. Must be the first message on a connection.
    #[schemars(title = "Hello")]
    Hello {
        /// Client identifier, for example `verter/0.1.0`.
        client: String,
        /// Protocol version the client speaks.
        protocol_version: u32,
    },
    /// Liveness check, answered with [`ServerMessage::Pong`].
    #[schemars(title = "Ping")]
    Ping {
        /// Opaque value echoed back in the reply.
        nonce: u64,
    },
    /// Run a command inside an isolation backend. Refusals use the error channel.
    #[schemars(title = "Exec")]
    Exec {
        /// Correlation id echoed in stdout, stderr, and exit messages.
        id: u64,
        /// Program and arguments. The first item is the executable.
        command: Vec<String>,
        /// Writable workspace directory on the host.
        workspace: String,
        /// Isolation backend. `auto` never falls through to the host.
        backend: ExecBackend,
        /// When true, outbound network is requested.
        network: bool,
        /// Wall-clock limit in milliseconds.
        timeout_ms: u64,
        /// Address-space / job memory limit in bytes.
        memory_bytes: u64,
        /// Process-count limit inside the sandbox.
        nproc: u64,
    },
    /// Ask the runner to exit.
    #[schemars(title = "Shutdown")]
    Shutdown,
}

/// Messages sent by the runner to the Verter-Bot core.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    /// Successful handshake.
    #[schemars(title = "HelloAck")]
    HelloAck {
        /// Runner version.
        server_version: String,
        /// Protocol version the runner speaks.
        protocol_version: u32,
        /// Host platform and isolation backends detected by the runner.
        platform: PlatformInfo,
    },
    /// Reply to [`ClientMessage::Ping`].
    #[schemars(title = "Pong")]
    Pong {
        /// The nonce from the matching ping.
        nonce: u64,
    },
    /// One chunk of the command's standard output, base64-encoded.
    #[schemars(title = "ExecStdout")]
    ExecStdout {
        /// The `id` from the matching [`ClientMessage::Exec`].
        id: u64,
        /// Standard-output bytes, standard base64 with padding.
        chunk_b64: String,
    },
    /// One chunk of the command's standard error, base64-encoded.
    #[schemars(title = "ExecStderr")]
    ExecStderr {
        /// The `id` from the matching [`ClientMessage::Exec`].
        id: u64,
        /// Standard-error bytes, standard base64 with padding.
        chunk_b64: String,
    },
    /// The sandboxed command has exited. Always the last message for an `exec`.
    #[schemars(title = "ExecExit")]
    ExecExit {
        /// The `id` from the matching [`ClientMessage::Exec`].
        id: u64,
        /// Process exit code. `124` when the wall-clock limit fired.
        exit_code: i32,
        /// True when the wall-clock limit fired and the process tree was killed.
        timed_out: bool,
        /// Backend that actually isolated the command.
        backend: BackendKind,
        /// Wall-clock milliseconds from spawn until exit.
        wall_ms: u64,
    },
    /// The previous message was rejected.
    #[schemars(title = "ErrorReply")]
    Error {
        /// Machine-readable error kind.
        code: ErrorCode,
        /// Human-readable description.
        message: String,
    },
}

/// Host platform reported during the handshake.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct PlatformInfo {
    /// Operating system, as in Rust's `std::env::consts::OS`.
    pub os: String,
    /// CPU architecture, as in Rust's `std::env::consts::ARCH`.
    pub arch: String,
    /// Isolation backends known on this platform.
    pub backends: Vec<BackendStatus>,
}

/// Availability of one isolation backend.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct BackendStatus {
    /// Backend identifier.
    pub kind: BackendKind,
    /// Whether the backend can be used on this host.
    pub available: bool,
    /// Location of the backend or the reason it is unavailable.
    pub detail: String,
}

/// Isolation backend identifiers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum BackendKind {
    /// Linux namespaces via the `bwrap` executable.
    Bubblewrap,
    /// Linux Landlock, seccomp, and rlimits without namespaces.
    Landlock,
    /// Windows lowbox `AppContainer` plus a Job Object.
    #[serde(rename = "appcontainer")]
    AppContainer,
    /// Windows local user created by `setup`, plus a Job Object.
    Isolated,
    /// Windows restricted token plus a Job Object. Network denial is not enforced.
    Restricted,
}

/// Backend requested by [`ClientMessage::Exec`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExecBackend {
    /// Host default. Never falls through to an unsandboxed run.
    Auto,
    /// Linux namespaces via the `bwrap` executable.
    Bubblewrap,
    /// Linux Landlock, seccomp, and rlimits without namespaces.
    Landlock,
    /// Windows lowbox `AppContainer` plus a Job Object.
    #[serde(rename = "appcontainer")]
    AppContainer,
    /// Windows local user created by `setup`, plus a Job Object.
    Isolated,
    /// Windows restricted token plus a Job Object.
    Restricted,
}

impl ExecBackend {
    /// Snake-case name used in error messages.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Bubblewrap => "bubblewrap",
            Self::Landlock => "landlock",
            Self::AppContainer => "appcontainer",
            Self::Isolated => "isolated",
            Self::Restricted => "restricted",
        }
    }
}

/// Reasons a message can be rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    /// The line was not a valid client message.
    InvalidMessage,
    /// A message other than `hello` or `shutdown` arrived before the handshake.
    HandshakeRequired,
    /// The client's protocol version is not supported.
    UnsupportedProtocolVersion,
    /// No isolation backend can run the command; it was not started on the host.
    IsolationUnavailable,
    /// The workspace path is missing, is not a directory, or cannot be used.
    InvalidWorkspace,
    /// The `exec` request was malformed or cannot be honored on this platform.
    InvalidExec,
}

/// Root type that exports both message directions in one schema document.
#[derive(JsonSchema)]
pub struct Protocol {
    /// Client-to-runner messages.
    pub client: ClientMessage,
    /// Runner-to-client messages.
    pub server: ServerMessage,
}

/// Returns the protocol JSON Schema, pretty-printed with a trailing newline.
///
/// # Panics
///
/// Never panics in practice: serializing a generated schema cannot fail.
#[must_use]
pub fn schema_json() -> String {
    let schema = schemars::schema_for!(Protocol);
    let mut out = serde_json::to_string_pretty(&schema).expect("schema serializes to JSON");
    out.push('\n');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn client_messages_use_snake_case_type_tags() {
        let hello = ClientMessage::Hello {
            client: "verter/test".into(),
            protocol_version: PROTOCOL_VERSION,
        };
        assert_eq!(
            serde_json::to_string(&hello).unwrap(),
            format!(
                r#"{{"type":"hello","client":"verter/test","protocol_version":{PROTOCOL_VERSION}}}"#
            )
        );
        assert_eq!(
            serde_json::to_string(&ClientMessage::Shutdown).unwrap(),
            r#"{"type":"shutdown"}"#
        );
        let exec = ClientMessage::Exec {
            id: 1,
            command: vec!["cmd".into(), "/c".into(), "echo ok".into()],
            workspace: r"C:\sessions\1".into(),
            backend: ExecBackend::Auto,
            network: false,
            timeout_ms: 30_000,
            memory_bytes: 536_870_912,
            nproc: 256,
        };
        let json = serde_json::to_string(&exec).unwrap();
        assert!(json.contains(r#""type":"exec""#));
        assert!(json.contains(r#""backend":"auto""#));
    }

    #[test]
    fn server_messages_round_trip() {
        let ack = ServerMessage::HelloAck {
            server_version: "0.1.0".into(),
            protocol_version: PROTOCOL_VERSION,
            platform: PlatformInfo {
                os: "linux".into(),
                arch: "x86_64".into(),
                backends: vec![
                    BackendStatus {
                        kind: BackendKind::Bubblewrap,
                        available: true,
                        detail: "/usr/bin/bwrap".into(),
                    },
                    BackendStatus {
                        kind: BackendKind::Landlock,
                        available: true,
                        detail: "landlock enforced".into(),
                    },
                ],
            },
        };
        let json = serde_json::to_string(&ack).unwrap();
        assert!(json.contains(r#""type":"hello_ack""#));
        assert!(json.contains(r#""kind":"bubblewrap""#));
        assert!(json.contains(r#""kind":"landlock""#));
        assert_eq!(serde_json::from_str::<ServerMessage>(&json).unwrap(), ack);

        let exit = ServerMessage::ExecExit {
            id: 1,
            exit_code: 0,
            timed_out: false,
            backend: BackendKind::AppContainer,
            wall_ms: 40,
        };
        let encoded = serde_json::to_string(&exit).unwrap();
        assert!(encoded.contains(r#""type":"exec_exit""#));
        assert!(encoded.contains(r#""backend":"appcontainer""#));
        assert_eq!(
            serde_json::from_str::<ServerMessage>(&encoded).unwrap(),
            exit
        );
    }

    #[test]
    fn exec_chunks_use_standard_base64() {
        assert_eq!(encode_b64(b"ok\r\n"), "b2sNCg==");
        assert_eq!(encode_b64(b"ok\n"), "b2sK");
        let stdout = ServerMessage::ExecStdout {
            id: 1,
            chunk_b64: encode_b64(b"ok\r\n"),
        };
        assert_eq!(
            serde_json::to_string(&stdout).unwrap(),
            r#"{"type":"exec_stdout","id":1,"chunk_b64":"b2sNCg=="}"#
        );
    }

    #[test]
    fn committed_schema_matches_types() {
        let committed = include_str!("../../../schema/protocol.schema.json").replace("\r\n", "\n");
        assert_eq!(
            committed,
            schema_json(),
            "sandbox/schema/protocol.schema.json is stale; run `just codegen`"
        );
    }
}
