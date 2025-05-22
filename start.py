import os
import sys
from PyQt5.QtWidgets import QApplication

from gui.main_window import MainWindow

if os.name == "nt":
    import PyQt5
    pyqt_path = os.path.dirname(PyQt5.__file__)
    plugin_path = os.path.join(pyqt_path, "Qt", "plugins", "platforms")
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugin_path

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
