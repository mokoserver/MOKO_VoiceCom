import sys
import os
import subprocess
import json
import queue
from pathlib import Path
from typing import List, Optional
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import QApplication, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit, QTextEdit, QFileDialog, QFrame, QMessageBox, QListWidget, QListWidgetItem, QComboBox


class Lamp(QFrame):
    def __init__(self, color_off: str, color_on: str, label: str):
        super().__init__()
        self.color_off = color_off
        self.color_on = color_on
        self.setFixedSize(40, 40)
        self.setStyleSheet(f"border-radius: 20px; background: {self.color_off}; border: 1px solid #666;")
        self.text = QLabel(label)
        self.text.setAlignment(Qt.AlignCenter)

    def set_on(self, on: bool):
        self.setStyleSheet(f"border-radius: 20px; background: {self.color_on if on else self.color_off}; border: 1px solid #666;")


class EqualizerWidget(QWidget):
    def __init__(self, bars: int = 8):
        super().__init__()
        self._levels = [0.0] * bars
        self._bars = bars
        self._samplerate = 16000
        self.setMinimumHeight(80)

    def set_levels(self, levels):
        if not levels:
            return
        if len(levels) != self._bars:
            if len(levels) > self._bars:
                self._levels = levels[:self._bars]
            else:
                padded = levels + [0.0] * (self._bars - len(levels))
                self._levels = padded
        else:
            self._levels = levels
        self.update()

    def set_bars(self, bars: int):
        self._bars = max(4, int(bars))
        self._levels = [0.0] * self._bars
        self.update()

    def set_samplerate(self, sr: int):
        if sr and sr > 1000:
            self._samplerate = int(sr)
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w = self.width()
        h = self.height()
        gap = 8
        left_axis = 36
        bottom_axis = 24
        plot_w = max(1, w - left_axis - gap)
        plot_h = max(1, h - bottom_axis - gap)
        bw = max(1, int((plot_w - gap * (self._bars - 1)) / self._bars))
        px0 = left_axis
        py0 = gap
        p.setPen(QColor(180, 180, 180))
        p.drawLine(px0, py0, px0, py0 + plot_h)
        p.drawLine(px0, py0 + plot_h, px0 + plot_w, py0 + plot_h)
        p.setPen(QColor(150, 150, 150))
        for val, label in [(0.0, "0.0"), (0.5, "0.5"), (1.0, "1.0")]:
            y = py0 + plot_h - int(val * plot_h)
            p.drawLine(px0 - 4, y, px0, y)
            p.drawText(4, y + 4, label)
        for i, lvl in enumerate(self._levels):
            lvl = max(0.0, min(1.0, float(lvl)))
            bh = int(lvl * plot_h)
            x = px0 + i * (bw + gap)
            y = py0 + plot_h - bh
            p.setBrush(QColor(0, 200, 0))
            p.setPen(Qt.NoPen)
            p.drawRect(x, y, bw, bh)
        nyq = self._samplerate // 2
        ticks = [125, 250, 500, 1000, 2000, 4000, 8000]
        p.setPen(QColor(150, 150, 150))
        for f in ticks:
            if f <= nyq:
                ratio = f / float(nyq)
                x = px0 + int(ratio * plot_w)
                p.drawLine(x, py0 + plot_h, x, py0 + plot_h + 4)
                p.drawText(x - 12, py0 + plot_h + bottom_axis - 6, f"{f}")


