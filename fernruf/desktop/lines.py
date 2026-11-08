"""Transcript rows for the window. A partial line is replaced in place."""

from __future__ import annotations


def make_line_model(parent=None):
    from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt

    class LineModel(QAbstractListModel):
        Speaker = Qt.ItemDataRole.UserRole + 1
        Text = Qt.ItemDataRole.UserRole + 2
        Clock = Qt.ItemDataRole.UserRole + 3
        Final = Qt.ItemDataRole.UserRole + 4

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._rows: list[dict] = []

        def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
            if parent.isValid():
                return 0
            return len(self._rows)

        def roleNames(self) -> dict:  # noqa: N802
            return {
                self.Speaker: QByteArray(b"speaker"),
                self.Text: QByteArray(b"text"),
                self.Clock: QByteArray(b"clock"),
                self.Final: QByteArray(b"final"),
            }

        def data(self, index, role=Qt.ItemDataRole.DisplayRole):
            if not index.isValid() or not 0 <= index.row() < len(self._rows):
                return None
            row = self._rows[index.row()]
            if role in (self.Speaker, Qt.ItemDataRole.DisplayRole):
                return row["speaker"]
            if role == self.Text:
                return row["text"]
            if role == self.Clock:
                return row["clock"]
            if role == self.Final:
                return row["final"]
            return None

        def clear(self) -> None:
            self.beginResetModel()
            self._rows.clear()
            self.endResetModel()

        def add(self, speaker: str, text: str, *, final: bool, clock: str) -> None:
            row = {"speaker": speaker, "text": text, "final": bool(final), "clock": clock}
            if (
                self._rows
                and self._rows[-1]["speaker"] == speaker
                and not self._rows[-1]["final"]
            ):
                self._rows[-1] = row
                top = self.index(len(self._rows) - 1)
                self.dataChanged.emit(top, top)
                return
            end = len(self._rows)
            self.beginInsertRows(QModelIndex(), end, end)
            self._rows.append(row)
            self.endInsertRows()

    return LineModel(parent)
