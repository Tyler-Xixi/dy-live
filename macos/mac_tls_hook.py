"""Use bundled public CA roots on Macs without a developer Python install."""
import os
import certifi
os.environ.setdefault('SSL_CERT_FILE',certifi.where())
