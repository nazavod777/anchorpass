use crate::{Error, ErrorKind, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{
    de::{MapAccess, SeqAccess, Visitor},
    Deserialize, Deserializer,
};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fmt,
    time::{SystemTime, UNIX_EPOCH},
};

pub(crate) const MAX_BUNDLE: usize = 65536;
const MAX_FIELD_BYTES: usize = 16384;
const MAX_TIMESTAMP: u64 = 9_007_199_254_740_991;
pub(crate) const FAMILY: &str = "recaptcha-v3/1";
pub(crate) use crate::TrustedKeys;
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Payload {
    pub schema_version: u32,
    pub protocol_family: String,
    pub revision: String,
    pub issued_at: u64,
    pub expires_at: u64,
    pub fields: BTreeMap<String, String>,
}
#[derive(Clone)]
pub(crate) struct Bundle {
    pub payload: Payload,
    pub payload_bytes: Vec<u8>,
    pub envelope: Vec<u8>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    key_id: String,
    payload: String,
    signature: String,
}
fn invalid() -> Error {
    Error::new(ErrorKind::ConfigInvalid, "bundle", "invalid signed bundle")
}

// serde_json's default map decoder accepts duplicate keys. Reject them at every depth.
struct Unique(Value);
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct UniqueVisitor;
        impl<'de> Visitor<'de> for UniqueVisitor {
            type Value = Unique;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("JSON without duplicate keys")
            }
            fn visit_map<M: MapAccess<'de>>(
                self,
                mut map: M,
            ) -> std::result::Result<Unique, M::Error> {
                let mut value = serde_json::Map::new();
                while let Some((key, item)) = map.next_entry::<String, Unique>()? {
                    if value.insert(key, item.0).is_some() {
                        return Err(serde::de::Error::custom("duplicate JSON key"));
                    }
                }
                Ok(Unique(Value::Object(value)))
            }
            fn visit_seq<S: SeqAccess<'de>>(
                self,
                mut seq: S,
            ) -> std::result::Result<Unique, S::Error> {
                let mut a = Vec::new();
                while let Some(v) = seq.next_element::<Unique>()? {
                    a.push(v.0)
                }
                Ok(Unique(Value::Array(a)))
            }
            fn visit_str<E: serde::de::Error>(self, v: &str) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::String(v.to_owned())))
            }
            fn visit_bool<E: serde::de::Error>(self, v: bool) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Bool(v)))
            }
            fn visit_u64<E: serde::de::Error>(self, v: u64) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Number(v.into())))
            }
            fn visit_i64<E: serde::de::Error>(self, v: i64) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Number(v.into())))
            }
            fn visit_f64<E: serde::de::Error>(self, v: f64) -> std::result::Result<Unique, E> {
                serde_json::Number::from_f64(v)
                    .map(|v| Unique(Value::Number(v)))
                    .ok_or_else(|| E::custom("invalid number"))
            }
            fn visit_unit<E: serde::de::Error>(self) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
        }
        d.deserialize_any(UniqueVisitor)
    }
}
pub(crate) fn strict_json<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    let value: Unique = serde_json::from_slice(bytes).map_err(|_| invalid())?;
    serde_json::from_value(value.0).map_err(|_| invalid())
}
pub(crate) fn revision_valid(v: &str) -> bool {
    !v.is_empty() && v.len() <= 20 && v.bytes().all(|b| b.is_ascii_digit()) && !v.starts_with('0')
}
pub(crate) fn revision_cmp(a: &str, b: &str) -> std::cmp::Ordering {
    a.len().cmp(&b.len()).then_with(|| a.cmp(b))
}
pub(crate) fn now() -> Result<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|t| t.as_secs())
        .map_err(|_| invalid())
}
impl Bundle {
    pub fn verify(bytes: &[u8], keys: &TrustedKeys) -> Result<Self> {
        if bytes.len() > MAX_BUNDLE {
            return Err(invalid());
        }
        let env: Envelope = strict_json(bytes)?;
        if env.key_id.is_empty()
            || env.key_id.len() > 64
            || !env
                .key_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        {
            return Err(invalid());
        }
        let key = keys.get(&env.key_id).ok_or_else(invalid)?;
        let key: [u8; 32] = STANDARD
            .decode(key)
            .map_err(|_| invalid())?
            .try_into()
            .map_err(|_| invalid())?;
        let key = VerifyingKey::from_bytes(&key).map_err(|_| invalid())?;
        let payload_bytes = STANDARD.decode(&env.payload).map_err(|_| invalid())?;
        if payload_bytes.len() > MAX_BUNDLE {
            return Err(invalid());
        }
        let signature =
            Signature::from_slice(&STANDARD.decode(env.signature).map_err(|_| invalid())?)
                .map_err(|_| invalid())?;
        let mut message = b"anchorpass.bundle.v1\0".to_vec();
        message.extend_from_slice(env.key_id.as_bytes());
        message.push(0);
        message.extend_from_slice(&payload_bytes);
        key.verify_strict(&message, &signature)
            .map_err(|_| invalid())?;
        let payload: Payload = strict_json(&payload_bytes)?;
        if payload.schema_version != 1
            || payload.protocol_family != FAMILY
            || !revision_valid(&payload.revision)
            || payload.issued_at > MAX_TIMESTAMP
            || payload.expires_at > MAX_TIMESTAMP
            || payload.issued_at > now()?.saturating_add(300)
            || payload.expires_at <= payload.issued_at
        {
            return Err(invalid());
        }
        let expected = ["bg", "chr", "hl", "reason", "size", "vh"];
        if payload.fields.len() != expected.len()
            || expected.iter().any(|k| !payload.fields.contains_key(*k))
            || payload
                .fields
                .values()
                .any(|v| v.is_empty() || v.len() > MAX_FIELD_BYTES)
        {
            return Err(invalid());
        }
        Ok(Self {
            payload,
            payload_bytes,
            envelope: bytes.to_vec(),
        })
    }
    pub fn valid_for(&self, seconds: u64) -> Result<()> {
        if self.payload.expires_at <= now()?.saturating_add(seconds) {
            return Err(Error::new(
                ErrorKind::ConfigExpired,
                "bundle",
                "signed bundle expired or expires before operation deadline",
            ));
        }
        Ok(())
    }
    pub fn advance(&self, candidate: &Self) -> Result<()> {
        match revision_cmp(&candidate.payload.revision, &self.payload.revision) {
            std::cmp::Ordering::Less => Err(Error::new(
                ErrorKind::ConfigRollback,
                "bundle",
                "bundle revision is below accepted highwater",
            )),
            std::cmp::Ordering::Equal if candidate.payload_bytes != self.payload_bytes => {
                Err(invalid())
            }
            _ => Ok(()),
        }
    }
}
