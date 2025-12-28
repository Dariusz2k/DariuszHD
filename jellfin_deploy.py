#!/usr/bin/env python3
"""
Jellyfin Integration for HomeRun Clone
Sets up M3U playlist and XMLTV guide for Jellyfin Live TV
"""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import requests
import os

class JellyfinIntegration:
    def __init__(self, base_url="http://192.168.1.171:5000"):
        self.base_url = base_url
        self.streaming_url = "http://192.168.1.171:8000"
        self.channels_file = "/opt/homerun-clone/config/channels.json"
        self.m3u_file = "/opt/homerun-clone/jellyfin_channels.m3u"
        self.xmltv_file = "/opt/homerun-clone/jellyfin_guide.xml"
        
    def load_channels(self):
        """Load channels from config"""
        try:
            with open(self.channels_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
    
    def generate_m3u_playlist(self):
        """Generate M3U playlist for Jellyfin"""
        channels = self.load_channels()
        
        m3u_content = "#EXTM3U\n"
        
        for channel_num, channel_info in channels.items():
            name = channel_info.get('name', f'Channel {channel_num}')
            # Create streaming URL for each channel
            stream_url = f"{self.streaming_url}/stream/{channel_num}"
            
            m3u_content += f'#EXTINF:-1 tvg-id="{channel_num}" tvg-name="{name}" tvg-logo="",{name}\n'
            m3u_content += f'{stream_url}\n'
        
        with open(self.m3u_file, 'w') as f:
            f.write(m3u_content)
        
        print(f"✓ M3U playlist generated: {self.m3u_file}")
        return self.m3u_file
    
    def generate_xmltv_guide(self):
        """Generate basic XMLTV guide for Jellyfin"""
        channels = self.load_channels()
        
        # Create root element
        tv = ET.Element("tv")
        tv.set("source-info-url", "homerun-clone")
        tv.set("generator-info-name", "HomeRun Clone")
        
        # Add channels
        for channel_num, channel_info in channels.items():
            channel_elem = ET.SubElement(tv, "channel")
            channel_elem.set("id", channel_num)
            
            display_name = ET.SubElement(channel_elem, "display-name")
            display_name.text = channel_info.get('name', f'Channel {channel_num}')
        
        # Add basic program info (placeholder)
        now = datetime.now()
        for channel_num in channels.keys():
            for hour in range(24):
                start_time = (now + timedelta(hours=hour)).strftime("%Y%m%d%H%M%S %z")
                stop_time = (now + timedelta(hours=hour+1)).strftime("%Y%m%d%H%M%S %z")
                
                programme = ET.SubElement(tv, "programme")
                programme.set("start", start_time)
                programme.set("stop", stop_time)
                programme.set("channel", channel_num)
                
                title = ET.SubElement(programme, "title")
                title.text = f"Live TV - Channel {channel_num}"
                
                desc = ET.SubElement(programme, "desc")
                desc.text = "Live television broadcast"
        
        # Write XML
        tree = ET.ElementTree(tv)
        tree.write(self.xmltv_file, encoding='utf-8', xml_declaration=True)
        
        print(f"✓ XMLTV guide generated: {self.xmltv_file}")
        return self.xmltv_file
    
    def setup_jellyfin_integration(self):
        """Setup complete Jellyfin integration"""
        print("Setting up Jellyfin integration...")
        
        # Generate files
        m3u_file = self.generate_m3u_playlist()
        xmltv_file = self.generate_xmltv_guide()
        
        print("\n" + "="*50)
        print("Jellyfin Live TV Setup Instructions:")
        print("="*50)
        print("1. Open Jellyfin Admin Dashboard")
        print("2. Go to Live TV section")
        print("3. Add TV Source:")
        print(f"   - Type: M3U Tuner")
        print(f"   - File or URL: {m3u_file}")
        print("4. Add Guide Source:")
        print(f"   - Type: XMLTV")
        print(f"   - File or URL: {xmltv_file}")
        print("5. Map channels in the Live TV setup")
        print("\nAlternatively, use these URLs if Jellyfin can access them:")
        print(f"   - M3U URL: http://192.168.1.171:5000/jellyfin/channels.m3u")
        print(f"   - XMLTV URL: http://192.168.1.171:5000/jellyfin/guide.xml")
        
        return True

if __name__ == "__main__":
    integration = JellyfinIntegration()
    integration.setup_jellyfin_integration()
