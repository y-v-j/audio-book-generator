"""Speech synthesis with Kokoro, in parallel worker processes.

Each worker loads the model once and reads pieces of text; every finished piece is saved
as its own WAV file, so an interrupted run picks up where it stopped. One single-threaded
worker per physical core gave the best throughput on a 4-core laptop CPU (about 0.6 s of
computing per second of speech, against 0.8 s for one 4-thread process).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import fade, to_int16, write_wav

_TTS = None
_STYLES: dict[str, np.ndarray] = {}


@dataclass(frozen=True)
class Job:
    key: str
    text: str
    voice_key: str
    voice_parts: tuple[tuple[str, float], ...]
    lang: str
    speed: float
    out: str


def _quiet_logs() -> None:
    for name in ("phonemizer", "kokoro_onnx", "onnxruntime"):
        logging.getLogger(name).setLevel(logging.ERROR)


def load(model_path: Path, voices_path: Path, threads: int):
    """A Kokoro instance using `threads` CPU threads."""
    import onnxruntime as rt
    from kokoro_onnx import Kokoro

    _quiet_logs()
    options = rt.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    session = rt.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    return Kokoro.from_session(session, str(voices_path))


def _init_worker(model_path: str, voices_path: str, threads: int) -> None:
    global _TTS
    _TTS = load(Path(model_path), Path(voices_path), threads)


def style(tts, voice_key: str, parts: tuple[tuple[str, float], ...]) -> np.ndarray:
    """The style vector for a voice, blending several voices by weight."""
    if voice_key not in _STYLES:
        blend = sum(weight * tts.get_voice_style(name) for name, weight in parts)
        _STYLES[voice_key] = np.asarray(blend, dtype=np.float32)
    return _STYLES[voice_key]


def speak(tts, text: str, voice_key: str, parts, lang: str, speed: float) -> np.ndarray:
    """Synthesize one piece of text as int16 samples (24 kHz mono)."""
    audio, _ = tts.create(text, voice=style(tts, voice_key, parts), speed=speed, lang=lang, trim=True)
    return to_int16(fade(np.asarray(audio, dtype=np.float32)))


def _run_job(job: Job) -> tuple[str, int]:
    samples = speak(_TTS, job.text, job.voice_key, job.voice_parts, job.lang, job.speed)
    out = Path(job.out)
    tmp = out.with_name(out.name + ".tmp")
    write_wav(tmp, samples)
    os.replace(tmp, out)
    return job.key, len(samples)


def default_workers() -> int:
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 2)
    physical = max(1, cores // 2)  # assume two threads per core
    available_gb = 8.0
    try:
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    available_gb = int(line.split()[1]) / 1e6
                    break
    except OSError:
        pass
    by_memory = max(1, int(available_gb // 0.9))  # each worker holds the model, ~0.7 GB
    return max(1, min(physical, by_memory, 6))


def run(jobs: Iterable[Job], model_path: Path, voices_path: Path, workers: int,
        on_done: Callable[[Job, int], None]) -> None:
    """Synthesize every job, calling on_done(job, samples) as each one finishes."""
    jobs = list(jobs)
    if not jobs:
        return
    threads = 1 if workers > 1 else max(1, (os.cpu_count() or 2) // 2)
    context = mp.get_context("spawn")  # onnxruntime is not fork-safe
    with ProcessPoolExecutor(workers, mp_context=context, initializer=_init_worker,
                             initargs=(str(model_path), str(voices_path), threads)) as pool:
        queue = iter(jobs)
        running = {}
        try:
            for job in queue:
                running[pool.submit(_run_job, job)] = job
                if len(running) >= workers * 3:
                    break
            while running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    job = running.pop(future)
                    try:
                        _, samples = future.result()
                    except Exception as error:
                        raise RuntimeError(f"could not synthesize: {job.text[:80]!r}: {error}") from error
                    on_done(job, samples)
                    nxt = next(queue, None)
                    if nxt is not None:
                        running[pool.submit(_run_job, nxt)] = nxt
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
