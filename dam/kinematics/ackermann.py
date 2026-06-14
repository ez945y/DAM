from __future__ import annotations

import numpy as np


class AckermannSolver:
    """Planar rigid-body rollout for differential-drive / Ackermann bases.

    Integrates a 2D pose ``[x, y, yaw]`` forward under a body-twist command
    ``[v, omega]`` (``v`` = linear velocity in m/s along the body x-axis,
    ``omega`` = yaw rate in rad/s) using the exact constant-twist arc solution.
    As ``omega -> 0`` it degenerates to the straight-line update.

    Differential-drive bases (e.g. the NVIDIA Jetbot) command ``[v, omega]``
    directly. For Ackermann (steered) inputs, convert a steering angle to a
    yaw rate first via :meth:`steering_to_yaw_rate` (needs ``wheel_base``).

    State / command may be a single vector or a batch (``[N, 3]`` / ``[N, 2]``);
    numpy in, numpy out — the guard pipeline operates on numpy arrays.
    """

    _EPS = 1e-6

    def __init__(
        self,
        wheel_base: float | None = None,
        track_width: float | None = None,
    ) -> None:
        self.wheel_base = float(wheel_base) if wheel_base is not None else None
        self.track_width = float(track_width) if track_width is not None else None

    def rollout(self, state: np.ndarray, command: np.ndarray, dt: float) -> np.ndarray:
        """Roll ``state`` forward by ``dt`` under the twist ``command``.

        ``state`` is ``[x, y, yaw]`` (or ``[N, 3]``); ``command`` is
        ``[v, omega]`` (or ``[N, 2]``). Returns the predicted next pose with the
        same shape as ``state``.
        """
        s = np.asarray(state, dtype=np.float64)
        c = np.asarray(command, dtype=np.float64)
        single = s.ndim == 1
        s2 = np.atleast_2d(s)
        c2 = np.atleast_2d(c)
        dt = float(dt)

        x, y, yaw = s2[:, 0], s2[:, 1], s2[:, 2]
        v, omega = c2[:, 0], c2[:, 1]

        yaw_next = yaw + omega * dt
        straight = np.abs(omega) < self._EPS
        # Arc solution for omega != 0: turning radius R = v / omega. The
        # ``where`` on the denominator avoids a divide-by-zero warning for the
        # straight rows, which are overwritten below anyway.
        safe_omega = np.where(straight, 1.0, omega)
        radius = np.where(straight, 0.0, v / safe_omega)
        x_arc = x + radius * (np.sin(yaw_next) - np.sin(yaw))
        y_arc = y - radius * (np.cos(yaw_next) - np.cos(yaw))
        x_straight = x + v * np.cos(yaw) * dt
        y_straight = y + v * np.sin(yaw) * dt

        x_next = np.where(straight, x_straight, x_arc)
        y_next = np.where(straight, y_straight, y_arc)
        out = np.stack([x_next, y_next, yaw_next], axis=1)
        return out[0] if single else out

    # Protocol-friendly alias: callbacks may select by a generic ``forward``.
    forward = rollout

    def steering_to_yaw_rate(
        self, v: np.ndarray | float, steering_angle: np.ndarray | float
    ) -> np.ndarray:
        """Ackermann steering angle -> yaw rate: ``omega = v * tan(delta) / L``."""
        if not self.wheel_base:
            raise ValueError(
                "AckermannSolver requires wheel_base to convert steering angle to yaw rate"
            )
        yaw_rate = (
            np.asarray(v, dtype=np.float64)
            * np.tan(np.asarray(steering_angle, dtype=np.float64))
            / self.wheel_base
        )
        return np.asarray(yaw_rate, dtype=np.float64)
