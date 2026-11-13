## v1.0.0 (2026-11-13)

## v0.2.0 (2026-11-13)

### Feat

- **campaign**: collect checks each cell's dependency commits against one set per campaign (#168)
- **campaign**: change plans pass the cell key and reuse a cell already run (#166)
- **campaign**: change-plan reference protocol, so an anchor pairs protocols within a cell (#158)
- **bench-parse**: --vs pairs two protocols of one cell seed by seed (#154)
- **cli**: ns3-tool with compare, sweep, diagnose and scaffold (#151)
- **campaign**: reuse a cell that already ran, found by a key over every pinned input (#149)
- **campaign**: plan shows each arm's ns-3 release, image and commit, and flags confounded controls (#147)
- **routing-bench**: a ##PERF## cost line per arm and seed (#97)

### Fix

- **campaign**: a malformed registry answer is a warning and ?, never a crash (#172)
- audit follow-ups (make lint and check, stale status texts, two references) (#167)
- **bench-parse**: --vs checks each '# stddev' line against its own seed block (#160)
- **readers**: reject malformed marker fields by default, --lenient to skip them (#146)
- **scenario-check**: hold each seed block's oracle refusals against its own drop row (#123)

## v0.1.0 (2026-11-11)

### Feat

- **campaign**: change-test plans in ns3-campaign, a port of AntHocNet's campaign.py (#82)
- **bench**: port scenario_check with an --anchors input and arm roles as arguments (#75)
- **campaign**: multi-protocol campaign driver on campaign images (#70)
- move the routing-bench harness and the result readers from AntHocNet (#13)

### Fix

- **campaign**: record the dispatch and container commands in the manifest (#78)
- **python**: readers fail or read n/a when two cells share no seeds; ship the licence texts (#72)
- **campaign**: find the base's immutable tag by digest, not by its revision label (#76)
