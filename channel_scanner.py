#!/usr/bin/env python3
"""
Enhanced Channel Scanner for HomeRun Clone - Pi Version
Automatically detects and populates channels with frequency mapping
"""

import subprocess
import json
import re
import time
import threading
from datetime import datetime
from pathlib import Path
import os
import sys

class ChannelScanner:
    def __init__(self, device_path=None, config_dir="/opt/homerun-clone/config"):
        # Auto-detect device if not specified
        self.device_path = device_path or self.find_video_device()
        self.config_dir = config_dir
        self.channels_file = f"{config_dir}/channels.json"
        self.scan_results_file = f"{config_dir}/scan_results.json"
        self.channels = {}
        self.scan_progress = 0
        self.scan_status = "idle"
        
        # US ATSC frequency table (MHz to Hz conversion)
        self.us_frequency_table = {
            # VHF Low Band
            2: 57000000,   3: 63000000,   4: 69000000,   5: 79000000,   6: 85000000,
            # VHF High Band  
            7: 177000000,  8: 183000000,  9: 189000000,  10: 195000000, 11: 201000000,
            12: 207000000, 13: 213000000,
            # UHF Band
            14: 473000000, 15: 479000000, 16: 485000000, 17: 491000000, 18: 497000000,
            19: 503000000, 20: 509000000, 21: 515000000, 22: 521000000, 23: 527000000,
            24: 533000000, 25: 539000000, 26: 545000000, 27: 551000000, 28: 557000000,
            29: 563000000, 30: 569000000, 31: 575000000, 32: 581000000, 33: 587000000,
            34: 593000000, 35: 599000000, 36: 605000000
        }
    
    def find_video_device(self):
        """Auto-detect video device"""
        # Try common video device paths
        for i in range(10):
            device = f"/dev/video{i}"
            if Path(device).exists():
                try:
                    # Test if it's a tuner device
                    result = subprocess.run(
                        f"v4l2-ctl -d {device} --get-tuner 2>/dev/null",
                        shell=True, capture_output=True, timeout=3
                    )
                    if result.returncode == 0 and "tuner" in result.stdout.lower():
                        print(f"Found tuner device: {device}")
                        return device
                except:
                    continue
        
        # Fallback to /dev/video0
        return "/dev/video0"
    
    def check_device_availability(self):
        """Check if the tuner device is available"""
        if not Path(self.device_path).exists():
            print(f"Error: Device {self.device_path} not found")
            return False
        
        try:
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
            time.sleep(2)
            
            # Get tuner status
            status_cmd = f"v4l2-ctl -d {self.device_path} --get-tuner"
            result = subprocess.run(status_cmd, shell=True, capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                output = result.stdout
                
                # Look for signal strength indicators
                signal_match = re.search(r'signal:\s*(\d+)', output, re.IGNORECASE)
                if signal_match:
                    return int(signal_match.group(1))
                
                # Alternative parsing
                if 'has signal' in output.lower() or 'locked' in output.lower():
                    return 75
                elif 'no signal' in output.lower():
                    return 0
                else:
                    return 50
            
            return 0
            
        except Exception as e:
            print(f"Signal check error for {frequency}: {e}")
            return 0
    
    def save_channels(self):
        """Save channel configuration"""
        try:
            Path(self.config_dir).mkdir(parents=True, exist_ok=True)
            with open(self.channels_file, 'w') as f:
                json.dump(self.channels, f, indent=2, sort_keys=True)
            print(f"Saved {len(self.channels)} channels to {self.channels_file}")
        except Exception as e:
            print(f"Error saving channels: {e}")
    
    def quick_scan_popular_channels(self):
        """Quick scan of most popular US channels"""
        popular_channels = [2, 4, 5, 7, 9, 11, 13, 20, 25]
        found_channels = {}
        
        print("Quick scan of popular channels...")
        print("-" * 40)
        
        for channel_num in popular_channels:
            if channel_num not in self.us_frequency_table:
                continue
            
            frequency = self.us_frequency_table[channel_num]
            print(f"Testing channel {channel_num} ({frequency/1000000:.1f} MHz)... ", end="", flush=True)
            
            signal_strength = self.get_signal_strength(frequency)
            
            if signal_strength >= 15:  # Lower threshold for quick scan
                print(f"FOUND! (Signal: {signal_strength}%)")
                
                main_channel = f"{channel_num}.1"
                found_channels[main_channel] = {
                    "name": f"Channel {channel_num}.1",
                    "frequency": str(frequency),
                    "rf_channel": channel_num,
                    "signal_strength": signal_strength,
                    "scan_date": datetime.now().isoformat(),
                    "type": "digital"
                }
            else:
                print(f"No signal ({signal_strength}%)")
        
        return found_channels
    
    def perform_full_scan(self, start_channel=2, end_channel=36, signal_threshold=20):
        """Perform a complete channel scan"""
        print(f"Scanning channels {start_channel}-{end_channel} (threshold: {signal_threshold}%)...")
        print("-" * 60)
        
        found_channels = {}
        
        for channel_num in range(start_channel, end_channel + 1):
            if channel_num not in self.us_frequency_table:
                continue
            
            frequency = self.us_frequency_table[channel_num]
            print(f"Channel {channel_num:2d} ({frequency/1000000:6.1f} MHz): ", end="", flush=True)
            
            signal_strength = self.get_signal_strength(frequency)
            
            if signal_strength >= signal_threshold:
                print(f"FOUND! Signal: {signal_strength}%")
                
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
                
                # Add potential subchannels
                for sub in range(2, 5):
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
        
        return found_channels

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='HomeRun Clone Channel Scanner')
    parser.add_argument('--device', help='Video device path (auto-detected if not specified)')
    parser.add_argument('--threshold', type=int, default=20, help='Signal strength threshold')
    parser.add_argument('--quick', action='store_true', help='Quick scan of popular channels')
    parser.add_argument('--start', type=int, default=2, help='Start channel')
    parser.add_argument('--end', type=int, default=36, help='End channel') 
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("HomeRun Clone - Enhanced Channel Scanner")
    print("=" * 60)
    
    scanner = ChannelScanner(device_path=args.device)
    
    print(f"Using device: {scanner.device_path}")
    print(f"Config directory: {scanner.config_dir}")
    print()
    
    # Check device availability
    if not scanner.check_device_availability():
        print("❌ Device not available. Please check:")
        print("  - Hauppauge tuner is connected")
        print("  - Drivers are loaded (run: sudo modprobe em28xx em28xx-dvb)")
        print("  - Device permissions")
        return 1
    
    print("✓ Device is available")
    print()
    
    try:
        if args.quick:
            found_channels = scanner.quick_scan_popular_channels()
        else:
            found_channels = scanner.perform_full_scan(
                start_channel=args.start,
                end_channel=args.end, 
                signal_threshold=args.threshold
            )
        
        if found_channels:
            scanner.channels = found_channels
            scanner.save_channels()
            
            print(f"\n✅ Scan complete! Found {len(found_channels)} channels:")
            print("-" * 40)
            for ch_id in sorted(found_channels.keys(), key=lambda x: float(x)):
                ch_info = found_channels[ch_id]
                print(f"{ch_id:>6} - {ch_info['name']:<20} (Signal: {ch_info['signal_strength']}%)")
            
            print(f"\nChannels saved to: {scanner.channels_file}")
        else:
            print("\n❌ No channels found. Try:")
            print("  - Lower signal threshold (--threshold 10)")
            print("  - Check antenna connection")
            print("  - Different frequency range")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\nScan interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Scan failed: {e}")
        return 1

if __name__ == "__main__":
    exit(main())
