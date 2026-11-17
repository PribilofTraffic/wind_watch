#!/usr/bin/env python3
import json
import os
import logging
import logging.handlers
import requests
from datetime import datetime, timezone, timedelta, date as date_type
from zoneinfo import ZoneInfo
import time
import sys

import diagnose

# --- Settings (environment) --------------------------------------------------
# Local time zone for every date and time shown in the output. A named zone
# (not a fixed offset) means the switch between AKST and AKDT happens on its
# own, and a change in the law reaches us through the time zone database.
LOCAL_TZ = ZoneInfo(os.environ.get('TZ') or 'America/Anchorage')
# How the service identifies itself to NDBC.
USER_AGENT = os.environ.get('USER_AGENT', 'wind-watch-collector/1.1')
FETCH_TIMEOUT = int(os.environ.get('FETCH_TIMEOUT', '30'))   # seconds per try
FETCH_RETRIES = int(os.environ.get('FETCH_RETRIES', '3'))
# A finished day's archive file is rewritten only while the day is this recent
# (so late-arriving observations are picked up). Older files are never touched.
BACKFILL_DAYS = int(os.environ.get('BACKFILL_DAYS', '3'))

# Where a station's observations live when stations.json gives no URL.
NDBC_URL = os.environ.get(
    'NDBC_URL', 'https://www.ndbc.noaa.gov/data/realtime2/{id}.txt')

# Per-station staleness limits from stations.json ("stale_threshold_min"):
# station id -> minutes. Stations not listed use STALE_THRESHOLD_MIN.
STALE_OVERRIDES = {}

# What NDBC last sent for each station: station id -> (Last-Modified, parsed
# entries). Lets the next cycle ask "anything newer than this?" and skip the
# download and the parsing when the answer is no.
FETCH_CACHE = {}

# Why the last fetch for each station failed: station id -> (cause, detail),
# as worked out by diagnose.py. Empty when the last fetch worked.
LAST_FAILURE = {}

