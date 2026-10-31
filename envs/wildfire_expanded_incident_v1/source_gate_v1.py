"""Immutable allowlist from the full private source-reconstruction gate.

The entries are code-owned trusted identities, not pins supplied by a document
being checked. A new source candidate requires a new independently reviewed
gate/version and complete source validation before it can be admitted.
"""
import hashlib
import json

VERSION = 'wildfire-expanded-source-validation-gate-v1'
STRICT_SHA256 = 'a63ba37b281a0f3317465a6bbac1f7d1b71793e37908a0b9be5579cd9336e86a'
WATER_V2_SHA256 = 'a44d432066d6c3039f49032ca21b8a82496dff47c855fee1e07f8e854ab15b87'
# Fingerprint of a private manifest whose audit was independently reconstructed
# from the pinned geographic sources. The audit identity stays private.
MIXED_AUDIT_MANIFEST_SHA256 = 'b5ba7b0e723480d38be6533df41cb8c0c311f82e4600d1618aa240a1d65c8e88'


def require_validated_maps(strict_pin, water_pin):
    if strict_pin != STRICT_SHA256 or water_pin != WATER_V2_SHA256:
        raise ValueError('source maps are not admitted by the independent full-source validation gate')


def require_validated_mixed_audit(strict_pin, water_pin, audit_pin, manifest):
    """Reject caller-rehashed audits even when hypotheses are self-consistent."""
    require_validated_maps(strict_pin, water_pin)
    required = {'version', 'strict_sha256', 'water_v2_sha256', 'audit_sha256', 'validation'}
    if (type(manifest) is not dict or set(manifest) != required
            or manifest.get('version') != 'wildfire-private-mixed-audit-trust-v1'
            or manifest.get('validation') != 'independently-source-reconstructed-private-audit-v1'
            or manifest.get('strict_sha256') != strict_pin or manifest.get('water_v2_sha256') != water_pin
            or manifest.get('audit_sha256') != audit_pin):
        raise ValueError('mixed audit is not admitted by the independent audit gate')
    encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if hashlib.sha256(encoded).hexdigest() != MIXED_AUDIT_MANIFEST_SHA256:
        raise ValueError('mixed audit is not admitted by the independent audit gate')
