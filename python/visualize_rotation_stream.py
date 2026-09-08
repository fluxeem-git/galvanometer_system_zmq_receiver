#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "matplotlib==3.10.6",
#   "pyzmq==27.1.0",
# ]
# ///
"""Visualize Terminal RotationStateV1 ZeroMQ messages in real time."""

from __future__ import annotations

import argparse
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass
from math import pi, sqrt
from pathlib import Path
from typing import Any

import zmq

from capture_rotation_stream import (
    DEFAULT_ENDPOINT,
    DEFAULT_TOPIC,
    Statistics,
    is_rotation_state_v1,
    iso_now,
    message_record,
)


@dataclass(frozen=True)
class RotationSample:
    elapsed_seconds: float
    frame_no: int
    axis: tuple[float, float, float]
    omega: tuple[float, float, float]

    @property
    def speed_rad_per_second(self) -> float:
        return sqrt(sum(component * component for component in self.omega))

    @property
    def speed_rpm(self) -> float:
        return self.speed_rad_per_second * 60 / (2 * pi)


@dataclass(frozen=True)
class StreamSnapshot:
    samples: tuple[RotationSample, ...]
    elapsed_seconds: float
    seconds_since_valid: float | None
    received: int
    invalid: int
    frame_gaps: int
    frame_regressions: int
    repeated_frames: int
    rolling_rate_hz: float


class RotationBuffer:
    def __init__(self, window_seconds: float, topic: str) -> None:
        self._window_seconds = window_seconds
        self._topic = topic
        self._started = time.monotonic()
        self._last_valid_at: float | None = None
        self._samples: deque[RotationSample] = deque()
        self._statistics = Statistics()
        self._lock = threading.Lock()

    def observe(self, frames: list[bytes]) -> None:
        now = time.monotonic()
        elapsed = now - self._started
        record = message_record(frames, iso_now(), elapsed, self._topic)
        with self._lock:
            self._statistics.observe(record)
            payload = record.get("payload")
            if (
                record.get("validTopic") is not True
                or not isinstance(payload, dict)
                or not is_rotation_state_v1(payload)
            ):
                return
            self._last_valid_at = now
            self._samples.append(
                RotationSample(
                    elapsed_seconds=elapsed,
                    frame_no=payload["mocapFrameNo"],
                    axis=tuple(payload["axisWorld"]),
                    omega=tuple(payload["omegaWorldRadPerSecond"]),
                )
            )
            self._trim(elapsed)

    def snapshot(self) -> StreamSnapshot:
        now = time.monotonic()
        elapsed = now - self._started
        with self._lock:
            self._trim(elapsed)
            samples = tuple(self._samples)
            recent = sum(sample.elapsed_seconds >= elapsed - 1 for sample in samples)
            statistics = self._statistics
            return StreamSnapshot(
                samples=samples,
                elapsed_seconds=elapsed,
                seconds_since_valid=(
                    None if self._last_valid_at is None else now - self._last_valid_at
                ),
                received=statistics.received,
                invalid=statistics.invalid,
                frame_gaps=statistics.frame_gaps,
                frame_regressions=statistics.frame_regressions,
                repeated_frames=statistics.repeated_frames,
                rolling_rate_hz=float(recent),
            )

    def _trim(self, elapsed: float) -> None:
        oldest = elapsed - self._window_seconds
        while self._samples and self._samples[0].elapsed_seconds < oldest:
            self._samples.popleft()


