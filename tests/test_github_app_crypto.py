import hashlib
import hmac


def test_verify_github_webhook_signature_ok():
    from app.services.github_app import verify_github_webhook

    secret = "s3cr3t"
    body = b'{"hello":"world"}'
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": f"sha256={mac}"}
    assert verify_github_webhook(headers, body, secret) is True


def test_verify_github_webhook_signature_bad():
    from app.services.github_app import verify_github_webhook

    secret = "s3cr3t"
    body = b"abc"
    headers = {"X-Hub-Signature-256": "sha256=deadbeef"}
    assert verify_github_webhook(headers, body, secret) is False


def test_normalize_pem_supports_escaped_newlines():
    from app.services.github_app import normalize_pem

    raw = "-----BEGIN KEY-----\\nLINE1\\nLINE2\\n-----END KEY-----"
    out = normalize_pem(raw)
    assert "\\n" not in out
    assert "LINE1\nLINE2" in out

