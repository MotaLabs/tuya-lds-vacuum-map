# Protocol notes

What the robot sends and accepts, as observed on a KaBuM! Smart 900 (Tuya product "Lds Robot",
product id `7vx4ulan46paequq`, firmware 0.1.14) through [LocalTuya](https://github.com/xZetsubou/hass-localtuya)
and the Tuya cloud. Everything here was captured from the real device; nothing was taken from a vendor
document. Other robots that show up in Tuya as "Lds Robot" are expected to share it, but each one has to be
confirmed (see [Adding a robot](../CONTRIBUTING.md#adding-a-robot)).

## Data points

| DP | Code | Type | Use here |
|---|---|---|---|
| 105 | `robot_state` | enum | Robot state: `idle`, `pointing`, `areaing`, `totaling`, `sweep`, `mop`, `fault`, `pause`, `chargring`, `tocharge`, `fullcharge`, `remotectl`, `dormant`, `curpointing`, `selectroom`, `dustcenterworking` |
| 123 | `path_comm` | raw | Live path, base64 JSON, `infoType` 21011 |
| 124 | `cmd_comm` | raw | Not used yet |
| 125 | `request_data` | enum | `map`, `path`, `both`; no local answer was observed for any of them |
| 127 | `comm_raw` | raw | Room list, areas and commands, base64 JSON |
| 144 | `room_mode_switch` | bool | Per-room settings on or off |

The full list of the robot's data points, with ranges, comes from the cloud model
(`GET /v2.0/cloud/thing/{device_id}/model`).

## Raw messages

DP 123 and DP 127 carry base64-encoded JSON. Every message has an `infoType`.

### 21011: path (DP 123)

```json
{"data": {"pathID": 1700001234, "startPos": 94, "pointCounts": 1, "totalPoints": 95, "posArray": [[480, -820]]}, "infoType": 21011}
```

Incremental: `posArray` holds the points from `startPos` on. A new `pathID` starts a new path. After a
reconnect the robot resends the whole path from `startPos` 0. Points are millimetres in the map frame.
A chunk arrives about every two seconds while the robot moves.

### 21004: rooms and areas (DP 127)

```json
{"data": {"mapId": 1700000000,
          "autoAreaValue": [{"id": 7, "name": "2b+2p+30+2p", "pos": [1200, -1600], "fanLevel": "quiet",
                             "cleanType": "sweepOnly", "cleanOrder": 0, "active": "normal", "mode": "autolayer"}],
          "value": [{"id": 101, "mode": "area", "active": "normal", "tag": "1z+2p+38+33+37",
                     "vertexs": [[2000, 0], [3500, 0], [3500, -2500], [2000, -2500]]},
                    {"id": 4, "active": "forbid", "isWall": true, "vertexs": [[-1000, -6500], [100, -6500], [100, -6550], [-1000, -6550]]}]},
 "infoType": 21004}
```

- `autoAreaValue` is the list of rooms. `id` matches the room id in the map cells. `pos` is where the app
  draws the room name. `fanLevel` and `waterPump` are the per-room settings, present only when set.
- Names are written as the base-36 code of each character, joined with `+`: `2b+2p+30+2p` is `Sala`.
- `value` holds the user's areas: `mode: "area"` is a cleaning area, `active: "forbid"` a no-go zone, and
  `isWall` a virtual wall. The area name is in `tag`, encoded the same way.

### 30000: commands (DP 127)

Commands are written to DP 127 as a `30000` envelope, and the robot echoes the ones the app sends.

```json
{"dInfo": {"ts": "1700000000000", "userId": "0"},
 "data": {"cmds": [{"data": {"cleanId": [-3], "extraAreas": [], "mapId": 1700000000, "segmentId": [3]}, "infoType": 21023},
                   {"data": {"mode": "reAppointClean"}, "infoType": 21005}],
          "mainCmds": [21005]},
 "infoType": 30000}
```

| Clean | `21023` data | Extra command |
|---|---|---|
| Rooms | `cleanId: [-3]`, `segmentId: [room ids]` | |
| Spot | `cleanId: [-3]`, `extraAreas: [spot area]` | |
| Areas | `cleanId: [-2]` | `21003` with the area list in `value`, sent first |

`21005` with `mode: "reAppointClean"` starts the job.

The spot area is a square around the tapped point; its side comes from the robot profile (`spot_size_mm`,
1.5 m in `lds-robot`):

```json
{"active": "depth", "id": 100, "mode": "point", "name": "aa", "tag": "room",
 "vertexs": [[-750, -750], [750, -750], [750, 750], [-750, 750]]}
```

`mode: "point"` is what marks it as a spot clean. The robot echoes the command on DP 127, which is how the
map also shows the square for a spot clean started from the app. `active`, `id`, `name` and `tag` are
copied from what the app sends; their meaning is unknown and the robot accepts them as they are.

DP 127 keeps the last command after the job ends, and localtuya's `localtuya_status_update` event carries
every data point, DP 127 included, about every 2 s while the robot is active. The same command therefore
arrives many times, long after its clean is over. The service takes a spot from a command it has already read
only while the robot is in a spot state, so a paused spot clean shows its square again when it resumes.

These message types and command shapes are this robot family's own JSON protocol, carried by Tuya over
DP 127. Tuya's standard laser robot protocol is a different one, with numbered binary commands
([RobotProtocol](https://developer.tuya.com/material/library_oHEKLjj0/component/RobotProtocol.md)).

## Cloud map

`GET /v1.0/users/sweepers/file/{device_id}/realtime-map` (needs the "Sweeping Robot Open Service" API on
the cloud project) returns two short-lived URLs: `map_type` 0 is the layout and 1 the last path. The
layout is JSON:

```json
{"data": {"width": 180, "height": 250, "resolution": 0.05, "x_min": -2.0, "y_min": -10.8,
          "map": "<base64 of LZ4 block>", "lz4_len": 5900, "mapId": 1700000000, "pathId": 1700001234,
          "chargeHandlePos": [500, -650], "chargeHandlePhi": 1570, "area": [...]},
 "infoType": 20002}
```

- `map` is an LZ4 block (no size header) that expands to `width * height` bytes, one per 5 cm cell.
- Cell values: `0` wall, `1`-`126` room id, `127` unknown, `255` obstacle.
- Cell `(col, row)` covers x from `x_min + col * resolution` and y from `y_min + row * resolution`
  (metres). Row 0 is the lowest y, so the image is flipped vertically to look like the app.
- The robot uploads a new layout during and after each cleaning. Its `pathId` matches the `pathID` of the
  local path, which is how the two are tied together.

The same layout fields (`autoAreaValue`, `chargeHandlePhi`, `area`) appear in what
[Xiaomi Cloud Map Extractor](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor)
calls the Roidmi format, which points to a navigation platform shared by several brands.
