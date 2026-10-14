"""Source GUI smoke on macOS; no browser, activation or order is started."""
import sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if sys.platform!='darwin':raise SystemExit('Run this GUI check on macOS')
with tempfile.TemporaryDirectory(prefix='dylive-mac-smoke-') as directory:
 with patch('mac_platform.application_data_dir',return_value=Path(directory)):
  import dy_grab_gui as gui
  app=gui.App()
  assert app.runner is None and app.worker is None
  app.update_idletasks()
  assert app.winfo_exists()
  app.after(250,app.on_close)
  app.mainloop()
print('Mac source GUI starts and closes; no browser or order created')
