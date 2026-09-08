#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pyzmq==27.1.0",
# ]
# ///
"""Capture Terminal RotationStateV1 ZeroMQ messages as loss-preserving NDJSON."""

from __future__ import annotations

import argparse
import base64
import json
import signal
import sys
import time
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

try:
    import zmq
except ImportError:
    print(
        "pyzmq is required; run this script with: uv run capture_rotation_stream.py",
        file=sys.stderr,
    )
    raise SystemExit(2)


DEFAULT_ENDPOINT = "tcp://127.0.0.1:5556"
DEFAULT_TOPIC = "terminal/rotation/v1"


def iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def default_output() -> Path:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S")
    return Path("rotation-captures") / f"rotation-{stamp}.ndjson"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Receive Terminal fused rotation messages and save NDJSON evidence."
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--output", type=Path, default=default_output())
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0,
        help="Stop automatically; 0 waits for Ctrl+C.",
    )
    args = parser.parse_args()
    if not args.endpoint.startswith("tcp://"):
        parser.error("--endpoint must start with tcp://")
    if not args.topic:
        parser.error("--topic must not be empty")
    if args.duration_seconds < 0:
        parser.error("--duration-seconds must be non-negative")
    return args


class Statistics:
    def __init__(self) -> None:
        self.received = 0
        self.valid_json = 0
        self.valid_payload = 0
        self.invalid = 0
        self.first_frame_no: int | None = None
        self.last_frame_no: int | None = None
        self.frame_regressions = 0
        self.repeated_frames = 0
        self.frame_gaps = 0

    def observe(self, record: dict[str, Any]) -> None:
        self.received += 1
        payload = record.get("payload")
        if (
            not record["validJson"]
            or record.get("validTopic", True) is not True
            or not isinstance(payload, dict)
        ):
            self.invalid += 1
            return
        self.valid_json += 1
        if not is_rotation_state_v1(payload):
            self.invalid += 1
            return
        self.valid_payload += 1
        frame_no = payload.get("mocapFrameNo")
        assert isinstance(frame_no, int)
        if self.first_frame_no is None:
            self.first_frame_no = frame_no
        if self.last_frame_no is not None:
            if frame_no < self.last_frame_no:
                self.frame_regressions += 1
            elif frame_no == self.last_frame_no:
                self.repeated_frames += 1
            elif frame_no > self.last_frame_no + 1:
                self.frame_gaps += frame_no - self.last_frame_no - 1
        self.last_frame_no = frame_no

    def summary(self, started_at: str, elapsed_seconds: float) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "summary",
            "startedAt": started_at,
            "stoppedAt": iso_now(),
            "durationSeconds": elapsed_seconds,
            "averageMessagesPerSecond": (
                self.received / elapsed_seconds if elapsed_seconds > 0 else 0
            ),
            "received": self.received,
            "validJson": self.valid_json,
            "validPayload": self.valid_payload,
            "invalid": self.invalid,
            "frameRegressions": self.frame_regressions,
            "repeatedFrames": self.repeated_frames,
            "frameGaps": self.frame_gaps,
        }
        if self.first_frame_no is not None:
            result["firstFrameNo"] = self.first_frame_no
            result["lastFrameNo"] = self.last_frame_no
        return result


def is_rotation_state_v1(payload: dict[str, Any]) -> bool:
    if set(payload) != {
        "axisWorld",
        "coordinateFrame",
        "mocapFrameNo",
        "omegaWorldRadPerSecond",
    }:
        return False
    frame_no = payload["mocapFrameNo"]
    return (
        payload["coordinateFrame"] == "mocap_world"
        and isinstance(frame_no, int)
        and not isinstance(frame_no, bool)
        and 0 <= frame_no <= 9_007_199_254_740_991
        and is_vector3(payload["axisWorld"])
        and is_vector3(payload["omegaWorldRadPerSecond"])
    )


def is_vector3(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(
            isinstance(component, (int, float))
            and not isinstance(component, bool)
            and isfinite(component)
            for component in value
        )
    )


def message_record(
    frames: list[bytes],
    received_at: str,
    elapsed_seconds: float,
    expected_topic: str | None = None,
) -> dict[str, Any]:
    topic = frames[0].decode("utf-8", errors="replace") if frames else ""
    base: dict[str, Any] = {
        "type": "message",
        "receivedAt": received_at,
        "elapsedSeconds": elapsed_seconds,
        "topic": topic,
    }
    if expected_topic is not None:
        base["validTopic"] = bool(
            frames and frames[0] == expected_topic.encode("utf-8")
        )
    if len(frames) != 2:
        return {
            **base,
            "validJson": False,
            "frameCount": len(frames),
            "framesBase64": [
                base64.b64encode(frame).decode("ascii") for frame in frames
            ],
            "error": "Expected exactly two ZeroMQ frames",
        }
    try:
        payload_text = frames[1].decode("utf-8", errors="strict")
        payload = json.loads(payload_text)
        return {
            **base,
            "validJson": True,
            "payloadText": payload_text,
            "payload": payload,
        }
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return {
            **base,
            "validJson": False,
            "payloadBase64": base64.b64encode(frames[1]).decode("ascii"),
            "error": str(error),
        }


def write_record(output: Any, record: dict[str, Any]) -> None:
    output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    output.flush()


def run(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started_at = iso_now()
    started_monotonic = time.monotonic()
    stopping = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)
    subscriber.setsockopt(zmq.RCVHWM, 10_000)
    subscriber.setsockopt(zmq.LINGER, 0)
    subscriber.setsockopt(zmq.SUBSCRIBE, args.topic.encode("utf-8"))
    subscriber.connect(args.endpoint)
    poller = zmq.Poller()
    poller.register(subscriber, zmq.POLLIN)
    statistics = Statistics()

    print(f"Capturing {args.topic} from {args.endpoint}")
    print(f"Writing NDJSON to {args.output.resolve()}")
    print(
        f"Stopping after {args.duration_seconds:g} seconds"
        if args.duration_seconds > 0
        else "Press Ctrl+C to stop"
    )

    try:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            write_record(
                output,
                {
                    "type": "session",
                    "startedAt": started_at,
                    "endpoint": args.endpoint,
                    "topic": args.topic,
                },
            )
            while not stopping:
                elapsed = time.monotonic() - started_monotonic
                if args.duration_seconds > 0 and elapsed >= args.duration_seconds:
                    break
                if subscriber not in dict(poller.poll(200)):
                    continue
                frames = subscriber.recv_multipart()
                elapsed = time.monotonic() - started_monotonic
                record = message_record(frames, iso_now(), elapsed, args.topic)
                statistics.observe(record)
                write_record(output, record)
            elapsed = time.monotonic() - started_monotonic
            summary = statistics.summary(started_at, elapsed)
            write_record(output, summary)
    finally:
        subscriber.close()
        context.term()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
