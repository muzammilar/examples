//! Checksummed framing for records on disk.
//!
//! ```text
//! +----------+----------+-----------------+--------------------+
//! | len: u32 | crc: u32 | header_crc: u32 | payload: len bytes |
//! +----------+----------+-----------------+--------------------+
//! ```
//!
//! Integers are little-endian. `crc` is the CRC-32 of the payload and
//! `header_crc` the CRC-32 of the eight bytes before it. Because the header
//! has its own checksum, a damaged length can't pass for a frame that runs
//! off the end of the file.

use std::io::{self, Read};

const HEADER_LEN: usize = 12;

/// Largest payload we will read back. A corrupt length field must not be able
/// to make replay allocate gigabytes.
pub(crate) const MAX_PAYLOAD_LEN: usize = 64 * 1024 * 1024;

/// Outcome of reading the next frame.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Decoded<'a> {
    Frame {
        payload: &'a [u8],
        /// Bytes the frame occupies on disk, header included.
        len: u64,
    },
    /// The input ended exactly on a frame boundary.
    End,
    /// The input ends partway through a frame, as a write torn by a crash
    /// would leave it; or the payload doesn't match its checksum.
    Invalid {
        /// How far the header says the frame extends, header included, or
        /// `None` if the input ends partway through the header.
        len: Option<u64>,
    },
    /// The header itself is damaged. A torn write only ever cuts bytes off
    /// the end; this isn't one.
    Corrupt,
}

/// The header that goes in front of `payload`.
///
/// # Errors
///
/// [`io::ErrorKind::InvalidInput`] if the payload exceeds [`MAX_PAYLOAD_LEN`].
pub(crate) fn header(payload: &[u8]) -> io::Result<[u8; HEADER_LEN]> {
    let len = u32::try_from(payload.len())
        .ok()
        .filter(|&len| len as usize <= MAX_PAYLOAD_LEN)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                format!(
                    "payload of {} bytes exceeds {MAX_PAYLOAD_LEN}",
                    payload.len()
                ),
            )
        })?;
    let mut header = [0; HEADER_LEN];
    header[..4].copy_from_slice(&len.to_le_bytes());
    header[4..8].copy_from_slice(&crc32fast::hash(payload).to_le_bytes());
    let header_crc = crc32fast::hash(&header[..8]);
    header[8..].copy_from_slice(&header_crc.to_le_bytes());
    Ok(header)
}

/// Wraps `payload` in a frame.
///
/// # Errors
///
/// As for [`header`].
pub(crate) fn encode(payload: &[u8]) -> io::Result<Vec<u8>> {
    let mut frame = Vec::with_capacity(HEADER_LEN + payload.len());
    frame.extend_from_slice(&header(payload)?);
    frame.extend_from_slice(payload);
    Ok(frame)
}

/// Reads the next frame from `reader`, using `buf` to hold its payload.
///
/// Only genuine I/O failures are errors. A short or damaged frame comes back
/// as [`Decoded::Invalid`] or [`Decoded::Corrupt`], and the caller decides
/// whether it is a torn write or real damage.
pub(crate) fn decode<'a>(reader: &mut impl Read, buf: &'a mut Vec<u8>) -> io::Result<Decoded<'a>> {
    let mut header = [0; HEADER_LEN];
    match read_full(reader, &mut header)? {
        0 => return Ok(Decoded::End),
        HEADER_LEN => {}
        _ => return Ok(Decoded::Invalid { len: None }),
    }
    let [l0, l1, l2, l3, c0, c1, c2, c3, h0, h1, h2, h3] = header;
    if crc32fast::hash(&header[..8]) != u32::from_le_bytes([h0, h1, h2, h3]) {
        return Ok(Decoded::Corrupt);
    }
    let payload_len = u32::from_le_bytes([l0, l1, l2, l3]) as usize;
    let crc = u32::from_le_bytes([c0, c1, c2, c3]);
    let len = (HEADER_LEN + payload_len) as u64;

    if payload_len > MAX_PAYLOAD_LEN {
        return Ok(Decoded::Corrupt);
    }
    buf.resize(payload_len, 0);
    if read_full(reader, buf)? != payload_len || crc32fast::hash(buf) != crc {
        return Ok(Decoded::Invalid { len: Some(len) });
    }
    Ok(Decoded::Frame { payload: buf, len })
}

