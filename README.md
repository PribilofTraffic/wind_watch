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

## Update Frequency
- NDBC data updates every 10 minutes
- Service polls for new data every 10 minutes
- Frontend sites update display every 30 seconds

## File Locations
- PAUA2: /docker_data/2025/PAUA2.json
- SGXA2: /docker_data/2025/SGXA2.json
