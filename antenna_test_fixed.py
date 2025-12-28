#!/usr/bin/env python3
"""
Fixed antenna test for Hauppauge WinTV-dualHD
"""

import subprocess
import time
import sys
import re

def test_frequency(adapter, frequency, channel_name):
    """Test a specific frequency"""
    try:
        print(f"  Tuning to {frequency/1000000:.1f} MHz... ", end="", flush=True)
        
        # Tune to frequency
        tune_cmd = ["dvb-fe-tool", "-a", str(adapter), "-f", str(frequency)]
        tune_result = subprocess.run(tune_cmd, capture_output=True, text=True, timeout=10)
        
        if tune_result.returncode != 0:
            print("❌ Tune failed")
            return False
        
        # Wait for lock attempt
        time.sleep(2)
        
        # Check status
        status_cmd = ["dvb-fe-tool", "-a", str(adapter), "--get-status"]
        status_result = subprocess.run(status_cmd, capture_output=True, text=True, timeout=10)
        
        if status_result.returncode != 0:
            print("❌ Status check failed")
            return False
        
        # Parse status
        status_text = status_result.stdout.upper()
        has_signal = any(indicator in status_text for indicator in [
            'HAS_SIGNAL', 'HAS_CARRIER', 'HAS_LOCK', 'LOCKED'
        ])
        
        if has_signal:
            print("✅ SIGNAL DETECTED!")
            return True
        else:
            print("❌ No signal")
            return False
            
    except subprocess.TimeoutExpired:
        print("⚠ Timeout")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def comprehensive_antenna_test(adapter=0):
    """Test multiple frequencies to detect antenna"""
    print(f"Testing antenna on DVB adapter {adapter}")
    print("=" * 50)
    
    # Test frequencies for common US channels
    test_frequencies = [
        (57000000, "Channel 2 (VHF-Lo)"),
        (69000000, "Channel 4 (VHF-Lo)"),  
        (177000000, "Channel 7 (VHF-Hi)"),
        (189000000, "Channel 9 (VHF-Hi)"),
        (201000000, "Channel 11 (VHF-Hi)"),
        (213000000, "Channel 13 (VHF-Hi)"),
        (473000000, "Channel 14 (UHF)"),
        (479000000, "Channel 15 (UHF)"),
        (509000000, "Channel 20 (UHF)"),
        (539000000, "Channel 25 (UHF)"),
        (569000000, "Channel 30 (UHF)"),
        (611000000, "Channel 37 (UHF)"),
    ]
    
    signals_detected = 0
    
    for frequency, name in test_frequencies:
        if test_frequency(adapter, frequency, name):
            signals_detected += 1
    
    print(f"\n📊 Results: {signals_detected} signals detected out of {len(test_frequencies)} tested")
    
    if signals_detected > 0:
        print("✅ Antenna is connected and receiving signals!")
        return True
    else:
        print("❌ No signals detected on any frequency")
        print("\n🔍 Troubleshooting:")
        print("1. Check coax cable connection to Hauppauge device")
        print("2. Check antenna is properly positioned/oriented")
        print("3. Verify you're in a broadcast TV coverage area")
        return False

if __name__ == "__main__":
    adapter = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    
    print("Hauppauge WinTV-dualHD Antenna Test")
    print("=" * 40)
    
    # Test the specified adapter
    success = comprehensive_antenna_test(adapter)
    
    if not success and adapter == 0:
        print("\nTrying second tuner (adapter 1)...")
        success = comprehensive_antenna_test(1)
    
    if success:
        print(f"\n🎉 SUCCESS! Antenna working on adapter {adapter}")
        print("You can now run channel scanning.")
    else:
        print("\n❌ No antenna signal detected on either tuner")
        print("Please check your antenna setup.")
    
    sys.exit(0 if success else 1)
