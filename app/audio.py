import threading
import time

import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self):
        self.stream = None
        self.chunks = []
        self.sample_rate = 48000
        self.level = 0.0
        self.lock = threading.Lock()

    def start(self):
        self.chunks = []
        self.level = 0.0
        device = sd.query_devices(kind="input")
        native = int(device.get("default_samplerate") or 48000)
        rates = []
        for rate in (native, 48000, 44100, 16000):
            if rate not in rates:
                rates.append(rate)
        last_error = None
        for rate in rates:
            try:
                stream = sd.InputStream(
                    device=device["index"],
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    blocksize=2048,
                    latency="high",
                    callback=self._callback,
                )
                stream.start()
                # Микрофону нужно чуть-чуть времени, иначе теряется первое слово.
                time.sleep(0.15)
                self.stream = stream
                self.sample_rate = rate
                return
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"microphone unavailable: {last_error}")

    def _callback(self, indata, frames, time_info, status):
        data = indata[:, 0].copy()
        with self.lock:
            self.chunks.append(data)
        rms = float(np.sqrt(np.mean(data * data))) if data.size else 0.0
        value = min(1.0, (rms ** 0.5) * 2.5)
        value = max(0.0, (value - 0.05) / 0.95)
        self.level = self.level * 0.55 + value * 0.45

    def stop(self):
        stream = self.stream
        self.stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        with self.lock:
            chunks = self.chunks
            self.chunks = []
        if not chunks:
            return np.zeros(0, dtype=np.float32), self.sample_rate
        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        self.level = 0.0
        return audio, self.sample_rate

    def cancel(self):
        self.stop()
