# tuya-lds-vacuum-map

A service that draws the map of Tuya LDS robot vacuums in Home Assistant and turns taps on the map into
local cleaning commands. Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing anything: the layout, the
test rules and the commit convention live there. This file only adds what an agent tends to miss.

- Everything in the repository is in English: code, docs, commit messages, issues and pull requests.
- No AI attribution or disclosure anywhere: no co-author trailers, no "generated with" lines, and no
  template or check that asks contributors about it.
- Nothing from a real home goes into the repository, an issue or a pull request: no device id, local key,
  token, map, room name or log line copied from a live robot.
- New robot behavior goes into this service and reaches Home Assistant through MQTT discovery, not through
  Home Assistant helpers, templates or automations: the goal is one install per robot.
- Planned work lives in the GitHub issues and milestones. The README links to them and keeps no second
  list.
- Each commit is small and passes the suite on its own.
- Run mutmut with `--max-children 4` under `nice`; with every core it starves the machine.
