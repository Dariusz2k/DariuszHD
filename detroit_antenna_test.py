#!/usr/bin/env python3
"""
Detroit-specific antenna test for actual broadcast frequencies
"""

import subprocess
import time
import sys

def test_detroit_frequency(adapter, rf_channel, frequency, virtual_channel, call_sign, network):
    """Test specific Detroit station"""
    try:
        print(f"Testing {virtual_channel} {call_sign} ({network}) on RF {rf_channel} ({frequency/1000000:.0f} MHz)... ", end="", flush=True)
        
        # Tune to frequency
        tune_cmd = ["dvb-fe-tool", "-a", str(adapter), "-f", str(frequency)]
        tune_result = subprocess.run(tune_cmd, capture_output=True, text=True, timeout=10)
        
        if tune_result.returncode != 0:
            print("❌ Tune failed")
            return False
        
        # Wait for lock
        time.sleep(3)
        
        # Check status
        status_cmd = ["dvb-fe-tool", "-a", str(adapter), "--get-status"]
        status_result = subprocess.run(status_cmd, capture_output=True, text=True, timeout=10)
        
        if status_result.returncode != 0:
            print("❌ Status failed")
            return False
        
        # Parse status for signal indicators
        status_text = status_result.stdout.upper()
        
        signal_indicators = ['HAS_SIGNAL', 'HAS_CARRIER', 'HAS_VITERBI', 'HAS_SYNC', 'HAS_LOCK']
        detected_indicators = [indicator for indicator in signal_indicators if indicator in status_text]
        
        if detected_indicators:
            print(f"✅ STRONG SIGNAL! ({', '.join(detected_indicators)})")
            return True
        else:
            print("❌ No signal")
            # Show actual status for debugging
            if status_result.stdout.strip():
                print(f"\n      Status: {status_result.stdout.strip()}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_detroit_stations(adapter=0):
    """Test all major Detroit TV stations"""
    print(f"Testing Detroit TV stations on DVB adapter {adapter}")
    print("=" * 60)
    
    # Detroit area TV stations with actual RF frequencies
    detroit_stations = [
        # RF_Ch, Frequency, Virtual, Call, Network
        (4, 69000000, "4.1", "WDIV", "NBC"),
        (7, 177000000, "2.1", "WJBK", "FOX"), 
        (14, 473000000, "50.1", "WKBD", "CW"),
        (16, 485000000, "56.1", "WDWB", "MyNet"),
        (26, 545000000, "62.1", "WTVS", "PBS"),
        (41, 647000000, "7.1", "WXYZ", "ABC"),
        
        # Additional Detroit area stations
        (31, 581000000, "20.1", "WMYD", "MyNet"),
        (44, 659000000, "47.1", "WMYD-DT2", "Bounce"),
    ]
    
    signals_found = 0
    strong_stations = []
    
    for rf_ch, freq, virtual, call, network in detroit_stations:
        if test_detroit_frequency(adapter, rf_ch, freq, virtual, call, network):
            signals_found += 1
            strong_stations.append(f"{virtual} {call} ({network})")
    
    print(f"\n📊 Detroit TV Reception Results:")
    print(f"   Found {signals_found} out of {len(detroit_stations)} major stations")
    
    if strong_stations:
        print(f"\n✅ Receiving these Detroit stations:")
        for station in strong_stations:
            print(f"   📺 {station}")
            
        print(f"\n🎉 SUCCESS! Your antenna is working in Detroit!")
        print(f"📍 Antenna appears to be pointed correctly toward Detroit broadcast towers")
        return True
    else:
        print(f"\n❌ No Detroit TV stations detected")
        print(f"\n🔍 Troubleshooting for Detroit area:")
        print(f"   📡 Detroit towers are ~15-20 miles from downtown")
        print(f"   📍 Point antenna toward northwest (Detroit towers)")
        print(f"   🏗️ Major towers are in Southfield/Oak Park area")
        print(f"   📶 Try different antenna heights/positions")
        return False

if __name__ == "__main__":
    adapter = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    
    print("🏙️  DETROIT TV MARKET ANTENNA TEST")
    print("=" * 50)
    
    success = test_detroit_stations(adapter)
    
    if not success and adapter == 0:
        print("\n🔄 Trying second tuner...")
        success = test_detroit_stations(1)
    
    if success:
        print(f"\n✅ Ready for Detroit channel scanning!")
        print(f"   Run: python3 /opt/homerun-clone/dvb_channel_scanner.py --full")
    else:
        print(f"\n📡 ANTENNA SETUP NEEDED")
        print(f"   🧭 Point antenna northwest toward Southfield/Detroit") 
        print(f"   📏 Detroit towers are 15-25 miles from most suburbs")
        print(f"   📶 Try higher antenna placement")
        
    sys.exit(0 if success else 1)
