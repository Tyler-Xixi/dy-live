"""Use Mac system certificate validation plus bundled public CA roots."""
import os
import certifi
import truststore
os.environ.setdefault('SSL_CERT_FILE',certifi.where())
truststore.inject_into_ssl()