class RotationDashboard:
    SERIES = (
        ("X", "#56B4E9", "o"),
        ("Y", "#E69F00", "x"),
        ("Z", "#CC79A7", "^"),
    )

    def __init__(
        self,
        buffer: RotationBuffer,
        endpoint: str,
        topic: str,
        window_seconds: float,
        stale_seconds: float,
    ) -> None:
        import matplotlib.pyplot as plt
        from matplotlib.widgets import Button

        self._plt = plt
        self._buffer = buffer
        self._window_seconds = window_seconds
        self._stale_seconds = stale_seconds
        self._paused = False
        self._last_snapshot: StreamSnapshot | None = None

        plt.style.use("dark_background")
        self.figure, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)
        self.figure.canvas.manager.set_window_title("Terminal Rotation Stream")
        self.figure.subplots_adjust(top=0.79, bottom=0.1, hspace=0.3)
        self.figure.suptitle(
            "Terminal Rotation Stream", x=0.06, y=0.965, ha="left", fontsize=18
        )
        self._status = self.figure.text(
            0.06,
            0.91,
            "WAITING FOR DATA",
            fontsize=11,
            weight="bold",
            color="#111827",
            bbox={"boxstyle": "round,pad=0.45", "facecolor": "#FBBF24"},
        )
        self._metrics = self.figure.text(
            0.06, 0.845, "", fontsize=10, family="monospace", color="#E5E7EB"
        )
        self.figure.text(
            0.06,
            0.805,
            f"{topic}  |  {endpoint}  |  Space: pause/resume  Q: quit",
            fontsize=9,
            color="#9CA3AF",
        )

        self._speed_axis, self._omega_axis, self._rotation_axis = axes
        self._speed_points = self._speed_axis.plot(
            [],
            [],
            color="#009E73",
            linestyle="None",
            marker="o",
            markersize=3.4,
            markeredgewidth=0,
            alpha=0.8,
            label="Speed (RPM)",
        )[0]
        self._omega_points = self._component_points(self._omega_axis, "omega")
        self._axis_points = self._component_points(self._rotation_axis, "axis")
        self._configure_axis(self._speed_axis, "Speed (RPM)")
        self._configure_axis(self._omega_axis, "Omega (rad/s)")
        self._configure_axis(self._rotation_axis, "Axis component")
        self._rotation_axis.set_xlabel(f"Elapsed time — last {window_seconds:g} s")

        button_axis = self.figure.add_axes((0.82, 0.91, 0.12, 0.045))
        self._pause_button = Button(
            button_axis, "Pause", color="#374151", hovercolor="#4B5563"
        )
        self._pause_button.on_clicked(self.toggle_pause)
        self.figure.canvas.mpl_connect("key_press_event", self._on_key)

    def start_animation(self, interval_ms: int) -> None:
        from matplotlib.animation import FuncAnimation

        self._animation = FuncAnimation(
            self.figure,
            self.update,
            interval=interval_ms,
            cache_frame_data=False,
        )

    def toggle_pause(self, _event: Any = None) -> None:
        self._paused = not self._paused
        self._pause_button.label.set_text("Resume" if self._paused else "Pause")
        self.update(0)
        self.figure.canvas.draw_idle()

    def update(self, _frame: Any) -> tuple[Any, ...]:
        latest = self._buffer.snapshot()
        if not self._paused or self._last_snapshot is None:
            self._last_snapshot = latest
        snapshot = self._last_snapshot
        self._update_status(latest)
        self._update_metrics(latest, snapshot)
        self._update_points(snapshot)
        return (
            self._status,
            self._metrics,
            self._speed_points,
            *self._omega_points,
            *self._axis_points,
        )

    def _update_status(self, live: StreamSnapshot) -> None:
        if self._paused:
            label, color = "PAUSED — RECEIVER STILL ACTIVE", "#93C5FD"
        elif live.seconds_since_valid is None:
            label, color = "WAITING FOR VALID DATA", "#FBBF24"
        elif live.seconds_since_valid > self._stale_seconds:
            label, color = (
                f"STALE — NO VALID DATA FOR {live.seconds_since_valid:.1f} s",
                "#FCA5A5",
            )
        else:
            label, color = "LIVE", "#6EE7B7"
        self._status.set_text(label)
        self._status.get_bbox_patch().set_facecolor(color)

    def _update_metrics(self, live: StreamSnapshot, displayed: StreamSnapshot) -> None:
        sample = displayed.samples[-1] if displayed.samples else None
        frame = "—" if sample is None else f"{sample.frame_no:,}"
        speed = "—" if sample is None else f"{sample.speed_rpm:,.2f} RPM"
        self._metrics.set_text(
            f"Frame {frame}   Speed {speed}   Rate {live.rolling_rate_hz:.0f} msg/s   "
            f"Received {live.received:,}   Invalid {live.invalid:,}\n"
            f"Frame gaps {live.frame_gaps:,}   Repeated {live.repeated_frames:,}   "
            f"Regressions {live.frame_regressions:,}"
        )

    def _update_points(self, snapshot: StreamSnapshot) -> None:
        samples = snapshot.samples
        times = [sample.elapsed_seconds for sample in samples]
        self._speed_points.set_data(times, [sample.speed_rpm for sample in samples])
        for component, points in enumerate(self._omega_points):
            points.set_data(times, [sample.omega[component] for sample in samples])
        for component, points in enumerate(self._axis_points):
            points.set_data(times, [sample.axis[component] for sample in samples])
        left = max(0.0, snapshot.elapsed_seconds - self._window_seconds)
        right = max(self._window_seconds, snapshot.elapsed_seconds)
        self._rotation_axis.set_xlim(left, right)
        for axis in (self._speed_axis, self._omega_axis, self._rotation_axis):
            axis.relim()
            axis.autoscale_view(scalex=False, scaley=True)

    def _component_points(self, axis: Any, prefix: str) -> tuple[Any, ...]:
        return tuple(
            axis.plot(
                [],
                [],
                color=color,
                linestyle="None",
                marker=marker,
                markersize=3.2,
                markeredgewidth=0.8,
                alpha=0.78,
                label=f"{prefix} {label}",
            )[0]
            for label, color, marker in self.SERIES
        )

    @staticmethod
    def _configure_axis(axis: Any, label: str) -> None:
        axis.set_ylabel(label)
        axis.grid(True, color="#374151", alpha=0.55)
        axis.legend(loc="upper left", ncols=3, frameon=False)

    def _on_key(self, event: Any) -> None:
        if event.key == " ":
            self.toggle_pause()
        elif event.key and event.key.lower() == "q":
            self._plt.close(self.figure)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize Terminal fused rotation messages in real time."
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--window-seconds", type=float, default=30)
    parser.add_argument("--stale-seconds", type=float, default=1)
    parser.add_argument("--max-fps", type=float, default=20)
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Headless mode: save a PNG after --duration-seconds and exit.",
    )
    parser.add_argument("--duration-seconds", type=float, default=5)
    args = parser.parse_args()
    if not args.endpoint.startswith("tcp://"):
        parser.error("--endpoint must start with tcp://")
    if not args.topic:
        parser.error("--topic must not be empty")
    if not 5 <= args.window_seconds <= 600:
        parser.error("--window-seconds must be between 5 and 600")
    if args.stale_seconds <= 0:
        parser.error("--stale-seconds must be positive")
    if not 1 <= args.max_fps <= 30:
        parser.error("--max-fps must be between 1 and 30")
    if args.snapshot and args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive in snapshot mode")
    return args


