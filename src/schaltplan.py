# ---------------------------------------------------------------------------
# File:   elektro_baukasten.py
# Author: (c) 2024, 2025, 2026 Jens Kallup - paule32
# All rights reserved
# ---------------------------------------------------------------------------
import json
import math
import os
import sqlite3
import sys
try:
    import elektro_symbole_rc
except ImportError:
    elektro_symbole_rc = None

import uuid

from pathlib         import Path

from dataclasses     import dataclass, field
from typing          import Dict, List, Optional, Set, Tuple

from PyQt5.QtCore    import (
    QPointF, QRectF, QSize, Qt, pyqtSignal, QFile, QByteArray,
)
from PyQt5.QtGui     import (
    QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QApplication, QDockWidget, QFileDialog, QFrame, QGraphicsEllipseItem,
    QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsTextItem, QGraphicsView, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMdiArea, QMdiSubWindow, QMenu, QMessageBox, QStyle,
    QStatusBar, QTabWidget, QToolBar, QVBoxLayout, QWidget, QLineEdit, QPushButton, QInputDialog,
)

import elektro_symbole

GRID = 20
WIRE_WIDTH = 3
NODE_RADIUS = 5
SNAP_DISTANCE = 12
COMPONENT_HEIGHT = GRID * 3
DEFAULT_COMPONENT_WIDTH = 100
SCENE_RECT = QRectF(0, 0, 4000, 3000)
DB_FILE = "schaltplan.sqlite3"

def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def load_symbol_pixmap(path: str, size: QSize, background: QColor = QColor("black")) -> QPixmap:
    pm = QPixmap(size)
    pm.fill(background)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)

    svg_data = None
    if path.lower().endswith(".svg"):
        if path.startswith(":/"):
            qfile = QFile(path)
            if qfile.open(QFile.ReadOnly):
                svg_data = bytes(qfile.readAll())
                qfile.close()
        else:
            p = Path(path)
            if p.exists():
                svg_data = p.read_bytes()

        if svg_data:
            text_data = svg_data.decode("utf-8", errors="ignore")
            # einfache 1-Farb-Umschreibung: schwarz -> weiß
            replacements = {
                'stroke="black"': 'stroke="white"',
                "stroke='black'": "stroke='white'",
                'fill="black"': 'fill="white"',
                "fill='black'": "fill='white'",
                '#000000': '#ffffff',
                '#000': '#fff',
            }
            for old, new in replacements.items():
                text_data = text_data.replace(old, new)

            renderer = QSvgRenderer(QByteArray(text_data.encode("utf-8")))
            if renderer.isValid():
                renderer.render(painter, QRectF(0, 0, size.width(), size.height()))
                painter.end()
                return pm

    fallback = QPixmap(path)
    if not fallback.isNull():
        fallback = fallback.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (size.width() - fallback.width()) // 2
        y = (size.height() - fallback.height()) // 2
        painter.drawPixmap(x, y, fallback)

    painter.end()
    return pm
    fallback = QPixmap(path)
    if not fallback.isNull():
        fallback = fallback.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (size.width() - fallback.width()) // 2
        y = (size.height() - fallback.height()) // 2
        painter.drawPixmap(x, y, fallback)
    painter.end()
    return pm

@dataclass
class ComponentPort:
    name: str
    kind: str
    x: int
    y: int

@dataclass
class ComponentDef:
    comp_id: str
    label: str
    pixmap_path: str
    ports: List[ComponentPort]
    size: Tuple[int, int] = (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)

