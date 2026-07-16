"""Tests for auth/tokens.py (D30): JWT issue/verify round trip, expiry via
an injectable clock, tamper rejection, and the persisted signing secret.
No key, no network, no real waiting.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.tokens import TokenError, issue_token, load_or_create_secret, verify_token

# 32+ bytes, matching what load_or_create_secret generates (PyJWT warns on
# HMAC keys shorter than SHA256's recommended minimum).
SECRET = "test-secret-0123456789abcdef0123456789abcdef"


def test_round_trip_carries_user_id_and_role():
    token = issue_token(42, "admin", SECRET)
    claims = verify_token(token, SECRET)
    assert claims == {"user_id": 42, "role": "admin"}, claims
    print("  issue/verify round trip carries user_id + role OK")


def test_expired_token_rejected():
    token = issue_token(1, "user", SECRET, ttl_s=3600, now=lambda: 1_000_000)
    # Still valid just before expiry, rejected at/after it.
    assert verify_token(token, SECRET, now=lambda: 1_000_000 + 3599)["user_id"] == 1
    try:
        verify_token(token, SECRET, now=lambda: 1_000_000 + 3600)
    except TokenError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("expected TokenError for an expired token")
    print("  expiry enforced against the injected clock OK")


def test_tampered_token_rejected():
    token = issue_token(1, "user", SECRET)
    header, payload, sig = token.split(".")
    for bad in (f"{header}.{payload}x.{sig}", token[:-4] + "AAAA", "not-a-token"):
        try:
            verify_token(bad, SECRET)
        except TokenError:
            pass
        else:
            raise AssertionError(f"tampered token {bad[:20]}... should be rejected")
    # Wrong secret is also a rejection.
    try:
        verify_token(token, "other-secret-0123456789abcdef0123456789abcdef")
    except TokenError:
        pass
    else:
        raise AssertionError("a token signed with another secret should be rejected")
    print("  tampered payloads and wrong secrets rejected OK")


def test_secret_is_persisted_and_env_wins():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "jwt_secret")
        first = load_or_create_secret(path, env_var="HARNESS_TEST_JWT_SECRET")
        second = load_or_create_secret(path, env_var="HARNESS_TEST_JWT_SECRET")
        assert first == second, "the generated secret must persist across loads"
        assert (os.stat(path).st_mode & 0o777) == 0o600, "secret file must be 0600"
        os.environ["HARNESS_TEST_JWT_SECRET"] = "from-the-env"
        try:
            assert load_or_create_secret(path, env_var="HARNESS_TEST_JWT_SECRET") == "from-the-env"
        finally:
            del os.environ["HARNESS_TEST_JWT_SECRET"]
    print("  secret persisted with 0600 perms; env var takes precedence OK")


def main():
    test_round_trip_carries_user_id_and_role()
    test_expired_token_rejected()
    test_tampered_token_rejected()
    test_secret_is_persisted_and_env_wins()
    print("TOKEN TESTS PASSED")


if __name__ == "__main__":
    main()
