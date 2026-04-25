"""MCAP session listing, metadata parsing, and frame serving.

All public methods are synchronous and safe to call from FastAPI route
handlers (FastAPI will run them in a threadpool via run_in_executor when
needed, but for small files the latency is acceptable inline).
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import mcap
    from mcap.exceptions import EndOfFile, McapError
    from mcap.reader import NonSeekingReader as _NonSeekingReader
    from mcap.reader import make_reader as _make_reader

    _HAS_MCAP = True
except ImportError:
    _HAS_MCAP = False

# Minimum valid MCAP file size: 8-byte opening magic + at least 1 byte
_MCAP_MAGIC_SIZE = 8

# Rust msgpack format metadata - CycleRecordData array indices
# Structure: [cycle_id, obs_timestamp, has_violation, has_clamp, violated_layer_mask,
#            clamped_layer_mask, active_task, active_boundaries, obs_joint_positions,
#            obs_joint_velocities, obs_end_effector_pose, obs_force_torque,
#            action_positions, action_velocities, validated_positions, validated_velocities,
#            was_clamped, fallback_triggered, guard_results, latency_stages,
#            latency_layers, latency_guards, image_data]
_IDX_CYCLE = 0
_IDX_OBS_TIMESTAMP = 1
_IDX_HAS_VIOLATION = 2
_IDX_HAS_CLAMP = 3
_IDX_VIOLATED_LAYER_MASK = 4
_IDX_CLAMPED_LAYER_MASK = 5
_IDX_ACTIVE_TASK = 6
_IDX_ACTIVE_BOUNDARIES = 7
_IDX_OBS_JOINT_POSITIONS = 8
_IDX_ACTION_POSITIONS = 12
_IDX_WAS_CLAMPED = 16
_IDX_GUARD_RESULTS = 18
_IDX_LATENCY_STAGES = 19
_IDX_LATENCY_LAYERS = 20

# GuardResult array indices (within guard_results list)
# Structure: [cycle_id, timestamp, guard_name, layer, layer_int, decision, reason, latency_ms, is_violation, is_clamp, fault_source]
_IDX_GR_CYCLE_ID = 0
_IDX_GR_TIMESTAMP = 1
_IDX_GR_GUARD_NAME = 2
_IDX_GR_LAYER = 3
_IDX_GR_LAYER_INT = 4
_IDX_GR_DECISION = 5
_IDX_GR_REASON = 6
_IDX_GR_LATENCY_MS = 7
_IDX_GR_IS_VIOLATION = 8
_IDX_GR_IS_CLAMP = 9
_IDX_GR_FAULT_SOURCE = 10


@contextmanager
def _mcap_open(path: Path) -> Generator[Any, None, None]:
    """Open *path*, preferring SeekingReader for fast index jumping if cleanly closed."""
    if not _HAS_MCAP:
        raise ImportError("mcap package not installed")
    if path.stat().st_size < _MCAP_MAGIC_SIZE:
        raise EOFError(f"{path.name}: file too small to contain MCAP magic")
    f = open(path, "rb")

    try:
        # Try SeekingReader (which reads Chunk Index / SummaryOffset at EOF instantly)
        reader = _make_reader(f)
    except Exception:
        # Fallback for active or incomplete streaming files
        f.seek(0)
        reader = _NonSeekingReader(f, validate_crcs=False)

    try:
        yield reader
    finally:
        f.close()


try:
    import msgpack as _msgpack  # type: ignore[import-untyped]

    _HAS_MSGPACK = True
except ImportError:
    _HAS_MSGPACK = False

_DETAIL_TOPICS = {
    "/dam/cycle",
    "/dam/obs",
    "/dam/action",
    "/dam/latency",
    "/dam/L0",
    "/dam/L1",
    "/dam/L2",
    "/dam/L3",
    "/dam/L4",
}
_LAYER_TOPICS = {f"/dam/L{i}" for i in range(5)}


class McapSessionService:
    """Read-only view over the MCAP loopback output directory with SQLite caching."""

    def __init__(self, output_dir: str) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

        # Initialize SQLite index
        self._db_path = self._dir / ".mcap_index.db"
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                filename TEXT PRIMARY KEY,
                mtime_ns INTEGER,
                min_cycle_id INTEGER,
                max_cycle_id INTEGER,
                info_json TEXT
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cycles (
                filename TEXT PRIMARY KEY,
                mtime_ns INTEGER,
                cycles_json TEXT
            )
        """)
        # Migration: Add min_cycle_id and max_cycle_id if they don't exist
        with contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute("ALTER TABLE sessions ADD COLUMN min_cycle_id INTEGER")
        with contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute("ALTER TABLE sessions ADD COLUMN max_cycle_id INTEGER")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS frames (
                filename TEXT,
                cam_name TEXT,
                mtime_ns INTEGER,
                frames_json TEXT,
                PRIMARY KEY (filename, cam_name)
            )
        """)
        self._conn.commit()

        # Per-thread lock to prevent concurrent SQLite access
        self._lock = threading.RLock()
        # Cache for freshly indexed cycles (keyed by (filename, mtime_ns))
        self._cycle_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield the shared SQLite connection under the thread lock."""
        with self._lock:
            yield self._conn

    def _mtime_ns(self, path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    @staticmethod
    def _mask_to_layers(mask: int) -> list[str]:
        return [f"L{i}" for i in range(5) if mask & (1 << i)]

    def _resolve(self, filename: str) -> Path | None:
        """Resolve a filename to a Path object safely and efficiently."""
        if not filename or not isinstance(filename, str):
            return None

        if not filename.startswith("session_") or not filename.endswith(".mcap"):
            return None

        # Prevent directory traversal by taking only the name and resolving
        safe_name = Path(filename).name
        target_path = (self._dir / safe_name).resolve()

        # Security boundary check
        if not target_path.is_relative_to(self._dir.resolve()):
            return None

        try:
            if target_path.is_file() and target_path.stat().st_size >= 100:
                return target_path
        except OSError:
            pass
        return None

    def list_sessions(self) -> list[dict[str, Any]]:
        if not self._dir.exists():
            return []
        sessions: list[dict[str, Any]] = []
        for path in self._dir.glob("session_*.mcap"):
            try:
                st = path.stat()
                if st.st_size < 100:
                    continue
                sessions.append(
                    {
                        "filename": path.name,
                        "size_bytes": st.st_size,
                        "size_mb": round(st.st_size / 1024 / 1024, 2),
                        "created_at": st.st_mtime,
                    }
                )
            except OSError:
                pass
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return sessions

    def get_session_info(self, filename: str) -> dict[str, Any] | None:
        if not filename or not isinstance(filename, (str, bytes)):
            return None
        path = self._resolve(filename)
        if path is None:
            return None
        mtime_ns = self._mtime_ns(path)
        if mtime_ns is None:
            return None

        # Check SQLite index first
        filename_str = str(filename)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT mtime_ns, info_json FROM sessions WHERE filename=?", (filename_str,)
            )
            row = cur.fetchone()
            if row and row[0] == mtime_ns:
                return json.loads(row[1])

            # If not in DB or stale, we'll continue below to parse and save (requires manual cur management or re-opening)

        # Re-fetch or continue logic... (To keep it clean, I'll move the DB storage inside the try block below)
        try:
            info = self._parse_session(path)
            stats = info.get("stats", {})
            min_cid = stats.get("min_cycle_id")
            max_cid = stats.get("max_cycle_id")

            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT OR REPLACE INTO sessions (filename, mtime_ns, min_cycle_id, max_cycle_id, info_json) VALUES (?, ?, ?, ?, ?)",
                    (filename_str, mtime_ns, min_cid, max_cid, json.dumps(info)),
                )
                conn.commit()
            return info
        except (EndOfFile, McapError):
            st = path.stat()
            return {
                "filename": filename,
                "size_bytes": st.st_size,
                "size_mb": round(st.st_size / 1024 / 1024, 2),
                "created_at": st.st_mtime,
                "metadata": {},
                "stats": {
                    "total_cycles": 0,
                    "violation_cycles": 0,
                    "clamp_cycles": 0,
                    "duration_sec": 0.0,
                    "cameras": [],
                    "violated_layers": [],
                    "clamped_layers": [],
                    "min_cycle_id": None,
                    "max_cycle_id": None,
                },
            }
        except Exception as e:
            logger.error("McapSessionService: failed to parse %s: %s", path.name, e)
            return None

    def _parse_session(self, path: Path) -> dict[str, Any]:
        metadata: dict[str, str] = {}
        total_cycles = 0
        violation_cycles, clamp_cycles = 0, 0
        first_ts, last_ts = None, None
        cameras, violated_layers, clamped_layers = set(), set(), set()
        min_cycle_id, max_cycle_id = None, None
        _got_total_from_summary = False
        # Track whether SeekingReader succeeded so we know if we need a
        # fallback image-channel scan for active (non-seekable) sessions.
        _seeking_ok = False

        with open(path, "rb") as f:
            try:
                # Fast path using standard MCAP summary metadata (avoids reading payloads)
                reader = _make_reader(f)
                summary = reader.get_summary()
                if summary and summary.statistics:
                    _seeking_ok = True
                    stats = summary.statistics
                    # Do NOT use stats.message_start_time / message_end_time here.
                    # Those timestamps cover ALL topics including /dam/images/* whose
                    # pre-event frames can go back window_sec (10 s) before the first
                    # cycle, wildly inflating duration_sec.  Let the second pass over
                    # /dam/cycle messages set first_ts / last_ts from actual cycle
                    # timestamps only.
                    for ch in reader.get_summary().channels.values():
                        if ch.topic == "/dam/cycle":
                            total_cycles = stats.channel_message_counts.get(ch.id, 0)
                            _got_total_from_summary = True
                        elif ch.topic.startswith("/dam/images/"):
                            cameras.add(ch.topic.split("/dam/images/", 1)[1])
            except Exception:
                pass  # Fallback to scanning for active or incomplete files

        with _mcap_open(path) as reader:
            # Metadata and channels are available as we stream or after header
            for meta in reader.iter_metadata():
                metadata.update(meta.metadata)

            # Single pass over /dam/cycle for violation/clamp stats.
            # If the fast path already gave us total_cycles, we skip re-counting it
            # to avoid doubling the number.
            import msgpack

            for _schema, channel, message in reader.iter_messages(topics=["/dam/cycle"]):
                topic = channel.topic
                if topic == "/dam/cycle":
                    try:
                        d = msgpack.unpackb(message.data, raw=False)
                        if not isinstance(d, (list, tuple)) or len(d) < 6:
                            continue
                        if not _got_total_from_summary:
                            total_cycles += 1
                        ts = d[1] if len(d) > 1 else 0.0
                        if first_ts is None or ts < first_ts:
                            first_ts = ts
                        if last_ts is None or ts > last_ts:
                            last_ts = ts

                        cid = d[0]
                        if cid is not None:
                            if min_cycle_id is None or cid < min_cycle_id:
                                min_cycle_id = cid
                            if max_cycle_id is None or cid > max_cycle_id:
                                max_cycle_id = cid

                        has_violation = bool(d[2]) if len(d) > 2 else False
                        if has_violation:
                            violation_cycles += 1
                            v_mask = d[4] if len(d) > 4 else 0
                            for i in range(5):
                                if v_mask & (1 << i):
                                    violated_layers.add(f"L{i}")
                        has_clamp = bool(d[3]) if len(d) > 3 else False
                        if has_clamp:
                            clamp_cycles += 1
                            c_mask = d[5] if len(d) > 5 else 0
                            for i in range(5):
                                if c_mask & (1 << i):
                                    clamped_layers.add(f"L{i}")
                    except Exception:  # noqa: BLE001
                        continue

            # Re-read channels after full pass for any missing cameras.
            # SeekingReader exposes ALL channels via get_summary().
            # NonSeekingReader.channels only contains channels encountered during
            # the topic-filtered iteration, so image channels in separate chunks
            # may be absent — handled by the fallback scan below.
            channels_dict = {}
            if hasattr(reader, "get_summary"):
                summary = reader.get_summary()
                if summary:
                    channels_dict = summary.channels
            else:
                channels_dict = getattr(reader, "channels", {})

            for channel in channels_dict.values():
                if channel.topic.startswith("/dam/images/"):
                    cameras.add(channel.topic.split("/dam/images/", 1)[1])

        # Active-session fallback: when the SeekingReader couldn't open the file
        # (session still being written), NonSeekingReader filtered to /dam/cycle
        # skips image-only chunks and misses those channel registrations.
        # Re-scan the file reading all channels to find /dam/images/* topics.
        if not cameras and not _seeking_ok:
            try:
                with open(path, "rb") as f_scan:
                    scan_reader = _NonSeekingReader(f_scan, validate_crcs=False)
                    for _, ch, _ in scan_reader.iter_messages():
                        if ch.topic.startswith("/dam/images/"):
                            cameras.add(ch.topic.split("/dam/images/", 1)[1])
            except Exception:
                pass

        st = path.stat()
        res = {
            "filename": path.name,
            "size_bytes": st.st_size,
            "size_mb": round(st.st_size / 1024 / 1024, 2),
            "created_at": st.st_mtime,
            "metadata": metadata,
            "stats": {
                "total_cycles": total_cycles,
                "violation_cycles": violation_cycles,
                "clamp_cycles": clamp_cycles,
                "duration_sec": round(last_ts - first_ts, 2) if first_ts and last_ts else 0.0,
                "cameras": sorted(cameras),
                "violated_layers": sorted(violated_layers),
                "clamped_layers": sorted(clamped_layers),
                "min_cycle_id": min_cycle_id,
                "max_cycle_id": max_cycle_id,
            },
        }
        return res

    def list_cycles(self, filename: str, since_cycle_id: int | None = None) -> list[dict[str, Any]]:
        path = self._resolve(filename)
        if not path:
            return []
        mtime_ns = self._mtime_ns(path)
        if not mtime_ns:
            return []

        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT mtime_ns, cycles_json FROM cycles WHERE filename=?", (filename,))
            row = cur.fetchone()

        full_list: list[dict[str, Any]] = []
        if row and row[0] == mtime_ns:
            full_list = json.loads(row[1])
        else:
            full_list = self._index_cycles(path)
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(
                    "INSERT OR REPLACE INTO cycles (filename, mtime_ns, cycles_json) VALUES (?, ?, ?)",
                    (filename, mtime_ns, json.dumps(full_list)),
                )
                self._conn.commit()

        if since_cycle_id is not None:
            return [c for c in full_list if c["cycle_id"] > since_cycle_id]
        return full_list

    def _index_cycles(self, path: Path) -> list[dict[str, Any]]:
        cycles = []
        try:
            import msgpack

            with _mcap_open(path) as reader:
                seq = 0
                for _schema, _channel, message in reader.iter_messages(topics=["/dam/cycle"]):
                    try:
                        d = msgpack.unpackb(message.data, raw=False)
                        if not isinstance(d, (list, tuple)) or len(d) < 6:
                            continue
                        cycles.append(
                            {
                                "cycle_id": d[0],
                                "seq": seq,
                                "timestamp_ns": message.log_time,
                                "timestamp": message.log_time / 1e9,
                                "has_violation": bool(d[2]),
                                "has_clamp": bool(d[3]),
                                "violated_layer_mask": d[4] if len(d) > 4 else 0,
                                "clamped_layer_mask": d[5] if len(d) > 5 else 0,
                                "violated_layers": self._mask_to_layers(d[4] if len(d) > 4 else 0),
                                "clamped_layers": self._mask_to_layers(d[5] if len(d) > 5 else 0),
                            }
                        )
                        seq += 1
                    except Exception:  # noqa: BLE001
                        continue
        except (EndOfFile, McapError):
            pass
        except Exception as exc:
            logger.error("McapSessionService: index_cycles failed: %s", exc)
        return cycles

    def get_cycle_detail(
        self, filename: str, cycle_id: int, ts_ns: int | None = None
    ) -> dict[str, Any] | None:
        path = self._resolve(filename)
        if not path:
            return None

        # 1. Coordinate target timestamp
        target_ts_ns = ts_ns
        cycles = []
        if target_ts_ns is None:
            cycles = self.list_cycles(filename)
            for c in cycles:
                if c["cycle_id"] == cycle_id:
                    target_ts_ns = c["timestamp_ns"]
                    break

        # Stale index check for active sessions
        if target_ts_ns is None and cycles and cycle_id > cycles[-1]["cycle_id"]:
            m_ns = self._mtime_ns(path)
            if m_ns:
                key = (filename, m_ns)
                self._cycle_cache[key] = self._index_cycles(path)
                for c in self._cycle_cache[key]:
                    if c["cycle_id"] == cycle_id:
                        target_ts_ns = c["timestamp_ns"]
                        break

        # 2. Extract detail
        detail: dict[str, Any] = {
            "cycle_id": cycle_id,
            "guard_results": [],
            "cameras": {},
            "latency": {},
        }
        found = False

        try:
            with _mcap_open(path) as reader:
                # Use a tight ±200 ms window: at 15 Hz that's ≤3 cycles before/after,
                # so we never accidentally read guard results from adjacent cycles.
                # The old -1 s window was pulling in ~15 cycles of guard messages.
                _half = 200_000_000  # 200 ms in ns
                msg_iter = reader.iter_messages(
                    topics=list(_DETAIL_TOPICS),
                    start_time=target_ts_ns - _half if target_ts_ns else None,
                    end_time=target_ts_ns + _half if target_ts_ns else None,
                    log_time_order=False,
                )
                for _, channel, msg in msg_iter:
                    try:
                        encoding = channel.message_encoding
                        if "msgpack" in encoding and _HAS_MSGPACK:
                            d = _msgpack.unpackb(msg.data, raw=False)
                            if not isinstance(d, (list, tuple)):
                                continue
                        else:
                            continue  # Only support msgpack format
                    except Exception:
                        continue

                    topic = channel.topic
                    msg_cid = d[0]
                    if msg_cid != cycle_id:
                        continue

                    if topic == "/dam/cycle":
                        found = True
                        # Rust msgpack format (array, 23 elements):
                        # [0]cycle_id [1]obs_timestamp [2]has_violation [3]has_clamp
                        # [4]violated_layer_mask [5]clamped_layer_mask [6]active_task
                        # [7]active_boundaries [8]obs_joint_positions [9]obs_joint_velocities
                        # [10]obs_end_effector_pose [11]obs_force_torque [12]action_positions
                        # [13]action_velocities [14]validated_positions [15]validated_velocities
                        # [16]was_clamped [17]fallback_triggered [18]guard_results
                        # [19]latency_stages [20]latency_layers [21]latency_guards [22]image_data
                        arr_len = len(d)

                        detail.update(
                            {
                                "timestamp_ns": msg.log_time,
                                "timestamp": msg.log_time / 1e9,
                                "has_violation": bool(d[_IDX_HAS_VIOLATION])
                                if arr_len > _IDX_HAS_VIOLATION
                                else False,
                                "has_clamp": bool(d[_IDX_HAS_CLAMP])
                                if arr_len > _IDX_HAS_CLAMP
                                else False,
                                "violated_layer_mask": d[_IDX_VIOLATED_LAYER_MASK]
                                if arr_len > _IDX_VIOLATED_LAYER_MASK
                                else 0,
                                "clamped_layer_mask": d[_IDX_CLAMPED_LAYER_MASK]
                                if arr_len > _IDX_CLAMPED_LAYER_MASK
                                else 0,
                                "violated_layers": self._mask_to_layers(
                                    d[_IDX_VIOLATED_LAYER_MASK]
                                    if arr_len > _IDX_VIOLATED_LAYER_MASK
                                    else 0
                                ),
                                "clamped_layers": self._mask_to_layers(
                                    d[_IDX_CLAMPED_LAYER_MASK]
                                    if arr_len > _IDX_CLAMPED_LAYER_MASK
                                    else 0
                                ),
                                "active_task": d[_IDX_ACTIVE_TASK]
                                if arr_len > _IDX_ACTIVE_TASK
                                else None,
                                "active_boundaries": d[_IDX_ACTIVE_BOUNDARIES]
                                if arr_len > _IDX_ACTIVE_BOUNDARIES
                                else [],
                                "source_ms": d[_IDX_LATENCY_STAGES].get("source", 0.0)
                                if arr_len > _IDX_LATENCY_STAGES
                                and isinstance(d[_IDX_LATENCY_STAGES], dict)
                                else 0.0,
                                "policy_ms": d[_IDX_LATENCY_STAGES].get("policy", 0.0)
                                if arr_len > _IDX_LATENCY_STAGES
                                and isinstance(d[_IDX_LATENCY_STAGES], dict)
                                else 0.0,
                                "guards_ms": d[_IDX_LATENCY_STAGES].get("guards", 0.0)
                                if arr_len > _IDX_LATENCY_STAGES
                                and isinstance(d[_IDX_LATENCY_STAGES], dict)
                                else 0.0,
                                "sink_ms": d[_IDX_LATENCY_STAGES].get("sink", 0.0)
                                if arr_len > _IDX_LATENCY_STAGES
                                and isinstance(d[_IDX_LATENCY_STAGES], dict)
                                else 0.0,
                                "total_ms": d[_IDX_LATENCY_STAGES].get("total", 0.0)
                                if arr_len > _IDX_LATENCY_STAGES
                                and isinstance(d[_IDX_LATENCY_STAGES], dict)
                                else 0.0,
                            }
                        )
                        if arr_len > _IDX_OBS_JOINT_POSITIONS:
                            detail["observation"] = {
                                "joint_positions": d[_IDX_OBS_JOINT_POSITIONS]
                                if isinstance(d[_IDX_OBS_JOINT_POSITIONS], list)
                                else [],
                                "obs_timestamp": d[_IDX_OBS_TIMESTAMP],
                            }
                        if arr_len > _IDX_ACTION_POSITIONS:
                            detail["action"] = {
                                "target_positions": d[_IDX_ACTION_POSITIONS]
                                if isinstance(d[_IDX_ACTION_POSITIONS], list)
                                else [],
                                "was_clamped": d[_IDX_WAS_CLAMPED]
                                if arr_len > _IDX_WAS_CLAMPED
                                else False,
                            }
                        if arr_len > _IDX_GUARD_RESULTS and isinstance(d[_IDX_GUARD_RESULTS], list):
                            for gr in d[_IDX_GUARD_RESULTS]:
                                if isinstance(gr, list) and len(gr) >= _IDX_GR_LATENCY_MS + 1:
                                    detail["guard_results"].append(
                                        {
                                            "guard_name": gr[_IDX_GR_GUARD_NAME]
                                            if len(gr) > _IDX_GR_GUARD_NAME
                                            else "",
                                            "layer": gr[_IDX_GR_LAYER]
                                            if len(gr) > _IDX_GR_LAYER
                                            else 0,
                                            "layer_name": f"L{gr[_IDX_GR_LAYER] if len(gr) > _IDX_GR_LAYER else 0}",
                                            "decision": gr[_IDX_GR_LAYER_INT]
                                            if len(gr) > _IDX_GR_LAYER_INT
                                            else 0,
                                            "decision_name": gr[_IDX_GR_DECISION]
                                            if len(gr) > _IDX_GR_DECISION
                                            else "PASS",
                                            "reason": gr[_IDX_GR_REASON]
                                            if len(gr) > _IDX_GR_REASON
                                            else "",
                                            "latency_ms": gr[_IDX_GR_LATENCY_MS]
                                            if len(gr) > _IDX_GR_LATENCY_MS
                                            else None,
                                            "is_violation": gr[_IDX_GR_IS_VIOLATION]
                                            if len(gr) > _IDX_GR_IS_VIOLATION
                                            else False,
                                            "is_clamp": gr[_IDX_GR_IS_CLAMP]
                                            if len(gr) > _IDX_GR_IS_CLAMP
                                            else False,
                                            "fault_source": gr[_IDX_GR_FAULT_SOURCE]
                                            if len(gr) > _IDX_GR_FAULT_SOURCE
                                            else None,
                                        }
                                    )
                        if arr_len > _IDX_LATENCY_LAYERS and isinstance(
                            d[_IDX_LATENCY_LAYERS], dict
                        ):
                            detail["latency"] = {k: v for k, v in d[_IDX_LATENCY_LAYERS].items()}
                    elif topic == "/dam/obs":
                        detail["observation"] = {
                            "joint_positions": d.get("joint_positions", []),
                            "obs_timestamp": d.get("timestamp"),
                        }
                    elif topic == "/dam/action":
                        detail["action"] = {
                            "target_positions": d.get("target_positions", []),
                            "was_clamped": bool(d.get("was_clamped")),
                        }
                    elif topic == "/dam/latency":
                        detail["latency"] = {
                            k: d.get(k, 0.0)
                            for k in (
                                "source_ms",
                                "policy_ms",
                                "guards_ms",
                                "sink_ms",
                                "total_ms",
                                "L0_ms",
                                "L1_ms",
                                "L2_ms",
                                "L3_ms",
                                "L4_ms",
                            )
                        }
                    elif topic in _LAYER_TOPICS:
                        _layer_int = d.get("layer", 0)
                        detail["guard_results"].append(
                            {
                                "guard_name": d.get("guard_name", ""),
                                "layer": _layer_int,
                                "layer_name": f"L{_layer_int}",
                                "decision": d.get("decision", 0),
                                "decision_name": d.get("decision_name", "PASS"),
                                "reason": d.get("reason", ""),
                                "latency_ms": d.get("latency_ms"),
                                "is_violation": bool(d.get("is_violation", False)),
                                "is_clamp": bool(d.get("is_clamp", False)),
                                "fault_source": d.get("fault_source"),
                            }
                        )
                    elif topic.startswith("/dam/camera/"):
                        detail["cameras"][topic.split("/")[-1]] = {"frame_idx": msg.sequence}

        except (EndOfFile, McapError):
            logger.debug(
                "McapSessionService: partial read for cycle %d (session writing)", cycle_id
            )
        except Exception as e:
            logger.error("McapSessionService: detail fetch failed: %s", e)
            return None

        if not found:
            return None
        detail["guard_results"].sort(key=lambda g: g.get("guard_name", ""))
        return detail

    def find_session_by_cycle(self, cycle_id: int) -> str | None:
        with self._lock:
            cur = self._conn.cursor()
            # Find session covering this cycle_id directly in SQLite
            cur.execute(
                "SELECT filename FROM sessions WHERE min_cycle_id <= ? AND max_cycle_id >= ?",
                (cycle_id, cycle_id),
            )
            row = cur.fetchone()
        if row:
            return row[0]

        # Fallback if not indexed or doesn't exist
        for session in self.list_sessions():
            filename = session["filename"]
            info = self.get_session_info(filename)
            if not info:
                continue
            stats = info.get("stats", {})
            min_cid = stats.get("min_cycle_id")
            max_cid = stats.get("max_cycle_id")
            if min_cid is not None and max_cid is not None and min_cid <= cycle_id <= max_cid:
                return filename
        return None

    def list_frames(self, filename: str, cam_name: str) -> list[dict[str, Any]]:
        path = self._resolve(filename)
        m_ns = self._mtime_ns(path) if path else None
        if not path or not m_ns:
            return []

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT mtime_ns, frames_json FROM frames WHERE filename=? AND cam_name=?",
                (filename, cam_name),
            )
            row = cur.fetchone()
        if row and row[0] == m_ns:
            return json.loads(row[1])

        frames = []
        try:
            with _mcap_open(path) as reader:
                for _, _, msg in reader.iter_messages(topics=[f"/dam/images/{cam_name}"]):
                    frames.append(
                        {
                            "idx": len(frames),
                            "timestamp": msg.log_time / 1e9,
                            "log_time_ns": msg.log_time,
                        }
                    )
        except (EndOfFile, McapError):
            pass

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO frames (filename, cam_name, mtime_ns, frames_json) VALUES (?, ?, ?, ?)",
                (filename, cam_name, m_ns, json.dumps(frames)),
            )
            self._conn.commit()
        return frames

    def get_frame_jpeg_at(self, filename: str, cam_name: str, ts_ns: int) -> bytes | None:
        path = self._resolve(filename)
        if not path:
            return None
        try:
            with _mcap_open(path) as reader:
                # O(1) jump using chunk index directly from time
                msg_iter = reader.iter_messages(
                    topics=[f"/dam/images/{cam_name}"],
                    start_time=max(0, ts_ns - 100_000_000),  # ±100ms window around target ts
                    end_time=ts_ns + 100_000_000,
                )
                closest = None
                closest_d = float("inf")
                for _, channel, msg in msg_iter:
                    d = abs(msg.log_time - ts_ns)
                    if d < closest_d:
                        closest_d = d
                        closest = (channel, msg)

                if closest:
                    channel, msg = closest
                    encoding = channel.message_encoding
                    if "msgpack" in encoding and _HAS_MSGPACK:
                        payload = _msgpack.unpackb(msg.data, raw=False)
                    else:
                        payload = json.loads(msg.data)
                    # ImageData format from Rust: [camera_name, timestamp, width, height, jpeg_bytes]
                    # jpeg_bytes is a list of integers, convert to bytes
                    if isinstance(payload, list) and len(payload) >= 5:
                        jpeg_data = payload[4]
                        if isinstance(jpeg_data, list):
                            # Convert list of integers to bytes
                            return bytes(jpeg_data)
                        elif isinstance(jpeg_data, bytes):
                            return jpeg_data
                    elif isinstance(payload, dict):
                        img = payload.get("data")
                        if img:
                            return img if isinstance(img, bytes) else base64.b64decode(img)
        except Exception:  # noqa: BLE001
            pass
        return None

    def get_frame_jpeg(self, filename: str, cam_name: str, frame_idx: int) -> bytes | None:
        path = self._resolve(filename)
        if not path:
            return None

        frames = self.list_frames(filename, cam_name)
        if 0 <= frame_idx < len(frames):
            target_ns = frames[frame_idx].get("log_time_ns")
            if not target_ns:
                return None
        else:
            return None

        try:
            with _mcap_open(path) as reader:
                # O(1) jump using chunk index; ±5 ms window handles minor timestamp drift
                msg_iter = reader.iter_messages(
                    topics=[f"/dam/images/{cam_name}"],
                    start_time=target_ns,
                    end_time=target_ns + 5_000_000,
                )
                for _, channel, msg in msg_iter:
                    encoding = channel.message_encoding
                    if "msgpack" in encoding and _HAS_MSGPACK:
                        payload = _msgpack.unpackb(msg.data, raw=False)
                    else:
                        payload = json.loads(msg.data)
                    # ImageData format from Rust: [camera_name, timestamp, width, height, jpeg_bytes]
                    # jpeg_bytes is a list of integers, convert to bytes
                    if isinstance(payload, list) and len(payload) >= 5:
                        jpeg_data = payload[4]
                        if isinstance(jpeg_data, list):
                            # Convert list of integers to bytes
                            return bytes(jpeg_data)
                        elif isinstance(jpeg_data, bytes):
                            return jpeg_data
                    elif isinstance(payload, dict):
                        img = payload.get("data")
                        if img:
                            return img if isinstance(img, bytes) else base64.b64decode(img)
        except Exception:  # noqa: BLE001
            pass
        return None

    def resolve_path(self, filename: str) -> Path | None:
        return self._resolve(filename)

    def delete_session(self, filename: str) -> bool:
        path = self._resolve(filename)
        if not path:
            return False
        try:
            path.unlink()
            with self._lock:
                cur = self._conn.cursor()
                cur.execute("DELETE FROM sessions WHERE filename=?", (filename,))
                cur.execute("DELETE FROM cycles WHERE filename=?", (filename,))
                cur.execute("DELETE FROM frames WHERE filename=?", (filename,))
                self._conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to delete session %s: %s", filename, e)
            return False
