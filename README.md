# Wind Data Service

This service fetches and processes wind data from NDBC stations in the Pribilof Islands.

## Station Locations

### St. Paul Island (PAUA2)
- Location: St. Paul Island Airport
- Coordinates: 57.167N 170.220W
- Elevation: 63 feet above sea level
- Equipment: Automated airport weather station
- Internet: Satellite connection via GCI

### St. George Island (SGXA2)
- Location: St. George Island
- Coordinates: 56.600N 169.557W
- Elevation: 125 feet above sea level
- Equipment: Automated weather station
- Internet: Satellite connection via GCI

## Satellite Internet Considerations

Both stations operate on GCI satellite internet which can be affected by:
- High-latitude location causing increased latency
- Weather interference (heavy rain, snow, freezing rain)
- Solar activity affecting satellite communications
- Seasonal variations in satellite coverage
- Equipment maintenance windows

Typical issues:
- Connection drops during severe weather
- Increased latency during peak usage
- Scheduled maintenance outages
- Power fluctuations affecting equipment

## Data Processing
- Fetches data from NDBC realtime2 URLs (PAUA2 and SGXA2)
- Converts units:
  * Wind speed: m/s → knots
  * Temperature: °C → °F
  * Time: UTC → AKST (AM/PM format)
- Provides wind direction in both degrees and cardinal format
- Calculates wind chill when applicable
- Rounds all measurements to 1 decimal place

## Output Format
JSON files are generated with:
- Station ID and name
- Last updated timestamp in AKST
- Data array with:
  * Wind speed (knots)
  * Wind direction (degrees and cardinal)
  * Temperature (°F)
  * Dewpoint (°F)
  * Wind chill (°F)
  * Gusts (knots)

## Daily Highest Wind/Gust Tracking
- Tracks highest wind speed and gust for each AKST day
- Day resets at midnight AKST (not UTC)
- For each highest value, records:
  * Speed/gust in knots
  * Time of occurrence in AM/PM AKST
- Handles missing gust values gracefully (shows "N/A")
- Updates in real-time as new data arrives

## Station Monitoring
Monitors the following stations:
- PAUA2 (St. Paul Island Airport)
- SGXA2 (St. George Island)

Monitoring features:
- Checks data freshness every 10 minutes
- Marks station as offline if no new data in 30+ minutes
- Records last successful data fetch time
- Includes status field in JSON output ("online"/"offline")
- Future integration with downdetect system planned

Additional behavior:
- Uses `STALE_THRESHOLD_MIN` to treat old observations as stale even if the fetch succeeds.
- When stale, station status is set to `offline`, and staleness metadata is included in the JSON output.

## Update Frequency
- NDBC data updates every 10 minutes
- Service polls for new data every 10 minutes
- Frontend sites update display every 30 seconds

## File Locations
- PAUA2: /docker_data/2025/PAUA2.json
- SGXA2: /docker_data/2025/SGXA2.json

## Getting Started

- **Prerequisites**
  - Docker (recommended) or Python 3.11+
  - Access to outbound HTTPS to `www.ndbc.noaa.gov`
- **Quick start (Docker)**
  - Build/run using your existing orchestration (e.g., `docker compose`) that mounts the output directory (see Configuration).
- **Quick start (Python)**
  - Create a virtualenv and install deps from `requirements.txt` (if applicable).
  - Run the scheduler/runner entrypoint that polls every 10 minutes.

## Configuration

- **Environment variables**
  - `OUTPUT_DIR` (default: `/docker_data`): Base directory for JSON output.
  - `POLL_INTERVAL_MIN` (default: `10`): Polling cadence in minutes.
  - `TZ` (default: `America/Anchorage`): Timezone used for display and day rollovers.
  - `YEAR_DIR_STRATEGY` (default: `YYYY`): If set, outputs will be nested under the current year (e.g., `/docker_data/2025/`).
  - `STALE_THRESHOLD_MIN` (default: `30`): If the latest observation is older than this many minutes, the station is marked `offline` and the output includes staleness metadata.
