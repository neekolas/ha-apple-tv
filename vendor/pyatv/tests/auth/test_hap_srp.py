"""Regression tests for HAP SRP serialization."""

from pyatv.auth.hap_srp import SRPAuthHandler


def test_srp_pair_setup_wire_values(monkeypatch):
    """Match the proof and key produced by srptools 1.0.1 for this exchange."""
    srp = SRPAuthHandler()
    # Private value 2 with generator 5 produces a one-byte public key.
    monkeypatch.setattr(srp, "_auth_private", bytes(31) + b"\x02")
    srp.step1(2271)
    public, proof = srp.step2(b"\x07", bytes(range(16)))

    assert public == b"\x19"
    assert proof == bytes.fromhex(
        "5f37fbbbe9efcce88d848c915b4149813989dd61703232e0c9c629dc17445fa47"
        "aae26c1cea2fcc131e23324941e00c017e401ee6ff9c04ddacbc72cb2231adc"
    )
    assert srp.shared_key == bytes.fromhex(
        "23d18da3b21a598321af115cfc5dac44650e8c6cd3d4c667795201be7a362e3f1"
        "01aec3b813a58da1ae9e48ca45176b17f2fae8987371c061ff6805628fda3fd"
    )
