#!/usr/bin/env python3
import json
import os
import logging
import logging.handlers
from datetime import datetime, timezone, timedelta
import requests
import time
import sys

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

def get_time(for_display=False, dt=None):
    """Single source of truth for time."""
    base_time = dt if dt else datetime.now(timezone.utc)
    if for_display:
        akst = base_time - timedelta(hours=9)
        return {
            'date': akst.strftime("%Y-%m-%d"),
            'time': akst.strftime("%I:%M:%S %p AKST"),
            'timestamp': akst.strftime("%Y-%m-%d %I:%M:%S %p AKST")
        }
    return base_time

def load_config():
    """Load station configuration from JSON file."""
    try:
        config_path = os.path.join(os.path.dirname(__file__), 'config', 'stations.json')
        with open(config_path, 'r') as f:
            config = json.load(f)
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
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        data = []
        lines = response.text.splitlines()[2:]
        
        for line in lines:
            entry = parse_line(line)
            if entry:
                data.append(entry)
        
        data.sort(key=lambda x: x['utc'])
        logger.info(f"Fetched {len(data)} entries from 45-day data at {url}")
        return data
    except Exception as e:
        logger.error(f"Failed to fetch 45-day data from {url} for {station_id}: {str(e)}")
        return []

def get_daily_statistics(data):
    """
    Calculate comprehensive daily statistics for AKST days.
    
    Tracks:
    - Highest wind speed (with timestamp)
    - Highest gust (with timestamp)
    - Highest temperature (with timestamp)
    - Lowest temperature (with timestamp)
    - Lowest wind chill (with timestamp)
    """
    daily_stats = {}
    
    for entry in data:
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
    
    return daily_stats

def is_akst_midnight(utc_time):
    """
    Check if the given UTC time corresponds to midnight in AKST.
    AKST is UTC-9.
    """
    akst_time = utc_time - timedelta(hours=9)
    return akst_time.hour == 0 and akst_time.minute == 0

def save_daily_statistics(station_id, name, date, daily_stats):
    """
    Save daily statistics to a JSON file in the format:
    data/daily/2025/[station_id]_[YYYY-MM-DD].json
    """
    year = date.split('-')[0]
    output_dir = os.path.join(os.environ.get('DATA_DIR', os.path.join(os.path.dirname(__file__), 'data')), 'daily', year)
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, f"{station_id}_{date}.json")
    
    output_data = {
        "station": station_id,
        "name": name,
        "date": date,
        "statistics": daily_stats
    }
    
    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    logger.info(f"Saved daily statistics for {station_id} on {date}")

def save_station_data(station_id, name, new_data):
    """Save station data to 2025 JSON file with comprehensive daily statistics."""
    output_dir = os.path.join(os.environ.get('DATA_DIR', os.path.join(os.path.dirname(__file__), 'data')), '2025')
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{station_id}.json")
    
    # Load existing data and daily statistics
    existing_data = []
    current_daily_stats = {}
    
    try:
        with open(output_file, 'r') as f:
            existing = json.load(f)
            existing_data = existing.get('data', [])
            current_daily_stats = existing.get('daily_statistics', {})
            logger.info(f"Loaded {len(existing_data)} existing entries for {station_id}")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.info(f"No existing data found for {station_id}, starting fresh")
    
    # Merge data, with error handling for missing 'utc'
    existing_dict = {}
    for entry in existing_data:
        if 'utc' not in entry:
            logger.error(f"Invalid existing entry for {station_id}, missing 'utc': {entry}")
            continue
        existing_dict[entry['utc']] = entry
    
    for entry in new_data:
        if 'utc' not in entry:
            logger.error(f"Invalid new entry for {station_id}, missing 'utc': {entry}")
            continue
        existing_dict[entry['utc']] = entry
    
    merged_data = list(existing_dict.values())
    merged_data.sort(key=lambda x: x['utc'])
    
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=45)
    filtered_data = [entry for entry in merged_data 
                    if datetime.fromisoformat(entry['utc'].rstrip('Z')).replace(tzinfo=timezone.utc) >= cutoff_time]
    
    # Check for AKST midnight transition in the new data
    current_date = None
    previous_date = None
    
    for entry in new_data:
        entry_utc = datetime.fromisoformat(entry['utc'].rstrip('Z')).replace(tzinfo=timezone.utc)
        entry_date = entry['date']
        
        if current_date is None:
            current_date = entry_date
        elif entry_date != current_date:
            # Date change detected
            previous_date = current_date
            current_date = entry_date
            
            # If we have stats for the previous day, save them to a daily file
            if previous_date in current_daily_stats:
                save_daily_statistics(station_id, name, previous_date, current_daily_stats[previous_date])
    
    # Get updated daily statistics
    daily_stats = get_daily_statistics(filtered_data)
    
    # No backups needed
    
    output_data = {
        "station": station_id,
        "name": name,
        "last_updated": get_time(for_display=True)['timestamp'],
        "data": filtered_data,
        "daily_statistics": daily_stats  # Replace daily_max_gusts with comprehensive daily_statistics
    }
    
    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    logger.info(f"Updated {station_id} data file with {len(filtered_data)} entries")
    
    return daily_stats

