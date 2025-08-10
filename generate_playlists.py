import requests
import gzip
import json
import os
import logging
from io import BytesIO

# --- Configuration ---
OUTPUT_DIR = "playlists"
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
REQUEST_TIMEOUT = 30 # seconds

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---
def fetch_url(url, is_json=True, is_gzipped=False, headers=None, stream=False):
    """Fetches data from a URL, handles gzip, and parses JSON if needed."""
    logging.info(f"Fetching URL: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, stream=stream)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        if stream: # Return the raw response object for streaming content (like Tubi's M3U)
             logging.info("Returning streaming response.")
             return response

        content = response.content
        if is_gzipped:
            logging.info("Decompressing gzipped content.")
            try:
                # Use BytesIO to treat the byte string as a file-like object
                with gzip.GzipFile(fileobj=BytesIO(content), mode='rb') as f:
                    content = f.read()
                content = content.decode('utf-8') # Decode bytes to string
            except gzip.BadGzipFile:
                logging.warning("Content was not gzipped, trying as plain text.")
                content = content.decode('utf-8') # Assume it was plain text
            except Exception as e:
                 logging.error(f"Error decompressing gzip: {e}")
                 raise # Re-raise the exception

        else:
             content = content.decode('utf-8') # Decode bytes to string for non-gzipped

        if is_json:
            logging.info("Parsing JSON data.")
            return json.loads(content)
        else:
            logging.info("Returning raw text content.")
            return content # Return raw text if not JSON

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching {url}: {e}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred for {url}: {e}")
        return None

def write_m3u_file(filename, content):
    """Writes content to a file in the output directory."""
    if not os.path.exists(OUTPUT_DIR):
        logging.info(f"Creating output directory: {OUTPUT_DIR}")
        os.makedirs(OUTPUT_DIR)

    filepath = os.path.join(OUTPUT_DIR, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logging.info(f"Successfully wrote playlist to {filepath}")
    except IOError as e:
        logging.error(f"Error writing file {filepath}: {e}")

def format_extinf(channel_id, tvg_id, tvg_chno, tvg_name, tvg_logo, group_title, display_name):
    """Formats the #EXTINF line."""
    # Ensure tvg_chno is empty if None or invalid
    chno_str = str(tvg_chno) if tvg_chno is not None and str(tvg_chno).isdigit() else ""
    
    # Basic sanitization for names/titles within the M3U format
    sanitized_tvg_name = tvg_name.replace('"', "'")
    sanitized_group_title = group_title.replace('"', "'")
    sanitized_display_name = display_name.replace(',', '') # Commas break the EXTINF line itself

    return (f'#EXTINF:-1 '
            f'channel-id="{channel_id}" '
            f'tvg-id="{tvg_id}" '
            f'tvg-chno="{chno_str}" '
            f'tvg-name="{sanitized_tvg_name}" '
            f'tvg-logo="{tvg_logo}" '
            f'group-title="{sanitized_group_title}",'
            f'{sanitized_display_name}\n')

# --- Service Functions ---

def generate_lgchannels_m3u(regions=['us', 'ca', 'gb', 'au', 'all'], sort='name'):
    """Generates M3U playlists for LGchannels."""
    LGCHANNELS_URL = 'https://lgchannels.com/#/'
    STREAM_URL_TEMPLATE = 'https://jmp2.uk/plu-{id}.m3u8'
    EPG_URL_TEMPLATE = 'https://github.com/matthuisman/i.mjh.nz/raw/master/PlutoTV/{region}.xml.gz'

    data = fetch_url(lgchannels_URL, is_json=True, is_gzipped=True)
    if not data or 'regions' not in data:
        logging.error("Failed to fetch or parse PlutoTV data.")
        return

    region_name_map = {
        "ar": "Argentina", "br": "Brazil", "ca": "Canada", "cl": "Chile", "co": "Colombia",
        "cr": "Costa Rica", "de": "Germany", "dk": "Denmark", "do": "Dominican Republic",
        "ec": "Ecuador", "es": "Spain", "fr": "France", "gb": "United Kingdom", "gt": "Guatemala",
        "it": "Italy", "mx": "Mexico", "no": "Norway", "pe": "Peru", "se": "Sweden",
        "us": "United States", "latam": "Latin America" # Add others as needed from data
    }

    for region in regions:
        logging.info(f"--- Generating lgchannels playlist for region: {region} ---")
        epg_url = EPG_URL_TEMPLATE.replace('{region}', region)
        output_lines = [f'#EXTM3U url-tvg="{epg_url}"\n']
        channels_to_process = {}
        is_all_region = region.lower() == 'all'

        if is_all_region:
            for region_key, region_data in data.get('regions', {}).items():
                region_full_name = region_name_map.get(region_key, region_key.upper())
                for channel_key, channel_info in region_data.get('channels', {}).items():
                    unique_channel_id = f"{channel_key}-{region_key}"
                    # Add region info for grouping in 'all' list
                    channels_to_process[unique_channel_id] = {
                        **channel_info,
                        'region_code': region_key,
                        'group_title_override': region_full_name,
                        'original_id': channel_key
                    }
        else:
            region_data = data.get('regions', {}).get(region)
            if not region_data:
                logging.warning(f"Region '{region}' not found in PlutoTV data. Skipping.")
                continue
            for channel_key, channel_info in region_data.get('channels', {}).items():
                 channels_to_process[channel_key] = {
                     **channel_info,
                     'region_code': region,
                     'original_id': channel_key
                 }

        # Sort channels
        try:
             if sort == 'chno':
                 sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: int(channels_to_process[k].get('chno', 99999)))
             else: # Default to name sort
                 sorted_channel_ids = sorted(channels_to_process.keys(), key=lambda k: channels_to_process[k].get('name', '').lower())
        except Exception as e:
             logging.warning(f"Sorting failed for PlutoTV {region}, using default order. Error: {e}")
             sorted_channel_ids = list(channels_to_process.keys())

        # Build M3U entries
        for channel_id in sorted_channel_ids:
            channel = channels_to_process[channel_id]
            chno = channel.get('chno')
            name = channel.get('name', 'Unknown Channel')
            logo = channel.get('logo', '')
            group = channel.get('group_title_override') if is_all_region else channel.get('group', 'Uncategorized')
            original_id = channel.get('original_id', channel_id.split('-')[0]) # Fallback for safety
            tvg_id = original_id # Use the base ID for EPG matching across regions

            extinf = format_extinf(channel_id, tvg_id, chno, name, logo, group, name)
            stream_url = STREAM_URL_TEMPLATE.replace('{id}', original_id)
            output_lines.append(extinf)
            output_lines.append(stream_url + '\n')

        write_m3u_file(f"lgchannels_{region}.m3u", "".join(output_lines))
