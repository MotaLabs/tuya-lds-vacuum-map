---
paths:
  - "src/tuya_lds_vacuum_map/lds/**"
  - "src/tuya_lds_vacuum_map/profiles/**"
  - "docs/protocol.md"
---

# Robot protocol

- A command shape is copied from a message the app sent, captured from `localtuya_device_dp_triggered` on the
  raw data point, written down in [docs/protocol.md](../../docs/protocol.md) and tested with its full payload.
  Never guess a field.
- A feature that sends a command is done only once the robot was seen acting on it, its state data point
  changing to match. Until then the README says it has not been tried on a robot.
- A profile describes the robot as it is. It does not say how or when a value was confirmed.