def receive_loop(
    args: argparse.Namespace, buffer: RotationBuffer, stop: threading.Event
) -> None:
    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)
    subscriber.setsockopt(zmq.RCVHWM, 10_000)
    subscriber.setsockopt(zmq.LINGER, 0)
    subscriber.setsockopt(zmq.SUBSCRIBE, args.topic.encode("utf-8"))
    subscriber.connect(args.endpoint)
    poller = zmq.Poller()
    poller.register(subscriber, zmq.POLLIN)
    try:
        while not stop.is_set():
            if subscriber in dict(poller.poll(100)):
                buffer.observe(subscriber.recv_multipart())
    finally:
        subscriber.close()
        context.term()


def run(args: argparse.Namespace) -> int:
    if args.snapshot:
        import matplotlib

        matplotlib.use("Agg")
    buffer = RotationBuffer(args.window_seconds, args.topic)
    stop = threading.Event()
    worker = threading.Thread(
        target=receive_loop, args=(args, buffer, stop), daemon=True
    )
    worker.start()
    dashboard = RotationDashboard(
        buffer,
        args.endpoint,
        args.topic,
        args.window_seconds,
        args.stale_seconds,
    )

    def request_stop(_signum: int, _frame: Any) -> None:
        stop.set()
        dashboard._plt.close(dashboard.figure)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        if args.snapshot:
            time.sleep(args.duration_seconds)
            dashboard.update(0)
            args.snapshot.parent.mkdir(parents=True, exist_ok=True)
            dashboard.figure.savefig(args.snapshot, dpi=150, facecolor="#111827")
            print(f"Saved snapshot to {args.snapshot.resolve()}")
        else:
            dashboard.start_animation(round(1_000 / args.max_fps))
            dashboard._plt.show()
    finally:
        stop.set()
        worker.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
