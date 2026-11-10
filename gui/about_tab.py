from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget

from utils.paths import get_resource_path
from version import COPYRIGHT, __version__


class AboutTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        icon = QLabel()
        pixmap = QPixmap(str(get_resource_path("michecklist-icon.png")))
        icon.setPixmap(pixmap.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon.setAlignment(Qt.AlignCenter)

        title = QLabel("MiChecklist")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignCenter)
        version = QLabel(f"Version {__version__}")
        version.setAlignment(Qt.AlignCenter)

        details = QGroupBox("Product Details")
        details_layout = QVBoxLayout(details)
        description = QLabel(
            "MiChecklist automates SQL Server, IIS, Windows service, folder backup, "
            "cleanup, scheduling, verification, retention, recovery, and notifications."
        )
        description.setWordWrap(True)
        details_layout.addWidget(description)
        details_layout.addWidget(QLabel("Product: MiChecklist"))
        details_layout.addWidget(QLabel(f"Version: {__version__}"))
        details_layout.addWidget(QLabel("Publisher: MILLENSYS - Support Department"))
        details_layout.addWidget(QLabel(COPYRIGHT))

        layout.addStretch()
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(version)
        layout.addWidget(details)
        layout.addStretch()