class Settings:
    def __init__(self):
        self.wake_word = "МОКО"
        self.commands: List[str] = ["старт", "стоп", "пауза"]
        self.vosk_model_path: Optional[str] = None
        self.input_device_index: Optional[int] = None
        self.equalizer_bars: int = 32
        self.samplerate: int = 16000
        self.wake_variants: List[str] = ["моко"]
        self.command_actions: dict = {}

    def phrases(self) -> List[str]:
        bases = [self.wake_word.lower()] + [v.lower() for v in self.wake_variants if v]
        phrases: List[str] = []
        for b in bases:
            for c in self.commands:
                phrases.append(f"{b} {c.lower()}")
        return phrases

    def to_dict(self) -> dict:
        return {
            "wake_word": self.wake_word,
            "wake_variants": self.wake_variants,
            "commands": self.commands,
            "vosk_model_path": self.vosk_model_path,
            "input_device_index": self.input_device_index,
            "equalizer_bars": self.equalizer_bars,
            "samplerate": self.samplerate,
            "command_actions": self.command_actions,
        }

    def update_from_dict(self, d: dict):
        self.wake_word = str(d.get("wake_word", self.wake_word))
        self.wake_variants = list(d.get("wake_variants", self.wake_variants)) or []
        self.commands = list(d.get("commands", self.commands)) or self.commands
        self.vosk_model_path = d.get("vosk_model_path", self.vosk_model_path)
        self.input_device_index = d.get("input_device_index", self.input_device_index)
        self.equalizer_bars = int(d.get("equalizer_bars", self.equalizer_bars))
        self.samplerate = int(d.get("samplerate", self.samplerate))
        ca = d.get("command_actions", None)
        if isinstance(ca, dict):
            self.command_actions = ca

    def config_path(self) -> Path:
        base = Path(__file__).parent / "settings"
        return base / "config.json"

    def load_from_disk(self):
        p = self.config_path()
        try:
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.update_from_dict(data)
        except Exception:
            pass

    def save_to_disk(self):
        p = self.config_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


