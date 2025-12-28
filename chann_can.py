#!/usr/bin/env python3
"""
Enhanced Channel Scanner for HomeRun Clone
Automatically detects and populates channels with frequency mapping
"""

import subprocess
import json
import re
import time
import threading
from datetime import datetime
from pathlib import Path

class ChannelScanner:
    def __init__(self, device_path="/dev/video0", config_dir="/opt/homerun-clone/config"):
        self.device_path = device_path
        self.config_dir = config_dir
        self.channels_file = f"{config_dir}/channels.json"
        self.scan_results_file = f"{config_dir}/scan_results.json"
        self.channels = {}
        self.scan_progress = 0
        self.scan_status = "idle"
        self.scan_thread = None
        
        # US ATSC frequency table (MHz to Hz conversion)
        self.us_frequency_table = {
            # VHF Low Band (54-88 MHz)
            2: 57000000,   # Channel 2: 54-60 MHz (center 57)
            3: 63000000,   # Channel 3: 60-66 MHz (center 63)
            4: 69000000,   # Channel 4: 66-72 MHz (center 69)
            5: 79000000,   # Channel 5: 76-82 MHz (center 79)
            6: 85000000,   # Channel 6: 82-88 MHz (center 85)
            
            # VHF High Band (174-216 MHz)
            7: 177000000,   # Channel 7: 174-180 MHz
            8: 183000000,   # Channel 8: 180-186 MHz
            9: 189000000,   # Channel 9: 186-192 MHz
            10: 195000000,  # Channel 10: 192-198 MHz
            11: 201000000,  # Channel 11: 198-204 MHz
            12: 207000000,  # Channel 12: 204-210 MHz
            13: 213000000,  # Channel 13: 210-216 MHz
            
            # UHF Band (470-806 MHz)
            14: 473000000,  15: 479000000,  16: 485000000,  17: 491000000,
            18: 497000000,  19: 503000000,  20: 509000000,  21: 515000000,
            22: 521000000,  23: 527000000,  24: 533000000,  25: 539000000,
            26: 545000000,  27: 551000000,  28: 557000000,  29: 563000000,
            30: 569000000,  31: 575000000,  32: 581000000,  33: 587000000,
            34: 593000000,  35: 599000000,  36: 605000000,  37: 611000000,
            38: 617000000,  39: 623000000,  40: 629000000,  41: 635000000,
            42: 641000000,  43: 647000000,  44: 653000000,  45: 659000000,
            46: 665000000,  47: 671000000,  48: 677000000,  49: 683000000,
            50: 689000000,  51: 695000000,  52: 701000000,  53: 707000000,
            54: 713000000,  55: 719000000,  56: 725000000,  57: 731000000,
            58: 737000000,  59: 743000000,  60: 749000000,  61: 755000000,
            62: 761000000,  63: 767000000,  64: 773000000,  65: 779000000,
            66: 785000000,  67: 791000000,  68: 797000000,  69: 803000000
        }
    
    def load_existing_channels(self):
        """Load existing channel configuration"""
        try:
            if Path(self.channels_file).exists():
                with open(self.channels_file, 'r') as f:
                    self.channels = json.load(f)
                print(f"Loaded {len(self.channels)} existing channels")
            else:
                self.channels = {}
        except Exception as e:
            print(f"Error loading channels: {e}")
            self.channels = {}
    
    def save_channels(self):
        """Save channel configuration"""
        try:
            Path(self.config_dir).mkdir(parents=True, exist_ok=True)
            with open(self.channels_file, 'w') as f:
                json.dump(self.channels, f, indent=2, sort_keys=True)
            print(f"Saved {len(self.channels)} channels to {self.channels_file}")
        except Exception as e:
            print(f"Error saving channels: {e}")
    
    def save_scan_results(self, results):
        """Save detailed scan results for debugging"""
        try:
            with open(self.scan_results_file, 'w') as f:
                json.dump(results, f, indent=2)
        except Exception as e:
            print(f"Error saving scan results: {e}")
    
    def check_device_availability(self):
        """Check if the tuner device is available"""
        if not Path(self.device_path).exists():
            print(f"Error: Device {self.device_path} not found")
            return False
        
        try:
            # Test basic v4l2 access
            result = subprocess.run(
                f"v4l2-ctl -d {self.device_path} --get-tuner",
                shell=True, capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Device check failed: {e}")
            return False
    
    def get_signal_strength(self, frequency):
        """Get signal strength for a specific frequency"""
        try:
            # Tune to frequency
            tune_cmd = f"v4l2-ctl -d {self.device_path} --set-freq={frequency}"
            subprocess.run(tune_cmd, shell=True, capture_output=True, timeout=5)
            
            # Give tuner time to lock
            time.sleep(1)
            
            # Get tuner status
            status_cmd = f"v4l2-ctl -d {self.device_path} --get-tuner"
            result = subprocess.run(status_cmd, shell=True, capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                # Parse signal strength from output
                output = result.stdout
                
                # Look for signal strength indicators
                signal_match = re.search(r'signal:\s*(\d+)', output, re.IGNORECASE)
                if signal_match:
                    return int(signal_match.group(1))
                
                # Alternative parsing - look for quality indicators
                if 'has signal' in output.lower() or 'locked' in output.lower():
                    return 75  # Assume good signal if locked
                elif 'no signal' in output.lower():
                    return 0
                else:
                    return 50  # Unknown but responsive
            
            return 0
            
        except Exception as e:
            print(f"Signal check error for {frequency}: {e}")
            return 0
    
    def detect_subchannel_programs(self, main_frequency):
        """Detect digital subchannels using ffprobe"""
        try:
            # Use ffprobe to analyze the stream
            cmd = [
                'timeout', '10',  # Limit analysis time
                'ffprobe',
                '-f', 'v4l2',
                '-i', self.device_path,
                '-show_programs',
                '-v', 'quiet',
                '-print_format', 'json'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                    programs = data.get('programs', [])
                    return len(programs)
                except json.JSONDecodeError:
                    pass
            
            return 1  # Default to 1 program if detection fails
            
        except Exception as e:
            print(f"Program detection error: {e}")
            return 1
    
    def scan_frequency_range(self, start_channel=2, end_channel=69, signal_threshold=20):
        """Scan a range of channels for signals"""
        found_channels = {}
        total_channels = end_channel - start_channel + 1
        current_progress = 0
        
        print(f"Scanning channels {start_channel}-{end_channel}...")
        print(f"Signal threshold: {signal_threshold}%")
        print("-" * 60)
        
        for channel_num in range(start_channel, end_channel + 1):
            if channel_num not in self.us_frequency_table:
                continue
            
            frequency = self.us_frequency_table[channel_num]
            current_progress += 1
            self.scan_progress = int((current_progress / total_channels) * 100)
            
            print(f"Scanning channel {channel_num} ({frequency/1000000:.1f} MHz)... ", end="", flush=True)
            
            signal_strength = self.get_signal_strength(frequency)
            
            if signal_strength >= signal_threshold:
                print(f"FOUND! (Signal: {signal_strength}%)")
                
                # Detect digital subchannels
                num_programs = self.detect_subchannel_programs(frequency)
                
                # Add main channel
                main_channel = f"{channel_num}.1"
                found_channels[main_channel] = {
                    "name": f"Channel {channel_num}.1",
                    "frequency": str(frequency),
                    "rf_channel": channel_num,
                    "signal_strength": signal_strength,
                    "scan_date": datetime.now().isoformat(),
                    "type": "digital"
                }
                
                # Add additional subchannels if detected
                for sub in range(2, min(num_programs + 1, 6)):  # Limit to reasonable number
                    sub_channel = f"{channel_num}.{sub}"
                    found_channels[sub_channel] = {
                        "name": f"Channel {channel_num}.{sub}",
                        "frequency": str(frequency),
                        "rf_channel": channel_num,
                        "signal_strength": signal_strength,
                        "scan_date": datetime.now().isoformat(),
                        "type": "digital",
                        "subchannel": sub
                    }
                
            else:
                print(f"No signal ({signal_strength}%)")
            
            # Small delay between channels
            time.sleep(0.5)
        
        return found_channels
    
    def enhanced_channel_identification(self, channel_data):
        """Try to identify actual channel names using stream analysis"""
        identified_channels = {}
        
        print("\nAttempting to identify channel names...")
        
        for channel_id, channel_info in channel_data.items():
            frequency = int(channel_info['frequency'])
            rf_channel = channel_info['rf_channel']
            
            try:
                # Tune to the channel
                self.get_signal_strength(frequency)
                time.sleep(2)  # Allow tuner to stabilize
                
                # Use ffprobe to get stream metadata
                cmd = [
                    'timeout', '8',
                    'ffprobe',
                    '-f', 'v4l2',
                    '-i', self.device_path,
                    '-show_format',
                    '-v', 'quiet',
                    '-print_format', 'json'
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0 and result.stdout:
                    try:
                        data = json.loads(result.stdout)
                        format_info = data.get('format', {})
                        tags = format_info.get('tags', {})
                        
                        # Look for station identification in metadata
                        station_name = None
                        for tag_name in ['title', 'service_name', 'station_name', 'channel']:
                            if tag_name in tags:
                                station_name = tags[tag_name]
                                break
                        
                        if station_name and len(station_name) > 1:
                            channel_info['name'] = station_name
                            print(f"  {channel_id}: {station_name}")
                        else:
                            print(f"  {channel_id}: No metadata found")
                            
                    except json.JSONDecodeError:
                        print(f"  {channel_id}: Metadata parse error")
                
            except Exception as e:
                print(f"  {channel_id}: ID error - {e}")
            
            identified_channels[channel_id] = channel_info
        
        return identified_channels
    
    def perform_full_scan(self, signal_threshold=20, identify_names=True):
        """Perform a complete channel scan"""
        self.scan_status = "running"
        self.scan_progress = 0
        scan_results = {}
        
        try:
            print("="*60)
            print("HomeRun Clone - Channel Scanner")
            print("="*60)
            print(f"Device: {self.device_path}")
            print(f"Signal Threshold: {signal_threshold}%")
            print(f"Identify Names: {identify_names}")
            print("="*60)
            
            # Check device availability
            if not self.check_device_availability():
                self.scan_status = "error"
                return False
            
            # Load existing channels
            self.load_existing_channels()
            backup_channels = self.channels.copy()
            
            # Scan for channels
            found_channels = self.scan_frequency_range(
                signal_threshold=signal_threshold
            )
            
            if not found_channels:
                print("\nNo channels found! Check antenna connection and signal threshold.")
                self.scan_status = "complete"
                return False
            
            print(f"\nFound {len(found_channels)} channels!")
            
            # Attempt to identify channel names
            if identify_names:
                found_channels = self.enhanced_channel_identification(found_channels)
            
            # Merge with existing channels (preserve custom names)
            for channel_id, channel_info in found_channels.items():
                if channel_id in self.channels:
                    # Preserve custom name if it exists
                    if 'custom_name' in self.channels[channel_id]:
                        channel_info['name'] = self.channels[channel_id]['custom_name']
                    # Update other info
                    channel_info.update({
                        'last_scan': datetime.now().isoformat(),
                        'signal_strength': channel_info['signal_strength']
                    })
                
                self.channels[channel_id] = channel_info
            
            # Save results
            self.save_channels()
            
            scan_results = {
                "scan_date": datetime.now().isoformat(),
                "channels_found": len(found_channels),
                "signal_threshold": signal_threshold,
                "device": self.device_path,
                "channels": found_channels
            }
            
            self.save_scan_results(scan_results)
            
            print("\n" + "="*60)
            print("SCAN COMPLETE!")
            print("="*60)
            print(f"Total channels found: {len(found_channels)}")
            print(f"Channels saved to: {self.channels_file}")
            print(f"Scan results saved to: {self.scan_results_file}")
            
            # Display channel list
            print("\nChannels found:")
            print("-" * 40)
            for channel_id in sorted(found_channels.keys(), key=lambda x: float(x)):
                channel = found_channels[channel_id]
                print(f"{channel_id:>6} - {channel['name']:<25} (Signal: {channel['signal_strength']}%)")
            
            self.scan_status = "complete"
            return True
            
        except Exception as e:
            print(f"\nScan failed with error: {e}")
            self.scan_status = "error"
            return False
        finally:
            self.scan_progress = 100
    
    def quick_scan(self, popular_channels_only=True):
        """Quick scan of most popular channels"""
        if popular_channels_only:
            # Common US broadcast channels
            popular_channels = [2, 4, 5, 7, 9, 11, 13, 20, 25, 32]
            print("Performing quick scan of popular channels...")
            
            found = {}
            for ch in popular_channels:
                if ch in self.us_frequency_table:
                    freq = self.us_frequency_table[ch]
                    signal = self.get_signal_strength(freq)
                    if signal >= 20:  # Lower threshold for quick scan
                        found[f"{ch}.1"] = {
                            "name": f"Channel {ch}.1",
                            "frequency": str(freq),
                            "rf_channel": ch,
                            "signal_strength": signal,
                            "scan_date": datetime.now().isoformat(),
                            "type": "digital"
                        }
                        print(f"Found: Channel {ch} (Signal: {signal}%)")
            
            if found:
                self.channels.update(found)
                self.save_channels()
                return True
            
        return False
    
    def scan_in_background(self, signal_threshold=20):
        """Run scan in background thread"""
        def scan_worker():
            self.perform_full_scan(signal_threshold)
        
        if self.scan_thread and self.scan_thread.is_alive():
            print("Scan already in progress")
            return False
        
        self.scan_thread = threading.Thread(target=scan_worker)
        self.scan_thread.daemon = True
        self.scan_thread.start()
        return True
    
    def get_scan_status(self):
        """Get current scan status and progress"""
        return {
            "status": self.scan_status,
            "progress": self.scan_progress,
            "channels_found": len(self.channels)
        }

def main():
    """Command line interface for channel scanner"""
    import argparse
    
    parser = argparse.ArgumentParser(description='HomeRun Clone Channel Scanner')
    parser.add_argument('--device', default='/dev/video0', help='Video device path')
    parser.add_argument('--threshold', type=int, default=20, help='Signal strength threshold (0-100)')
    parser.add_argument('--quick', action='store_true', help='Quick scan of popular channels only')
    parser.add_argument('--no-identify', action='store_true', help='Skip channel name identification')
    parser.add_argument('--start-channel', type=int, default=2, help='Start channel number')
    parser.add_argument('--end-channel', type=int, default=69, help='End channel number')
    
    args = parser.parse_args()
    
    scanner = ChannelScanner(device_path=args.device)
    
    if args.quick:
        success = scanner.quick_scan()
    else:
        success = scanner.perform_full_scan(
            signal_threshold=args.threshold,
            identify_names=not args.no_identify
        )
    
    return 0 if success else 1

if __name__ == "__main__":
    exit(main())
