#!/usr/bin/env python3
"""
Container health check for wind-watch.

Healthy means the collector loop is alive: every station's status file was
rewritten within three update intervals. The loop rewrites those files on
every cycle whether or not NDBC or the station answered, so an outage at the
source does not make the container unhealthy (restarting it would not help).
Why a station is offline is recorded in the status file itself.

Inputs: /app/config/stations.json (station list and update interval).
Exit code 0 = healthy, 1 = unhealthy.
"""

import json
import os
import sys
import time

CONFIG = '/app/config/stations.json'
STATUS_DIR = '/app/data/wind_watch/status'

try:
    with open(CONFIG) as f:
        config = json.load(f)
    interval = int(config['output']['current']['update_interval'])
    stations = [s['id'] for s in config['stations']]
except Exception as e:
    print(f"unhealthy: cannot read {CONFIG}: {e}")
    sys.exit(1)

max_age = 3 * interval
problems = []
for station_id in stations:
    path = os.path.join(STATUS_DIR, f"{station_id}_status.json")
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError as e:
        problems.append(f"{station_id}: {e}")
        continue
    if age > max_age:
        problems.append(f"{station_id}: status file is {int(age)}s old (limit {max_age}s)")

if problems:
    print("unhealthy: " + "; ".join(problems))
    sys.exit(1)
print(f"healthy: {len(stations)} station status files updated within {max_age}s")
