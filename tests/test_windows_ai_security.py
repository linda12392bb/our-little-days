"""Requires Windows DPAPI; Linux cannot prove the Windows account boundary."""
import sys
import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows per-user DPAPI unavailable on Linux')
def test_windows_key_encrypted_roundtrip_and_reopen(tmp_path):
    from our_little_days.ai_provider import WindowsKeyStore
    dummy = 'local-security-test-not-an-api-key'
    first = WindowsKeyStore(tmp_path)
    first.save(dummy)
    assert dummy.encode() not in first.path.read_bytes()
    second = WindowsKeyStore(tmp_path)
    assert second.read() == dummy
    assert not first.path.with_suffix('.new').exists()
