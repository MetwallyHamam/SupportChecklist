#!/usr/bin/env python3
"""Test script to verify IIS site selection workflow."""

import json
from pathlib import Path
from utils.config_manager import ConfigManager
from modules.iis_manager import IISManager
from modules.checklist_runner import ChecklistRunner

def main():
    # Step 1: Load config and show initial state
    print("=" * 60)
    print("STEP 1: Load config and show initial state")
    print("=" * 60)
    config_mgr = ConfigManager()
    config_mgr.load_config()
    print(f"Initial selected_sites: {config_mgr.get('iis', 'selected_sites', [])}")

    # Step 2: Simulate user selecting only "SupportDashboard"
    print("\n" + "=" * 60)
    print("STEP 2: Simulate user selecting only 'SupportDashboard'")
    print("=" * 60)
    config_mgr.set("iis", "selected_sites", ["SupportDashboard"])
    config_mgr.save_config()
    print("Saved selected_sites: ['SupportDashboard']")

    # Step 3: Reload config to verify persistence
    print("\n" + "=" * 60)
    print("STEP 3: Reload config to verify persistence")
    print("=" * 60)
    config_mgr2 = ConfigManager()
    config_mgr2.load_config()
    saved_sites = config_mgr2.get('iis', 'selected_sites', [])
    print(f"Reloaded selected_sites: {saved_sites}")

    # Step 4: Check what backup_iis would do with this config
    print("\n" + "=" * 60)
    print("STEP 4: Simulate backup_iis filtering")
    print("=" * 60)
    sites = IISManager().detect_sites()
    selected_site_names = set(config_mgr2.get("iis", "selected_sites", []))
    print(f"All detected sites: {[s.name for s in sites]}")
    print(f"Selected sites: {selected_site_names}")

    if selected_site_names:
        sites_to_backup = [s for s in sites if s.name in selected_site_names]
        print(f"FILTERED - Will backup: {[s.name for s in sites_to_backup]}")
    else:
        print(f"FALLBACK - Will backup ALL: {[s.name for s in sites]}")

    # Verify config file contents
    print("\n" + "=" * 60)
    print("STEP 5: Verify config.json file contents")
    print("=" * 60)
    config_file = Path(__file__).resolve().parent / "config" / "config.json"
    with open(config_file) as f:
        config_data = json.load(f)
        print(f"File contents - selected_sites: {config_data['iis'].get('selected_sites', [])}")

if __name__ == "__main__":
    main()