class RecognizerThread(QThread):
    recognized = pyqtSignal(str)
    status = pyqtSignal(str)
    audio_levels = pyqtSignal(list)
    partial = pyqtSignal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._stop = False
        self._q = queue.Queue()
        self._use_vosk = False
        self._vosk = None
        self._sd = None
        self._stream = None
        self._np = None

    def run(self):
        self._setup_engine()
        if self._use_vosk:
            self._run_vosk()
        elif self._sd is not None:
            self._run_levels_only()
        else:
            self.status.emit("Нет доступа к микрофону: sounddevice недоступен")

    def stop(self):
        self._stop = True
        try:
            if self._stream:
                self._stream.close()
        except Exception:
            pass

    def feed_simulated_text(self, text: str):
        self.recognized.emit(text.strip().lower())

    def _audio_cb(self, indata, frames, time, status):
        if status:
            return
        self._q.put(bytes(indata))

    def _setup_engine(self):
        # Инициализация звука и распознавания
        self._use_vosk = False
        self._sd = None
        self._vosk = None
        self._np = None
        # sounddevice
        try:
            import sounddevice as sd  # type: ignore
            self._sd = sd
        except Exception:
            self._sd = None
        # numpy (опционально для FFT)
        try:
            import numpy as np  # type: ignore
            self._np = np
        except Exception:
            self._np = None
        # vosk и модель
        try:
            import vosk  # type: ignore
            self._vosk = vosk
            if self.settings.vosk_model_path and Path(self.settings.vosk_model_path).exists():
                self._use_vosk = True
            else:
                self._use_vosk = False
                self.status.emit("Модель Vosk не найдена. Работает только индикация звука.")
        except Exception:
            self._vosk = None
            self._use_vosk = False
            if self._sd is not None:
                self.status.emit("Vosk недоступен. Работает только индикация звука.")
            else:
                self.status.emit("Vosk и звук недоступны.")

    def _run_vosk(self):
        try:
            model = self._vosk.Model(self.settings.vosk_model_path)
            samplerate = self.settings.samplerate
            rec = self._vosk.KaldiRecognizer(model, samplerate)
            self._stream = self._sd.RawInputStream(samplerate=samplerate, blocksize=8000, device=self.settings.input_device_index, dtype="int16", channels=1, callback=self._audio_cb)
            self._stream.start()
            self.status.emit("Распознавание запущено")
            while not self._stop:
                data = self._q.get()
                self._emit_levels(data)
                accepted = rec.AcceptWaveform(data)
                if accepted:
                    result = rec.Result()
                    try:
                        obj = json.loads(result)
                        text = obj.get("text", "").strip().lower()
                        if text:
                            self.recognized.emit(text)
                    except Exception:
                        pass
                else:
                    try:
                        pr = rec.PartialResult()
                        obj = json.loads(pr)
                        pt = obj.get("partial", "").strip()
                        if pt:
                            self.partial.emit(pt)
                    except Exception:
                        pass
        except Exception as e:
            self.status.emit(f"Ошибка распознавания: {e}")
            self._use_vosk = False
            # Попробуем хотя бы индикацию звука
            if self._sd is not None:
                self._run_levels_only()

    def _emit_levels(self, data: bytes):
        try:
            if self._np is not None:
                arr = self._np.frombuffer(data, dtype=self._np.int16).astype(self._np.float32)
                if arr.size == 0:
                    return
                arr /= 32768.0
                sp = self._np.abs(self._np.fft.rfft(arr))
                n = sp.size
                bars = self.settings.equalizer_bars if hasattr(self.settings, "equalizer_bars") else 8
                if n < bars:
                    lvl = float(self._np.clip(self._np.sqrt((arr * arr).mean()), 0.0, 1.0))
                    self.audio_levels.emit([lvl] * bars)
                    return
                step = n // bars
                levels = []
                for i in range(bars):
                    seg = sp[i * step:(i + 1) * step]
                    v = float(seg.mean())
                    levels.append(v)
                mx = max(levels) if levels else 1.0
                if mx <= 0:
                    mx = 1.0
                levels = [min(1.0, v / mx) for v in levels]
                self.audio_levels.emit(levels)
            else:
                import struct
                count = len(data) // 2
                if count == 0:
                    return
                samples = struct.unpack("<" + "h" * count, data)
                rms = sum(s * s for s in samples) / float(count)
                rms = (rms ** 0.5) / 32768.0
                lvl = max(0.0, min(1.0, float(rms)))
                bars = self.settings.equalizer_bars if hasattr(self.settings, "equalizer_bars") else 8
                self.audio_levels.emit([lvl] * bars)
        except Exception:
            pass

    def _run_levels_only(self):
        try:
            samplerate = self.settings.samplerate if hasattr(self.settings, "samplerate") else 16000
            self._stream = self._sd.RawInputStream(samplerate=samplerate, blocksize=8000, device=self.settings.input_device_index, dtype="int16", channels=1, callback=self._audio_cb)
            self._stream.start()
            self.status.emit("Индикация звука включена (без распознавания)")
            while not self._stop:
                data = self._q.get()
                self._emit_levels(data)
        except Exception as e:
            self.status.emit(f"Ошибка аудио потока: {e}")


