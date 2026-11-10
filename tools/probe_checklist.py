from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.iis_manager import IISManager
from modules.service_backup import ServiceBackup
from modules.sql_backup import SQLBackup
from utils.config_manager import ConfigManager

cm = ConfigManager()
print('Config backup_root:', cm.get('general','backup_root_folder','(not set)'))

print('\nIIS Sites:')
try:
    ims = IISManager()
    sites = ims.detect_sites()
    print('Detected', len(sites), 'sites')
    for s in sites:
        print('-', s.name, s.physical_path)
    apps = ims.list_applications()
    print('\nIIS Applications:', len(apps))
    for a in apps[:20]:
        print('-', a.get('site'), a.get('path'), '->', a.get('application_pool'))
    pools = ims.list_app_pools()
    print('\nApp Pools:', len(pools))
    for p in pools[:20]:
        print('-', p.get('name'), p.get('state'))
except Exception as e:
    print('IIS probe failed:', e)

print('\nServices:')
try:
    sb = ServiceBackup()
    services = sb.list_services()
    print('Detected', len(services), 'services')
    for svc in services[:20]:
        print('-', svc.name, '|', svc.display_name, '|', svc.executable_path, '|', svc.status)
except Exception as e:
    print('Service probe failed:', e)

print('\nDatabases:')
try:
    sqlcfg = cm.get_section('sql_server')
    sql = SQLBackup(server=str(sqlcfg.get('server_name','')), instance=str(sqlcfg.get('instance_name','')), database=str(sqlcfg.get('database_name','')), username=str(sqlcfg.get('username','')), password=str(sqlcfg.get('password','')), use_windows_auth=bool(sqlcfg.get('use_windows_auth', True)))
    dbs = sql.list_databases()
    print('Found', len(dbs), 'databases')
    for d in dbs[:50]:
        print('-', d.get('name'), d.get('state'))
except Exception as e:
    print('Database probe failed:', e)

print('\nProbe complete')
