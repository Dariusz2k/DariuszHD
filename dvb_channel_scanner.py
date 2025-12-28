#!/usr/bin/env python3
"""
DVB Channel Scanner for Hauppauge WinTV-dualHD
"""

import subprocess
import json
import re
import time
from datetime import datetime
from pathlib import Path

class DVBChannelScanner:
    def __init__(self):
        self.config_dir = "/opt/homerun-clone/config"
        self.channels_file = f"{self.config_dir}/channels.json"
        self.m3u_file = f"{self.config_dir}/channels.m3u"
        self.scan_results_file = f"{self.config_dir}/dvb_scan_results.json"
        
    def find_dvb_adapters(self):
        """Find available DVB adapters"""
        dvb_path = Path("/dev/dvb")
        if not dvb_path.exists():
            return []
        
        adapters = []
        for adapter_dir in dvb_path.glob("adapter*"):
            adapter_num = adapter_dir.name.replace("adapter", "")
            frontend_path = adapter_dir / "frontend0"
            if frontend_path.exists():
                adapters.append(int(adapter_num))
        
        return sorted(adapters)
    
    def test_frontend(self, adapter_num):
        """Test if frontend is working"""
        try:
            cmd = f"dvb-fe-tool -a {adapter_num}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except:
            return False
    
    def scan_channels_wscan(self, adapter_num=0):
        """Scan channels using w_scan"""
        print(f"Scanning channels on adapter {adapter_num} using w_scan...")
        
        try:
            # Try w-scan-cpp first, then w_scan
            for cmd_base in ["w_scan_cpp", "w_scan"]:
                try:
                    # US ATSC terrestrial scan
                    cmd = [
                        cmd_base,
                        "-f", "a",           # ATSC terrestrial
                        "-c", "US",          # Country: US
                        "-a", str(adapter_num),  # Adapter number
                        "-C", "UTF-8",       # Character encoding
                        "-X"                 # Generate initial tuning data
                    ]
                    
                    print(f"Running: {' '.join(cmd)}")
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    
                    if result.returncode == 0:
                        print("✓ Channel scan completed successfully!")
                        return self.parse_wscan_output(result.stdout)
                    else:
                        print(f"⚠ {cmd_base} failed: {result.stderr}")
                        continue
                        
                except FileNotFoundError:
                    print(f"⚠ {cmd_base} not found")
                    continue
            
            print("❌ No working w_scan tool found")
            return {}
            
        except Exception as e:
            print(f"❌ Scan error: {e}")
            return {}
    
    def parse_wscan_output(self, output):
        """Parse w_scan output to extract channels"""
        channels = {}
        lines = output.strip().split('\n')
        
        for line in lines:
            # Look for channel lines (simplified parsing)
            if ':' in line and any(x in line for x in ['MHz', 'kHz']):
                parts = line.split(':')
                if len(parts) >= 3:
                    try:
                        # Extract basic info
                        freq_part = parts[0].strip()
                        name_part = parts[2].strip() if len(parts) > 2 else "Unknown"
                        
                        # Generate channel ID
                        channel_id = f"ch_{len(channels) + 1}"
                        
                        channels[channel_id] = {
                            "name": name_part[:50],  # Limit name length
                            "frequency": freq_part,
                            "scan_date": datetime.now().isoformat(),
                            "type": "ATSC",
                            "raw_data": line
                        }
                        
                    except Exception as e:
                        print(f"Parse error for line: {line} - {e}")
                        continue
        
        return channels
    
    def quick_scan_popular_frequencies(self, adapter_num=0):
        """Quick scan using dvbv5-scan with known frequencies"""
        print(f"Quick scan on adapter {adapter_num}...")
        
        # Common US ATSC frequencies (in Hz)
        frequencies = {
            "Ch 7": 177000000,
            "Ch 9": 189000000, 
            "Ch 11": 201000000,
            "Ch 13": 213000000,
            "Ch 20": 509000000,
            "Ch 25": 539000000
        }
        
        found_channels = {}
        
        for ch_name, freq in frequencies.items():
            print(f"Testing {ch_name} ({freq/1000000:.0f} MHz)... ", end="", flush=True)
            
            try:
                # Use dvbv5-scan to test frequency
                cmd = [
                    "dvbv5-scan",
                    "-a", str(adapter_num),
                    "-f", str(freq),
                    "-I", "1",  # Timeout 1 second
                    "/dev/null"
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0 or "Lock" in result.stderr:
                    print("FOUND!")
                    found_channels[f"ch_{len(found_channels)}"] = {
                        "name": ch_name,
                        "frequency": str(freq),
                        "signal_strength": 75,  # Assume good if locked
                        "scan_date": datetime.now().isoformat(),
                        "type": "ATSC"
                    }
                else:
                    print("No signal")
                    
            except Exception as e:
                print(f"Error: {e}")
        
        return found_channels
    
    def save_channels(self, channels):
        """Save channels to JSON file"""
        try:
            Path(self.config_dir).mkdir(parents=True, exist_ok=True)
            
            with open(self.channels_file, 'w') as f:
                json.dump(channels, f, indent=2, sort_keys=True)
            
            print(f"✓ Saved {len(channels)} channels to {self.channels_file}")
            
            # Also save detailed scan results
            scan_results = {
                "scan_date": datetime.now().isoformat(),
                "scanner_type": "DVB",
                "channels_found": len(channels),
                "channels": channels
            }
            
            with open(self.scan_results_file, 'w') as f:
                json.dump(scan_results, f, indent=2)
                
        except Exception as e:
            print(f"❌ Error saving channels: {e}")
    
    def generate_m3u_playlist(self, channels):
        """Generate M3U playlist for streaming"""
        try:
            m3u_content = "#EXTM3U\n"
            
            for ch_id, ch_info in channels.items():
                name = ch_info.get('name', ch_id)
                # Create streaming URL (you'll need to implement the actual streaming)
                stream_url = f"http://192.168.1.171:8000/stream/{ch_id}"
                
                m3u_content += f'#EXTINF:-1 tvg-id="{ch_id}" tvg-name="{name}",{name}\n'
                m3u_content += f'{stream_url}\n'
            
            with open(self.m3u_file, 'w') as f:
                f.write(m3u_content)
            
            print(f"✓ M3U playlist saved to {self.m3u_file}")
            
        except Exception as e:
            print(f"❌ Error generating M3U: {e}")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='DVB Channel Scanner for Hauppauge')
    parser.add_argument('--adapter', type=int, default=0, help='DVB adapter number (0 or 1)')
    parser.add_argument('--quick', action='store_true', help='Quick scan of popular frequencies')
    parser.add_argument('--full', action='store_true', help='Full w_scan channel scan')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Hauppauge WinTV-dualHD DVB Channel Scanner")
    print("=" * 60)
    
    scanner = DVBChannelScanner()
    
    # Find DVB adapters
    adapters = scanner.find_dvb_adapters()
    print(f"Found DVB adapters: {adapters}")
    
    if not adapters:
        print("❌ No DVB adapters found!")
        return 1
    
    if args.adapter not in adapters:
        print(f"❌ Adapter {args.adapter} not found. Available: {adapters}")
        return 1
    
    print(f"Using DVB adapter {args.adapter}")
    
    # Test frontend
    if not scanner.test_frontend(args.adapter):
        print(f"⚠ Frontend {args.adapter} may not be ready")
    else:
        print(f"✓ Frontend {args.adapter} is working")
    
    print()
    
    # Run scan
    channels = {}
    try:
        if args.quick:
            channels = scanner.quick_scan_popular_frequencies(args.adapter)
        elif args.full:
            channels = scanner.scan_channels_wscan(args.adapter)
        else:
            # Default: try quick scan first
            print("Running quick scan first...")
            channels = scanner.quick_scan_popular_frequencies(args.adapter)
            
            if not channels:
                print("No channels found in quick scan, trying full scan...")
                channels = scanner.scan_channels_wscan(args.adapter)
        
        if channels:
            scanner.save_channels(channels)
            scanner.generate_m3u_playlist(channels)
            
            print(f"\n✅ Found {len(channels)} channels:")
            print("-" * 40)
            for ch_id, ch_info in channels.items():
                name = ch_info.get('name', 'Unknown')
                freq = ch_info.get('frequency', 'Unknown')
                print(f"  {ch_id}: {name} ({freq})")
                
        else:
            print("❌ No channels found. Try:")
            print("  - Check antenna connection")
            print("  - Try different adapter (--adapter 1)")
            print("  - Run full scan (--full)")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\nScan interrupted by user")
        return 1
    except Exception as e:
        print(f"❌ Scan failed: {e}")
        return 1

if __name__ == "__main__":
    exit(main())