class TrainTab(QWidget):
    settings_changed = pyqtSignal(Settings)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        layout = QVBoxLayout()
        wl = QHBoxLayout()
        wl.addWidget(QLabel("Слово активации"))
        self.wake_edit = QLineEdit(self.settings.wake_word)
        wl.addWidget(self.wake_edit)
        layout.addLayout(wl)

        layout.addWidget(QLabel("Варианты слова активации"))
        self.wake_list = QListWidget()
        init_wakes = [self.settings.wake_word] + getattr(self.settings, "wake_variants", [])
        seen_w = set()
        for w in init_wakes:
            lw = w.strip().lower()
            if lw and lw not in seen_w:
                self.wake_list.addItem(QListWidgetItem(lw))
                seen_w.add(lw)
        layout.addWidget(self.wake_list)

        wl2 = QHBoxLayout()
        self.wake_var_edit = QLineEdit()
        self.wake_var_edit.setPlaceholderText("добавить вариант...")
        add_wake_btn = QPushButton("Добавить вариант")
        rm_wake_btn = QPushButton("Удалить выбранный")
        wl2.addWidget(self.wake_var_edit)
        wl2.addWidget(add_wake_btn)
        wl2.addWidget(rm_wake_btn)
        layout.addLayout(wl2)

        layout.addWidget(QLabel("Команды"))
        self.cmd_list = QListWidget()
        for c in self.settings.commands:
            self.cmd_list.addItem(QListWidgetItem(c))
        layout.addWidget(self.cmd_list)

        layout.addWidget(QLabel("Действие для выбранной команды"))
        actl = QHBoxLayout()
        self.cmd_action_edit = QLineEdit()
        self.cmd_action_edit.setPlaceholderText("путь к .exe или команда")
        self.cmd_action_choose = QPushButton("Выбрать файл...")
        self.cmd_action_save = QPushButton("Сохранить действие")
        actl.addWidget(self.cmd_action_edit)
        actl.addWidget(self.cmd_action_choose)
        actl.addWidget(self.cmd_action_save)
        layout.addLayout(actl)

        cl = QHBoxLayout()
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText("новая команда...")
        add_btn = QPushButton("Добавить")
        rm_btn = QPushButton("Удалить выбранную")
        cl.addWidget(self.cmd_edit)
        cl.addWidget(add_btn)
        cl.addWidget(rm_btn)
        layout.addLayout(cl)

        ml = QHBoxLayout()
        ml.addWidget(QLabel("Путь к модели Vosk"))
        self.model_path = QLineEdit(self.settings.vosk_model_path or "")
        choose_btn = QPushButton("Выбрать...")
        ml.addWidget(self.model_path)
        ml.addWidget(choose_btn)
        layout.addLayout(ml)

        save_btn = QPushButton("Сохранить")
        layout.addWidget(save_btn)
        self.setLayout(layout)

        add_btn.clicked.connect(self.add_command)
        rm_btn.clicked.connect(self.remove_selected)
        choose_btn.clicked.connect(self.choose_model)
        save_btn.clicked.connect(self.save)
        add_wake_btn.clicked.connect(self.add_wake_variant)
        rm_wake_btn.clicked.connect(self.remove_selected_wake)
        self.cmd_list.currentRowChanged.connect(self.on_cmd_selected)
        self.cmd_action_choose.clicked.connect(self.choose_action_file)
        self.cmd_action_save.clicked.connect(self.save_action_for_selected)

    def add_command(self):
        t = self.cmd_edit.text().strip().lower()
        if not t:
            return
        for i in range(self.cmd_list.count()):
            if self.cmd_list.item(i).text() == t:
                return
        self.cmd_list.addItem(QListWidgetItem(t))
        self.cmd_edit.clear()

    def remove_selected(self):
        for i in list(range(self.cmd_list.count()))[::-1]:
            if self.cmd_list.item(i).isSelected():
                self.cmd_list.takeItem(i)

    def add_wake_variant(self):
        t = self.wake_var_edit.text().strip().lower()
        if not t:
            return
        for i in range(self.wake_list.count()):
            if self.wake_list.item(i).text() == t:
                return
        self.wake_list.addItem(QListWidgetItem(t))
        self.wake_var_edit.clear()

    def remove_selected_wake(self):
        for i in list(range(self.wake_list.count()))[::-1]:
            if self.wake_list.item(i).isSelected():
                self.wake_list.takeItem(i)

    def choose_model(self):
        d = QFileDialog.getExistingDirectory(self, "Выберите папку модели Vosk")
        if d:
            self.model_path.setText(d)

    def on_cmd_selected(self, idx: int):
        if idx < 0 or idx >= self.cmd_list.count():
            self.cmd_action_edit.setText("")
            return
        cmd = self.cmd_list.item(idx).text().strip().lower()
        act = self.settings.command_actions.get(cmd, "")
        self.cmd_action_edit.setText(act)

    def choose_action_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Выберите программу или ярлык", "", "Programs (*.exe *.bat *.cmd *.lnk);;All (*)")
        if p:
            self.cmd_action_edit.setText(p)

    def save_action_for_selected(self):
        idx = self.cmd_list.currentRow()
        if idx < 0 or idx >= self.cmd_list.count():
            return
        cmd = self.cmd_list.item(idx).text().strip().lower()
        act = self.cmd_action_edit.text().strip()
        if cmd:
            if act:
                self.settings.command_actions[cmd] = act
            elif cmd in self.settings.command_actions:
                del self.settings.command_actions[cmd]
        self.settings.save_to_disk()

    def save(self):
        self.settings.wake_word = self.wake_edit.text().strip() or "МОКО"
        wakes = []
        for i in range(self.wake_list.count()):
            w = self.wake_list.item(i).text().strip()
            if w:
                wakes.append(w)
        lw = self.settings.wake_word.lower()
        if lw not in [x.lower() for x in wakes]:
            wakes.append(self.settings.wake_word)
        self.settings.wake_variants = wakes
        cmds = []
        for i in range(self.cmd_list.count()):
            cmds.append(self.cmd_list.item(i).text().strip())
        self.settings.commands = cmds or ["старт", "стоп", "пауза"]
        p = self.model_path.text().strip()
        self.settings.vosk_model_path = p if p else None
        ca = {}
        for k, v in (self.settings.command_actions or {}).items():
            if k in [c.lower() for c in self.settings.commands] and v:
                ca[k] = v
        self.settings.command_actions = ca
        self.settings.save_to_disk()
        self.settings_changed.emit(self.settings)
        QMessageBox.information(self, "OK", "Настройки сохранены")


