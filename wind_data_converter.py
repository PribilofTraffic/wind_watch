#!/usr/bin/env python3
import json
import os
import logging
import logging.handlers
import requests
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
        config_path = os.path.join('/app/config/stations.json')
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
        
        # Sort in reverse chronological order
        data.sort(key=lambda x: x['utc'], reverse=True)
        logger.info(f"Fetched {len(data)} entries from 45-day data at {url}")
        return data
    except Exception as e:
        logger.error(f"Failed to fetch 45-day data from {url} for {station_id}: {str(e)}")
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
    """Save station data to wind_watch/2025 JSON file with comprehensive daily statistics."""
    try:
        # Ensure the wind_watch directory exists
        data_dir = os.path.join('/app/data/wind_watch/2025')
        os.makedirs(data_dir, exist_ok=True)

        # Save the data
        data_file = os.path.join(data_dir, f"{station_id}.json")
        
        # Calculate daily statistics
        daily_stats = get_daily_statistics(new_data)
        
        # Get the latest entry (data is already sorted newest-to-oldest)
        latest_entry = new_data[0] if new_data else None
        
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
            'daily_stats': sorted_stats
        }
        
        with open(data_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        logger.info(f"Saved {len(new_data)} entries to {data_file}")
        return daily_stats
    except Exception as e:
        logger.error(f"Failed to save station data: {str(e)}")
        raise

def save_daily_statistics(station_id, name, date, daily_stats):
    """Save daily statistics to a JSON file in the format:
    wind_watch/daily/2025/[station_id]_[YYYY-MM-DD].json
    """
    try:
        # Ensure the daily directory exists
        daily_dir = os.path.join('/app/data/wind_watch/daily/2025')
        os.makedirs(daily_dir, exist_ok=True)

        # Save the data
        daily_file = os.path.join(daily_dir, f"{station_id}_{date}.json")
        
        output_data = {
            'station': {
                'id': station_id,
                'name': name
            },
            'date': date,
            'statistics': daily_stats
        }
        
        with open(daily_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        logger.info(f"Saved daily statistics to {daily_file}")
    except Exception as e:
        logger.error(f"Failed to save daily statistics: {str(e)}")
        raise

def update_status(station_id, is_online, station_data=None):
    """Update station status and detect offline periods."""
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
            if status_data["status"] == "offline":
                offline_since = datetime.fromisoformat(status_data["offline_since"].rstrip('Z')).replace(tzinfo=timezone.utc)
                status_data["status"] = "online"
                status_data["last_outage"] = status_data["offline_since"]
                status_data["offline_since"] = None
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
    except Exception as e:
        logger.error(f"Failed to update status: {str(e)}")
        raise

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
