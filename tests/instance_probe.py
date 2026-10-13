import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6 import QtCore, QtWidgets

from app.platform.instance_guard import InstanceGuard

app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
profile = Path(sys.argv[1])
guard = InstanceGuard(profile)
guard.activation_requested.connect(lambda: (profile / 'activated').write_text('yes'))
owned = guard.acquire()
print('owned' if owned else 'existing', flush=True)
if owned:
  QtCore.QTimer.singleShot(30000, app.quit)
  app.exec()
  guard.release()
