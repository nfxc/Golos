import logging
import os
import re
import threading
import time

import numpy as np
import onnx_asr
from faster_whisper import WhisperModel
from PyQt6.QtCore import QObject, pyqtSignal

from .win import models_dir

RUS_FILLER = re.compile(r"(?<![\w-])([аэы])(?:[\s\-\u2013\u2014]*\1)+(?![\w-])", re.IGNORECASE)
RUS_SOFT_FILLER = re.compile(r"(?<![\w-])([мн])(?:[\s\-\u2013\u2014]*\1)+(?![\w-])", re.IGNORECASE)
LATIN_FILLER = re.compile(r"(?<![\w-])(?:u+h+|u+m+|h+m+|m+h*m+|e+r+m+|a+h+)(?![\w-])", re.IGNORECASE)
STANDALONE_E = re.compile(r"(?<![\w-])[эЭ](?![\w-])[,.!?…]?\s*")
TRAILING_DASH = re.compile(r"^\s*[\u2014\u2013-]\s+")
SENTENCE_START = re.compile(r"([.!?…]\s+)([а-яёa-z])")


def to_16k(audio, sample_rate):
    data = np.asarray(audio, dtype=np.float32)
    if data.size == 0:
        return data
    data = data - float(np.mean(data))
    if sample_rate != 16000:
        size = max(1, int(round(data.size * 16000 / sample_rate)))
        old = np.linspace(0.0, 1.0, data.size, endpoint=False)
        new = np.linspace(0.0, 1.0, size, endpoint=False)
        data = np.interp(new, old, data).astype(np.float32)
    if data.size > 2000:
        spectrum = np.fft.rfft(data)
        freqs = np.fft.rfftfreq(data.size, d=1.0 / 16000)
        data = np.fft.irfft(spectrum * np.clip(freqs / 80.0, 0.0, 1.0), n=data.size).astype(np.float32)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 0.01:
        data = data * (0.9 / peak)
    return data.astype(np.float32)


