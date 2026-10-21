import sys
from mac_platform import platform_name
APP_VERSION = '6.2.39'
PRODUCT_ID = 'DYLiveAssistant'
PLATFORM = platform_name() if sys.platform == 'darwin' else 'macos-development'
