"""A new key pair for push notifications (VAPID). The private key goes only into the server's .env."""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def new_keys():
    key = ec.generate_private_key(ec.SECP256R1())
    private = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
    public = _b64(key.public_key().public_bytes(serialization.Encoding.X962,
                                                serialization.PublicFormat.UncompressedPoint))
    return f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}"