- **Notes**
  - If a year subfolder is used, it should roll automatically each January 1 based on `TZ`.
  - Ensure the process has write permissions to `OUTPUT_DIR`.

## How It Runs

- **Long-running service**: Polls NDBC every `POLL_INTERVAL_MIN` minutes and writes JSON files.
- **Containerized**: Typically deployed as a Docker container with a bind mount for `OUTPUT_DIR`.
- **Alternatives**: Can be scheduled via cron or a systemd timer if not containerized.

## Data Sources

- **NDBC realtime2 endpoints**
  - PAUA2: `https://www.ndbc.noaa.gov/data/realtime2/PAUA2.txt`
  - SGXA2: `https://www.ndbc.noaa.gov/data/realtime2/SGXA2.txt`
- The service parses the latest observations to produce normalized JSON.

## Timezone and Day Boundaries

- Display times and day rollovers use `TZ` (default `America/Anchorage`).
- AKST/AKDT transitions are handled automatically by the IANA timezone database.
- “Daily highest” values reset at local midnight in `TZ`.

## File Path Conventions

- Examples above show `/docker_data/2025/*.json`. Prefer a rolling year folder:
  - `"${OUTPUT_DIR}/$(YYYY)/PAUA2.json"`
  - `"${OUTPUT_DIR}/$(YYYY)/SGXA2.json"`
- If you do not want year subfolders, set `YEAR_DIR_STRATEGY` empty and write directly under `OUTPUT_DIR`.

## Example Output

- **PAUA2**
  ```json
  {
    "station": { "id": "PAUA2", "name": "St. Paul Island Airport" },
    "last_update": "2025-10-18 03:20:00 PM AKST",
    "status": "online",
    "stale": { "is_stale": false, "age_minutes": 4, "threshold_min": 30 },
    "data": [
      {
        "time_ak": "03:20 PM",
        "wind_knots": 22.5,
        "wind_dir_deg": 210,
        "wind_dir_cardinal": "SSW",
        "temp_f": 41.0,
        "dewpoint_f": 37.4,
        "wind_chill_f": 33.8,
        "gust_knots": 28.2
      }
    ],
    "daily_highest": {
      "wind_knots": { "value": 32.1, "time_ak": "11:40 AM" },
      "gust_knots": { "value": 38.6, "time_ak": "12:10 PM" }
    }
  }
  ```
- **SGXA2**
  ```json
  {
    "station": { "id": "SGXA2", "name": "St. George Island" },
    "last_update": "2025-10-18 03:20:00 PM AKST",
    "status": "online",
    "stale": { "is_stale": false, "age_minutes": 4, "threshold_min": 30 },
    "data": [
      {
        "time_ak": "03:20 PM",
        "wind_knots": 18.0,
        "wind_dir_deg": 160,
        "wind_dir_cardinal": "SSE",
        "temp_f": 39.2,
        "dewpoint_f": 36.0,
        "wind_chill_f": 32.9,
        "gust_knots": 24.7
      }
    ],
    "daily_highest": {
      "wind_knots": { "value": 25.3, "time_ak": "10:30 AM" },
      "gust_knots": { "value": 31.0, "time_ak": "01:05 PM" }
    }
  }
  ```

## Operations

- **Logging**: Emits structured logs for fetch, parse, convert, and write steps.
- **Retries**: Network calls use backoff with jitter; transient errors do not flip status immediately.
- **Freshness/Status**: Marks station `offline` if no new data for 30+ minutes; resumes to `online` when data updates.
- **Health**: Provide a simple heartbeat log or optional HTTP health endpoint if running as a web service.
- **Future work**: Planned integration with a downdetect/alerting system.

## Metadata

- **Output only**: This service writes files; it does not serve an HTTP API unless integrated into a larger app.
- **License**: Add project license here.
- **Maintainer**: Add maintainer/contact information here.
