"""Embedded release identity; never infer this from neighboring ZIP names."""
APP_VERSION = '6.1.21-mac.1'
PRODUCT_ID = 'DYLiveAssistant'
import sys
from mac_platform import platform_name
PLATFORM = platform_name() if sys.platform == 'darwin' else 'macos-development'