class WorkTab(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.rec_thread: Optional[RecognizerThread] = None
        layout = QVBoxLayout()

        bl = QHBoxLayout()
        self.start_btn = QPushButton("Старт мониторинг")
        self.stop_btn = QPushButton("Стоп мониторинг")
        bl.addWidget(self.start_btn)
        bl.addWidget(self.stop_btn)
        layout.addLayout(bl)

        dl = QHBoxLayout()
        dl.addWidget(QLabel("Микрофон"))
        self.device_combo = QComboBox()
        self.refresh_dev_btn = QPushButton("Обновить")
        dl.addWidget(self.device_combo)
        dl.addWidget(self.refresh_dev_btn)
        layout.addLayout(dl)

        tl = QHBoxLayout()
        tl.addWidget(QLabel("Онлайн распознавание"))
        self.partial_edit = QLineEdit()
        self.partial_edit.setReadOnly(True)
        tl.addWidget(self.partial_edit)
        layout.addLayout(tl)

        layout.addWidget(QLabel("Промежуточный лог распознавания"))
        self.partial_log = QTextEdit()
        self.partial_log.setReadOnly(True)
        layout.addWidget(self.partial_log)

        ll = QHBoxLayout()
        self.lamp_start = Lamp("#2a2", "#0f0", "СТАРТ")
        self.lamp_stop = Lamp("#a22", "#f00", "СТОП")
        self.lamp_pause = Lamp("#aa2", "#ff0", "ПАУЗА")
        ll.addWidget(self.lamp_start)
        ll.addWidget(self.lamp_stop)
        ll.addWidget(self.lamp_pause)
        layout.addLayout(ll)

        layout.addWidget(QLabel("Лог распознанных фраз"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        sl = QHBoxLayout()
        self.sim_input = QLineEdit()
        self.sim_input.setPlaceholderText("симулировать фразу...")
        self.sim_btn = QPushButton("Отправить")
        sl.addWidget(self.sim_input)
        sl.addWidget(self.sim_btn)
        layout.addLayout(sl)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        self.eq = EqualizerWidget(self.settings.equalizer_bars if hasattr(self.settings, "equalizer_bars") else 8)
        self.eq.set_samplerate(self.settings.samplerate if hasattr(self.settings, "samplerate") else 16000)
        layout.addWidget(self.eq)
        self.setLayout(layout)

        self.start_btn.clicked.connect(self.start_monitoring)
        self.stop_btn.clicked.connect(self.stop_monitoring)
        self.sim_btn.clicked.connect(self.send_simulation)
        self.refresh_dev_btn.clicked.connect(self.populate_devices)
        self.populate_devices()

    def start_monitoring(self):
        if self.rec_thread and self.rec_thread.isRunning():
            return
        self.clear_lamps()
        self.apply_selected_device()
        self.rec_thread = RecognizerThread(self.settings)
        self.rec_thread.recognized.connect(self.on_recognized)
        self.rec_thread.status.connect(self.on_status)
        self.rec_thread.audio_levels.connect(self.eq.set_levels)
        self.rec_thread.partial.connect(self.partial_edit.setText)
        self.rec_thread.partial.connect(self.on_partial)
        self.rec_thread.start()
        self.status_label.setText("Мониторинг запущен")

    def stop_monitoring(self):
        if self.rec_thread:
            self.rec_thread.stop()
            self.rec_thread.wait()
        self.rec_thread = None
        self.status_label.setText("Мониторинг остановлен")

    def send_simulation(self):
        if not self.rec_thread:
            self.start_monitoring()
        if self.rec_thread:
            t = self.sim_input.text().strip()
            if t:
                self.rec_thread.feed_simulated_text(t)
                self.sim_input.clear()

    def on_recognized(self, text: str):
        self.log.append(text)
        variants = [self.settings.wake_word.lower()] + [v.lower() for v in getattr(self.settings, "wake_variants", [])]
        for base in variants:
            pref = base + " "
            if text.startswith(pref):
                cmd = text[len(pref):].strip()
                self.update_lamps(cmd)
                break

    def update_lamps(self, cmd: str):
        self.clear_lamps()
        c = cmd.lower()
        if c == "старт":
            self.lamp_start.set_on(True)
        elif c == "стоп":
            self.lamp_stop.set_on(True)
        elif c == "пауза":
            self.lamp_pause.set_on(True)
        self.run_command_action(c)

    def clear_lamps(self):
        self.lamp_start.set_on(False)
        self.lamp_stop.set_on(False)
        self.lamp_pause.set_on(False)

    def populate_devices(self):
        self.device_combo.clear()
        try:
            import sounddevice as sd  # type: ignore
            devs = sd.query_devices()
            seen = set()
            for i, d in enumerate(devs):
                if d.get("max_input_channels", 0) > 0:
                    name = d.get("name", f"Device {i}")
                    key = name.strip().lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    self.device_combo.addItem(f"{i}: {name}", i)
            if self.device_combo.count() == 0:
                self.device_combo.addItem("Нет входных устройств", None)
                self.device_combo.setEnabled(False)
            else:
                self.device_combo.setEnabled(True)
        except Exception:
            self.device_combo.addItem("sounddevice недоступен", None)
            self.device_combo.setEnabled(False)

    def apply_selected_device(self):
        idx = self.device_combo.currentData()
        if isinstance(idx, int):
            self.settings.input_device_index = idx
        else:
            self.settings.input_device_index = None

    def on_partial(self, text: str):
        if text:
            self.partial_log.append(text)

    def on_status(self, text: str):
        self.status_label.setText(text)
        if "Индикация звука" in text:
            self.partial_log.append("Распознавание отключено: установите Vosk и модель")
        elif "Распознавание запущено" in text:
            self.partial_log.append("Распознавание активно")
 
    def run_command_action(self, cmd: str):
        act = self.settings.command_actions.get(cmd.lower())
        if not act:
            return
        try:
            p = Path(act)
            if p.exists():
                os.startfile(str(p))
                self.log.append(f"Запуск: {act}")
                return
            subprocess.Popen(act, shell=True)
            self.log.append(f"Команда: {act}")
        except Exception as e:
            self.log.append(f"Ошибка запуска: {e}")

class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MOKO VoiceCom")
        self.settings = Settings()
        self.settings.load_from_disk()
        layout = QVBoxLayout()
        self.tabs = QTabWidget()
        self.train_tab = TrainTab(self.settings)
        self.work_tab = WorkTab(self.settings)
        self.tabs.addTab(self.train_tab, "Обучать")
        self.tabs.addTab(self.work_tab, "Работа")
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self.train_tab.settings_changed.connect(self.apply_settings)
        self.resize(640, 480)

    def apply_settings(self, s: Settings):
        self.settings = s
        self.work_tab.settings = s


def main():
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
