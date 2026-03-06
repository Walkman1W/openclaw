"""Unit tests for auth_service helpers (TASK-004 / TASK-005).

These tests are fully self-contained and do NOT require a running database
or any external services.
"""
from __future__ import annotations

import pytest

from app.services.auth_service import (
    create_access_token,
    decode_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


# ---------------------------------------------------------------------------
# 1. Password hashing / verification
# ---------------------------------------------------------------------------

def test_hash_and_verify_password() -> None:
    """bcrypt hash round-trips correctly and rejects wrong passwords."""
    plain = "supersecret123"
    hashed = hash_password(plain)

    # Hashed value differs from the original
    assert hashed != plain
    # Correct password verifies
    assert verify_password(plain, hashed) is True
    # Wrong password does not verify
    assert verify_password("wrongpassword", hashed) is False


# ---------------------------------------------------------------------------
# 2. JWT creation / decoding
# ---------------------------------------------------------------------------

def test_create_and_decode_token() -> None:
    """JWT round-trip preserves payload data."""
    payload = {"sub": "some-account-uuid", "role": "human"}
    token = create_access_token(payload)

    assert isinstance(token, str)
    assert len(token) > 0

    decoded = decode_token(token)
    assert decoded["sub"] == "some-account-uuid"
    assert decoded["role"] == "human"
    # Expiry claim must be present
    assert "exp" in decoded


# ---------------------------------------------------------------------------
# 3. API Key format
# ---------------------------------------------------------------------------

def test_generate_api_key_format() -> None:
    """Generated API key starts with 'oc_' and has 64 hex chars after the prefix."""
    api_key = generate_api_key()

    assert api_key.startswith("oc_"), f"Expected 'oc_' prefix, got: {api_key[:10]!r}"
    hex_part = api_key[3:]  # strip "oc_"
    assert len(hex_part) == 64, f"Expected 64 hex chars, got {len(hex_part)}"
    # All characters after the prefix should be valid hexadecimal digits
    assert all(c in "0123456789abcdef" for c in hex_part), (
        f"Non-hex character in api_key hex part: {hex_part!r}"
    )


# ---------------------------------------------------------------------------
# 4. API Key hash determinism
# ---------------------------------------------------------------------------

def test_hash_api_key_deterministic() -> None:
    """SHA-256 hash of the same API key always produces the same digest."""
    api_key = generate_api_key()
    hash1 = hash_api_key(api_key)
    hash2 = hash_api_key(api_key)

    assert hash1 == hash2, "hash_api_key should be deterministic"
    # SHA-256 hex digest is always 64 characters
    assert len(hash1) == 64
    # Different keys must produce different hashes (collision resistance sanity check)
    other_key = generate_api_key()
    assert hash_api_key(other_key) != hash1