class DatabaseManager:
    def __init__(self, filename: str):
        self.conn = sqlite3.connect(filename)
        self.create_tables()

    def create_tables(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comp_uid TEXT UNIQUE,
                comp_type TEXT,
                x REAL,
                y REAL,
                pixmap_path TEXT,
                ports_json TEXT,
                width INTEGER,
                height INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS wires (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wire_uid TEXT UNIQUE,
                x1 REAL,
                y1 REAL,
                x2 REAL,
                y2 REAL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT,
                source_uid TEXT,
                source_anchor TEXT,
                target_type TEXT,
                target_uid TEXT,
                target_anchor TEXT
            )
            """
        )
        self.conn.commit()

    def clear(self):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM links")
        cur.execute("DELETE FROM wires")
        cur.execute("DELETE FROM components")
        self.conn.commit()

    def save_scene(self, scene: "SchematicScene"):
        self.clear()
        cur = self.conn.cursor()
        for comp in scene.components:
            cur.execute(
                """
                INSERT INTO components (comp_uid, comp_type, x, y, pixmap_path, ports_json, width, height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    comp.uid,
                    comp.comp_def.comp_id,
                    comp.pos().x(),
                    comp.pos().y(),
                    comp.comp_def.pixmap_path,
                    json.dumps([
                        {
                            "name": p.name,
                            "kind": p.kind,
                            "x": p.x,
                            "y": p.y,
                        }
                        for p in comp.comp_def.ports
                    ]),
                    comp.comp_def.size[0],
                    comp.comp_def.size[1],
                ),
            )
        for wire in scene.wires:
            cur.execute(
                """
                INSERT INTO wires (wire_uid, x1, y1, x2, y2)
                VALUES (?, ?, ?, ?, ?)
                """,
                (wire.uid, wire.start.x(), wire.start.y(), wire.end.x(), wire.end.y()),
            )
        for source_kind, source_uid, source_anchor, target_kind, target_uid, target_anchor in scene.connection_rows():
            cur.execute(
                """
                INSERT INTO links (source_type, source_uid, source_anchor, target_type, target_uid, target_anchor)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_kind, source_uid, source_anchor, target_kind, target_uid, target_anchor),
            )
        self.conn.commit()

class GridView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#202124")))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        super().drawBackground(painter, rect)
        left = int(math.floor(rect.left() / GRID) * GRID)
        top = int(math.floor(rect.top() / GRID) * GRID)
        painter.setPen(QPen(QColor("#31343b"), 1))
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += GRID
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += GRID

class AnchorNode:
    def __init__(self, scene: "SchematicScene", owner, anchor_name: str, kind: str):
        self.scene = scene
        self.owner = owner
        self.anchor_name = anchor_name
        self.kind = kind
        self.links: List[Tuple["WireItem", str]] = []

    @property
    def pos(self) -> QPointF:
        return self.owner.anchor_scene_pos(self.anchor_name)

    def attach(self, wire: "WireItem", endpoint_name: str):
        entry = (wire, endpoint_name)
        if entry not in self.links:
            self.links.append(entry)

    def detach(self, wire: "WireItem", endpoint_name: str):
        entry = (wire, endpoint_name)
        if entry in self.links:
            self.links.remove(entry)

class DotItem(QGraphicsEllipseItem):
    def __init__(self, parent_wire: "WireItem", endpoint_name: str):
        r = NODE_RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r, parent_wire)
        self.parent_wire = parent_wire
        self.endpoint_name = endpoint_name
        self.setBrush(QBrush(QColor("#d4af37")))
        self.setPen(QPen(QColor("#d4af37"), 1))
        self.setZValue(3)

class PortDotItem(QGraphicsEllipseItem):
    def __init__(self, x: int, y: int, kind: str, parent=None):
        r = NODE_RADIUS + 1
        super().__init__(-r, -r, 2 * r, 2 * r, parent)
        self.kind = kind
        self.setPos(x, y)
        self.setBrush(QBrush(QColor("#7cc7ff") if kind == "in" else QColor("#9be38c")))
        self.setPen(QPen(QColor("#efefef"), 1))
        self.setZValue(5)


class ConnectionOverlayItem(QGraphicsPathItem):
    def __init__(self, scene: "SchematicScene"):
        super().__init__()
        self.scene_ref = scene
        self.setPen(QPen(QColor("#66e0ff"), 2, Qt.DashLine))
        self.setZValue(20)
        self.hide()

    def refresh(self):
        if self.scene_ref.mode != self.scene_ref.MODE_SIMULATE:
            self.hide()
            return
        path = QPainterPath()
        for src, dst in self.scene_ref.compute_simulation_edges():
            path.moveTo(src)
            path.lineTo(dst)
        self.setPath(path)
        self.setVisible(not path.isEmpty())

class JunctionOverlayItem(QGraphicsItem):
    def __init__(self, scene: "SchematicScene"):
        super().__init__()
        self.scene_ref = scene
        self.points = []
        self.setZValue(30)

    def boundingRect(self) -> QRectF:
        if not self.points:
            return QRectF()
        xs = [p.x() for p in self.points]
        ys = [p.y() for p in self.points]
        return QRectF(min(xs) - 8, min(ys) - 8, max(xs) - min(xs) + 16, max(ys) - min(ys) + 16)

    def paint(self, painter, option, widget=None):
        if not self.points:
            return
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#101010"), 1))
        painter.setBrush(QBrush(QColor("#d4af37")))
        for point in self.points:
            painter.drawEllipse(point, 4, 4)

    def refresh(self):
        self.prepareGeometryChange()
        self.points = self.scene_ref.compute_junction_points()
        self.update()

class BridgeOverlayItem(QGraphicsItem):
    def __init__(self, scene: "SchematicScene"):
        super().__init__()
        self.scene_ref = scene
        self.bridges = []
        self.setZValue(31)

    def boundingRect(self) -> QRectF:
        if not self.bridges:
            return QRectF()
        xs = []
        ys = []
        for center, orientation in self.bridges:
            xs.append(center.x())
            ys.append(center.y())
        return QRectF(min(xs) - 12, min(ys) - 12, max(xs) - min(xs) + 24, max(ys) - min(ys) + 24)

    def paint(self, painter, option, widget=None):
        if not self.bridges:
            return
        painter.setRenderHint(QPainter.Antialiasing)
        for center, orientation in self.bridges:
            # bridge background clears the crossing area
            if orientation == "horizontal_over_vertical":
                painter.setPen(QPen(QColor("#202124"), 7, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(QPointF(center.x() - 8, center.y()), QPointF(center.x() + 8, center.y()))
                path = QPainterPath(QPointF(center.x() - 8, center.y()))
                path.quadTo(QPointF(center.x(), center.y() - 8), QPointF(center.x() + 8, center.y()))
                painter.setPen(QPen(QColor("#d4af37"), WIRE_WIDTH, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPath(path)
            else:
                painter.setPen(QPen(QColor("#202124"), 7, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(QPointF(center.x(), center.y() - 8), QPointF(center.x(), center.y() + 8))
                path = QPainterPath(QPointF(center.x(), center.y() - 8))
                path.quadTo(QPointF(center.x() - 8, center.y()), QPointF(center.x(), center.y() + 8))
                painter.setPen(QPen(QColor("#d4af37"), WIRE_WIDTH, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPath(path)

    def refresh(self):
        self.prepareGeometryChange()
        self.bridges = self.scene_ref.compute_bridge_crossings()
        self.update()

class WireItem(QGraphicsObject):
    def __init__(self, scene: "SchematicScene", start: QPointF, end: QPointF):
        super().__init__()
        self.uid = uid("wire")
        self.scene_ref = scene
        self.start = scene.snap_point(start)
        self.end = scene.snap_orthogonal(self.start, end)
        self.path_item = QGraphicsPathItem(self)
        self.path_item.setPen(QPen(QColor("#d4af37"), WIRE_WIDTH, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.start_dot = DotItem(self, "start")
        self.end_dot = DotItem(self, "end")
        self.start_node = AnchorNode(scene, self, "start", "wire")
        self.end_node = AnchorNode(scene, self, "end", "wire")
        self.selected_endpoint: Optional[str] = None
        self.drag_mode: Optional[str] = None
        self.last_mouse_scene_pos = QPointF()
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptedMouseButtons(Qt.LeftButton | Qt.RightButton)
        self.setZValue(2)
        self.update_geometry()

    def boundingRect(self) -> QRectF:
        return self.childrenBoundingRect().adjusted(-8, -8, 8, 8)

    def paint(self, painter, option, widget=None):
        return

    def anchor_scene_pos(self, anchor_name: str) -> QPointF:
        return self.start if anchor_name == "start" else self.end

    def update_geometry(self):
        self.prepareGeometryChange()
        path = QPainterPath()
        path.moveTo(self.start)
        path.lineTo(self.end)
        self.path_item.setPath(path)
        self.start_dot.setPos(self.start)
        self.end_dot.setPos(self.end)
        self.update_dot_visibility()
        self.update()

    def update_dot_visibility(self):
        self.start_dot.setVisible(not self.scene_ref.is_anchor_covered(self.start_node))
        self.end_dot.setVisible(not self.scene_ref.is_anchor_covered(self.end_node))

    def is_horizontal(self) -> bool:
        return abs(self.start.y() - self.end.y()) < 0.1

    def is_vertical(self) -> bool:
        return abs(self.start.x() - self.end.x()) < 0.1

    def anchor_moved(self, anchor_name: str, pos: QPointF):
        pos = self.scene_ref.snap_point(pos)
        if anchor_name == "start":
            self.start = pos
            self.start = self.scene_ref.snap_orthogonal(self.end, self.start)
        else:
            self.end = pos
            self.end = self.scene_ref.snap_orthogonal(self.start, self.end)
        self.update_geometry()

    def move_by_delta(self, delta: QPointF):
        self.start = self.scene_ref.snap_point(self.start + delta)
        self.end = self.scene_ref.snap_point(self.end + delta)
        self.update_geometry()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            menu = QMenu()
            delete_action = menu.addAction("Löschen")
            chosen = menu.exec_(event.screenPos())
            if chosen == delete_action:
                self.scene_ref.delete_wire(self)
                event.accept()
                return
        if self.scene_ref.mode in (self.scene_ref.MODE_WIRE, self.scene_ref.MODE_SIMULATE):
            event.ignore()
            return
        p = event.scenePos()
        self.last_mouse_scene_pos = QPointF(p)
        if self.scene_ref.distance(p, self.start) <= SNAP_DISTANCE:
            self.selected_endpoint = "start"
            self.drag_mode = "endpoint"
            event.accept()
            return
        if self.scene_ref.distance(p, self.end) <= SNAP_DISTANCE:
            self.selected_endpoint = "end"
            self.drag_mode = "endpoint"
            event.accept()
            return
        self.selected_endpoint = None
        self.drag_mode = "line"
        super().mousePressEvent(event)
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_mode == "endpoint" and self.selected_endpoint:
            new_pos = self.scene_ref.snap_point(event.scenePos())
            self.anchor_moved(self.selected_endpoint, new_pos)
            self.scene_ref.try_attach_wire_endpoint(self, self.selected_endpoint)
            self.scene_ref.refresh_after_geometry_change()
            event.accept()
            return

        if (
            self.scene_ref.mode == self.scene_ref.MODE_SELECT
            and self.isSelected()
            and len(self.scene_ref.selectedItems()) > 1
            and (event.buttons() & Qt.LeftButton)
        ):
            delta = event.scenePos() - event.lastScenePos()
            if self.scene_ref.move_selected_group_by(delta, leader=self):
                event.accept()
                return

        if self.drag_mode == "line" and (event.buttons() & Qt.LeftButton):
            delta = event.scenePos() - self.last_mouse_scene_pos
            snapped = self.scene_ref.snap_delta(delta)
            if self.is_horizontal():
                move = QPointF(0, snapped.y())
            else:
                move = QPointF(snapped.x(), 0)
            if move.x() or move.y():
                self.scene_ref.move_wire_network(self, move)
                self.last_mouse_scene_pos = QPointF(self.last_mouse_scene_pos + move)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.selected_endpoint:
            self.scene_ref.try_attach_wire_endpoint(self, self.selected_endpoint)
        self.selected_endpoint = None
        self.drag_mode = None
        self.scene_ref.refresh_after_geometry_change()
        super().mouseReleaseEvent(event)

class PortLabelItem(QGraphicsTextItem):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setDefaultTextColor(QColor("#f0f0f0"))
        font = self.font()
        font.setPointSize(7)
        self.setFont(font)
        self.setZValue(3)



class ComponentItem(QGraphicsPixmapItem):
    def __init__(self, scene: "SchematicScene", comp_def: ComponentDef, pos: QPointF):
        super().__init__()
        self.uid = uid("comp")
        self.scene_ref = scene
        self.comp_def = comp_def
        self.port_items: Dict[str, PortDotItem] = {}
        self.port_labels: Dict[str, PortLabelItem] = {}
        self.anchor_nodes: Dict[str, AnchorNode] = {}
        self.port_layout_mode = "default"
        self.custom_port_positions: Dict[str, Tuple[int, int]] = {}
        self.rotation_state = 0
        self.flipped_vertical = False
        self.setPixmap(self.load_pixmap())
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setPos(scene.snap_component_point(pos))
        self.setZValue(4)
        self.setTransformOriginPoint(self.comp_def.size[0] / 2, self.comp_def.size[1] / 2)
        for port in comp_def.ports:
            self.anchor_nodes[port.name] = AnchorNode(scene, self, port.name, "component")
            self.port_items[port.name] = PortDotItem(port.x, port.y, port.kind, self)
            self.port_labels[port.name] = PortLabelItem(port.name, self)
        self.refresh_port_positions()


    def is_seven_segment(self) -> bool:
        return self.comp_def.comp_id in {"display7_single", "display7_double", "display7_quad"}

    def port_position(self, anchor_name: str) -> QPointF:
        if anchor_name in self.custom_port_positions:
            x, y = self.custom_port_positions[anchor_name]
            return QPointF(x, y)
        for port in self.comp_def.ports:
            if port.name == anchor_name:
                return QPointF(port.x, port.y)
        return QPointF(0, 0)

    def all_port_names(self):
        return [p.name for p in self.comp_def.ports]

    def apply_display_pin_layout(self, mode: str):
        width, height = self.comp_def.size
        self.port_layout_mode = mode
        self.custom_port_positions = {}
        if not self.is_seven_segment():
            self.refresh_port_positions()
            self.scene_ref.update_component_links(self)
            self.scene_ref.refresh_after_geometry_change()
            return

        names = self.all_port_names()
        if mode == "left":
            step = GRID * 2 if self.comp_def.comp_id == "display7_single" else GRID
            for i, name in enumerate(names):
                self.custom_port_positions[name] = (GRID // 2, GRID // 2 + i * step)
        elif mode == "right":
            step = GRID * 2 if self.comp_def.comp_id == "display7_single" else GRID
            for i, name in enumerate(names):
                self.custom_port_positions[name] = (width - GRID // 2, GRID // 2 + i * step)
        elif mode == "top":
            usable = max(1, len(names))
            step = max(GRID, int((width - GRID) / usable))
            for i, name in enumerate(names):
                self.custom_port_positions[name] = (GRID // 2 + i * step, GRID // 2)
        elif mode == "bottom":
            usable = max(1, len(names))
            step = max(GRID, int((width - GRID) / usable))
            for i, name in enumerate(names):
                self.custom_port_positions[name] = (GRID // 2 + i * step, height - GRID // 2)
        self.refresh_port_positions()
        self.scene_ref.update_component_links(self)
        self.scene_ref.refresh_after_geometry_change()

    def load_pixmap(self) -> QPixmap:
        w, h = self.comp_def.size
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#d4af37"), 2))
        painter.setBrush(QBrush(QColor("#2b2b2b")))
        painter.drawRoundedRect(4, 4, w - 8, h - 8, 10, 10)

        if self.comp_def.comp_id in {"display7_single", "display7_double", "display7_quad"}:
            painter.setPen(QPen(QColor("#bcbcbc"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(QBrush(QColor("#f2f2f2")))

            def draw_segment_polygon(points, label=None, label_pos=None):
                poly = QPolygonF([QPointF(x, y) for x, y in points])
                painter.drawPolygon(poly)
                if label and label_pos:
                    painter.setPen(QColor("#303030"))
                    painter.drawText(QRectF(label_pos[0] - 8, label_pos[1] - 8, 16, 16), Qt.AlignCenter, label)
                    painter.setPen(QPen(QColor("#bcbcbc"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

            def draw_digit_symbol(x0: int, y0: int, dw: int, dh: int, show_dp: bool = True):
                left = x0 + 6
                right = x0 + dw - 6
                top = y0 + 6
                bottom = y0 + dh - 6
                mid = (top + bottom) / 2
                t = max(7, int(dw * 0.14))
                s = max(7, int(dw * 0.13))

                inner_w = max(12, dw - 42)
                painter.fillRect(QRectF(x0 + (dw - inner_w) / 2, y0 + 18, inner_w, max(10, dh * 0.28)), QColor("#050505"))
                painter.fillRect(QRectF(x0 + (dw - inner_w) / 2, mid + 6, inner_w, max(10, dh * 0.25)), QColor("#050505"))

                # a
                draw_segment_polygon([
                    (left + 12, top), (right - 12, top), (right - 4, top + t),
                    (left + 4, top + t)
                ], "a", ((left + right) / 2, top + t / 2))
                # g
                draw_segment_polygon([
                    (left + 12, mid - t / 2), (right - 12, mid - t / 2), (right - 4, mid + t / 2),
                    (left + 4, mid + t / 2)
                ], "g", ((left + right) / 2, mid))
                # d
                draw_segment_polygon([
                    (left + 12, bottom - t), (right - 12, bottom - t), (right - 4, bottom),
                    (left + 4, bottom)
                ], "d", ((left + right) / 2, bottom - t / 2))
                # f
                draw_segment_polygon([
                    (left, top + 12), (left + s, top + 4), (left + s, mid - 10),
                    (left, mid - 2), (left - 1, mid - 8), (left - 1, top + 18)
                ], "f", (left + s / 2 - 1, (top + mid) / 2 - 4))
                # b
                draw_segment_polygon([
                    (right, top + 12), (right + 1, top + 18), (right + 1, mid - 8),
                    (right, mid - 2), (right - s, mid - 10), (right - s, top + 4)
                ], "b", (right - s / 2 + 1, (top + mid) / 2 - 4))
                # e
                draw_segment_polygon([
                    (left, mid + 2), (left + s, mid + 10), (left + s, bottom - 4),
                    (left, bottom - 12), (left - 1, bottom - 18), (left - 1, mid + 8)
                ], "e", (left + s / 2 - 1, (mid + bottom) / 2 + 4))
                # c
                draw_segment_polygon([
                    (right, mid + 2), (right + 1, mid + 8), (right + 1, bottom - 18),
                    (right, bottom - 12), (right - s, bottom - 4), (right - s, mid + 10)
                ], "c", (right - s / 2 + 1, (mid + bottom) / 2 + 4))

                # feine Gegenstücke / Gegenkanten für schaltbildnahe Symmetrie
                painter.setPen(QPen(QColor("#8f8f8f"), 1))
                painter.drawLine(QPointF(left + 10, top + 1), QPointF(right - 10, top + 1))
                painter.drawLine(QPointF(left + 10, bottom - 1), QPointF(right - 10, bottom - 1))
                painter.drawLine(QPointF(left + 1, top + 16), QPointF(left + 1, mid - 6))
                painter.drawLine(QPointF(right - 1, top + 16), QPointF(right - 1, mid - 6))
                painter.drawLine(QPointF(left + 1, mid + 6), QPointF(left + 1, bottom - 16))
                painter.drawLine(QPointF(right - 1, mid + 6), QPointF(right - 1, bottom - 16))
                painter.setPen(QPen(QColor("#bcbcbc"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

                if show_dp:
                    painter.setBrush(QBrush(QColor("#d0d0d0")))
                    painter.setPen(QPen(QColor("#9a9a9a"), 2))
                    dp_rect = QRectF(right - 4, bottom - 4, 20, 20)
                    painter.drawEllipse(dp_rect)
                    painter.setPen(QColor("#303030"))
                    painter.drawText(dp_rect, Qt.AlignCenter, "dp")
                    painter.setPen(QPen(QColor("#bcbcbc"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    painter.setBrush(QBrush(QColor("#f2f2f2")))

            count = 1
            if self.comp_def.comp_id == "display7_double":
                count = 2
            elif self.comp_def.comp_id == "display7_quad":
                count = 4

            inner_margin = max(10, int(w * 0.05))
            gap = max(12, int(w * 0.025))
            avail_w = w - inner_margin * 2 - gap * (count - 1)
            digit_w = max(60, int((avail_w / count) * 0.46))
            digit_h = max(70, int((h - 24) * 0.52))
            total_digits_w = count * digit_w + (count - 1) * gap
            start_x = (w - total_digits_w) / 2
            y0 = (h - digit_h) / 2 - 4

            for i in range(count):
                x0 = start_x + i * (digit_w + gap)
                draw_digit_symbol(int(x0), int(y0), int(digit_w), int(digit_h), show_dp=True)
        else:
            painter.setPen(QColor("#f0f0f0"))
            painter.drawText(pm.rect(), Qt.AlignCenter, self.comp_def.label)

        painter.end()
        return pm

    def anchor_scene_pos(self, anchor_name: str) -> QPointF:
        return self.mapToScene(self.port_position(anchor_name))

    def refresh_port_positions(self):
        width = self.comp_def.size[0]
        height = self.comp_def.size[1]
        for port in self.comp_def.ports:
            pos = self.port_position(port.name)
            if port.name in self.port_items:
                self.port_items[port.name].setPos(pos.x(), pos.y())
            if port.name in self.port_labels:
                label_item = self.port_labels[port.name]
                br = label_item.boundingRect()
                if pos.x() <= GRID:
                    label_item.setPos(pos.x() + 10, pos.y() - br.height() / 2)
                elif pos.x() >= width - GRID:
                    label_item.setPos(pos.x() - br.width() - 10, pos.y() - br.height() / 2)
                elif pos.y() <= GRID:
                    label_item.setPos(pos.x() - br.width() / 2, pos.y() + 8)
                else:
                    label_item.setPos(pos.x() - br.width() / 2, pos.y() - br.height() - 8)

    def rotate_component(self, delta_degrees: int):
        self.rotation_state = (self.rotation_state + delta_degrees) % 360
        self.setRotation(self.rotation_state)
        self.scene_ref.update_component_links(self)
        self.scene_ref.refresh_after_geometry_change()

    def flip_vertical(self):
        self.flipped_vertical = not self.flipped_vertical
        current = self.transform()
        if self.flipped_vertical:
            self.setScale(-1)
        else:
            self.setScale(1)
        self.scene_ref.update_component_links(self)
        self.scene_ref.refresh_after_geometry_change()

    def contextMenuEvent(self, event):
        menu = QMenu()
        action_left = menu.addAction("links drehen")
        action_right = menu.addAction("rechts drehen")
        action_vertical = menu.addAction("vertikal drehen")
        seg_left = seg_right = seg_top = seg_bottom = None
        if self.is_seven_segment():
            menu.addSeparator()
            seg_menu = menu.addMenu("Pin-Anordnung 7-Segment")
            seg_left = seg_menu.addAction("Pins links")
            seg_right = seg_menu.addAction("Pins rechts")
            seg_top = seg_menu.addAction("Pins oben")
            seg_bottom = seg_menu.addAction("Pins unten")
        menu.addSeparator()
        action_delete = menu.addAction("Löschen")
        chosen = menu.exec_(event.screenPos())
        if chosen == action_left:
            self.rotate_component(180)
            event.accept()
            return
        if chosen == action_right:
            self.rotate_component(-180)
            event.accept()
            return
        if chosen == action_vertical:
            self.rotate_component(90 if (self.rotation_state % 180 == 0) else -90)
            event.accept()
            return
        if chosen == seg_left:
            self.apply_display_pin_layout("left")
            event.accept()
            return
        if chosen == seg_right:
            self.apply_display_pin_layout("right")
            event.accept()
            return
        if chosen == seg_top:
            self.apply_display_pin_layout("top")
            event.accept()
            return
        if chosen == seg_bottom:
            self.apply_display_pin_layout("bottom")
            event.accept()
            return
        if chosen == action_delete:
            self.scene_ref.delete_component(self)
            event.accept()
            return

    def port_kind(self, anchor_name: str) -> str:
        for port in self.comp_def.ports:
            if port.name == anchor_name:
                return port.kind
        return "in"

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            target_pos = self.scene_ref.snap_component_point(value)
            if (
                not self.scene_ref._group_drag_active
                and self.scene_ref.mode == self.scene_ref.MODE_SELECT
            ):
                delta = target_pos - self.pos()
                if self.isSelected() and len(self.scene_ref.selectedItems()) > 1:
                    self.scene_ref.move_selected_group_by(delta, leader=self)
                    return self.pos()
                if not self.isSelected():
                    self.scene_ref.clearSelection()
                    self.setSelected(True)
                if self.scene_ref.move_component_network(self, delta):
                    return self.pos()
            return self.scene_ref.find_free_component_position(self, target_pos)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.scene_ref.update_component_links(self)
        return super().itemChange(change, value)




class ComponentPalette(QWidget):
    componentSelected = pyqtSignal(object)
    connectionActivated = pyqtSignal(object)
    pathActivated = pyqtSignal(object)
    librarySelected = pyqtSignal(object)
    workspaceAddRequested = pyqtSignal()
    workspaceCopyRequested = pyqtSignal()
    workspacePasteRequested = pyqtSignal()
    workspaceDeleteRequested = pyqtSignal()
    workspaceActivated = pyqtSignal(str)

    def __init__(self, components: List[ComponentDef], parent=None):
        super().__init__(parent)
        self.components = components
        self.favorite_ids = set()
        self.category_map = {
            "Favoriten": [],
            "Passiv": [
                "resistor", "potentiometer", "heating_resistor", "photoresistor", "varistor",
                "capacitor", "ceramic_capacitor", "capacitor_polarized", "electrolytic_capacitor",
                "inductor", "choke"
            ],
            "Quellen": ["battery", "voltage_source", "current_source", "solar_cell"],
            "Schalter": ["switch_open", "switch_closed", "fuse", "spst", "spdt", "spco", "sptt", "sp3t", "dpst", "dpdt", "dpco"],
            "Logik": ["and_gate", "or_gate", "xor_gate", "nand_gate", "nor_gate", "xnor_gate", "not_gate"],
            "ICs": ["dual_d_flipflop_4013", "ic_4014", "ic_4017", "ic_4020", "ic_4025", "ic_4026", "ic_4040", "ic_4511", "ne555_timer"],
            "Anzeigen": ["display7_single", "display7_double", "display7_quad"],
            "LEDs": ["led", "white_led", "green_led", "blue_led", "yellow_led"],
            "Halbleiter": ["diode", "zener", "schottky_diode", "diac", "triac", "magnet_diode", "npn", "pnp"],
            "Sensorik": ["piezo", "light_barrier"],
            "Aktoren": ["lamp", "relay", "electromagnet", "speaker"],
            "Analog": ["opamp", "amplifier", "transformer", "quartz", "microphone", "ground"],
        }
        self.library_map = {
            "Transistoren-Schaltungen": [
                {"name": "Emitterschaltung", "components": [{"ref":"Q1","comp_id":"npn","x":0,"y":0},{"ref":"RC","comp_id":"resistor","x":140,"y":-60},{"ref":"RE","comp_id":"resistor","x":140,"y":60}], "wires":[{"from":["Q1","OUT1"],"to":["RC","IN1"]},{"from":["Q1","OUT2"],"to":["RE","IN1"]}]},
                {"name": "Kollektorschaltung", "components": [{"ref":"Q1","comp_id":"npn","x":0,"y":0},{"ref":"RE","comp_id":"resistor","x":140,"y":0}], "wires":[{"from":["Q1","OUT2"],"to":["RE","IN1"]}]},
                {"name": "Basisschaltung", "components": [{"ref":"Q1","comp_id":"npn","x":0,"y":0},{"ref":"RC","comp_id":"resistor","x":140,"y":-60}], "wires":[{"from":["Q1","OUT1"],"to":["RC","IN1"]}]},
            ]
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        self.main_tabs = QTabWidget()
        layout.addWidget(self.main_tabs, 3)

        components_page = QWidget()
        components_layout = QVBoxLayout(components_page)
        components_layout.setContentsMargins(2, 2, 2, 2)
        components_layout.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Komponente suchen ...")
        components_layout.addWidget(self.search_edit)
        self.category_tabs = QTabWidget()
        components_layout.addWidget(self.category_tabs, 1)
        self.main_tabs.addTab(components_page, "Komponenten")

        library_page = QWidget()
        library_layout = QVBoxLayout(library_page)
        library_layout.setContentsMargins(2, 2, 2, 2)
        library_layout.setSpacing(6)
        self.library_tabs = QTabWidget()
        library_layout.addWidget(self.library_tabs, 1)
        self.main_tabs.addTab(library_page, "Bibliothek")

        nets_page = QWidget()
        nets_layout = QVBoxLayout(nets_page)
        nets_layout.setContentsMargins(2, 2, 2, 2)
        nets_layout.setSpacing(6)
        nets_layout.addWidget(QLabel("Netze"))
        self.connections_list = QListWidget()
        nets_layout.addWidget(self.connections_list, 1)
        self.main_tabs.addTab(nets_page, "Netze")

        paths_page = QWidget()
        paths_layout = QVBoxLayout(paths_page)
        paths_layout.setContentsMargins(2, 2, 2, 2)
        paths_layout.setSpacing(6)
        paths_layout.addWidget(QLabel("Pfade / Stromkreise"))
        self.paths_list = QListWidget()
        paths_layout.addWidget(self.paths_list, 1)
        self.main_tabs.addTab(paths_page, "Pfade")

        layout.addWidget(QLabel("Arbeitsflächen"))
        self.workspace_list = QListWidget()
        layout.addWidget(self.workspace_list, 1)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_copy = QPushButton("Copy")
        self.btn_paste = QPushButton("Paste")
        self.btn_delete = QPushButton("Delete")
        for btn in (self.btn_add, self.btn_copy, self.btn_paste, self.btn_delete):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self.category_lists = {}
        self.library_lists = {}
        self.build_category_tabs()
        self.build_library_tabs()

        self.search_edit.textChanged.connect(self.apply_filter)
        self.connections_list.itemClicked.connect(self.on_connection_clicked)
        self.paths_list.itemClicked.connect(self.on_path_clicked)
        self.workspace_list.itemClicked.connect(self.on_workspace_clicked)
        self.workspace_list.itemDoubleClicked.connect(self.on_workspace_double_clicked)
        self.btn_add.clicked.connect(self.workspaceAddRequested)
        self.btn_copy.clicked.connect(self.workspaceCopyRequested)
        self.btn_paste.clicked.connect(self.workspacePasteRequested)
        self.btn_delete.clicked.connect(self.workspaceDeleteRequested)

    def build_category_tabs(self):
        while self.category_tabs.count():
            self.category_tabs.removeTab(0)
        self.category_lists.clear()
        for category_name in self.category_map.keys():
            lw = QListWidget()
            lw.setIconSize(QSize(48, 48))
            lw.itemClicked.connect(self.on_item_clicked)
            lw.setContextMenuPolicy(Qt.CustomContextMenu)
            lw.customContextMenuRequested.connect(lambda pos, list_widget=lw: self.show_component_context_menu(list_widget, pos))
            self.category_lists[category_name] = lw
            self.category_tabs.addTab(lw, category_name)
        self.refresh_component_lists()

    def build_library_tabs(self):
        while self.library_tabs.count():
            self.library_tabs.removeTab(0)
        self.library_lists.clear()
        for category_name, templates in self.library_map.items():
            lw = QListWidget()
            for template in templates:
                item = QListWidgetItem(template["name"])
                item.setData(Qt.UserRole, template)
                lw.addItem(item)
            lw.itemClicked.connect(self.on_library_clicked)
            self.library_lists[category_name] = lw
            self.library_tabs.addTab(lw, category_name)

    def refresh_component_lists(self):
        comp_by_id = {comp.comp_id: comp for comp in self.components}
        self.category_map["Favoriten"] = sorted(self.favorite_ids)
        for category_name, lw in self.category_lists.items():
            lw.clear()
            for comp_id in self.category_map.get(category_name, []):
                comp = comp_by_id.get(comp_id)
                if comp is None:
                    continue
                item = QListWidgetItem(comp.label)
                item.setData(Qt.UserRole, comp)
                pm = load_symbol_pixmap(comp.pixmap_path, QSize(48, 48), QColor("black"))
                if pm.isNull():
                    pm = QPixmap(48, 48)
                    pm.fill(Qt.black)
                    p = QPainter(pm)
                    p.setPen(QPen(QColor("#f0f0f0"), 2))
                    p.drawRoundedRect(4, 4, 40, 40, 6, 6)
                    p.end()
                item.setIcon(QIcon(pm))
                if comp.comp_id in self.favorite_ids:
                    item.setText(f"★ {comp.label}")
                lw.addItem(item)
        self.apply_filter(self.search_edit.text())

    def set_workspaces(self, workspace_names, active_name=None):
        self.workspace_list.blockSignals(True)
        try:
            self.workspace_list.clear()
            for name in workspace_names:
                self.workspace_list.addItem(name)
            if active_name:
                matches = self.workspace_list.findItems(active_name, Qt.MatchExactly)
                if matches:
                    self.workspace_list.setCurrentItem(matches[0])
                    self.workspace_list.scrollToItem(matches[0])
        finally:
            self.workspace_list.blockSignals(False)

    def current_workspace_name(self):
        item = self.workspace_list.currentItem()
        return item.text() if item else None

    def on_workspace_clicked(self, item):
        if item:
            self.workspaceActivated.emit(item.text())

    def on_workspace_double_clicked(self, item):
        if item:
            self.workspaceActivated.emit(item.text())

    def show_component_context_menu(self, list_widget: QListWidget, pos):
        item = list_widget.itemAt(pos)
        if item is None:
            return
        comp = item.data(Qt.UserRole)
        if comp is None:
            return
        menu = QMenu(self)
        if comp.comp_id in self.favorite_ids:
            fav_action = menu.addAction("Aus Favoriten entfernen")
        else:
            fav_action = menu.addAction("Zu Favoriten hinzufügen")
        chosen = menu.exec_(list_widget.viewport().mapToGlobal(pos))
        if chosen == fav_action:
            if comp.comp_id in self.favorite_ids:
                self.favorite_ids.remove(comp.comp_id)
            else:
                self.favorite_ids.add(comp.comp_id)
            self.refresh_component_lists()
            self.select_component_def(comp.comp_id)

    def apply_filter(self, text_value: str):
        term = (text_value or "").strip().lower()
        for lw in self.category_lists.values():
            for row in range(lw.count()):
                item = lw.item(row)
                comp = item.data(Qt.UserRole)
                label = getattr(comp, "label", "")
                comp_id = getattr(comp, "comp_id", "")
                visible = (not term) or (term in label.lower()) or (term in comp_id.lower())
                item.setHidden(not visible)

    def on_item_clicked(self, item: QListWidgetItem):
        comp = item.data(Qt.UserRole)
        if comp is not None:
            self.componentSelected.emit(comp)

    def on_library_clicked(self, item: QListWidgetItem):
        template = item.data(Qt.UserRole)
        if template is not None:
            self.librarySelected.emit(template)

    def on_connection_clicked(self, item: QListWidgetItem):
        self.connectionActivated.emit(item.data(Qt.UserRole))

    def on_path_clicked(self, item: QListWidgetItem):
        self.pathActivated.emit(item.data(Qt.UserRole))

    def refresh_connections(self, rows, path_rows=None):
        self.connections_list.clear()
        for text_value, payload in rows:
            item = QListWidgetItem(text_value)
            item.setData(Qt.UserRole, payload)
            self.connections_list.addItem(item)
        self.paths_list.clear()
        for text_value, payload in (path_rows or []):
            item = QListWidgetItem(text_value)
            item.setData(Qt.UserRole, payload)
            self.paths_list.addItem(item)

    def select_component_def(self, comp_id: str):
        for tab_index in range(self.category_tabs.count()):
            lw = self.category_tabs.widget(tab_index)
            if not isinstance(lw, QListWidget):
                continue
            lw.blockSignals(True)
            try:
                lw.clearSelection()
                for row in range(lw.count()):
                    item = lw.item(row)
                    comp_def = item.data(Qt.UserRole)
                    if comp_def and getattr(comp_def, "comp_id", None) == comp_id:
                        item.setHidden(False)
                        lw.setCurrentItem(item)
                        item.setSelected(True)
                        lw.scrollToItem(item)
                        self.main_tabs.setCurrentIndex(0)
                        self.category_tabs.setCurrentIndex(tab_index)
                        return
            finally:
                lw.blockSignals(False)


class SchematicScene(QGraphicsScene):
    MODE_SELECT = 0
    MODE_WIRE = 1
    MODE_COMPONENT = 2
    MODE_SIMULATE = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(SCENE_RECT)
        self.mode = self.MODE_SELECT
        self.temp_start: Optional[QPointF] = None
        self.temp_wire: Optional[WireItem] = None
        self.pending_component: Optional[ComponentDef] = None
        self.components: List[ComponentItem] = []
        self.wires: List[WireItem] = []
        self._selection_sync_active = False
        self._group_drag_active = False
        self.overlay = ConnectionOverlayItem(self)
        self.addItem(self.overlay)
        self.junction_overlay = JunctionOverlayItem(self)
        self.addItem(self.junction_overlay)
        self.bridge_overlay = BridgeOverlayItem(self)
        self.addItem(self.bridge_overlay)
        self.selectionChanged.connect(self.on_selection_changed)
        self.changed.connect(lambda _=None: self.notify_connections_changed())
        self.host_widget = None


    def clear_model(self):
        for wire in list(self.wires):
            self.removeItem(wire)
        for comp in list(self.components):
            self.removeItem(comp)
        self.wires.clear()
        self.components.clear()
        self.temp_start = None
        self.temp_wire = None
        self.clearSelection()
        self.refresh_after_geometry_change()

    def set_host_widget(self, widget):
        self.host_widget = widget

    def notify_connections_changed(self):
        if self.host_widget:
            self.host_widget.refresh_connections_view()
            if self.pending_component is None and self.mode != self.MODE_COMPONENT:
                self.host_widget.sync_component_selection_from_scene()

    def snap_value(self, value: float) -> float:
        return round((value - GRID / 2) / GRID) * GRID + GRID / 2

    def snap_point(self, point: QPointF) -> QPointF:
        return QPointF(self.snap_value(point.x()), self.snap_value(point.y()))

    def snap_component_value(self, value: float) -> float:
        return round(value / GRID) * GRID

    def snap_component_point(self, point: QPointF) -> QPointF:
        return QPointF(self.snap_component_value(point.x()), self.snap_component_value(point.y()))

    def snap_delta(self, delta: QPointF) -> QPointF:
        return QPointF(round(delta.x() / GRID) * GRID, round(delta.y() / GRID) * GRID)

    def snap_orthogonal(self, fixed: QPointF, moving: QPointF) -> QPointF:
        dx = abs(moving.x() - fixed.x())
        dy = abs(moving.y() - fixed.y())
        if dx >= dy:
            return QPointF(self.snap_value(moving.x()), fixed.y())
        return QPointF(fixed.x(), self.snap_value(moving.y()))

    def distance(self, p1: QPointF, p2: QPointF) -> float:
        return math.hypot(p1.x() - p2.x(), p1.y() - p2.y())

    def set_mode(self, mode: int):
        self.mode = mode
        if mode != self.MODE_WIRE:
            self.temp_start = None
            self.temp_wire = None
        self.overlay.refresh()
        self.notify_connections_changed()

    def add_component_from_def(self, comp_def: ComponentDef):
        self.pending_component = comp_def
        self.set_mode(self.MODE_COMPONENT)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.mode == self.MODE_COMPONENT and self.pending_component:
            comp = ComponentItem(self, self.pending_component, event.scenePos())
            free_pos = self.find_free_component_position(comp, self.snap_component_point(comp.pos()))
            comp.setPos(free_pos)
            self.addItem(comp)
            self.components.append(comp)
            self.refresh_after_geometry_change()
            event.accept()
            return

        if event.button() == Qt.LeftButton and self.mode == self.MODE_WIRE:
            p = self.snap_point(event.scenePos())
            if self.temp_start is None:
                self.temp_start = self.find_best_anchor_pos(p)
                self.temp_wire = WireItem(self, self.temp_start, self.temp_start)
                self.addItem(self.temp_wire)
                self.wires.append(self.temp_wire)
                event.accept()
                return
            self.temp_wire.end = self.snap_orthogonal(self.temp_start, p)
            self.temp_wire.update_geometry()
            self.try_attach_wire_endpoint(self.temp_wire, "start")
            self.try_attach_wire_endpoint(self.temp_wire, "end")
            self.temp_start = None
            self.temp_wire = None
            self.refresh_after_geometry_change()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.mode == self.MODE_WIRE and self.temp_start is not None and self.temp_wire is not None:
            self.temp_wire.end = self.snap_orthogonal(self.temp_start, event.scenePos())
            self.temp_wire.update_geometry()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def component_rect_at(self, comp: ComponentItem, pos: QPointF) -> QRectF:
        return QRectF(pos.x(), pos.y(), comp.pixmap().width(), comp.pixmap().height())

    def collides_with_other_component(self, comp: ComponentItem, pos: QPointF) -> bool:
        new_rect = self.component_rect_at(comp, pos).adjusted(-2, -2, 2, 2)
        for other in self.components:
            if other is comp:
                continue
            other_rect = self.component_rect_at(other, other.pos())
            if new_rect.intersects(other_rect):
                return True
        return False

    def find_free_component_position(self, comp: ComponentItem, desired_pos: QPointF) -> QPointF:
        snapped = self.snap_component_point(desired_pos)
        if not self.collides_with_other_component(comp, snapped):
            return snapped
        directions = [
            QPointF(GRID, 0), QPointF(-GRID, 0), QPointF(0, GRID), QPointF(0, -GRID),
            QPointF(GRID, GRID), QPointF(-GRID, GRID), QPointF(GRID, -GRID), QPointF(-GRID, -GRID),
        ]
        for step in range(1, 18):
            for d in directions:
                candidate = self.snap_component_point(QPointF(snapped.x() + d.x() * step, snapped.y() + d.y() * step))
                if not self.sceneRect().contains(self.component_rect_at(comp, candidate)):
                    continue
                if not self.collides_with_other_component(comp, candidate):
                    return candidate
        return comp.pos()

    def anchor_candidates(self) -> List[AnchorNode]:
        nodes: List[AnchorNode] = []
        for w in self.wires:
            nodes.extend([w.start_node, w.end_node])
        for c in self.components:
            nodes.extend(c.anchor_nodes.values())
        return nodes

    def find_nearest_anchor(self, pos: QPointF) -> Optional[AnchorNode]:
        best = None
        best_dist = SNAP_DISTANCE
        for node in self.anchor_candidates():
            if node.owner is None:
                continue
            d = self.distance(node.pos, pos)
            if d <= best_dist:
                best_dist = d
                best = node
        return best

    def find_best_anchor_pos(self, pos: QPointF) -> QPointF:
        node = self.find_nearest_anchor(pos)
        return node.pos if node else self.snap_point(pos)

    def is_port_compatible(self, target_node: AnchorNode, wire_endpoint_name: str, wire: WireItem) -> bool:
        if target_node.kind != "component":
            return True
        port_kind = target_node.owner.port_kind(target_node.anchor_name)
        if wire_endpoint_name == "start":
            return port_kind == "out"
        return port_kind == "in"

    def try_attach_wire_endpoint(self, wire: WireItem, endpoint_name: str):
        pos = wire.anchor_scene_pos(endpoint_name)
        node = self.find_nearest_anchor(pos)
        target_node = wire.start_node if endpoint_name == "start" else wire.end_node
        for other_node in self.anchor_candidates():
            other_node.detach(wire, endpoint_name)
        target_node.links = [(w, ep) for (w, ep) in target_node.links if not (w is wire and ep == endpoint_name)]
        if node and node is not target_node and self.is_port_compatible(node, endpoint_name, wire):
            wire.anchor_moved(endpoint_name, node.pos)
            node.attach(wire, endpoint_name)
            target_node.attach(wire, endpoint_name)
        self.refresh_after_geometry_change()

    def is_anchor_covered(self, node: AnchorNode) -> bool:
        if node.kind == "component":
            return False
        nearby = 0
        for other in self.anchor_candidates():
            if other is node:
                continue
            if self.distance(other.pos, node.pos) <= 1:
                nearby += 1
        return nearby > 0

    def on_selection_changed(self):
        if self._selection_sync_active:
            return
        selected = self.selectedItems()
        if len(selected) < 2:
            return
        expanded = self.expand_selection_graph(set(selected))
        if expanded == set(selected):
            return
        self._selection_sync_active = True
        try:
            for item in expanded:
                item.setSelected(True)
        finally:
            self._selection_sync_active = False

    def expand_selection_graph(self, initial_items: Set[QGraphicsItem]) -> Set[QGraphicsItem]:
        expanded = set(initial_items)
        changed = True
        while changed:
            changed = False
            for item in list(expanded):
                if isinstance(item, ComponentItem):
                    comps, wires = self.collect_connected_graph("component", item, next(iter(item.anchor_nodes)))
                    new_items = set(comps) | set(wires)
                else:
                    comps, wires = self.collect_connected_graph("wire", item, "start")
                    new_items = set(comps) | set(wires)
                if not new_items.issubset(expanded):
                    expanded |= new_items
                    changed = True
        return expanded

    def find_linked_owner(self, wire: WireItem, endpoint_name: str):
        owners = self.find_linked_owners(wire, endpoint_name)
        return owners[0][0] if owners else None

    def endpoint_point(self, wire: WireItem, endpoint_name: str) -> QPointF:
        return wire.anchor_scene_pos(endpoint_name)

    def point_lies_on_wire_segment(self, point: QPointF, wire: WireItem, tolerance: float = 1.0) -> bool:
        if self.distance(point, wire.start) <= tolerance or self.distance(point, wire.end) <= tolerance:
            return True

        if abs(wire.start.x() - wire.end.x()) <= tolerance:
            if abs(point.x() - wire.start.x()) > tolerance:
                return False
            y1, y2 = sorted([wire.start.y(), wire.end.y()])
            return y1 - tolerance <= point.y() <= y2 + tolerance

        if abs(wire.start.y() - wire.end.y()) <= tolerance:
            if abs(point.y() - wire.start.y()) > tolerance:
                return False
            x1, x2 = sorted([wire.start.x(), wire.end.x()])
            return x1 - tolerance <= point.x() <= x2 + tolerance

        return False

    def point_has_wire_environment(self, point: QPointF, exclude_wire: Optional[WireItem] = None) -> bool:
        component_hits = 0
        wire_hits = 0

        for comp in self.components:
            for anchor_name in comp.anchor_nodes:
                if self.distance(comp.anchor_scene_pos(anchor_name), point) <= 1:
                    component_hits += 1

        for other in self.wires:
            if exclude_wire is not None and other is exclude_wire:
                continue
            if self.point_lies_on_wire_segment(point, other, tolerance=1.0):
                wire_hits += 1

        return component_hits > 0 or wire_hits > 1

    def find_linked_owners(self, wire: WireItem, endpoint_name: str):
        point = self.endpoint_point(wire, endpoint_name)
        owners = []
        seen = set()

        for comp in self.components:
            for anchor_name in comp.anchor_nodes:
                if self.distance(comp.anchor_scene_pos(anchor_name), point) <= 1:
                    token = ("component", comp.uid, anchor_name)
                    if token not in seen:
                        owners.append((comp, anchor_name))
                        seen.add(token)

        for other in self.wires:
            if other is wire:
                continue

            # Direkter Endpunkt-Endpunkt-Kontakt
            for other_endpoint in ("start", "end"):
                if self.distance(other.anchor_scene_pos(other_endpoint), point) <= 1:
                    token = ("wire", other.uid, other_endpoint)
                    if token not in seen:
                        owners.append((other, other_endpoint))
                        seen.add(token)

            # T-Abzweig: Endpunkt liegt auf einem fremden Drahtsegment.
            # Das zählt nur dann als Netzkontakt, wenn an dieser Stelle auch
            # eine Draht-Umgebung existiert und es nicht nur ein loses Überdecken ist.
            if self.point_lies_on_wire_segment(point, other, tolerance=1.0):
                if self.point_has_wire_environment(point, exclude_wire=wire):
                    token = ("wire", other.uid, "segment")
                    if token not in seen:
                        owners.append((other, "segment"))
                        seen.add(token)

        return owners

    def nearest_component_ports(self, point: QPointF, max_distance: float = SNAP_DISTANCE):
        matches = []
        for comp in self.components:
            for port in comp.comp_def.ports:
                port_pos = comp.anchor_scene_pos(port.name)
                dist = self.distance(port_pos, point)
                if dist <= max_distance:
                    matches.append((dist, comp, port.name))
        matches.sort(key=lambda item: item[0])
        return matches

    def endpoint_status_label(self, wire: WireItem, endpoint_name: str):
        owners = self.find_linked_owners(wire, endpoint_name)
        if owners:
            labels = []
            component_uids = []
            wire_uids = [wire.uid]
            for owner, owner_anchor in owners:
                if isinstance(owner, ComponentItem):
                    labels.append(f"{owner.comp_def.label}.{owner_anchor}")
                    component_uids.append(owner.uid)
                else:
                    labels.append(f"Wire:{owner.uid[-5:]}.{owner_anchor}")
                    wire_uids.append(owner.uid)
            return " + ".join(labels), list(dict.fromkeys(component_uids)), list(dict.fromkeys(wire_uids))
        near_matches = self.nearest_component_ports(self.endpoint_point(wire, endpoint_name))
        if near_matches:
            dist, comp, port_name = near_matches[0]
            return f"nahe {comp.comp_def.label}.{port_name} ({dist:.1f})", [comp.uid], [wire.uid]
        return f"frei:{endpoint_name}", [], [wire.uid]

    def selected_components(self) -> List[ComponentItem]:
        return [i for i in self.selectedItems() if isinstance(i, ComponentItem)]

    def selected_wires(self) -> List[WireItem]:
        return [i for i in self.selectedItems() if isinstance(i, WireItem)]

    def move_selected_group_by(self, delta: QPointF, leader=None) -> bool:
        snapped_delta = self.snap_delta(delta)
        if snapped_delta.x() == 0 and snapped_delta.y() == 0:
            return False
        components = self.selected_components()
        wires = self.selected_wires()
        if not components and not wires:
            return False
        self._group_drag_active = True
        try:
            for comp in components:
                target = self.snap_component_point(comp.pos() + snapped_delta)
                if self.collides_with_other_component(comp, target):
                    return False
            moved_component_uids = {c.uid for c in components}
            for comp in components:
                comp.setPos(self.snap_component_point(comp.pos() + snapped_delta))
            for wire in wires:
                attached_to_moved_component = False
                for endpoint_name in ("start", "end"):
                    linked = self.find_linked_owner(wire, endpoint_name)
                    if isinstance(linked, ComponentItem) and linked.uid in moved_component_uids:
                        attached_to_moved_component = True
                if not attached_to_moved_component:
                    wire.move_by_delta(snapped_delta)
            self.refresh_after_geometry_change()
            return True
        finally:
            self._group_drag_active = False

    def point_neighbors(self, point: QPointF) -> List[Tuple[str, object, str]]:
        result = []
        for comp in self.components:
            for name, node in comp.anchor_nodes.items():
                if self.distance(node.pos, point) <= 1:
                    result.append(("component", comp, name))
        for wire in self.wires:
            if self.distance(wire.start, point) <= 1:
                result.append(("wire", wire, "start"))
            if self.distance(wire.end, point) <= 1:
                result.append(("wire", wire, "end"))
            elif self.point_lies_on_wire_segment(point, wire, tolerance=1.0):
                result.append(("wire", wire, "segment"))
        return result

    def collect_connected_graph(self, seed_kind: str, seed_obj, seed_anchor: str):
        visited = set()
        queue = [(seed_kind, getattr(seed_obj, "uid", id(seed_obj)), seed_obj, seed_anchor)]
        component_set = set()
        wire_set = set()
        while queue:
            kind, key, obj, anchor = queue.pop(0)
            token = (kind, key, anchor)
            if token in visited:
                continue
            visited.add(token)
            component_set.add(obj) if kind == "component" else wire_set.add(obj)
            point = obj.anchor_scene_pos(anchor)
            for n_kind, n_obj, n_anchor in self.point_neighbors(point):
                n_key = getattr(n_obj, "uid", id(n_obj))
                if (n_kind, n_key, n_anchor) not in visited:
                    queue.append((n_kind, n_key, n_obj, n_anchor))
            if kind == "wire":
                other_anchor = "end" if anchor == "start" else "start"
                if (kind, key, other_anchor) not in visited:
                    queue.append((kind, key, obj, other_anchor))
        return component_set, wire_set

    def move_component_network(self, component: ComponentItem, delta: QPointF):
        if delta.x() == 0 and delta.y() == 0:
            return False
        seed_anchor = next(iter(component.anchor_nodes), None)
        if seed_anchor is None:
            return False
        components, wires = self.collect_connected_graph("component", component, seed_anchor)
        snapped_delta = self.snap_delta(delta)
        self._group_drag_active = True
        try:
            for comp in components:
                target = self.snap_component_point(comp.pos() + snapped_delta)
                if comp not in components:
                    continue
                if self.collides_with_other_component(comp, target):
                    return False
            for comp in components:
                comp.setPos(self.snap_component_point(comp.pos() + snapped_delta))
            moved_components = {c.uid for c in components}
            for wire in wires:
                anchored = False
                for ep in ("start", "end"):
                    linked = self.find_linked_owner(wire, ep)
                    if isinstance(linked, ComponentItem) and linked.uid in moved_components:
                        anchored = True
                        break
                if not anchored:
                    wire.move_by_delta(snapped_delta)
            self.refresh_after_geometry_change()
            return True
        finally:
            self._group_drag_active = False

    def move_wire_network(self, wire: WireItem, delta: QPointF):
        if delta.x() == 0 and delta.y() == 0:
            return
        comps1, wires1 = self.collect_connected_graph("wire", wire, "start")
        comps2, wires2 = self.collect_connected_graph("wire", wire, "end")
        components = set(comps1) | set(comps2)
        wires = set(wires1) | set(wires2)
        snapped_delta = self.snap_delta(delta)
        self._group_drag_active = True
        try:
            for comp in components:
                target = self.snap_component_point(comp.pos() + snapped_delta)
                if self.collides_with_other_component(comp, target):
                    return
            for comp in components:
                comp.setPos(self.snap_component_point(comp.pos() + snapped_delta))
            moved_components = {c.uid for c in components}
            for w in wires:
                anchored = False
                for ep in ("start", "end"):
                    linked = self.find_linked_owner(w, ep)
                    if isinstance(linked, ComponentItem) and linked.uid in moved_components:
                        anchored = True
                        break
                if not anchored:
                    w.move_by_delta(snapped_delta)
            self.refresh_after_geometry_change()
        finally:
            self._group_drag_active = False

    def update_component_links(self, comp: ComponentItem):
        for anchor_name, anchor_node in comp.anchor_nodes.items():
            new_pos = comp.anchor_scene_pos(anchor_name)
            for wire, endpoint_name in list(anchor_node.links):
                wire.anchor_moved(endpoint_name, new_pos)
        self.refresh_after_geometry_change()


    def delete_component(self, comp: ComponentItem):
        for anchor_name, anchor_node in comp.anchor_nodes.items():
            for wire, endpoint_name in list(anchor_node.links):
                wire.anchor_moved(endpoint_name, wire.anchor_scene_pos(endpoint_name))
                wire_node = wire.start_node if endpoint_name == "start" else wire.end_node
                wire_node.links = [(w, ep) for (w, ep) in wire_node.links if not (w is wire and ep == endpoint_name)]
            anchor_node.links = []
        if comp in self.components:
            self.components.remove(comp)
        self.removeItem(comp)
        self.refresh_after_geometry_change()

    def delete_wire(self, wire: WireItem):
        for node in self.anchor_candidates():
            node.links = [(w, ep) for (w, ep) in node.links if w is not wire]
        if wire in self.wires:
            self.wires.remove(wire)
        self.removeItem(wire)
        self.refresh_after_geometry_change()


    def compute_junction_points(self):
        point_map = {}

        def add_hit(point: QPointF, source_key):
            key = self.point_key(point)
            point_map.setdefault(key, {"point": point, "hits": set()})
            point_map[key]["hits"].add(source_key)

        for comp in self.components:
            for port in comp.comp_def.ports:
                port_point = comp.anchor_scene_pos(port.name)
                if self.point_has_wire_environment(port_point):
                    add_hit(port_point, ("component", comp.uid, port.name))

        for wire in self.wires:
            for endpoint_name in ("start", "end"):
                point = wire.anchor_scene_pos(endpoint_name)
                owners = self.find_linked_owners(wire, endpoint_name)
                if owners:
                    add_hit(point, ("wire", wire.uid, endpoint_name))
                    for owner, owner_anchor in owners:
                        add_hit(point, (getattr(owner, "uid", id(owner)), owner_anchor))

        result = []
        for info in point_map.values():
            if len(info["hits"]) >= 3:
                result.append(info["point"])
        return result


    def compute_bridge_crossings(self):
        bridges = []
        seen = set()
        for w1 in self.wires:
            for w2 in self.wires:
                if w1 is w2:
                    continue
                # only orthogonal crossings can form a bridge
                w1_horizontal = abs(w1.start.y() - w1.end.y()) <= 1
                w1_vertical = abs(w1.start.x() - w1.end.x()) <= 1
                w2_horizontal = abs(w2.start.y() - w2.end.y()) <= 1
                w2_vertical = abs(w2.start.x() - w2.end.x()) <= 1

                if w1_horizontal and w2_vertical:
                    cx = w2.start.x()
                    cy = w1.start.y()
                    point = QPointF(cx, cy)
                    if not self.point_lies_on_wire_segment(point, w1, 1.0):
                        continue
                    if not self.point_lies_on_wire_segment(point, w2, 1.0):
                        continue
                    # only draw a bridge when this is NOT a real connection/junction
                    if self.point_has_wire_environment(point):
                        continue
                    key = (round(cx), round(cy), "horizontal_over_vertical")
                    if key not in seen:
                        bridges.append((point, "horizontal_over_vertical"))
                        seen.add(key)

                elif w1_vertical and w2_horizontal:
                    cx = w1.start.x()
                    cy = w2.start.y()
                    point = QPointF(cx, cy)
                    if not self.point_lies_on_wire_segment(point, w1, 1.0):
                        continue
                    if not self.point_lies_on_wire_segment(point, w2, 1.0):
                        continue
                    if self.point_has_wire_environment(point):
                        continue
                    key = (round(cx), round(cy), "horizontal_over_vertical")
                    if key not in seen:
                        bridges.append((point, "horizontal_over_vertical"))
                        seen.add(key)
        return bridges

    def refresh_after_geometry_change(self):
        for wire in self.wires:
            wire.update_dot_visibility()
        self.overlay.refresh()
        self.junction_overlay.refresh()
        self.bridge_overlay.refresh()
        self.notify_connections_changed()

    def connection_rows(self) -> List[Tuple[str, str, str, str, str, str]]:
        rows = []
        seen = set()
        for wire in self.wires:
            for endpoint in ("start", "end"):
                for owner, owner_anchor in self.find_linked_owners(wire, endpoint):
                    target_kind = "component" if isinstance(owner, ComponentItem) else "wire"
                    entry = ("wire", wire.uid, endpoint, target_kind, owner.uid, owner_anchor)
                    if entry not in seen:
                        rows.append(entry)
                        seen.add(entry)
        return rows

    def network_payload_for_seed(self, seed_kind: str, seed_obj, seed_anchor: str):
        comps, wires = self.collect_connected_graph(seed_kind, seed_obj, seed_anchor)
        return {
            "component_uids": sorted(c.uid for c in comps),
            "wire_uids": sorted(w.uid for w in wires),
        }

    def connection_display_rows(self):
        rows = []
        seen_nets = set()
        for comp in self.components:
            for port in comp.comp_def.ports:
                payload = self.network_payload_for_seed("component", comp, port.name)
                net_key = (tuple(payload["component_uids"]), tuple(payload["wire_uids"]))
                if net_key in seen_nets:
                    continue
                seen_nets.add(net_key)

                port_labels = []
                for comp_uid in payload["component_uids"]:
                    c = next((x for x in self.components if x.uid == comp_uid), None)
                    if not c:
                        continue
                    for p in c.comp_def.ports:
                        pt = c.anchor_scene_pos(p.name)
                        neighbors = self.point_neighbors(pt)
                        if len(neighbors) > 1:
                            port_labels.append(f"{c.comp_def.label}.{p.name}")

                free_endpoints = []
                for wire_uid in payload["wire_uids"]:
                    w = next((x for x in self.wires if x.uid == wire_uid), None)
                    if not w:
                        continue
                    for endpoint in ("start", "end"):
                        owners = self.find_linked_owners(w, endpoint)
                        if not owners:
                            near_matches = self.nearest_component_ports(self.endpoint_point(w, endpoint))
                            if near_matches:
                                dist, comp_near, port_name = near_matches[0]
                                free_endpoints.append(f"nahe {comp_near.comp_def.label}.{port_name} ({dist:.1f})")
                            else:
                                free_endpoints.append(f"{w.uid[-4:]}.{endpoint}=frei")

                port_labels = sorted(set(port_labels))
                if port_labels:
                    text_value = f"Netz: {' ↔ '.join(port_labels)} | Wires: {len(payload['wire_uids'])}"
                else:
                    text_value = f"Netz ohne Port | Wires: {len(payload['wire_uids'])}"
                if free_endpoints:
                    text_value += " | Offen: " + ", ".join(sorted(set(free_endpoints)))
                rows.append((text_value, payload))

        if not rows:
            return [("Keine Verbindungen vorhanden", None)]
        return rows

    def resolve_connection_entry(self, payload):
        if not payload:
            return []
        selected = []
        wire_uids = set(payload.get("wire_uids", []))
        component_uids = set(payload.get("component_uids", []))
        for wire in self.wires:
            if wire.uid in wire_uids:
                selected.append(wire)
        for comp in self.components:
            if comp.uid in component_uids:
                selected.append(comp)
        return selected

    def select_connection_entry(self, payload):
        items = self.resolve_connection_entry(payload)
        if not items:
            return
        self.clearSelection()
        for item in items:
            item.setSelected(True)
        first = items[0]
        center = first.pos() if isinstance(first, ComponentItem) else QPointF((first.start.x() + first.end.x()) / 2, (first.start.y() + first.end.y()) / 2)
        if self.host_widget and hasattr(self.host_widget, "view"):
            self.host_widget.view.centerOn(center)

    def compute_simulation_edges(self) -> List[Tuple[QPointF, QPointF]]:
        edges = []
        graph = self.build_point_graph()
        sources = [
            (comp, port)
            for comp in self.components
            for port in comp.comp_def.ports
            if port.kind == "out"
        ]
        for comp, port in sources:
            start = comp.anchor_scene_pos(port.name)
            visited = {self.point_key(start): 0}
            queue = [start]
            while queue:
                current = queue.pop(0)
                dist = visited[self.point_key(current)]
                neighbors = sorted(graph.get(self.point_key(current), []), key=lambda p: self.distance(current, p))
                for neighbor in neighbors:
                    key = self.point_key(neighbor)
                    if key in visited:
                        continue
                    visited[key] = dist + self.distance(current, neighbor)
                    queue.append(neighbor)
                    edges.append((current, neighbor))
        return edges

    def point_key(self, p: QPointF):
        return (round(p.x()), round(p.y()))

    def build_point_graph(self) -> Dict[Tuple[int, int], List[QPointF]]:
        graph: Dict[Tuple[int, int], List[QPointF]] = {}

        def add_edge(a: QPointF, b: QPointF):
            ka, kb = self.point_key(a), self.point_key(b)
            graph.setdefault(ka, []).append(b)
            graph.setdefault(kb, []).append(a)

        for wire in self.wires:
            add_edge(wire.start, wire.end)
        return graph

    def connected_ports_for_payload(self, payload):
        wire_uids = set(payload.get("wire_uids", []))
        result = []
        for comp in self.components:
            for port in comp.comp_def.ports:
                pt = comp.anchor_scene_pos(port.name)
                for wire in self.wires:
                    if wire.uid not in wire_uids:
                        continue
                    if self.distance(wire.start, pt) <= 1 or self.distance(wire.end, pt) <= 1:
                        result.append((comp, port))
                        break
        return result

    def port_label(self, comp: ComponentItem, port: ComponentPort) -> str:
        return f"{comp.comp_def.label}.{port.name} [{port.kind.upper()}]"

    def port_node_payload(self, comp: ComponentItem, port: ComponentPort):
        return {
            "component_uids": [comp.uid],
            "wire_uids": [],
        }

    def path_display_rows(self):
        rows = []
        seen = set()
        out_ports = []
        in_ports = []
        for comp in self.components:
            for port in comp.comp_def.ports:
                if port.kind == "out":
                    out_ports.append((comp, port))
                elif port.kind == "in":
                    in_ports.append((comp, port))

        for out_comp, out_port in out_ports:
            out_payload = self.network_payload_for_seed("component", out_comp, out_port.name)
            out_wires = set(out_payload.get("wire_uids", []))
            if not out_wires:
                continue
            for in_comp, in_port in in_ports:
                if out_comp is in_comp and out_port.name == in_port.name:
                    continue
                in_payload = self.network_payload_for_seed("component", in_comp, in_port.name)
                in_wires = set(in_payload.get("wire_uids", []))
                if not in_wires:
                    continue
                if out_wires & in_wires:
                    combined_wire_uids = sorted(out_wires | in_wires)
                    combined_comp_uids = sorted(set(out_payload.get("component_uids", [])) | set(in_payload.get("component_uids", [])))
                    key = (
                        out_comp.uid, out_port.name,
                        in_comp.uid, in_port.name,
                        tuple(combined_wire_uids),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    payload = {
                        "component_uids": combined_comp_uids,
                        "wire_uids": combined_wire_uids,
                    }
                    text_value = f"Pfad: {self.port_label(out_comp, out_port)} ↔ {self.port_label(in_comp, in_port)} | Wires: {len(combined_wire_uids)}"
                    rows.append((text_value, payload))
        return rows or [("Keine Pfade vorhanden", None)]


class SchematicSubWindow(QWidget):
    def __init__(self, db: DatabaseManager, palette_widget: ComponentPalette, component_defs: List[ComponentDef], parent=None):
        super().__init__(parent)
        self.db = db
        self.palette_widget = palette_widget
        self.component_defs = component_defs
        self.current_file: Optional[str] = None
        self.scene = SchematicScene(self)
        self.scene.set_host_widget(self)
        self.palette_widget.connectionActivated.connect(self.scene.select_connection_entry)
        self.palette_widget.pathActivated.connect(self.scene.select_connection_entry)
        self.view = GridView(self.scene, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    def component_def_by_id(self, comp_id: str) -> Optional[ComponentDef]:
        for comp_def in self.component_defs:
            if comp_def.comp_id == comp_id:
                return comp_def
        return None

    def serialize_model(self) -> dict:
        return {
            "format": "elektro-baukasten-plan",
            "version": 1,
            "components": [
                {
                    "uid": comp.uid,
                    "comp_id": comp.comp_def.comp_id,
                    "x": comp.pos().x(),
                    "y": comp.pos().y(),
                    "rotation": comp.rotation(),
                    "scale": comp.scale(),
                    "port_layout_mode": getattr(comp, "port_layout_mode", "default"),
                    "custom_port_positions": getattr(comp, "custom_port_positions", {}),
                    "ports": [
                        {"name": p.name, "kind": p.kind, "x": p.x, "y": p.y}
                        for p in comp.comp_def.ports
                    ],
                }
                for comp in self.scene.components
            ],
            "wires": [
                {
                    "uid": wire.uid,
                    "x1": wire.start.x(),
                    "y1": wire.start.y(),
                    "x2": wire.end.x(),
                    "y2": wire.end.y(),
                }
                for wire in self.scene.wires
            ],
            "connections": [
                {
                    "source_type": source_kind,
                    "source_uid": source_uid,
                    "source_anchor": source_anchor,
                    "target_type": target_kind,
                    "target_uid": target_uid,
                    "target_anchor": target_anchor,
                }
                for source_kind, source_uid, source_anchor, target_kind, target_uid, target_anchor
                in self.scene.connection_rows()
            ],
        }

    def save_to_file(self, filename: str):
        data = self.serialize_model()
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.current_file = filename

    def load_from_file(self, filename: str):
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.scene.clear_model()
        comp_map = {}
        wire_map = {}

        for comp_info in data.get("components", []):
            comp_def = self.component_def_by_id(comp_info.get("comp_id", ""))
            if comp_def is None:
                continue
            comp = ComponentItem(self.scene, comp_def, QPointF(comp_info.get("x", 0), comp_info.get("y", 0)))
            comp.uid = comp_info.get("uid", comp.uid)
            self.scene.addItem(comp)
            self.scene.components.append(comp)
            comp.setRotation(comp_info.get("rotation", 0))
            comp.setScale(comp_info.get("scale", 1))
            comp.port_layout_mode = comp_info.get("port_layout_mode", "default")
            custom_positions = comp_info.get("custom_port_positions", {})
            comp.custom_port_positions = {k: tuple(v) for k, v in custom_positions.items()}
            comp.refresh_port_positions()
            comp_map[comp.uid] = comp

        for wire_info in data.get("wires", []):
            wire = WireItem(
                self.scene,
                QPointF(wire_info.get("x1", 0), wire_info.get("y1", 0)),
                QPointF(wire_info.get("x2", 0), wire_info.get("y2", 0)),
            )
            wire.uid = wire_info.get("uid", wire.uid)
            wire.start = QPointF(wire_info.get("x1", 0), wire_info.get("y1", 0))
            wire.end = QPointF(wire_info.get("x2", 0), wire_info.get("y2", 0))
            wire.update_geometry()
            self.scene.addItem(wire)
            self.scene.wires.append(wire)
            wire_map[wire.uid] = wire

        for link in data.get("connections", []):
            if link.get("source_type") != "wire":
                continue
            wire = wire_map.get(link.get("source_uid"))
            if not wire:
                continue
            endpoint = link.get("source_anchor")
            target_type = link.get("target_type")
            target_uid = link.get("target_uid")
            target_anchor = link.get("target_anchor")
            if endpoint not in ("start", "end"):
                continue
            target_node = None
            if target_type == "component":
                comp = comp_map.get(target_uid)
                if comp:
                    target_node = comp.anchor_nodes.get(target_anchor)
            elif target_type == "wire":
                other_wire = wire_map.get(target_uid)
                if other_wire and target_anchor in ("start", "end"):
                    target_node = other_wire.start_node if target_anchor == "start" else other_wire.end_node
            if target_node is not None:
                wire.anchor_moved(endpoint, target_node.pos)
                target_node.attach(wire, endpoint)
                source_node = wire.start_node if endpoint == "start" else wire.end_node
                source_node.attach(wire, endpoint)

        self.current_file = filename
        self.scene.refresh_after_geometry_change()

    def save(self):
        self.db.save_scene(self.scene)

    def refresh_connections_view(self):
        self.palette_widget.refresh_connections(
            self.scene.connection_display_rows(),
            self.scene.path_display_rows(),
        )

    def sync_component_selection_from_scene(self):
        selected_comp = None
        for item in self.scene.selectedItems():
            if isinstance(item, ComponentItem):
                selected_comp = item
                break
        if selected_comp is not None:
            self.palette_widget.select_component_def(selected_comp.comp_def.comp_id)

    def insert_library_template(self, template: dict):
        center_scene = self.view.mapToScene(self.view.viewport().rect().center())
        center_scene = self.scene.snap_component_point(center_scene)
        ref_map = {}

        for comp_info in template.get("components", []):
            comp_def = self.component_def_by_id(comp_info.get("comp_id", ""))
            if comp_def is None:
                continue
            pos = QPointF(center_scene.x() + comp_info.get("x", 0), center_scene.y() + comp_info.get("y", 0))
            comp = ComponentItem(self.scene, comp_def, pos)
            free_pos = self.scene.find_free_component_position(comp, self.scene.snap_component_point(pos))
            comp.setPos(free_pos)
            self.scene.addItem(comp)
            self.scene.components.append(comp)
            ref_map[comp_info.get("ref")] = comp

        for wire_info in template.get("wires", []):
            src_ref, src_anchor = wire_info.get("from", [None, None])
            dst_ref, dst_anchor = wire_info.get("to", [None, None])
            src_comp = ref_map.get(src_ref)
            dst_comp = ref_map.get(dst_ref)
            if not src_comp or not dst_comp:
                continue
            start_pos = src_comp.anchor_scene_pos(src_anchor)
            end_pos = dst_comp.anchor_scene_pos(dst_anchor)
            wire = WireItem(self.scene, start_pos, end_pos)
            wire.start = start_pos
            wire.end = end_pos
            wire.update_geometry()
            self.scene.addItem(wire)
            self.scene.wires.append(wire)

            src_node = src_comp.anchor_nodes.get(src_anchor)
            dst_node = dst_comp.anchor_nodes.get(dst_anchor)
            if src_node:
                src_node.attach(wire, "start")
                wire.start_node.attach(wire, "start")
            if dst_node:
                dst_node.attach(wire, "end")
                wire.end_node.attach(wire, "end")

        self.scene.refresh_after_geometry_change()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Qt5 MDI Schaltplan Editor")
        self.resize(1500, 920)
        self.db = DatabaseManager(DB_FILE)

        self.component_defs = [

            ComponentDef("resistor", "Widerstand", "svg/widerstand.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("potentiometer", "Potentiometer", "svg/potentiometer.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH // 2, GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("heating_resistor", "Heizwiderstand", "svg/widerstand.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("photoresistor", "Fotowiderstand", "svg/widerstand.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("varistor", "Varistor", "svg/widerstand.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("capacitor", "Kondensator", "svg/kondensator_ungepolt.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("ceramic_capacitor", "Keramikkondensator", "svg/kondensator_ungepolt.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("capacitor_polarized", "Elko", "svg/kondensator_gepolt.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("electrolytic_capacitor", "Elektrolytkondensator", "svg/kondensator_gepolt.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("inductor", "Spule", "svg/spule.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("choke", "Drossel", "svg/spule.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("diode", "Diode", "svg/diode.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("led", "LED", "svg/led.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("zener", "Z-Diode", "svg/zdiode.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("schottky_diode", "Schottky-Diode", "svg/diode.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("diac", "Diac", "svg/diode.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("triac", "Triac", "svg/diode.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("magnet_diode", "Magnet-Diode", "svg/diode.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("battery", "Batterie", "svg/batterie.svg",
                [ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH // 2, GRID // 2),
                 ComponentPort("IN1", "in", DEFAULT_COMPONENT_WIDTH // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("voltage_source", "Spannungsquelle", "svg/spannungsquelle_dc.svg",
                [ComponentPort("OUT1", "out", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("IN1", "in", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("current_source", "Stromquelle", "svg/stromquelle.svg",
                [ComponentPort("OUT1", "out", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("IN1", "in", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("solar_cell", "Solarzelle", "svg/spannungsquelle_dc.svg",
                [ComponentPort("OUT1", "out", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("IN1", "in", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("switch_open", "Schalter offen", "svg/schalter_offen.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("switch_closed", "Schalter zu", "svg/schalter_geschlossen.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("spst", "SPST", "svg/schalter_offen.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("spdt", "SPDT", "svg/schalter_offen.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("spco", "SPCO", "svg/schalter_offen.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("sptt", "SPTT", "svg/schalter_offen.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("sp3t", "SP3T", "svg/schalter_offen.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT3", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("dpst", "DPST", "svg/schalter_geschlossen.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("dpdt", "DPDT", "svg/schalter_offen.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("dpco", "DPCO", "svg/schalter_offen.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("fuse", "Sicherung", "svg/sicherung.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("lamp", "Lampe", "svg/lampe.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("piezo", "Piezo", "svg/sprecher.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("light_barrier", "Lichtschranke", "svg/led.svg",
                [ComponentPort("OUT1", "out", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("IN1", "in", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("relay", "Relais", "svg/relais_spule.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("electromagnet", "Elektro-Magnet", "svg/relais_spule.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("transformer", "Trafo", "svg/trafo.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("and_gate", "AND", "svg/and.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("or_gate", "OR", "svg/or.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("xor_gate", "XOR", "svg/xor.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("nand_gate", "NAND", "svg/nand.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("nor_gate", "NOR", "svg/nor.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("xnor_gate", "XNOR", "svg/xnor.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("not_gate", "NOT", "svg/not.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("npn", "Transistor NPN", "svg/transistor_npn.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("pnp", "Transistor PNP", "svg/transistor_pnp.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, GRID // 2),
                 ComponentPort("OUT2", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("opamp", "OPV", "svg/opamp.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("IN2", "in", GRID // 2, COMPONENT_HEIGHT - GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("amplifier", "Verstärker", "svg/opamp.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("quartz", "Quarz", "svg/quarz.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("speaker", "Lautsprecher", "svg/sprecher.svg",
                [ComponentPort("IN1", "in", GRID // 2, GRID // 2),
                 ComponentPort("OUT1", "out", GRID // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("microphone", "Mikrofon", "svg/mikrofon.svg",
                [ComponentPort("OUT1", "out", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("IN1", "in", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("ground", "Masse", "svg/masse.svg",
                [ComponentPort("IN1", "in", DEFAULT_COMPONENT_WIDTH // 2, GRID // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH // 2, COMPONENT_HEIGHT - GRID // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),

            ComponentDef("white_led", "LED weiß", "svg/white_led.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("green_led", "LED grün", "svg/green_led.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("blue_led", "LED blau", "svg/blue_led.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("yellow_led", "LED gelb", "svg/yellow_led.svg",
                [ComponentPort("IN1", "in", GRID // 2, COMPONENT_HEIGHT // 2),
                 ComponentPort("OUT1", "out", DEFAULT_COMPONENT_WIDTH - GRID // 2, COMPONENT_HEIGHT // 2)],
                (DEFAULT_COMPONENT_WIDTH, COMPONENT_HEIGHT)),
            ComponentDef("dual_d_flipflop_4013", "Dual D Flip Flop (4013)", "svg/ic_dip14.svg",
                [ComponentPort("1SET", "in", GRID // 2, GRID // 2),
                 ComponentPort("1D", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("1CLK", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("1RST", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("2SET", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("2D", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("2CLK", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("1Q", "out", 230, GRID // 2),
                 ComponentPort("1NQ", "out", 230, GRID // 2 + GRID * 2),
                 ComponentPort("1VSS", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("2RST", "out", 230, GRID // 2 + GRID * 6),
                 ComponentPort("2Q", "out", 230, GRID // 2 + GRID * 8),
                 ComponentPort("2NQ", "out", 230, GRID // 2 + GRID * 10),
                 ComponentPort("2VDD", "out", 230, GRID // 2 + GRID * 12)],
                (240, GRID * 14)),
            ComponentDef("ic_4014", "4014", "svg/ic_dip16.svg",
                [ComponentPort("P1", "in", GRID // 2, GRID // 2),
                 ComponentPort("P2", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("P3", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("P4", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("CLK", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("DATA", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("STR", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("Q6", "out", 230, GRID // 2),
                 ComponentPort("Q7", "out", 230, GRID // 2 + GRID * 2),
                 ComponentPort("VSS", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("P7", "out", 230, GRID // 2 + GRID * 6),
                 ComponentPort("P8", "out", 230, GRID // 2 + GRID * 8),
                 ComponentPort("Q8", "out", 230, GRID // 2 + GRID * 10),
                 ComponentPort("VDD", "out", 230, GRID // 2 + GRID * 12)],
                (240, GRID * 14)),
            ComponentDef("ic_4017", "4017", "svg/ic_dip16.svg",
                [ComponentPort("CLK", "in", GRID // 2, GRID // 2),
                 ComponentPort("INH", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("RST", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("Q5", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("Q1", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("Q0", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("Q2", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("Q6", "out", 230, GRID // 2),
                 ComponentPort("Q7", "out", 230, GRID // 2 + GRID * 2),
                 ComponentPort("Q3", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("Q8", "out", 230, GRID // 2 + GRID * 6),
                 ComponentPort("Q4", "out", 230, GRID // 2 + GRID * 8),
                 ComponentPort("Q9", "out", 230, GRID // 2 + GRID * 10),
                 ComponentPort("CO", "out", 230, GRID // 2 + GRID * 12)],
                (240, GRID * 14)),
            ComponentDef("ic_4020", "4020", "svg/ic_dip16.svg",
                [ComponentPort("CLK", "in", GRID // 2, GRID // 2),
                 ComponentPort("RST", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("Q11", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("Q5", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("Q4", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("Q6", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("Q3", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("Q12", "out", 230, GRID // 2),
                 ComponentPort("Q13", "out", 230, GRID // 2 + GRID * 2),
                 ComponentPort("Q14", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("Q7", "out", 230, GRID // 2 + GRID * 6),
                 ComponentPort("Q8", "out", 230, GRID // 2 + GRID * 8),
                 ComponentPort("Q9", "out", 230, GRID // 2 + GRID * 10),
                 ComponentPort("Q10", "out", 230, GRID // 2 + GRID * 12)],
                (240, GRID * 14)),
            ComponentDef("ic_4026", "4026", "svg/ic_dip16.svg",
                [ComponentPort("CLK", "in", GRID // 2, GRID // 2),
                 ComponentPort("INH", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("DEI", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("DEO", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("CO", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("RST", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("CLKO", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("a-g", "out", 230, GRID // 2),
                 ComponentPort("SEG1", "out", 230, GRID // 2 + GRID * 2),
                 ComponentPort("SEG2", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("SEG3", "out", 230, GRID // 2 + GRID * 6),
                 ComponentPort("SEG4", "out", 230, GRID // 2 + GRID * 8),
                 ComponentPort("SEG5", "out", 230, GRID // 2 + GRID * 10),
                 ComponentPort("SEG6", "out", 230, GRID // 2 + GRID * 12)],
                (240, GRID * 14)),
            ComponentDef("ic_4025", "4025", "svg/ic_dip14.svg",
                [ComponentPort("A1", "in", GRID // 2, GRID // 2),
                 ComponentPort("B1", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("C1", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("A2", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("B2", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("C2", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("Y1", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("Y2", "out", 230, GRID // 2),
                 ComponentPort("A3", "out", 230, GRID // 2 + GRID * 2),
                 ComponentPort("B3", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("C3", "out", 230, GRID // 2 + GRID * 6),
                 ComponentPort("Y3", "out", 230, GRID // 2 + GRID * 8),
                 ComponentPort("VSS", "out", 230, GRID // 2 + GRID * 10),
                 ComponentPort("VDD", "out", 230, GRID // 2 + GRID * 12)],
                (240, GRID * 14)),
            ComponentDef("ic_4040", "4040", "svg/ic_dip16.svg",
                [ComponentPort("CLK", "in", GRID // 2, GRID // 2),
                 ComponentPort("RST", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("Q1", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("Q2", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("Q3", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("Q4", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("Q5", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("Q6", "out", 230, GRID // 2),
                 ComponentPort("Q7", "out", 230, GRID // 2 + GRID * 2),
                 ComponentPort("Q8", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("Q9", "out", 230, GRID // 2 + GRID * 6),
                 ComponentPort("Q10", "out", 230, GRID // 2 + GRID * 8),
                 ComponentPort("Q11", "out", 230, GRID // 2 + GRID * 10),
                 ComponentPort("Q12", "out", 230, GRID // 2 + GRID * 12)],
                (240, GRID * 14)),
            ComponentDef("ic_4511", "4511", "svg/ic_dip16.svg",
                [ComponentPort("A", "in", GRID // 2, GRID // 2),
                 ComponentPort("B", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("C", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("D", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("LE", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("BI", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("LT", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("a", "out", 230, GRID // 2),
                 ComponentPort("b", "out", 230, GRID // 2 + GRID * 2),
                 ComponentPort("c", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("d", "out", 230, GRID // 2 + GRID * 6),
                 ComponentPort("e", "out", 230, GRID // 2 + GRID * 8),
                 ComponentPort("f", "out", 230, GRID // 2 + GRID * 10),
                 ComponentPort("g", "out", 230, GRID // 2 + GRID * 12)],
                (240, GRID * 14)),
            ComponentDef("display7_single", "7-Segment einfach", "svg/display7_single.svg",
                [ComponentPort("aL", "in", GRID // 2, GRID // 2),
                 ComponentPort("bL", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("cL", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("dL", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("eL", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("aR", "in", 230, GRID // 2),
                 ComponentPort("bR", "in", 230, GRID // 2 + GRID * 2),
                 ComponentPort("cR", "in", 230, GRID // 2 + GRID * 4),
                 ComponentPort("dR", "in", 230, GRID // 2 + GRID * 6),
                 ComponentPort("eR", "in", 230, GRID // 2 + GRID * 8)],
                (240, GRID * 10)),
            ComponentDef("display7_double", "7-Segment doppelt", "svg/display7_double.svg",
                [ComponentPort("L1", "in", GRID // 2, GRID // 2 + GRID * 0),
                 ComponentPort("L2", "in", GRID // 2, GRID // 2 + GRID * 1),
                 ComponentPort("L3", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("L4", "in", GRID // 2, GRID // 2 + GRID * 3),
                 ComponentPort("L5", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("L6", "in", GRID // 2, GRID // 2 + GRID * 5),
                 ComponentPort("L7", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("L8", "in", GRID // 2, GRID // 2 + GRID * 7),
                 ComponentPort("L9", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("L10", "in", GRID // 2, GRID // 2 + GRID * 9),
                 ComponentPort("R1", "in", 470, GRID // 2 + GRID * 0),
                 ComponentPort("R2", "in", 470, GRID // 2 + GRID * 1),
                 ComponentPort("R3", "in", 470, GRID // 2 + GRID * 2),
                 ComponentPort("R4", "in", 470, GRID // 2 + GRID * 3),
                 ComponentPort("R5", "in", 470, GRID // 2 + GRID * 4),
                 ComponentPort("R6", "in", 470, GRID // 2 + GRID * 5),
                 ComponentPort("R7", "in", 470, GRID // 2 + GRID * 6),
                 ComponentPort("R8", "in", 470, GRID // 2 + GRID * 7),
                 ComponentPort("R9", "in", 470, GRID // 2 + GRID * 8),
                 ComponentPort("R10", "in", 470, GRID // 2 + GRID * 9)],
                (480, GRID * 11)),
            ComponentDef("display7_quad", "7-Segment vierfach", "svg/display7_quad.svg",
                [ComponentPort("L1", "in", GRID // 2, GRID // 2 + GRID * 0),
                 ComponentPort("L2", "in", GRID // 2, GRID // 2 + GRID * 1),
                 ComponentPort("L3", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("L4", "in", GRID // 2, GRID // 2 + GRID * 3),
                 ComponentPort("L5", "in", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("L6", "in", GRID // 2, GRID // 2 + GRID * 5),
                 ComponentPort("L7", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("L8", "in", GRID // 2, GRID // 2 + GRID * 7),
                 ComponentPort("L9", "in", GRID // 2, GRID // 2 + GRID * 8),
                 ComponentPort("L10", "in", GRID // 2, GRID // 2 + GRID * 9),
                 ComponentPort("L11", "in", GRID // 2, GRID // 2 + GRID * 10),
                 ComponentPort("L12", "in", GRID // 2, GRID // 2 + GRID * 11),
                 ComponentPort("L13", "in", GRID // 2, GRID // 2 + GRID * 12),
                 ComponentPort("L14", "in", GRID // 2, GRID // 2 + GRID * 13),
                 ComponentPort("L15", "in", GRID // 2, GRID // 2 + GRID * 14),
                 ComponentPort("L16", "in", GRID // 2, GRID // 2 + GRID * 15),
                 ComponentPort("L17", "in", GRID // 2, GRID // 2 + GRID * 16),
                 ComponentPort("L18", "in", GRID // 2, GRID // 2 + GRID * 17),
                 ComponentPort("L19", "in", GRID // 2, GRID // 2 + GRID * 18),
                 ComponentPort("L20", "in", GRID // 2, GRID // 2 + GRID * 19),
                 ComponentPort("R1", "in", 950, GRID // 2 + GRID * 0),
                 ComponentPort("R2", "in", 950, GRID // 2 + GRID * 1),
                 ComponentPort("R3", "in", 950, GRID // 2 + GRID * 2),
                 ComponentPort("R4", "in", 950, GRID // 2 + GRID * 3),
                 ComponentPort("R5", "in", 950, GRID // 2 + GRID * 4),
                 ComponentPort("R6", "in", 950, GRID // 2 + GRID * 5),
                 ComponentPort("R7", "in", 950, GRID // 2 + GRID * 6),
                 ComponentPort("R8", "in", 950, GRID // 2 + GRID * 7),
                 ComponentPort("R9", "in", 950, GRID // 2 + GRID * 8),
                 ComponentPort("R10", "in", 950, GRID // 2 + GRID * 9),
                 ComponentPort("R11", "in", 950, GRID // 2 + GRID * 10),
                 ComponentPort("R12", "in", 950, GRID // 2 + GRID * 11),
                 ComponentPort("R13", "in", 950, GRID // 2 + GRID * 12),
                 ComponentPort("R14", "in", 950, GRID // 2 + GRID * 13),
                 ComponentPort("R15", "in", 950, GRID // 2 + GRID * 14),
                 ComponentPort("R16", "in", 950, GRID // 2 + GRID * 15),
                 ComponentPort("R17", "in", 950, GRID // 2 + GRID * 16),
                 ComponentPort("R18", "in", 950, GRID // 2 + GRID * 17),
                 ComponentPort("R19", "in", 950, GRID // 2 + GRID * 18),
                 ComponentPort("R20", "in", 950, GRID // 2 + GRID * 19)],
                (960, GRID * 21)),
            ComponentDef("ne555_timer", "NE555 IC Timer", "svg/ne555.svg",
                [ComponentPort("GND", "in", GRID // 2, GRID // 2),
                 ComponentPort("TRIG", "in", GRID // 2, GRID // 2 + GRID * 2),
                 ComponentPort("OUT", "out", GRID // 2, GRID // 2 + GRID * 4),
                 ComponentPort("RST", "in", GRID // 2, GRID // 2 + GRID * 6),
                 ComponentPort("CTRL", "in", 230, GRID // 2),
                 ComponentPort("THR", "in", 230, GRID // 2 + GRID * 2),
                 ComponentPort("DIS", "out", 230, GRID // 2 + GRID * 4),
                 ComponentPort("VCC", "out", 230, GRID // 2 + GRID * 6)],
                (240, GRID * 8)),

        ]

        self.workspace_records = {}
        self.workspace_clipboard = None
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setTabsClosable(True)
        self.workspace_tabs.setMovable(True)
        self.workspace_tabs.currentChanged.connect(self.on_workspace_tab_changed)
        self.workspace_tabs.tabCloseRequested.connect(self.close_workspace_tab)
        self.workspace_tabs.tabBarDoubleClicked.connect(self.rename_workspace_tab_dialog)
        self.setCentralWidget(self.workspace_tabs)
        self.setStatusBar(QStatusBar())

        self.create_actions()
        self.create_menus()
        self.create_toolbars()
        self.create_dock()
        self.add_workspace("Unbenannt")

    def active_editor(self) -> Optional[SchematicSubWindow]:
        widget = self.workspace_tabs.currentWidget()
        return widget if isinstance(widget, SchematicSubWindow) else None

    def create_actions(self):
        style = self.style()
        self.act_new     = QAction(style.standardIcon(QStyle.SP_FileIcon), "Neu", self)
        self.act_open    = QAction(style.standardIcon(QStyle.SP_DialogOpenButton), "Laden...", self)
        self.act_save    = QAction(style.standardIcon(QStyle.SP_DialogSaveButton), "Speichern", self)
        self.act_save_as = QAction(style.standardIcon(QStyle.SP_DialogSaveButton), "Speichern unter...", self)
        self.act_copy    = QAction(style.standardIcon(QStyle.SP_FileDialogDetailedView), "Kopieren", self)
        self.act_paste   = QAction(style.standardIcon(QStyle.SP_DialogOpenButton), "Einfügen", self)
        self.act_cascade = QAction("Kaskadieren", self)
        self.act_tile    = QAction("Nebeneinander", self)
        self.act_about   = QAction("Über", self)

        self.act_mode_select   = QAction(style.standardIcon(QStyle.SP_ArrowBack), "Auswahlmodus", self)
        self.act_mode_wire     = QAction(style.standardIcon(QStyle.SP_CommandLink), "Add-Wire-Modus", self)
        self.act_mode_simulate = QAction(style.standardIcon(QStyle.SP_MediaPlay), "Simulieren", self)
        for act in (self.act_mode_select, self.act_mode_wire, self.act_mode_simulate):
            act.setCheckable(True)

        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusive(True)
        for act in (self.act_mode_select, self.act_mode_wire, self.act_mode_simulate):
            self.mode_group.addAction(act)

        self.act_mode_select.setChecked(True)

        self.act_new.triggered.connect(self.new_document)
        self.act_open.triggered.connect(self.open_document)
        self.act_save.triggered.connect(self.save_document)
        self.act_save_as.triggered.connect(self.save_document_as)
        self.act_cascade.triggered.connect(lambda: None)
        self.act_tile.triggered.connect(lambda: None)
        self.act_about.triggered.connect(self.show_about)
        self.act_mode_select.triggered.connect(self.mode_select)
        self.act_mode_wire.triggered.connect(self.mode_wire)
        self.act_mode_simulate.triggered.connect(self.mode_simulate)

    def create_menus(self):
        menu_file = self.menuBar().addMenu("Datei")
        menu_edit = self.menuBar().addMenu("Bearbeiten")
        menu_window = self.menuBar().addMenu("Fenster")
        menu_help = self.menuBar().addMenu("Hilfe")
        menu_file.addAction(self.act_new)
        menu_file.addAction(self.act_open)
        menu_file.addAction(self.act_save)
        menu_file.addAction(self.act_save_as)
        menu_edit.addAction(self.act_copy)
        menu_edit.addAction(self.act_paste)
        menu_window.addAction(self.act_cascade)
        menu_window.addAction(self.act_tile)
        menu_help.addAction(self.act_about)

    def create_toolbars(self):
        tb_main = QToolBar("Datei")
        tb_main.addAction(self.act_new)
        tb_main.addAction(self.act_open)
        tb_main.addAction(self.act_save)
        tb_main.addAction(self.act_save_as)
        tb_main.addSeparator()
        tb_main.addAction(self.act_copy)
        tb_main.addAction(self.act_paste)
        self.addToolBar(Qt.TopToolBarArea, tb_main)

        tb_modes = QToolBar("Modi")
        tb_modes.addAction(self.act_mode_select)
        tb_modes.addAction(self.act_mode_wire)
        tb_modes.addAction(self.act_mode_simulate)
        self.addToolBar(Qt.TopToolBarArea, tb_modes)

    def create_dock(self):
        self.dock = QDockWidget("Bauteile", self)
        self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.palette = ComponentPalette(self.component_defs, self)
        self.palette.componentSelected.connect(self.component_selected)
        self.palette.librarySelected.connect(self.library_selected)
        self.palette.connectionActivated.connect(self.connection_selected)
        self.palette.workspaceAddRequested.connect(self.add_workspace_dialog)
        self.palette.workspaceCopyRequested.connect(self.copy_workspace)
        self.palette.workspacePasteRequested.connect(self.paste_workspace)
        self.palette.workspaceDeleteRequested.connect(self.delete_current_workspace)
        self.palette.workspaceActivated.connect(self.open_workspace_by_name)
        self.dock.setWidget(self.palette)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock)


    def refresh_workspace_list(self, active_name=None):
        names = list(self.workspace_records.keys())
        if active_name is None:
            editor = self.active_editor()
            if editor:
                active_name = getattr(editor, "workspace_name", None)
        self.palette.set_workspaces(names, active_name)

    def create_workspace_editor(self, name: str):
        editor = SchematicSubWindow(self.db, self.palette, self.component_defs, self)
        editor.workspace_name = name
        return editor

    def add_workspace_dialog(self):
        name, ok = QInputDialog.getText(self, "Arbeitsfläche hinzufügen", "Name der Arbeitsfläche:")
        if not ok or not str(name).strip():
            return
        self.add_workspace(str(name).strip())

    def add_workspace(self, name: str, serialized=None, open_now=True):
        if name in self.workspace_records:
            if open_now:
                self.open_workspace_by_name(name)
            return False
        self.workspace_records[name] = {"serialized": serialized or None, "open": False}
        if open_now:
            self.open_workspace_by_name(name)
        self.refresh_workspace_list(name)
        return True

    def open_workspace_by_name(self, name: str):
        if name not in self.workspace_records:
            return
        for i in range(self.workspace_tabs.count()):
            w = self.workspace_tabs.widget(i)
            if getattr(w, "workspace_name", None) == name:
                self.workspace_tabs.setCurrentIndex(i)
                self.refresh_workspace_list(name)
                return
        editor = self.create_workspace_editor(name)
        serialized = self.workspace_records[name].get("serialized")
        if serialized:
            tmp = Path("/mnt/data/__workspace_tmp.plan")
            tmp.write_text(json.dumps(serialized, ensure_ascii=False), encoding="utf-8")
            editor.load_from_file(str(tmp))
            tmp.unlink(missing_ok=True)
        idx = self.workspace_tabs.addTab(editor, name)
        self.workspace_tabs.setCurrentIndex(idx)
        self.workspace_records[name]["open"] = True
        self.refresh_workspace_list(name)
        editor.refresh_connections_view()

    def snapshot_editor_to_record(self, editor):
        if editor and getattr(editor, "workspace_name", None) in self.workspace_records:
            self.workspace_records[editor.workspace_name]["serialized"] = editor.serialize_model()

    def on_workspace_tab_changed(self, index):
        editor = self.active_editor()
        if editor:
            self.refresh_workspace_list(editor.workspace_name)
            self.mode_select()

    def close_workspace_tab(self, index):
        widget = self.workspace_tabs.widget(index)
        if not widget:
            return
        self.snapshot_editor_to_record(widget)
        name = getattr(widget, "workspace_name", None)
        self.workspace_tabs.removeTab(index)
        if name in self.workspace_records:
            self.workspace_records[name]["open"] = False
        widget.deleteLater()
        self.refresh_workspace_list()

    def rename_workspace_tab_dialog(self, index):
        if index < 0:
            return
        widget = self.workspace_tabs.widget(index)
        if not widget:
            return
        old_name = getattr(widget, "workspace_name", "")
        new_name, ok = QInputDialog.getText(self, "Arbeitsfläche umbenennen", "Neuer Name:", text=old_name)
        if not ok or not str(new_name).strip():
            return
        new_name = str(new_name).strip()
        if new_name == old_name:
            return
        if new_name in self.workspace_records:
            QMessageBox.warning(self, "Name existiert", "Eine Arbeitsfläche mit diesem Namen ist bereits vorhanden.")
            return
        self.workspace_records[new_name] = self.workspace_records.pop(old_name)
        widget.workspace_name = new_name
        self.workspace_tabs.setTabText(index, new_name)
        self.refresh_workspace_list(new_name)

    def copy_workspace(self):
        editor = self.active_editor()
        if not editor:
            return
        self.workspace_clipboard = editor.serialize_model()
        self.statusBar().showMessage(f"Arbeitsfläche kopiert: {editor.workspace_name}", 2500)

    def paste_workspace(self):
        if not self.workspace_clipboard:
            return
        name, ok = QInputDialog.getText(self, "Arbeitsfläche einfügen", "Name der neuen Arbeitsfläche:")
        if not ok or not str(name).strip():
            return
        self.add_workspace(str(name).strip(), serialized=self.workspace_clipboard, open_now=True)

    def delete_current_workspace(self):
        editor = self.active_editor()
        if not editor:
            return
        name = editor.workspace_name
        self.workspace_tabs.removeTab(self.workspace_tabs.currentIndex())
        if name in self.workspace_records:
            del self.workspace_records[name]
        editor.deleteLater()
        self.refresh_workspace_list()

    def component_selected(self, comp_def: ComponentDef):
        editor = self.active_editor()
        if editor:
            self.palette.select_component_def(comp_def.comp_id)
            editor.scene.add_component_from_def(comp_def)
            self.statusBar().showMessage(f"Komponente ausgewählt: {comp_def.label}", 3000)

    def library_selected(self, template):
        editor = self.active_editor()
        if editor:
            editor.insert_library_template(template)
            self.statusBar().showMessage(f"Bibliothek eingefügt: {template.get('name', 'Gruppe')}", 3000)

    def connection_selected(self, payload):
        editor = self.active_editor()
        if editor:
            editor.scene.select_connection_entry(payload)

    def new_document(self):
        self.add_workspace_dialog()

    def save_document(self):
        editor = self.active_editor()
        if not editor:
            return
        if not editor.current_file:
            self.save_document_as()
            return
        editor.save_to_file(editor.current_file)
        self.statusBar().showMessage(f"Modell gespeichert: {editor.current_file}", 4000)

    def save_document_as(self):
        editor = self.active_editor()
        if not editor:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Modell speichern unter",
            editor.current_file or "modell.plan",
            "Plan-Dateien (*.plan);;Alle Dateien (*)",
        )
        if not filename:
            return
        editor.save_to_file(filename)
        editor.workspace_name = Path(filename).stem
        current_index = self.workspace_tabs.currentIndex()
        if current_index >= 0:
            self.workspace_tabs.setTabText(current_index, editor.workspace_name)
            if editor.workspace_name not in self.workspace_records:
                self.workspace_records[editor.workspace_name] = {"serialized": editor.serialize_model(), "open": True}
            else:
                self.workspace_records[editor.workspace_name]["serialized"] = editor.serialize_model()
            self.refresh_workspace_list(editor.workspace_name)
        self.statusBar().showMessage(f"Modell gespeichert: {filename}", 4000)

    def open_document(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Modell laden",
            "",
            "Plan-Dateien (*.plan);;Alle Dateien (*)",
        )
        if not filename:
            return
        name = Path(filename).stem
        if name in self.workspace_records:
            self.open_workspace_by_name(name)
            return
        editor = self.create_workspace_editor(name)
        editor.load_from_file(filename)
        self.workspace_records[name] = {"serialized": editor.serialize_model(), "open": True}
        idx = self.workspace_tabs.addTab(editor, name)
        self.workspace_tabs.setCurrentIndex(idx)
        self.refresh_workspace_list(name)
        self.statusBar().showMessage(f"Modell geladen: {filename}", 4000)

    def mode_select(self):
        editor = self.active_editor()
        if editor:
            editor.scene.set_mode(SchematicScene.MODE_SELECT)
            self.statusBar().showMessage("Modus: Auswahl", 2000)

    def mode_wire(self):
        editor = self.active_editor()
        if editor:
            editor.scene.set_mode(SchematicScene.MODE_WIRE)
            self.statusBar().showMessage("Modus: Add-Wire", 2000)

    def mode_simulate(self):
        editor = self.active_editor()
        if editor:
            editor.scene.set_mode(SchematicScene.MODE_SIMULATE)
            self.statusBar().showMessage(
                "Modus: Simulation aktiv – aktuell mit Vorrang für kürzere Strecken.",
                4000,
            )

    def show_about(self):
        QMessageBox.information(
            self,
            "Über",
            "MDI-Schaltplaneditor mit Komponenten-Ports, Dock-Fenster, Verbindungsübersicht und einfacher Simulation.",
        )

def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")
    palette = app.palette()
    
    palette.setColor(palette.Window         , QColor("#1f1f1f"))
    palette.setColor(palette.WindowText     , QColor("#f0f0f0"))
    palette.setColor(palette.Base           , QColor("#202124"))
    palette.setColor(palette.AlternateBase  , QColor("#2b2b2b"))
    palette.setColor(palette.ToolTipBase    , QColor("#2b2b2b"))
    palette.setColor(palette.ToolTipText    , QColor("#f0f0f0"))
    palette.setColor(palette.Text           , QColor("#f0f0f0"))
    palette.setColor(palette.Button         , QColor("#2b2b2b"))
    palette.setColor(palette.ButtonText     , QColor("#f0f0f0"))
    palette.setColor(palette.BrightText     , QColor("#ffffff"))
    palette.setColor(palette.Highlight      , QColor("#3d6fb4"))
    palette.setColor(palette.HighlightedText, QColor("#ffffff"))
    
    app.setPalette(palette)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_palette(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
