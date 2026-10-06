"""Key formats: 32-byte x-only hex keys and their NIP-19 bech32 forms.

Pure Python (BIP-173 bech32), so identities can be validated and displayed
without the optional crypto dependency.
"""
from __future__ import annotations

import re

from .errors import KeyFormatError

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _polymod(values: list[int]) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((top >> i) & 1) else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _convertbits(data: bytes | list[int], frombits: int, tobits: int, pad: bool) -> list[int]:
    acc = bits = 0
    out: list[int] = []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or value >> frombits:
            raise KeyFormatError("invalid bech32 data")
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise KeyFormatError("invalid bech32 padding")
    return out


def bech32_encode(hrp: str, data: bytes) -> str:
    five = _convertbits(data, 8, 5, True)
    values = _hrp_expand(hrp) + five
    polymod = _polymod([*values, 0, 0, 0, 0, 0, 0]) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[d] for d in five + checksum)


def bech32_decode(s: str, expected_hrp: str) -> bytes:
    if not isinstance(s, str) or s.lower() != s and s.upper() != s:
        raise KeyFormatError("bech32 string must not mix case")
    s = s.lower()
    pos = s.rfind("1")
    if pos < 1 or pos + 7 > len(s):
        raise KeyFormatError(f"not a bech32 string: {s[:16]!r}")
    hrp, data = s[:pos], s[pos + 1 :]
    if hrp != expected_hrp:
        raise KeyFormatError(f"expected a {expected_hrp} string, got {hrp!r}")
    try:
        values = [_CHARSET.index(c) for c in data]
    except ValueError:
        raise KeyFormatError("invalid bech32 character") from None
    if _polymod(_hrp_expand(hrp) + values) != 1:
        raise KeyFormatError("bech32 checksum mismatch")
    raw = bytes(_convertbits(values[:-6], 5, 8, False))
    if len(raw) != 32:
        raise KeyFormatError(f"{expected_hrp} must encode 32 bytes, got {len(raw)}")
    return raw


def check_hex32(value: str, what: str = "key") -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise KeyFormatError(f"{what} must be 64 lowercase hex characters")
    return value


def encode_npub(pubkey_hex: str) -> str:
    return bech32_encode("npub", bytes.fromhex(check_hex32(pubkey_hex, "pubkey")))


def decode_npub(npub: str) -> str:
    return bech32_decode(npub, "npub").hex()


def encode_nsec(seckey_hex: str) -> str:
    return bech32_encode("nsec", bytes.fromhex(check_hex32(seckey_hex, "secret key")))


def decode_nsec(nsec: str) -> str:
    return bech32_decode(nsec, "nsec").hex()
