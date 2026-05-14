# DAM 0.4.0 Release Notes

DAM 0.4.0 tightens the runtime ownership model and moves camera image flow out
of the Python control loop. The main result is a cleaner controller -> runner ->
runtime boundary, lower stop/start coupling, and a more predictable live/MCAP
camera path.

## Runtime Lifecycle

- RuntimeControlService now controls only the runner API. Runtime loop ownership,
  pacing, pause/resume semantics, and shutdown boundaries live in the runner.
- Runner start failures roll back to an idle, retryable state instead of leaving
  the runner stuck in `starting`.
- Runtime recheck preserves the ROS2 node used during runner construction.
- Duplicate shutdown paths are guarded so normal stop and process shutdown do not
  repeatedly disconnect the same hardware resources.

## Camera And Loopback

- OpenCV cameras are peer-level source entries with top-level device settings:
  `index_or_path`, `width`, `height`, and `fps`.
- Camera device settings under `params` are rejected. `params` is reserved for
  boundary and guard configuration.
- Camera frames flow through the image hub for live preview and MCAP attachment,
  so loopback no longer depends on ObservationBus image writes in the control
  loop.
- MCAP image attachments support one or many cameras by camera name, with
  per-camera topics under `/dam/images/{camera}`.

## Console And Telemetry

- Page navigation no longer resets stopped-session telemetry and recounts
  replayed history as new checks.
- Live image refresh uses the latest camera frames from the runtime image path,
  reducing contention with the Python control loop.
- Stop-time camera capture failures are treated as shutdown behavior instead of
  noisy runtime errors.

## Breaking Changes

- OpenCV camera device options must be declared directly on the source entry.
  Stackfiles that place camera options under `params` must be updated.
- Legacy LeRobot split source/sink adapters were removed in favor of the unified
  LeRobot adapter.
- MCAP cycle/image records follow the 0.4.0 schema; old MCAP compatibility is
  not maintained for this release.
