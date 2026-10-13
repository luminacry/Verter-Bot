//! Wire protocol between the Verter-Bot core and `verter-sandbox serve`.
//!
//! Every message is a single JSON object on its own line (NDJSON), tagged by its
//! `type` field. The client must send [`ClientMessage::Hello`] before anything else.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Version of this protocol. Bump on any incompatible change.
pub const PROTOCOL_VERSION: u32 = 1;

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
            r#"{"type":"hello","client":"verter/test","protocol_version":1}"#
        );
        assert_eq!(
            serde_json::to_string(&ClientMessage::Shutdown).unwrap(),
            r#"{"type":"shutdown"}"#
        );
    }

    #[test]
    fn server_messages_round_trip() {
        let ack = ServerMessage::HelloAck {
            server_version: "0.1.0".into(),
            protocol_version: PROTOCOL_VERSION,
            platform: PlatformInfo {
                os: "linux".into(),
                arch: "x86_64".into(),
                backends: vec![BackendStatus {
                    kind: BackendKind::Bubblewrap,
                    available: true,
                    detail: "/usr/bin/bwrap".into(),
                }],
            },
        };
        let json = serde_json::to_string(&ack).unwrap();
        assert!(json.contains(r#""type":"hello_ack""#));
        assert!(json.contains(r#""kind":"bubblewrap""#));
        assert_eq!(serde_json::from_str::<ServerMessage>(&json).unwrap(), ack);
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