/// Like [`Read::read_exact`], but returns how many bytes were read before EOF
/// instead of failing. That tells a clean end from a short frame.
fn read_full(reader: &mut impl Read, buf: &mut [u8]) -> io::Result<usize> {
    let mut filled = 0;
    while filled < buf.len() {
        match reader.read(&mut buf[filled..]) {
            Ok(0) => break,
            Ok(n) => filled += n,
            Err(err) if err.kind() == io::ErrorKind::Interrupted => {}
            Err(err) => return Err(err),
        }
    }
    Ok(filled)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encode(payload: &[u8]) -> Vec<u8> {
        super::encode(payload).unwrap()
    }

    fn decode_one<'a>(bytes: &[u8], buf: &'a mut Vec<u8>) -> Decoded<'a> {
        decode(&mut &bytes[..], buf).unwrap()
    }

    #[test]
    fn round_trips() {
        for payload in [&b"hello"[..], b""] {
            let frame = encode(payload);
            let len = frame.len() as u64;
            let mut buf = Vec::new();
            assert_eq!(
                decode_one(&frame, &mut buf),
                Decoded::Frame { payload, len }
            );
        }
    }

    #[test]
    fn reads_consecutive_frames() {
        let mut input = encode(b"one");
        input.extend(encode(b"two"));
        let mut reader = &input[..];
        let mut buf = Vec::new();

        for expected in [b"one", b"two"] {
            match decode(&mut reader, &mut buf).unwrap() {
                Decoded::Frame { payload, .. } => assert_eq!(payload, expected),
                other => panic!("expected a frame, got {other:?}"),
            }
        }
        assert_eq!(decode(&mut reader, &mut buf).unwrap(), Decoded::End);
    }

    #[test]
    fn oversized_payload_is_refused() {
        let payload = vec![0; MAX_PAYLOAD_LEN + 1];
        let err = super::encode(&payload).unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
    }

    #[test]
    fn empty_input_is_a_clean_end() {
        assert_eq!(decode_one(&[], &mut Vec::new()), Decoded::End);
    }

    #[test]
    fn truncated_header_has_no_length() {
        let frame = encode(b"hello");
        let mut buf = Vec::new();
        for cut in 1..HEADER_LEN {
            let decoded = decode_one(&frame[..cut], &mut buf);
            assert_eq!(decoded, Decoded::Invalid { len: None });
        }
    }

    #[test]
    fn truncated_payload_reports_the_declared_length() {
        let frame = encode(b"hello");
        let len = Some(frame.len() as u64);
        let mut buf = Vec::new();
        for cut in HEADER_LEN..frame.len() {
            let decoded = decode_one(&frame[..cut], &mut buf);
            assert_eq!(decoded, Decoded::Invalid { len });
        }
    }

    #[test]
    fn flipped_payload_bit_fails_the_payload_checksum() {
        let mut frame = encode(b"hello");
        *frame.last_mut().unwrap() ^= 1;
        let len = Some(frame.len() as u64);
        assert_eq!(
            decode_one(&frame, &mut Vec::new()),
            Decoded::Invalid { len }
        );
    }

    #[test]
    fn any_damaged_header_byte_is_corruption() {
        for byte in 0..HEADER_LEN {
            let mut frame = encode(b"hello");
            frame[byte] ^= 1;
            assert_eq!(
                decode_one(&frame, &mut Vec::new()),
                Decoded::Corrupt,
                "byte {byte}"
            );
        }
    }

    #[test]
    fn oversized_length_is_rejected_without_allocating() {
        let mut frame = encode(b"hello");
        frame[..4].copy_from_slice(&u32::MAX.to_le_bytes());
        let header_crc = crc32fast::hash(&frame[..8]);
        frame[8..12].copy_from_slice(&header_crc.to_le_bytes());
        let mut buf = Vec::new();
        assert_eq!(decode_one(&frame, &mut buf), Decoded::Corrupt);
        assert_eq!(buf.capacity(), 0);
    }
}