def update_status(station_id, is_online, station_data=None):
    """Update station status and detect offline periods."""
    status_dir = os.path.join(os.path.dirname(__file__), 'data', 'status')
    os.makedirs(status_dir, exist_ok=True)
    status_file = os.path.join(status_dir, f"{station_id}_status.json")
    
    current_time = datetime.now(timezone.utc)
    display_time = get_time(for_display=True)
    
    status_data = {}
    if os.path.exists(status_file):
        try:
            with open(status_file, 'r') as f:
                status_data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            logger.error(f"Error reading status file for {station_id}, creating new one")
    
    if not status_data:
        status_data = {"status": "unknown", "last_online": None, "offline_since": None}
    
    if is_online and station_data:
        latest_utc = datetime.fromisoformat(station_data['utc'].rstrip('Z')).replace(tzinfo=timezone.utc)
        time_gap = (current_time - latest_utc).total_seconds() / 60
        
        if time_gap > 15:
            if status_data["status"] != "offline":
                status_data["offline_since"] = (latest_utc + timedelta(minutes=1)).isoformat() + 'Z'
                status_data["status"] = "offline"
                logger.warning(f"Station {station_id} data stale by {time_gap:.1f} minutes, marked offline")
        else:
            status_data["status"] = "online"
            status_data["last_online"] = display_time['timestamp']
            if status_data.get("status") == "offline":
                offline_since = datetime.fromisoformat(status_data["offline_since"].rstrip('Z')).replace(tzinfo=timezone.utc)
                duration_min = int((current_time - offline_since).total_seconds() / 60)
                status_data["last_outage_duration"] = f"{duration_min} minutes"
                logger.info(f"Station {station_id} back online after {duration_min} minutes")
            status_data["data_summary"] = station_data
    else:
        if status_data["status"] != "offline":
            status_data["status"] = "offline"
            status_data["offline_since"] = current_time.isoformat() + 'Z'
            logger.warning(f"Station {station_id} marked offline at {display_time['timestamp']}")
    
    with open(status_file, 'w') as f:
        json.dump(status_data, f, indent=2)
    
    return status_data["status"]

def check_and_save_daily_files(station_id, name, daily_stats):
    """Check for completed days and save daily statistics files."""
    today = get_time(for_display=True)['date']
    
    # Find dates that are not today (completed days)
    completed_dates = [date for date in daily_stats.keys() if date != today]
    
    for date in completed_dates:
        save_daily_statistics(station_id, name, date, daily_stats[date])
        logger.info(f"Saved completed day statistics for {station_id} on {date}")

def main():
    """Main function to process wind data."""
    config = load_config()
    update_interval = config['output']['current']['update_interval']
    
    logger.info("Starting wind data conversion service...")
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        logger.info("Running in test mode - single iteration")
        try:
            for station in config['stations']:
                station_id = station['id']
                logger.info(f"Getting 45-day data for {station_id}")
                data_45day = process_45day_data(station['source_45day'], station_id)
                
                if data_45day:
                    daily_stats = save_station_data(station_id, station['name'], data_45day)
                    check_and_save_daily_files(station_id, station['name'], daily_stats)
                    latest_data = data_45day[-1]
                    update_status(station_id, True, latest_data)
                    logger.info(f"Updated {station_id} with {len(data_45day)} entries")
                else:
                    logger.error(f"No data found for {station_id}")
                    update_status(station_id, False)
            logger.info("Test run completed successfully")
            return
        except Exception as e:
            logger.error(f"Error in test mode: {str(e)}")
            raise
    
    while True:
        try:
            start_time = time.time()
            
            for station in config['stations']:
                station_id = station['id']
                logger.info(f"Getting 45-day data for {station_id}")
                try:
                    data_45day = process_45day_data(station['source_45day'], station_id)
                    
                    if data_45day:
                        daily_stats = save_station_data(station_id, station['name'], data_45day)
                        check_and_save_daily_files(station_id, station['name'], daily_stats)
                        latest_data = data_45day[-1]
                        update_status(station_id, True, latest_data)
                        logger.info(f"Updated {station_id} with {len(data_45day)} entries")
                    else:
                        logger.error(f"No data found for {station_id}")
                        update_status(station_id, False)
                except Exception as e:
                    logger.error(f"Error processing station {station_id}: {str(e)}")
                    update_status(station_id, False)
            
            elapsed = time.time() - start_time
            sleep_time = max(0, update_interval - elapsed)
            time.sleep(sleep_time)
        except Exception as e:
            logger.error(f"Error in main loop: {str(e)}")
            time.sleep(update_interval)

if __name__ == '__main__':
    main()
