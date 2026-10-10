"""Release-time public configuration. Never place a server private key here."""
LICENSE_ORIGIN = "https://license.txblog.cn"
# Exported from the deployed server; public verification key only.
import base64
LICENSE_PUBLIC_KEYS: dict[str, bytes] = {
    "primary-v1": base64.b64decode("T9C8Lf6iTO5C7gME6LRoC7LmYomWjdzIrz2Gz3pLDzA=")
}
