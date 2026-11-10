# IIS Site Selection Feature - User Guide

## Overview
The Support Checklist backup tool now allows you to select which IIS sites to backup, preventing unnecessary backup of large or unused sites like "Default Web Site".

## How to Use

### Step 1: Open Configuration
1. Launch the Support Checklist application
2. Click the **Configuration** tab

### Step 2: Select IIS Sites
1. In the IIS Settings section, you'll see an "IIS Sites to Backup" table with your detected sites:
   - Default Web Site
   - SupportDashboard
   - (any other configured sites)

2. **Check only the sites you want to backup:**
   - Leave the checkbox **UNCHECKED** for Default Web Site (recommended to avoid large backups)
   - **CHECK** SupportDashboard
   - Check/uncheck other sites as needed

### Step 3: Save Configuration
1. Click the **Save IIS Settings** button
2. You should see a confirmation message: "IIS settings saved successfully."

### Step 4: Run Backup
1. Go to the **Dashboard** tab
2. Click **Start Checklist**
3. The backup will now **only backup the selected sites**

## Important Notes

- **Default Behavior:** If you don't explicitly select any sites, the backup will backup ALL detected sites (fallback to ensure nothing is missed)
- **Physical Paths:** 
  - Default Web Site: `C:\inetpub\wwwroot` (system default)
  - SupportDashboard: `D:\Metwally\Developing tools\SupportDashboard`
- **Recursion Protection:** The tool automatically skips backing up a site if its path is inside the backup destination folder
- **Configuration Persistence:** Your selections are saved to `config.json` and persist between app restarts

## Troubleshooting

### Sites Not Showing in Table
- Click the **Refresh IIS Sites** button to reload the list from the system

### Still Backing Up Unwanted Sites
- Ensure you **Save IIS Settings** after making changes
- Wait for the confirmation message
- Check the config.json file to verify your selections were saved

### Slow Backups
- Default Web Site (`C:\inetpub\wwwroot`) contains system files that can be large
- **Always uncheck Default Web Site** unless you need to back it up
- Only select sites you actually use

## Technical Details

- **Configuration File:** `config/config.json`
- **Backup Filtering Logic:** 
  - If selected_sites list is NOT empty → backup only those sites
  - If selected_sites list IS empty → backup all detected sites
- **Site Names:** Matched by exact name as shown in IIS Manager