# Configure logging
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.handlers.TimedRotatingFileHandler(
    os.path.join(log_dir, 'wind.log'),
    when='midnight',
    interval=1,
    backupCount=30
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Also log to stdout so container logs are visible in Dozzle
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

def get_time(for_display=False, dt=None):
    """Single source of truth for time."""
    base_time = dt if dt else datetime.now(timezone.utc)
    if for_display:
        # Real Alaska local time with the right label for that moment:
        # AKST in winter, AKDT in summer. (This used to be a fixed UTC-9
        # labelled "AKST" all year, an hour behind the clock every summer.)
        local = base_time.astimezone(LOCAL_TZ)
        zone = local.strftime("%Z")
        return {
            'date': local.strftime("%Y-%m-%d"),
            'time': local.strftime("%I:%M:%S %p ") + zone,
            'timestamp': local.strftime("%Y-%m-%d %I:%M:%S %p ") + zone
        }
    return base_time


def write_json(path, data):
    """Write JSON to a temp file, then rename it over the target, so a
    reader (web server, cron copy) never sees a half-written file."""
    tmp = f"{path}.tmp"
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def fetch_text(url, if_modified_since=None):
    """GET a URL with a timeout and a few retries.

    Returns (text, last_modified). If if_modified_since is given and the
    source has nothing newer (HTTP 304), returns (None, if_modified_since)
    without downloading the body.

    Retries network errors and 5xx answers with a growing pause. Raises the
    last error if every try fails.
    """
    headers = {'User-Agent': USER_AGENT}
    if if_modified_since:
        headers['If-Modified-Since'] = if_modified_since
    last_error = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            response = requests.get(url, timeout=FETCH_TIMEOUT, headers=headers)
            if response.status_code == 304:
                return None, if_modified_since
            if response.status_code < 500:
                response.raise_for_status()
                return response.text, response.headers.get('Last-Modified')
            last_error = requests.HTTPError(
                f"{response.status_code} from {url}", response=response)
        except requests.HTTPError as e:
            raise e  # a 4xx: asking again will not change it
        except requests.RequestException as e:
            last_error = e
        if attempt < FETCH_RETRIES:
            time.sleep(5 * attempt)
    raise last_error

def get_stale_threshold_minutes(station_id=None):
    """Staleness threshold in minutes: the station's own setting from
    stations.json if it has one, otherwise STALE_THRESHOLD_MIN (default 30)."""
    if station_id in STALE_OVERRIDES:
        return STALE_OVERRIDES[station_id]
    try:
        return int(os.environ.get('STALE_THRESHOLD_MIN', '30'))
    except ValueError:
        return 30

def compute_staleness(latest_entry_utc_iso, station_id=None):
    """Return (is_stale, age_minutes, threshold_min) for the latest entry.

    latest_entry_utc_iso is ISO string with trailing 'Z'.
    """
    threshold = get_stale_threshold_minutes(station_id)
    now = datetime.now(timezone.utc)
    try:
        latest_dt = datetime.fromisoformat(latest_entry_utc_iso.rstrip('Z')).replace(tzinfo=timezone.utc)
        age_minutes = int((now - latest_dt).total_seconds() / 60)
        return (age_minutes > threshold, age_minutes, threshold)
    except Exception:
        # If we cannot parse, treat as stale
        return (True, None, threshold)

def load_config():
    """Load station configuration from JSON file."""
    try:
        config_path = os.path.join('/app/config/stations.json')
        with open(config_path, 'r') as f:
            config = json.load(f)
        for station in config['stations']:
            # Only "id" is required. The name defaults to the id and the URL
            # to NDBC's standard address for that id.
            station.setdefault('name', station['id'])
            if not station.get('source_45day'):
                station['source_45day'] = NDBC_URL.format(id=station['id'])
            if station.get('stale_threshold_min') is not None:
                STALE_OVERRIDES[station['id']] = int(station['stale_threshold_min'])
        logger.info("Successfully loaded station configuration")
        return config
    except Exception as e:
        logger.error(f"Failed to load configuration: {str(e)}")
        raise

def convert_ms_to_knots(speed_ms):
    """Convert wind speed from m/s to knots."""
    if speed_ms in ['MM', '99.0']:
        return None
    return round(float(speed_ms) * 1.94384, 1)

def convert_c_to_f(temp_c):
    """Convert temperature from Celsius to Fahrenheit."""
    if temp_c in ['MM', '99.0']:
        return None
    try:
        return round((float(temp_c) * 9/5) + 32, 1)
    except (ValueError, TypeError):
        return None

def calculate_wind_chill(temp_f, wind_knots):
    """Calculate wind chill using NWS formula."""
    if temp_f is None or wind_knots is None:
        return None
    wind_mph = wind_knots * 1.15078
    if temp_f <= 50 and wind_mph >= 3:
        wind_chill = 35.74 + (0.6215 * temp_f) - (35.75 * pow(wind_mph, 0.16)) + (0.4275 * temp_f * pow(wind_mph, 0.16))
        return round(wind_chill, 1)
    return None

def get_cardinal_direction(degrees):
    """Convert degrees to cardinal direction."""
    if degrees in ['MM', '99.0']:
        return None
    degrees = float(degrees)
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    index = round(degrees / 22.5) % 16
    return directions[index]

def parse_utc_time(utc_date, utc_hour, utc_minute):
    """Parse UTC time and return UTC datetime object."""
    dt = datetime.strptime(f"{utc_date} {utc_hour}:{utc_minute}", "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=timezone.utc)

def parse_line(line):
    """Parse a single line of wind data."""
    try:
        parts = line.strip().split()
        if len(parts) < 15:
            return None

        year, month, day = parts[0:3]
        hour, minute = parts[3:5]
        utc_time = parse_utc_time(f"{year}-{month}-{day}", hour, minute)
        akst_display = get_time(for_display=True, dt=utc_time)
        
        direction_deg = parts[5]
        direction_card = get_cardinal_direction(direction_deg)
        wind_speed = convert_ms_to_knots(parts[6])
        gust_speed = convert_ms_to_knots(parts[7])
        temp_f = convert_c_to_f(parts[13])
        dewpoint_f = convert_c_to_f(parts[15])
        wind_chill = calculate_wind_chill(temp_f, wind_speed)

        return {
            'utc': utc_time.isoformat() + 'Z',
            'date': akst_display['date'],
            'time': akst_display['time'],
            'wind': wind_speed,
            'gust': gust_speed,
            'direction': {
                'degrees': int(float(direction_deg)) if direction_deg not in ['MM', '99.0'] else None,
                'cardinal': direction_card
            },
            'temperature': temp_f,
            'dewpoint': dewpoint_f,
            'windchill': wind_chill
        }
    except Exception as e:
        logger.error(f"Error parsing line: {str(e)}")
        return None

def process_45day_data(url, station_id):
    """Process 45-day data from NDBC realtime2 URL."""
    LAST_FAILURE.pop(station_id, None)
    try:
        cached_modified, cached_data = FETCH_CACHE.get(station_id, (None, None))
        text, last_modified = fetch_text(url, cached_modified)
        if text is None:
            # 304: NDBC has nothing newer than what we parsed last time.
            logger.info(f"No change at NDBC for {station_id} since {cached_modified}; "
                        f"reusing {len(cached_data)} entries")
            return cached_data

        data = []
        lines = text.splitlines()[2:]
        
        for line in lines:
            entry = parse_line(line)
            if entry:
                data.append(entry)

        if not data:
            # NDBC answered but nothing usable came back.
            if lines:
                LAST_FAILURE[station_id] = (
                    diagnose.OUR_ERROR,
                    f'{len(lines)} lines received but none could be parsed (format changed?)')
            else:
                LAST_FAILURE[station_id] = (
                    diagnose.SOURCE_STALE,
                    'NDBC has no observations from this station in its 45-day file')
        
        # Sort in reverse chronological order
        data.sort(key=lambda x: x['utc'], reverse=True)
        logger.info(f"Fetched {len(data)} entries from 45-day data at {url}")
        if data and last_modified:
            FETCH_CACHE[station_id] = (last_modified, data)
        else:
            FETCH_CACHE.pop(station_id, None)
        return data
    except Exception as e:
        LAST_FAILURE[station_id] = diagnose.classify_failure(e)
        logger.error(f"Failed to fetch 45-day data from {url} for {station_id}: {str(e)}")
        logger.warning(f"Diagnosis for {station_id}: {diagnose.describe(*LAST_FAILURE[station_id])}")
        return []

def get_daily_statistics(data):
    """Calculate comprehensive daily statistics for AKST days."""
    daily_stats = {}
    
    # Process data in reverse chronological order
    for entry in sorted(data, key=lambda x: x['date'], reverse=True):
        date = entry['date']
        if date not in daily_stats:
            daily_stats[date] = {
                'max_wind': {'value': None, 'time': None, 'utc': None},
                'max_gust': {'value': None, 'time': None, 'utc': None},
                'max_temp': {'value': None, 'time': None, 'utc': None},
                'min_temp': {'value': None, 'time': None, 'utc': None},
                'min_windchill': {'value': None, 'time': None, 'utc': None}
            }
        
        # Check and update wind speed
        if entry['wind'] is not None:
            if daily_stats[date]['max_wind']['value'] is None or entry['wind'] > daily_stats[date]['max_wind']['value']:
                daily_stats[date]['max_wind'] = {
                    'value': entry['wind'],
                    'time': entry['time'],
                    'utc': entry['utc']
                }
        
        # Check and update gust
        if entry['gust'] is not None:
            if daily_stats[date]['max_gust']['value'] is None or entry['gust'] > daily_stats[date]['max_gust']['value']:
                daily_stats[date]['max_gust'] = {
                    'value': entry['gust'],
                    'time': entry['time'],
                    'utc': entry['utc']
                }
        
        # Check and update temperature (max)
        if entry['temperature'] is not None:
            if daily_stats[date]['max_temp']['value'] is None or entry['temperature'] > daily_stats[date]['max_temp']['value']:
                daily_stats[date]['max_temp'] = {
                    'value': entry['temperature'],
                    'time': entry['time'],
                    'utc': entry['utc']
                }
            
            # Check and update temperature (min)
            if daily_stats[date]['min_temp']['value'] is None or entry['temperature'] < daily_stats[date]['min_temp']['value']:
                daily_stats[date]['min_temp'] = {
                    'value': entry['temperature'],
                    'time': entry['time'],
                    'utc': entry['utc']
                }
        
        # Check and update wind chill
        if entry['windchill'] is not None:
            if daily_stats[date]['min_windchill']['value'] is None or entry['windchill'] < daily_stats[date]['min_windchill']['value']:
                daily_stats[date]['min_windchill'] = {
                    'value': entry['windchill'],
                    'time': entry['time'],
                    'utc': entry['utc']
                }
    
    # Sort the daily stats by date in reverse chronological order
    return dict(sorted(daily_stats.items(), reverse=True))

def save_station_data(station_id, name, new_data):
    """Save station data to wind_watch/YYYY JSON file with comprehensive daily statistics."""
    try:
        # Determine the year from the latest data entry
        current_year = datetime.now(timezone.utc).year
        # Ensure the wind_watch directory exists
        data_dir = os.path.join(f'/app/data/wind_watch/{current_year}')
        os.makedirs(data_dir, exist_ok=True)

        # Save the data
        data_file = os.path.join(data_dir, f"{station_id}.json")
        
        # Calculate daily statistics
        daily_stats = get_daily_statistics(new_data)
        
        # Get the latest entry (data is already sorted newest-to-oldest)
        latest_entry = new_data[0] if new_data else None

        # Compute staleness metadata
        if latest_entry:
            is_stale, age_minutes, threshold_min = compute_staleness(latest_entry['utc'], station_id)
            stale_meta = {
                'is_stale': is_stale,
                'age_minutes': age_minutes,
                'threshold_min': threshold_min
            }
        else:
            stale_meta = {
                'is_stale': True,
                'age_minutes': None,
                'threshold_min': get_stale_threshold_minutes(station_id)
            }
        
        # Sort daily stats in reverse chronological order
        sorted_stats = dict(sorted(daily_stats.items(), reverse=True))
        
        # Create the output data structure
        output_data = {
            'station': {
                'id': station_id,
                'name': name
            },
            'last_update': get_time(for_display=True)['timestamp'],
            'current': latest_entry,
            'status': 'offline' if stale_meta.get('is_stale') else 'online',
            'stale': stale_meta,
            'daily_stats': sorted_stats
        }
        
        write_json(data_file, output_data)
        
        logger.info(f"Saved {len(new_data)} entries to {data_file}")
        return daily_stats
    except Exception as e:
        logger.error(f"Failed to save station data: {str(e)}")
        raise

def save_daily_statistics(station_id, name, date, daily_stats):
    """Save one finished day's statistics to
    wind_watch/daily/YYYY/[station_id]_[YYYY-MM-DD].json

    Returns True if the file was written, False if it was left alone. A file
    is written when it does not exist yet, or when its content has changed
    and the day is within the last BACKFILL_DAYS days. Anything older is
    history and is never rewritten.
    """
    try:
        # Extract year from the date (YYYY-MM-DD format)
        year = date.split('-')[0]
        # Ensure the daily directory exists
        daily_dir = os.path.join(f'/app/data/wind_watch/daily/{year}')
        os.makedirs(daily_dir, exist_ok=True)

        daily_file = os.path.join(daily_dir, f"{station_id}_{date}.json")
        
        output_data = {
            'station': {
                'id': station_id,
                'name': name
            },
            'date': date,
            'statistics': daily_stats
        }

        if os.path.exists(daily_file):
            today = datetime.now(LOCAL_TZ).date()
            age_days = (today - date_type.fromisoformat(date)).days
            if age_days > BACKFILL_DAYS:
                return False
            try:
                with open(daily_file, 'r') as f:
                    if json.load(f) == output_data:
                        return False
            except (OSError, ValueError):
                pass  # unreadable file for a recent day: write a good one

        write_json(daily_file, output_data)
        return True
    except Exception as e:
        logger.error(f"Failed to save daily statistics: {str(e)}")
        raise

def update_status(station_id, is_online, station_data=None, cause=None, detail=None):
    """Update station status and detect offline periods.

    cause/detail say whose end a problem is on (see diagnose.py) and are
    written to the status file as "cause" and "cause_detail"; both are null
    while the station is online.
    """
    try:
        # Ensure the status directory exists
        status_dir = os.path.join('/app/data/wind_watch/status')
        os.makedirs(status_dir, exist_ok=True)

        # Get current time
        current_time = get_time()
        display_time = get_time(for_display=True)
        
        # Status file path
        status_file = os.path.join(status_dir, f"{station_id}_status.json")
        
        # Load existing status or create new
        if os.path.exists(status_file):
            with open(status_file, 'r') as f:
                status_data = json.load(f)
        else:
            status_data = {
                "station_id": station_id,
                "status": "unknown",
                "last_update": current_time.isoformat() + 'Z',
                "last_outage": None,
                "last_outage_duration": None,
                "offline_since": None,
                "data_summary": None
            }
        
        # Update status
        status_data["last_update"] = current_time.isoformat() + 'Z'
        
        if is_online:
            if status_data["status"] == "offline" and status_data.get("offline_since"):
                offline_since = datetime.fromisoformat(status_data["offline_since"].rstrip('Z')).replace(tzinfo=timezone.utc)
                status_data["last_outage"] = status_data["offline_since"]
                status_data["offline_since"] = None
                duration_min = int((current_time - offline_since).total_seconds() / 60)
                status_data["last_outage_duration"] = f"{duration_min} minutes"
                logger.info(f"Station {station_id} back online after {duration_min} minutes "
                            f"(was: {diagnose.describe(status_data.get('cause'))})")
            status_data["status"] = "online"
            status_data["data_summary"] = station_data
            status_data["cause"] = None
            status_data["cause_detail"] = None
        else:
            if status_data["status"] != "offline":
                status_data["status"] = "offline"
                status_data["offline_since"] = current_time.isoformat() + 'Z'
                logger.warning(f"Station {station_id} marked offline at {display_time['timestamp']}: "
                               f"{diagnose.describe(cause, detail)}")
            status_data["cause"] = cause
            status_data["cause_detail"] = detail
        
        write_json(status_file, status_data)
        
        return status_data["status"]
    except Exception as e:
        logger.error(f"Failed to update status: {str(e)}")
        raise

def check_and_save_daily_files(station_id, name, daily_stats):
    """Check for completed days and save daily statistics files."""
    today = get_time(for_display=True)['date']
    
    # Find dates that are not today (completed days)
    completed_dates = [date for date in daily_stats.keys() if date != today]
    
    written = [date for date in completed_dates
               if save_daily_statistics(station_id, name, date, daily_stats[date])]
    if written:
        logger.info(f"Saved daily statistics for {station_id}: {', '.join(sorted(written))}")


def mark_station_file_stale(station_id):
    """After a failed fetch, bring the station file's status up to date.

    The file keeps its last good reading, but its "status" and "stale"
    fields were worked out when that reading was fresh. Recompute them from
    the reading's age so pages stop showing an old reading as "online".
    """
    try:
        year = datetime.now(timezone.utc).year
        data_file = f'/app/data/wind_watch/{year}/{station_id}.json'
        with open(data_file, 'r') as f:
            output_data = json.load(f)
        latest = output_data.get('current')
        if not latest:
            return
        is_stale, age_minutes, threshold_min = compute_staleness(latest['utc'], station_id)
        new_status = 'offline' if is_stale else 'online'
        new_stale = {'is_stale': is_stale, 'age_minutes': age_minutes, 'threshold_min': threshold_min}
        if output_data.get('status') == new_status and output_data.get('stale') == new_stale:
            return
        output_data['status'] = new_status
        output_data['stale'] = new_stale
        write_json(data_file, output_data)
    except (OSError, ValueError, KeyError) as e:
        logger.warning(f"Could not refresh staleness in the {station_id} file: {e}")


def process_station(station):
    """One fetch-and-save pass for one station."""
    station_id = station['id']
    logger.info(f"Getting 45-day data for {station_id}")
    try:
        data_45day = process_45day_data(station['source_45day'], station_id)

        if data_45day:
            daily_stats = save_station_data(station_id, station['name'], data_45day)
            check_and_save_daily_files(station_id, station['name'], daily_stats)
            latest_data = data_45day[0]
            is_stale, age_minutes, threshold_min = compute_staleness(latest_data['utc'], station_id)
            if is_stale:
                # NDBC answered, but the station itself has gone quiet:
                # its sensor, power or satellite link.
                update_status(station_id, False, None, diagnose.SOURCE_STALE,
                              f'newest observation is {age_minutes} minutes old')
            else:
                update_status(station_id, True, latest_data)
            logger.info(f"Updated {station_id} with {len(data_45day)} entries")
        else:
            cause, detail = LAST_FAILURE.get(station_id, (None, None))
            logger.error(f"No data found for {station_id}")
            mark_station_file_stale(station_id)
            update_status(station_id, False, None, cause, detail)
    except Exception as e:
        logger.error(f"Error processing station {station_id}: {str(e)}")
        update_status(station_id, False, None, diagnose.OUR_ERROR,
                      f'{type(e).__name__}: {e}'[:200])

def main():
    """Main function to process wind data."""
    config = load_config()
    update_interval = config['output']['current']['update_interval']
    
    logger.info("Starting wind data conversion service...")
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        logger.info("Running in test mode - single iteration")
        for station in config['stations']:
            process_station(station)
        logger.info("Test run completed successfully")
        return
    
    while True:
        try:
            start_time = time.time()
            
            for station in config['stations']:
                process_station(station)
            
            elapsed = time.time() - start_time
            sleep_time = max(0, update_interval - elapsed)
            time.sleep(sleep_time)
        except Exception as e:
            logger.error(f"Error in main loop: {str(e)}")
            time.sleep(update_interval)

if __name__ == '__main__':
    main()