# Убираем слова-паразиты и приводим пунктуацию в нормальный вид.
def clean_text(text, final=True):
    text = (text or "").strip()
    if not text:
        return ""
    text = TRAILING_DASH.sub("", text)
    text = re.sub(r"\[[^\]]{0,60}\]", " ", text)
    text = re.sub(r"\*[^*]{0,60}\*", " ", text)
    text = re.sub(r"[♪♫]+", " ", text)
    text = re.sub(
        r"\((?:[^)]{0,60})\)",
        lambda match: " " if re.search(
            r"музык|смех|смей|аплоди|вздох|шум|тишин|пауз|кашл|laugh|music|applause|sigh|noise|silence",
            match.group(0),
            re.IGNORECASE,
        ) else match.group(0),
        text,
    )
    text = RUS_FILLER.sub(" ", text)
    text = RUS_SOFT_FILLER.sub(" ", text)
    text = LATIN_FILLER.sub(" ", text)
    text = STANDALONE_E.sub(" ", text)
    for phrase in ("как бы", "типа", "это самое", "короче"):
        text = re.sub(rf"(?<![\w-]){phrase}(?![\w-])\s*,?\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*ну\s*,?\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(^|[.!?…]\s+|[,;:]\s*|\u2014\s*)ну\s*,\s*", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
    text = re.sub(r"\.{3,}", "…", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"([,;:])\s*([.!?…])", r"\2", text)
    text = re.sub(r"([.!?…])\s*,", r"\1", text)
    text = re.sub(r"([,.!?…])\1+", r"\1", text)
    text = re.sub(r"([,.;:!?…])(?=[^\s\d])", r"\1 ", text)
    text = re.sub(r"\s*«\s*", " «", text)
    text = re.sub(r"\s*»\s*", "» ", text)
    text = re.sub(r"^\s+«", "«", text)
    text = re.sub(r"(\s*»)\s+([,.!?…])", r"\1\2", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"^\s*[,.;:!?…\-\u2013\u2014«]+\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    text = SENTENCE_START.sub(lambda match: match.group(1) + match.group(2).upper(), text)
    if final:
        text = text.rstrip()
        if text[-1] in ",;:-\u2014\u2013":
            text = text[:-1].rstrip()
        if text and text[-1] not in ".!?…»\"')":
            text += "."
    return text


def strip_punctuation(text):
    text = re.sub(r"[.,!?;:…]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# GigaAM не любит куски длиннее 30 секунд. Режем по самому тихому месту.
def split_long(audio, limit_seconds=28):
    limit = int(16000 * limit_seconds)
    if audio.size <= limit:
        return [audio]
    chunks = []
    start = 0
    while audio.size - start > limit:
        low = start + int(16000 * 12)
        high = start + limit
        split = high
        quietest = None
        for pos in range(low, high - 1600, 1600):
            rms = float(np.mean(audio[pos:pos + 1600] ** 2))
            if quietest is None or rms < quietest:
                quietest = rms
                split = pos + 800
        chunks.append(audio[start:split])
        start = split
    chunks.append(audio[start:])
    return chunks


# Русский идёт через GigaAM, английский перевод через Whisper.
class SpeechEngine(QObject):
    final_ready = pyqtSignal(int, str)
    failed = pyqtSignal(int, str)
    load_failed = pyqtSignal(str)
    loaded = pyqtSignal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.giga = None
        self.whisper = None
        self.load_lock = threading.Lock()
        self.inference_lock = threading.Lock()
        self.load_started = False

    def preload(self):
        if self.load_started:
            return
        self.load_started = True
        threading.Thread(target=self._load, name="GolosModels", daemon=True).start()

    def _load(self):
        if self.settings.get("language", "ru") == "ru":
            ok = self._load_giga()
        else:
            ok = self._load_whisper()
        if ok:
            self.loaded.emit()

    def _load_giga(self):
        with self.load_lock:
            if self.giga is not None:
                return True
            path = models_dir() / "gigaam-v3-e2e-ctc"
            if not (path / "v3_e2e_ctc.int8.onnx").exists():
                self.load_failed.emit("Не найдена русская модель")
                return False
            try:
                started = time.perf_counter()
                self.giga = onnx_asr.load_model(
                    "gigaam-v3-e2e-ctc",
                    path=path,
                    quantization="int8",
                    providers=["CPUExecutionProvider"],
                )
                logging.info("gigaam loaded in %.1fs", time.perf_counter() - started)
                return True
            except Exception:
                logging.exception("gigaam load failed")
                self.load_failed.emit("Не удалось загрузить русскую модель")
                return False

    def _load_whisper(self):
        with self.load_lock:
            if self.whisper is not None:
                return True
            path = models_dir() / "faster-whisper-small"
            if not (path / "model.bin").exists():
                self.load_failed.emit("Не найдена модель перевода")
                return False
            try:
                started = time.perf_counter()
                threads = max(4, min(8, (os.cpu_count() or 6) - 2))
                self.whisper = WhisperModel(
                    str(path),
                    device="cpu",
                    compute_type="int8_float32",
                    cpu_threads=threads,
                )
                logging.info("whisper loaded in %.1fs", time.perf_counter() - started)
                return True
            except Exception:
                logging.exception("whisper load failed")
                self.load_failed.emit("Не удалось загрузить модель перевода")
                return False

    def submit_final(self, session, audio):
        language = self.settings.get("language", "ru")
        punctuation = bool(self.settings.get("punctuation", True))
        threading.Thread(
            target=self._recognize,
            args=(session, np.array(audio, copy=True), language, punctuation),
            name="GolosFinal",
            daemon=True,
        ).start()

    def _recognize(self, session, audio, language, punctuation):
        started = time.perf_counter()
        try:
            if language == "ru":
                if not self._load_giga():
                    self.failed.emit(session, "Модель недоступна")
                    return
                parts = []
                with self.inference_lock:
                    for chunk in split_long(audio):
                        result = self.giga.recognize(chunk, sample_rate=16000)
                        if result:
                            parts.append(str(result).strip())
                text = " ".join(parts)
            else:
                if not self._load_whisper():
                    self.failed.emit(session, "Модель недоступна")
                    return
                with self.inference_lock:
                    text = self._translate(audio)
            text = clean_text(text, final=True)
            if not punctuation:
                text = strip_punctuation(text)
            self.final_ready.emit(session, text)
            logging.info("recognized in %.2fs: %s", time.perf_counter() - started, text)
        except Exception:
            logging.exception("recognition failed")
            self.failed.emit(session, "Ошибка распознавания")

    def _translate(self, audio):
        segments, _ = self.whisper.transcribe(
            np.ascontiguousarray(audio, dtype=np.float32),
            language=None,
            task="translate",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
        )
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip())
