from __future__ import annotations
import asyncio, os, sqlite3, time
import base64, json, secrets, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field

DB = Path(os.getenv('GCP_PANEL_DB', 'panel.db'))
app = FastAPI(title='GCP IP 管理面板', version='0.1.0')
app.add_middleware(SessionMiddleware, secret_key=os.getenv('SESSION_SECRET', 'change-this-session-secret'))
monitor_task = None
aws_monitor_task = None
aws_quota_task = None
aws_terminating_instances = set()
aws_runtime_scan_lock = asyncio.Lock()

ADMIN_USER = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
CREDENTIALS_PATH = os.getenv('GCP_CREDENTIALS_PATH', '/etc/gcp-ip-panel/gcp-service-account.json')
AWS_CREDENTIALS_PATH = os.getenv('AWS_CREDENTIALS_PATH', '/etc/gcp-ip-panel/aws-credentials.json')
AWS_QUOTA_CODE = 'L-1216C47A'
AWS_QUOTA_NAME = 'EC2 标准型按需实例 vCPU'
AWS_REGION_LABELS = {
    'us-east-1': '美国 弗吉尼亚 (us-east-1)', 'us-west-2': '美国 俄勒冈 (us-west-2)',
    'us-east-2': '美国 俄亥俄 (us-east-2)', 'us-west-1': '美国 加州 (us-west-1)',
    'ap-east-1': '香港 (ap-east-1)', 'ap-east-2': '台湾 (ap-east-2)',
    'ap-northeast-3': '日本 大阪 (ap-northeast-3)', 'ap-northeast-1': '日本 东京 (ap-northeast-1)',
    'ap-northeast-2': '韩国 首尔 (ap-northeast-2)', 'ap-southeast-1': '新加坡 (ap-southeast-1)',
    'ap-southeast-2': '澳大利亚 悉尼 (ap-southeast-2)', 'ap-southeast-4': '澳大利亚 墨尔本 (ap-southeast-4)',
    'ap-south-1': '印度 孟买 (ap-south-1)', 'af-south-1': '南非 开普敦 (af-south-1)',
    'eu-west-1': '爱尔兰 (eu-west-1)', 'eu-west-2': '英国 伦敦 (eu-west-2)',
    'eu-central-1': '德国 法兰克福 (eu-central-1)', 'eu-north-1': '瑞典 斯德哥尔摩 (eu-north-1)',
    'me-central-1': '阿联酋 (me-central-1)', 'il-central-1': '以色列 (il-central-1)',
    'sa-east-1': '巴西 圣保罗 (sa-east-1)', 'ca-central-1': '加拿大 (ca-central-1)'
}
AWS_REGION_COUNTRIES = {
    'us-east-1': ('美国', '🇺🇸', 'US'), 'us-east-2': ('美国', '🇺🇸', 'US'),
    'us-west-1': ('美国', '🇺🇸', 'US'), 'us-west-2': ('美国', '🇺🇸', 'US'),
    'ca-central-1': ('加拿大', '🇨🇦', 'CA'), 'sa-east-1': ('巴西', '🇧🇷', 'BR'),
    'mx-central-1': ('墨西哥', '🇲🇽', 'MX'),
    'ap-east-1': ('中国香港', '🇭🇰', 'HK'), 'ap-east-2': ('中国台湾', '🇹🇼', 'TW'),
    'ap-northeast-1': ('日本', '🇯🇵', 'JP'), 'ap-northeast-2': ('韩国', '🇰🇷', 'KR'),
    'ap-northeast-3': ('日本', '🇯🇵', 'JP'), 'ap-south-1': ('印度', '🇮🇳', 'IN'),
    'ap-southeast-1': ('新加坡', '🇸🇬', 'SG'), 'ap-southeast-2': ('澳大利亚', '🇦🇺', 'AU'),
    'ap-southeast-3': ('印度尼西亚', '🇮🇩', 'ID'), 'ap-southeast-4': ('澳大利亚', '🇦🇺', 'AU'),
    'eu-west-1': ('爱尔兰', '🇮🇪', 'IE'), 'eu-west-2': ('英国', '🇬🇧', 'GB'),
    'eu-west-3': ('法国', '🇫🇷', 'FR'), 'eu-central-1': ('德国', '🇩🇪', 'DE'),
    'eu-central-2': ('瑞士', '🇨🇭', 'CH'), 'eu-north-1': ('瑞典', '🇸🇪', 'SE'),
    'eu-south-1': ('意大利', '🇮🇹', 'IT'), 'eu-south-2': ('西班牙', '🇪🇸', 'ES'),
    'af-south-1': ('南非', '🇿🇦', 'ZA'), 'me-central-1': ('阿联酋', '🇦🇪', 'AE'),
    'me-south-1': ('巴林', '🇧🇭', 'BH'), 'il-central-1': ('以色列', '🇮🇱', 'IL'),
}

def aws_region_info(region: str | None):
    """Return stable Chinese country/flag metadata for an AWS region."""
    region = (region or '').strip()
    country, flag, code = AWS_REGION_COUNTRIES.get(region, ('未知地区', '🌐', ''))
    return {
        'region': region,
        'region_name': AWS_REGION_LABELS.get(region) or (f'{country} ({region})' if region else '未指定区域'),
        'country_name': country,
        'country_flag': flag,
        'country_code': code,
    }
AWS_IMAGE_LABELS = {
    'ubuntu-20.04': ('Ubuntu', '20.04'),
    'ubuntu-22.04': ('Ubuntu', '22.04'),
    'ubuntu-24.04': ('Ubuntu', '24.04'),
    'debian-10': ('Debian', '10'),
    'debian-11': ('Debian', '11'),
    'debian-12': ('Debian', '12'),
    'debian-13': ('Debian', '13'),
    'windows-2019': ('Windows', '2019'),
    'windows-2022': ('Windows', '2022'),
}
AWS_TYPE_FALLBACK = [
    {'id': 't3.nano', 'vcpu': 2, 'memory_mib': 512, 'description': '2 vCPU, 0.5 GB 内存'},
    {'id': 't3.micro', 'vcpu': 2, 'memory_mib': 1024, 'description': '2 vCPU, 1.0 GB 内存'},
    {'id': 't3.small', 'vcpu': 2, 'memory_mib': 2048, 'description': '2 vCPU, 2.0 GB 内存'},
    {'id': 't3.medium', 'vcpu': 2, 'memory_mib': 4096, 'description': '2 vCPU, 4.0 GB 内存'},
    {'id': 't3.large', 'vcpu': 2, 'memory_mib': 8192, 'description': '2 vCPU, 8.0 GB 内存'},
    {'id': 't3.xlarge', 'vcpu': 4, 'memory_mib': 16384, 'description': '4 vCPU, 16.0 GB 内存'},
    {'id': 't3.2xlarge', 'vcpu': 8, 'memory_mib': 32768, 'description': '8 vCPU, 32.0 GB 内存'},
    {'id': 'm5.large', 'vcpu': 2, 'memory_mib': 8192, 'description': '2 vCPU, 8.0 GB 内存'},
    {'id': 'm5.xlarge', 'vcpu': 4, 'memory_mib': 16384, 'description': '4 vCPU, 16.0 GB 内存'},
    {'id': 'c6in.large', 'vcpu': 2, 'memory_mib': 4096, 'description': '2 vCPU, 4.0 GB 内存'},
    {'id': 'c6in.xlarge', 'vcpu': 4, 'memory_mib': 8192, 'description': '4 vCPU, 8.0 GB 内存'},
    {'id': 'c6in.2xlarge', 'vcpu': 8, 'memory_mib': 16384, 'description': '8 vCPU, 16.0 GB 内存'},
    {'id': 'c6in.4xlarge', 'vcpu': 16, 'memory_mib': 32768, 'description': '16 vCPU, 32.0 GB 内存'},
]
AWS_TYPE_FALLBACK_ARM = [
    {'id': 't4g.nano', 'vcpu': 2, 'memory_mib': 512, 'description': '2 vCPU, 0.5 GB 内存'},
    {'id': 't4g.micro', 'vcpu': 2, 'memory_mib': 1024, 'description': '2 vCPU, 1.0 GB 内存'},
    {'id': 't4g.small', 'vcpu': 2, 'memory_mib': 2048, 'description': '2 vCPU, 2.0 GB 内存'},
    {'id': 't4g.medium', 'vcpu': 2, 'memory_mib': 4096, 'description': '2 vCPU, 4.0 GB 内存'},
    {'id': 't4g.large', 'vcpu': 2, 'memory_mib': 8192, 'description': '2 vCPU, 8.0 GB 内存'},
    {'id': 't4g.xlarge', 'vcpu': 4, 'memory_mib': 16384, 'description': '4 vCPU, 16.0 GB 内存'},
]
AWS_IMAGE_NAME_PATTERNS = [
    'ubuntu/images/hvm-ssd*/ubuntu-focal-20.04-amd64-server-*',
    'ubuntu/images/hvm-ssd*/ubuntu-jammy-22.04-amd64-server-*',
    'ubuntu/images/hvm-ssd*/ubuntu-noble-24.04-amd64-server-*',
    'debian-10-amd64-*', 'debian-11-amd64-*', 'debian-12-amd64-*', 'debian-13-amd64-*',
    'Windows_Server-2019-English-Full-Base-*', 'Windows_Server-2022-English-Full-Base-*',
]

def logged_in(request: Request):
    if not request.session.get('authenticated'):
        raise HTTPException(401, '请先登录管理面板')

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get('authenticated'):
        return RedirectResponse('/')
    return HTMLResponse('''<!doctype html><meta charset="utf-8"><title>登录 GCP IP 管理面板</title><style>body{font:16px sans-serif;background:#f3f6fa;display:grid;place-items:center;height:100vh}.box{background:#fff;padding:30px;border-radius:12px;width:330px;box-shadow:0 4px 20px #0002}input,button{width:100%;padding:12px;margin:8px 0;box-sizing:border-box}button{background:#2878f0;color:#fff;border:0;border-radius:6px;cursor:pointer}.err{color:#d33}</style><div class="box"><h2>GCP IP 管理面板</h2><form method="post" action="/login"><input name="username" placeholder="管理员账号" required><input name="password" type="password" placeholder="管理员密码" required><button>登录</button></form></div>''')

@app.post('/login')
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if ADMIN_PASSWORD and secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(password, ADMIN_PASSWORD):
        request.session['authenticated'] = True
        return RedirectResponse('/', status_code=303)
    return HTMLResponse('<p>账号或密码错误。<a href="/login">返回</a></p>', status_code=401)

@app.post('/logout')
def logout(request: Request):
    request.session.clear()
    return RedirectResponse('/login', status_code=303)

@app.get('/credentials', response_class=HTMLResponse)
def credentials_page(request: Request):
    if not request.session.get('authenticated'):
        return RedirectResponse('/login')
    # 保留旧地址，统一回到主管理面板操作。
    return RedirectResponse('/')

@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    if not request.session.get('authenticated'):
        return RedirectResponse('/login')
    return HTMLResponse((Path(__file__).parent / 'static' / 'index.html').read_text(encoding='utf-8'))

def now(): return datetime.now(timezone.utc).isoformat()
def db_connect():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute('PRAGMA busy_timeout=30000')
    return conn

def db_init():
    with closing(db_connect()) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''CREATE TABLE IF NOT EXISTS ip_blacklist(ip TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operation_log(id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, instance TEXT, old_ip TEXT, new_ip TEXT, result TEXT, detail TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS panel_config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aws_groups(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aws_accounts(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, access_key_id TEXT NOT NULL, secret_access_key TEXT NOT NULL, session_token TEXT, note TEXT, group_name TEXT, proxy TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aws_deployments(id INTEGER PRIMARY KEY AUTOINCREMENT, group_name TEXT NOT NULL, account_id INTEGER NOT NULL, instance_id TEXT NOT NULL, region TEXT NOT NULL, image_id TEXT NOT NULL, instance_type TEXT NOT NULL, password TEXT, user_data TEXT, instance_name TEXT, port INTEGER NOT NULL DEFAULT 443, auto_enabled INTEGER NOT NULL DEFAULT 1, failures INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aws_quota_status(account_id INTEGER PRIMARY KEY, region TEXT NOT NULL, state TEXT NOT NULL, quota_name TEXT NOT NULL, quota_value REAL, usage_value REAL, usage_ratio REAL, detail TEXT NOT NULL, checked_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aws_account_runtime(account_id INTEGER PRIMARY KEY, region TEXT NOT NULL, running_count INTEGER NOT NULL DEFAULT 0, instance_count INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL);'''); c.commit()
        columns = {row[1] for row in c.execute('PRAGMA table_info(aws_accounts)').fetchall()}
        if 'proxy' not in columns:
            c.execute("ALTER TABLE aws_accounts ADD COLUMN proxy TEXT NOT NULL DEFAULT ''")
        deployment_columns = {row[1] for row in c.execute('PRAGMA table_info(aws_deployments)').fetchall()}
        for name, definition in {
            'gfw_enabled': 'INTEGER NOT NULL DEFAULT 0',
            'gfw_probe_hosts': "TEXT NOT NULL DEFAULT ''",
            'gfw_check_once': 'INTEGER NOT NULL DEFAULT 0',
            'architecture': "TEXT NOT NULL DEFAULT 'x86_64'",
            'disk_gb': 'INTEGER NOT NULL DEFAULT 0',
            'no_public_ip': 'INTEGER NOT NULL DEFAULT 0',
            'spot': 'INTEGER NOT NULL DEFAULT 0',
            'ipv6': 'INTEGER NOT NULL DEFAULT 0',
            'static_ip': 'INTEGER NOT NULL DEFAULT 0',
            'ip_cidr': "TEXT NOT NULL DEFAULT ''",
            'probe_token': "TEXT NOT NULL DEFAULT ''",
            'backup_group': "TEXT NOT NULL DEFAULT ''",
            'backup_max_instances': 'INTEGER NOT NULL DEFAULT 0',
        }.items():
            if name not in deployment_columns:
                c.execute(f'ALTER TABLE aws_deployments ADD COLUMN {name} {definition}')
        c.execute("INSERT OR IGNORE INTO aws_groups(name, created_at) SELECT DISTINCT group_name, ? FROM aws_accounts WHERE trim(COALESCE(group_name, '')) <> ''", (now(),))
        c.commit()
@app.on_event('startup')
async def startup():
    global monitor_task, aws_monitor_task, aws_quota_task
    db_init()
    monitor_task = asyncio.create_task(auto_monitor())
    aws_monitor_task = asyncio.create_task(aws_auto_monitor())
    # 配额只在添加账号时做一次初始检测；后续不再周期性轮询。
    aws_quota_task = None

@app.on_event('shutdown')
async def shutdown():
    global monitor_task, aws_monitor_task, aws_quota_task
    for task in (monitor_task, aws_monitor_task, aws_quota_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

class ProbeRequest(BaseModel):
    ip: str; port: int = Field(443, ge=1, le=65535); timeout: float = Field(5, gt=0, le=30)
class RotateRequest(BaseModel):
    project: str; zone: str; instance: str; port: int = Field(443, ge=1, le=65535); max_attempts: int = Field(5, ge=1, le=20); probe_hosts: list[str] = []
class PanelConfigRequest(BaseModel):
    project: str = ''
    zone: str = ''
    instance: str = ''
    port: int = Field(443, ge=1, le=65535)
    auto_enabled: bool = True
    interval_seconds: int = Field(120, ge=60, le=3600)
    failures_required: int = Field(2, ge=1, le=5)

class AWSConfigRequest(BaseModel):
    region: str = 'us-east-1'
    name: str = 'default'

class AWSQuotaConfigRequest(BaseModel):
    enabled: bool = True
    interval_seconds: int = Field(120, ge=60, le=3600)
    alert_ratio: float = Field(0.8, ge=0.5, le=1.0)
    region: str = 'us-east-1'
    telegram_enabled: bool = False
    telegram_bot_token: str = ''
    telegram_chat_id: str = ''
    telegram_proxy: str = ''

class AWSProbeConfigRequest(BaseModel):
    nodes: str = '[]'
    mainland_quorum: int = Field(1, ge=1, le=10)

class AWSAccountRequest(BaseModel):
    name: str
    access_key_id: str
    secret_access_key: str
    session_token: str = ''
    note: str = ''
    group_name: str = ''
    group_id: int = 0
    proxy: str = ''

class AWSAccountUpdateRequest(BaseModel):
    name: str
    access_key_id: str
    secret_access_key: str = ''
    session_token: str = ''
    note: str = ''
    group_name: str = ''
    group_id: int = 0
    proxy: str = ''

class AWSGroupRequest(BaseModel):
    name: str

class AWSCreateRequest(BaseModel):
    account_id: int
    region: str
    image_id: str
    instance_type: str = 't3.micro'
    count: int = Field(1, ge=1, le=20)
    architecture: str = 'x86_64'
    disk_gb: int = Field(0, ge=0, le=16384)
    no_public_ip: bool = False
    password: str = ''
    user_data: str = ''
    name: str = ''
    spot: bool = False
    ipv6: bool = False
    static_ip: bool = False
    ip_cidr: str = ''
    auto_enabled: bool = True
    port: int = Field(443, ge=1, le=65535)
    gfw_enabled: bool = False
    gfw_probe_hosts: str = ''
    gfw_check_once: bool = False
    backup_group: str = ''
    backup_max_instances: int = Field(0, ge=0, le=1000)

class AWSDeploymentUpdateRequest(BaseModel):
    user_data: str = ''
    instance_name: str = ''
    port: int = Field(22, ge=1, le=65535)
    auto_enabled: bool = True
    gfw_enabled: bool = False
    gfw_probe_hosts: str = ''
    backup_group: str = ''

def panel_config():
    with closing(db_connect()) as c:
        rows = c.execute('SELECT key,value FROM panel_config').fetchall()
    result = {key: value for key, value in rows}
    return {'project': result.get('project', ''), 'zone': result.get('zone', ''),
            'instance': result.get('instance', ''), 'port': int(result.get('port', '443')),
            'auto_enabled': result.get('auto_enabled', '1') == '1',
            'interval_seconds': int(result.get('interval_seconds', '120')),
            'failures_required': int(result.get('failures_required', '2'))}

@app.get('/api/config')
def get_panel_config(request: Request):
    logged_in(request)
    path = Path(CREDENTIALS_PATH)
    return {**panel_config(), 'credentials_configured': path.is_file(), 'credentials_path': str(path)}

@app.post('/api/config')
def save_panel_config(req: PanelConfigRequest, request: Request):
    logged_in(request)
    with closing(db_connect()) as c:
        for key, value in {'project': req.project.strip(), 'zone': req.zone.strip(),
                           'instance': req.instance.strip(), 'port': str(req.port),
                           'auto_enabled': '1' if req.auto_enabled else '0',
                           'interval_seconds': str(req.interval_seconds),
                           'failures_required': str(req.failures_required)}.items():
            c.execute('INSERT INTO panel_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
        c.commit()
    log('save_config', instance=req.instance, detail='面板配置已保存')
    return {'ok': True, **panel_config()}

def panel_values(prefix=''):
    with closing(db_connect()) as c:
        rows = c.execute('SELECT key,value FROM panel_config WHERE key LIKE ?', (prefix + '%',)).fetchall()
    return {key[len(prefix):]: value for key, value in rows}

def save_panel_values(values, prefix=''):
    with closing(db_connect()) as c:
        for key, value in values.items():
            c.execute('INSERT INTO panel_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (prefix + key, str(value)))
        c.commit()

@app.get('/api/aws/accounts')
def aws_accounts(request: Request):
    logged_in(request)
    with closing(db_connect()) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute('''SELECT a.id,a.name,a.access_key_id,a.note,a.group_name,a.proxy,a.created_at,
                                   g.id AS group_id,
                                   (SELECT COUNT(*) FROM aws_deployments d WHERE d.account_id=a.id) AS deployment_count,
                                   (SELECT q.state FROM aws_quota_status q WHERE q.account_id=a.id ORDER BY q.checked_at DESC LIMIT 1) AS quota_state,
                                   (SELECT q.region FROM aws_quota_status q WHERE q.account_id=a.id ORDER BY q.checked_at DESC LIMIT 1) AS quota_region,
                                   (SELECT q.quota_value FROM aws_quota_status q WHERE q.account_id=a.id ORDER BY q.checked_at DESC LIMIT 1) AS quota_value,
                                   (SELECT q.usage_value FROM aws_quota_status q WHERE q.account_id=a.id ORDER BY q.checked_at DESC LIMIT 1) AS quota_usage,
                                   (SELECT q.usage_ratio FROM aws_quota_status q WHERE q.account_id=a.id ORDER BY q.checked_at DESC LIMIT 1) AS quota_ratio,
                                   (SELECT q.detail FROM aws_quota_status q WHERE q.account_id=a.id ORDER BY q.checked_at DESC LIMIT 1) AS quota_detail,
                                   (SELECT q.checked_at FROM aws_quota_status q WHERE q.account_id=a.id ORDER BY q.checked_at DESC LIMIT 1) AS quota_checked_at,
                                   r.region AS runtime_region,r.running_count,r.instance_count,r.detail AS runtime_detail,r.checked_at AS runtime_checked_at
                            FROM aws_accounts a
                            LEFT JOIN aws_groups g ON g.name=a.group_name
                            LEFT JOIN aws_account_runtime r ON r.account_id=a.id
                            ORDER BY a.id DESC''').fetchall()
    result = []
    for row in rows:
        item = dict(row, access_key_id=(row['access_key_id'][:4] + '****' + row['access_key_id'][-4:]))
        # Access keys are global; use the latest quota/runtime region as the account's
        # displayed home location. Instance cards below always use their real region.
        runtime_region = item.get('runtime_region')
        home_region = (runtime_region if runtime_region and runtime_region != 'all' else None) or item.get('quota_region') or aws_cfg().get('region', 'us-east-1')
        quota_value = item.get('quota_value')
        quota_usage = item.get('quota_usage')
        item['quota_remaining'] = (float(quota_value) - float(quota_usage)) if quota_value is not None and quota_usage is not None else None
        item.update({
            'account_region': home_region,
            'account_region_name': aws_region_info(home_region)['region_name'],
            'country_name': aws_region_info(home_region)['country_name'],
            'country_flag': aws_region_info(home_region)['country_flag'],
            'country_code': aws_region_info(home_region)['country_code'],
        })
        # AWS API 账号异常时无法读取实时实例，但不要把面板中已登记的实例显示成 0。
        # 这些部署记录仍然有效，实例卡片会标记为“账号异常/待恢复”，待账号恢复后再刷新云端状态。
        if item.get('quota_state') in {'blocked', 'error'} and not item.get('instance_count'):
            item['instance_count'] = item.get('deployment_count', 0)
            item['running_count'] = item.get('deployment_count', 0)
            item['runtime_detail'] = item.get('quota_detail') or '账号异常，暂时无法读取 AWS 实例；已保留面板登记记录'
        result.append(item)
    return result

@app.get('/api/aws/groups')
def aws_groups(request: Request):
    logged_in(request)
    with closing(db_connect()) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute('''SELECT g.id,g.name,g.created_at,COUNT(a.id) AS account_count
                            FROM aws_groups g LEFT JOIN aws_accounts a ON a.group_name=g.name
                            GROUP BY g.id ORDER BY g.id DESC''').fetchall()
    return [dict(row) for row in rows]

@app.post('/api/aws/groups')
def add_aws_group(req: AWSGroupRequest, request: Request):
    logged_in(request)
    name = req.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(400, '分组名称不能为空且不能超过 80 个字符')
    try:
        with closing(db_connect()) as c:
            cur = c.execute('INSERT INTO aws_groups(name,created_at) VALUES(?,?)', (name, now()))
            group_id = cur.lastrowid
            c.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, '分组名称已存在')
    log('aws_add_group', detail=f'group_id={group_id}, name={name}')
    return {'ok': True, 'id': group_id, 'name': name}

@app.delete('/api/aws/groups/{group_id}')
def delete_aws_group(group_id: int, request: Request):
    logged_in(request)
    with closing(db_connect()) as c:
        row = c.execute('SELECT name FROM aws_groups WHERE id=?', (group_id,)).fetchone()
        if not row:
            raise HTTPException(404, '分组不存在')
        account_count = c.execute('SELECT COUNT(*) FROM aws_accounts WHERE group_name=?', (row[0],)).fetchone()[0]
        if account_count:
            raise HTTPException(409, '分组中仍有 AWS 账号，请先移除或重新分组')
        c.execute('DELETE FROM aws_groups WHERE id=?', (group_id,))
        c.commit()
    log('aws_delete_group', detail=f'group_id={group_id}')
    return {'ok': True}

@app.post('/api/aws/accounts')
async def add_aws_account(req: AWSAccountRequest, request: Request):
    logged_in(request)
    if not req.name.strip() or not req.access_key_id.strip() or not req.secret_access_key.strip():
        raise HTTPException(400, '名称、Access Key ID、Secret Access Key 不能为空')
    proxy = req.proxy.strip()
    if proxy and not proxy.lower().startswith(('http://', 'https://', 'socks5://', 'socks5h://')):
        raise HTTPException(400, '代理格式应为 http://、https://、socks5:// 或 socks5h://')
    group_name = req.group_name.strip()
    with closing(db_connect()) as c:
        if req.group_id:
            group = c.execute('SELECT name FROM aws_groups WHERE id=?', (req.group_id,)).fetchone()
            if not group:
                raise HTTPException(400, '选择的 AWS 分组不存在')
            group_name = group[0]
        elif group_name:
            c.execute('INSERT OR IGNORE INTO aws_groups(name,created_at) VALUES(?,?)', (group_name, now()))
        cur = c.execute('INSERT INTO aws_accounts(name,access_key_id,secret_access_key,session_token,note,group_name,proxy,created_at) VALUES(?,?,?,?,?,?,?,?)',
                        (req.name.strip(), req.access_key_id.strip(), req.secret_access_key.strip(), req.session_token.strip(), req.note.strip(), group_name, proxy, now()))
        account_id = cur.lastrowid
        c.commit()
    log('aws_add_account', detail=f'account_id={account_id}, name={req.name.strip()}, group={group_name or "default"}')
    cfg = aws_quota_config()
    try:
        result = await asyncio.to_thread(aws_quota_check_core, account_id, cfg['region'], cfg['alert_ratio'])
    except Exception as exc:
        result = await asyncio.to_thread(save_aws_quota_error, account_id, cfg['region'], exc)
    await notify_aws_quota_result(result)
    return {'ok': True, 'id': account_id, 'name': req.name.strip(), 'group_name': group_name}

@app.get('/api/aws/accounts/{account_id}')
def get_aws_account(account_id: int, request: Request):
    logged_in(request)
    with closing(db_connect()) as c:
        c.row_factory = sqlite3.Row
        row = c.execute('''SELECT a.id,a.name,a.access_key_id,a.secret_access_key,a.session_token,a.note,a.group_name,a.proxy,
                                  g.id AS group_id
                           FROM aws_accounts a LEFT JOIN aws_groups g ON g.name=a.group_name
                           WHERE a.id=?''', (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'AWS 账号不存在')
    return dict(row)

@app.put('/api/aws/accounts/{account_id}')
def update_aws_account(account_id: int, req: AWSAccountUpdateRequest, request: Request):
    logged_in(request)
    if not req.name.strip() or not req.access_key_id.strip():
        raise HTTPException(400, '名称和 Access Key ID 不能为空')
    proxy = req.proxy.strip()
    if proxy and not proxy.lower().startswith(('http://', 'https://', 'socks5://', 'socks5h://')):
        raise HTTPException(400, '代理格式应为 http://、https://、socks5:// 或 socks5h://')
    with closing(db_connect()) as c:
        exists = c.execute('SELECT 1 FROM aws_accounts WHERE id=?', (account_id,)).fetchone()
        if not exists:
            raise HTTPException(404, 'AWS 账号不存在')
        group_name = req.group_name.strip()
        if req.group_id:
            group = c.execute('SELECT name FROM aws_groups WHERE id=?', (req.group_id,)).fetchone()
            if not group:
                raise HTTPException(400, '选择的 AWS 分组不存在')
            group_name = group[0]
        elif group_name:
            c.execute('INSERT OR IGNORE INTO aws_groups(name,created_at) VALUES(?,?)', (group_name, now()))
        if req.secret_access_key.strip():
            c.execute('''UPDATE aws_accounts SET name=?,access_key_id=?,secret_access_key=?,session_token=?,note=?,group_name=?,proxy=? WHERE id=?''',
                      (req.name.strip(), req.access_key_id.strip(), req.secret_access_key.strip(), req.session_token.strip(), req.note.strip(), group_name, proxy, account_id))
        else:
            c.execute('''UPDATE aws_accounts SET name=?,access_key_id=?,session_token=?,note=?,group_name=?,proxy=? WHERE id=?''',
                      (req.name.strip(), req.access_key_id.strip(), req.session_token.strip(), req.note.strip(), group_name, proxy, account_id))
        c.commit()
    log('aws_update_account', detail=f'account_id={account_id}, name={req.name.strip()}, group={group_name or "default"}')
    return {'ok': True, 'id': account_id, 'name': req.name.strip(), 'group_name': group_name}

@app.delete('/api/aws/accounts/{account_id}')
def delete_aws_account(account_id: int, request: Request, force: bool = False):
    logged_in(request)
    with closing(db_connect()) as c:
        row = c.execute('SELECT name FROM aws_accounts WHERE id=?', (account_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'AWS 账号不存在')
        deployment_count = c.execute('SELECT COUNT(*) FROM aws_deployments WHERE account_id=?', (account_id,)).fetchone()[0]
        if deployment_count and not force:
            raise HTTPException(409, f'账号仍关联 {deployment_count} 个 EC2 部署；如确认只删除面板账号记录，请使用 force=1')
        # 删除面板关联和检测数据，但不调用 TerminateInstances，避免删除账号时误删 AWS 实例。
        c.execute('DELETE FROM aws_deployments WHERE account_id=?', (account_id,))
        c.execute('DELETE FROM aws_quota_status WHERE account_id=?', (account_id,))
        c.execute('DELETE FROM aws_account_runtime WHERE account_id=?', (account_id,))
        c.execute('DELETE FROM aws_accounts WHERE id=?', (account_id,))
        c.commit()
    log('aws_delete_account', detail=f'account_id={account_id}, name={row[0]}, deployments_removed={deployment_count}')
    return {'ok': True, 'message': 'AWS 账号及面板记录已删除，AWS 云端实例未终止', 'deployments_removed': deployment_count}

@app.get('/api/aws/config')
def aws_config(request: Request):
    logged_in(request)
    cfg = panel_values('aws_')
    return {'region': cfg.get('region', 'us-east-1'), 'name': cfg.get('name', 'default'),
            'credentials_configured': Path(AWS_CREDENTIALS_PATH).is_file(),
            'credentials_path': AWS_CREDENTIALS_PATH}

@app.post('/api/aws/config')
def save_aws_config(req: AWSConfigRequest, request: Request):
    logged_in(request)
    region = req.region.strip()
    if not region or len(region) > 32:
        raise HTTPException(400, 'AWS Region 无效')
    save_panel_values({'region': region, 'name': req.name.strip() or 'default'}, 'aws_')
    log('aws_save_config', detail=f'AWS region={region}')
    return {'ok': True, **aws_config(request)}

def aws_quota_config():
    cfg = panel_values('aws_')
    token = cfg.get('telegram_bot_token', '').strip()
    chat_id = cfg.get('telegram_chat_id', '').strip()
    telegram_proxy = cfg.get('telegram_proxy', '').strip()
    try:
        interval = max(60, min(int(cfg.get('quota_interval_seconds', '120')), 3600))
    except ValueError:
        interval = 120
    try:
        alert_ratio = max(0.5, min(float(cfg.get('quota_alert_ratio', '0.8')), 1.0))
    except ValueError:
        alert_ratio = 0.8
    return {
        'enabled': cfg.get('quota_enabled', '1') == '1',
        'interval_seconds': interval,
        'alert_ratio': alert_ratio,
        'region': cfg.get('quota_region') or cfg.get('region') or 'us-east-1',
        'telegram_enabled': cfg.get('telegram_enabled', '0') == '1',
        'telegram_configured': bool(token and chat_id),
        'telegram_chat_id': chat_id,
        'telegram_proxy_configured': bool(telegram_proxy),
    }

@app.get('/api/aws/quota-config')
def get_aws_quota_config(request: Request):
    logged_in(request)
    return aws_quota_config()

@app.post('/api/aws/quota-config')
def save_aws_quota_config(req: AWSQuotaConfigRequest, request: Request):
    logged_in(request)
    region = req.region.strip()
    if not region or len(region) > 32:
        raise HTTPException(400, '配额检测区域无效')
    current = panel_values('aws_')
    candidate_token = req.telegram_bot_token.strip() or current.get('telegram_bot_token', '').strip()
    candidate_chat_id = req.telegram_chat_id.strip() or current.get('telegram_chat_id', '').strip()
    telegram_proxy = req.telegram_proxy.strip() or current.get('telegram_proxy', '').strip()
    if telegram_proxy and not telegram_proxy.lower().startswith(('http://', 'https://', 'socks5://', 'socks5h://')):
        raise HTTPException(400, 'Telegram 代理格式应为 http://、https://、socks5:// 或 socks5h://')
    if req.telegram_enabled and not (candidate_token and candidate_chat_id):
        raise HTTPException(400, '已启用 Telegram，但 Bot Token 或 Chat ID 未配置')
    values = {
        'quota_enabled': '1' if req.enabled else '0',
        'quota_interval_seconds': str(req.interval_seconds),
        'quota_alert_ratio': str(req.alert_ratio),
        'quota_region': region,
        'telegram_enabled': '1' if req.telegram_enabled else '0',
    }
    if req.telegram_bot_token.strip():
        values['telegram_bot_token'] = req.telegram_bot_token.strip()
    if req.telegram_chat_id.strip():
        values['telegram_chat_id'] = req.telegram_chat_id.strip()
    if req.telegram_proxy.strip():
        values['telegram_proxy'] = req.telegram_proxy.strip()
    save_panel_values(values, 'aws_')
    log('aws_save_quota_config', detail=f'配额检测区域={region}, interval={req.interval_seconds}s')
    return {'ok': True, **aws_quota_config()}

def aws_probe_config():
    cfg = panel_values('aws_')
    raw = cfg.get('probe_nodes_json', '[]')
    try:
        nodes = json.loads(raw)
    except Exception:
        nodes = []
    if not isinstance(nodes, list):
        nodes = []
    clean = []
    for node in nodes[:20]:
        if not isinstance(node, dict):
            continue
        location = str(node.get('location', '')).strip().lower()
        url = str(node.get('url', '')).strip().rstrip('/')
        token = str(node.get('token', '')).strip()
        if location not in {'mainland', 'hk', 'overseas'} or not url or not token:
            continue
        clean.append({'name': str(node.get('name', '')).strip() or location,
                      'location': location, 'url': url, 'token': token})
    return {'nodes': clean, 'mainland_quorum': int(cfg.get('probe_mainland_quorum', '1') or 1),
            'configured': bool(clean)}

@app.get('/api/aws/probe-config')
def get_aws_probe_config(request: Request):
    logged_in(request)
    return aws_probe_config()

@app.post('/api/aws/probe-config')
def save_aws_probe_config(req: AWSProbeConfigRequest, request: Request):
    logged_in(request)
    try:
        nodes = json.loads(req.nodes or '[]')
    except Exception as exc:
        raise HTTPException(400, f'探针 JSON 格式无效：{exc}')
    if not isinstance(nodes, list) or len(nodes) > 20:
        raise HTTPException(400, '探针配置必须是最多 20 个节点的 JSON 数组')
    for node in nodes:
        if not isinstance(node, dict):
            raise HTTPException(400, '每个探针必须是 JSON 对象')
        if str(node.get('location', '')).lower() not in {'mainland', 'hk', 'overseas'}:
            raise HTTPException(400, '探针 location 只能是 mainland、hk 或 overseas')
        url = str(node.get('url', '')).strip()
        if not url.startswith(('https://', 'http://')):
            raise HTTPException(400, '探针 URL 必须以 http:// 或 https:// 开头')
        if not str(node.get('token', '')).strip():
            raise HTTPException(400, '每个探针都必须配置 token')
    save_panel_values({'probe_nodes_json': json.dumps(nodes, ensure_ascii=False),
                       'probe_mainland_quorum': str(req.mainland_quorum)}, 'aws_')
    log('aws_save_probe_config', detail=f'探针数量={len(nodes)}, 大陆失败阈值={req.mainland_quorum}')
    return {'ok': True, **aws_probe_config()}

async def send_telegram_message(text: str, force: bool = False):
    cfg = panel_values('aws_')
    if not force and cfg.get('telegram_enabled', '0') != '1':
        return False
    token = cfg.get('telegram_bot_token', '').strip()
    chat_id = cfg.get('telegram_chat_id', '').strip()
    if not token or not chat_id:
        return False
    try:
        import httpx
        proxy = cfg.get('telegram_proxy', '').strip()
        client_args = {'timeout': httpx.Timeout(connect=8, read=15, write=15, pool=8)}
        if proxy:
            client_args['proxy'] = proxy
        async with httpx.AsyncClient(**client_args) as client:
            response = await client.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                         json={'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True})
        try:
            data = response.json()
        except Exception:
            data = {}
        if response.status_code >= 400 or not data.get('ok'):
            description = data.get('description') or response.text[:240] or f'HTTP {response.status_code}'
            raise RuntimeError(f'{description}（HTTP {response.status_code}）')
        return True
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        if 'ConnectError' in exc.__class__.__name__:
            detail = f'无法连接 Telegram API，请检查服务器网络或配置代理：{detail}'
        elif 'socks' in detail.lower() and 'install' in detail.lower():
            detail = '使用 SOCKS 代理需要安装 httpx[socks] 依赖'
        log('aws_quota_telegram', result='failed', detail=detail)
        if force:
            raise HTTPException(502, f'Telegram 发送失败：{detail}')
        return False

async def notify_telegram_event(title: str, **fields):
    """Send one site-level event notification when Telegram is enabled."""
    lines = ['GCPMX 站点通知', f'事件：{title}']
    for key, value in fields.items():
        if value not in (None, ''):
            lines.append(f'{key}：{value}')
    return await send_telegram_message('\n'.join(lines))

@app.post('/api/aws/quota-telegram-test')
async def aws_quota_telegram_test(request: Request):
    logged_in(request)
    await send_telegram_message('GCPMX AWS 配额检测 Telegram 通知测试成功。', force=True)
    log('aws_quota_telegram', detail='测试通知已发送')
    return {'ok': True, 'message': 'Telegram 测试通知已发送'}

def log(action, instance=None, old_ip=None, new_ip=None, result='ok', detail=''):
    with closing(db_connect()) as c:
        c.execute('INSERT INTO operation_log(action,instance,old_ip,new_ip,result,detail,created_at) VALUES(?,?,?,?,?,?,?)',(action,instance,old_ip,new_ip,result,detail,now())); c.commit()
def blacklist(ip, reason):
    with closing(db_connect()) as c:
        c.execute('INSERT OR IGNORE INTO ip_blacklist(ip,reason,created_at) VALUES(?,?,?)',(ip,reason,now())); c.commit()
async def tcp_probe(ip, port, timeout):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout); writer.close(); await writer.wait_closed(); return True
    except Exception: return False

async def aws_gfw_probe(ip: str, port: int):
    """从已配置的大陆、香港和海外探针判断目标 IP 是否疑似被阻断。"""
    cfg = aws_probe_config()
    nodes = cfg['nodes']
    if not nodes:
        return {'state': 'unconfigured', 'detail': '未配置 GFW 探针'}
    import httpx
    async def call(node):
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.post(node['url'] + '/v1/check',
                                             headers={'X-Probe-Token': node['token']},
                                             json={'target_ip': ip, 'port': port, 'timeout_ms': 5000})
                response.raise_for_status()
                data = response.json()
                return {'name': node['name'], 'location': node['location'],
                        'reachable': bool(data.get('reachable')), 'latency_ms': data.get('latency_ms'),
                        'error': data.get('error', '')}
        except Exception as exc:
            return {'name': node['name'], 'location': node['location'],
                    'reachable': None, 'latency_ms': None, 'error': str(exc)}
    results = await asyncio.gather(*(call(node) for node in nodes))
    usable = [x for x in results if x['reachable'] is not None]
    mainland = [x for x in usable if x['location'] == 'mainland']
    foreign = [x for x in usable if x['location'] in {'hk', 'overseas'}]
    mainland_failed = sum(1 for x in mainland if not x['reachable'])
    foreign_ok = sum(1 for x in foreign if x['reachable'])
    if mainland_failed >= cfg['mainland_quorum'] and foreign_ok:
        state = 'blocked'
        detail = f'大陆探针失败 {mainland_failed}/{len(mainland)}，香港/海外正常 {foreign_ok}/{len(foreign)}'
    elif mainland and all(x['reachable'] for x in mainland):
        state = 'ok'
        detail = f'大陆探针正常 {len(mainland)}/{len(mainland)}'
    else:
        state = 'unknown'
        detail = f'有效探针 {len(usable)}/{len(nodes)}，无法确认 GFW 状态'
    return {'state': state, 'detail': detail, 'results': results}

def aws_cfg():
    cfg = panel_values('aws_')
    return {'region': cfg.get('region', 'us-east-1'), 'name': cfg.get('name', 'default')}

def aws_client(service_name='ec2', region=None, account_id=None):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise HTTPException(500, 'AWS 依赖未安装，请执行 pip install -r requirements.txt')
    data = None
    if account_id:
        with closing(db_connect()) as c:
            row = c.execute('SELECT access_key_id,secret_access_key,session_token,proxy FROM aws_accounts WHERE id=?', (account_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'AWS 账号不存在')
        data = {'AccessKeyId': row[0], 'SecretAccessKey': row[1], 'SessionToken': row[2] or '', 'Proxy': row[3] or ''}
    path = Path(AWS_CREDENTIALS_PATH)
    if data is None and path.is_file():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            raise HTTPException(500, 'AWS 凭据 JSON 无法读取')
    kwargs = {'region_name': region or aws_cfg()['region']}
    proxy = ''
    if data:
        kwargs.update({'aws_access_key_id': data.get('AccessKeyId') or data.get('access_key_id'),
                       'aws_secret_access_key': data.get('SecretAccessKey') or data.get('secret_access_key')})
        token = data.get('SessionToken') or data.get('session_token')
        if token:
            kwargs['aws_session_token'] = token
        proxy = (data.get('Proxy') or data.get('proxy') or '').strip()
    # 查询实例时不能让某个无权限或不可达区域拖住整个面板。
    # AWS SDK 默认会重试多次，24 个区域并发时会表现为页面一直“读取中”。
    config_args = {
        'connect_timeout': 4,
        'read_timeout': 8,
        'retries': {'max_attempts': 1, 'mode': 'standard'},
    }
    if proxy:
        config_args['proxies'] = {'http': proxy, 'https': proxy}
    kwargs['config'] = Config(**config_args)
    return boto3.client(service_name, **kwargs)

def aws_ec2(region=None, account_id=None):
    return aws_client('ec2', region, account_id)

def aws_default_subnet_and_open_sg(client):
    """Choose a usable subnet and reuse a panel-managed open IPv4 SG."""
    subnets = client.describe_subnets(
        Filters=[{'Name': 'default-for-az', 'Values': ['true']}]
    ).get('Subnets', [])
    if not subnets:
        vpcs = client.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}]).get('Vpcs', [])
        if vpcs:
            subnets = client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpcs[0]['VpcId']]}]
            ).get('Subnets', [])
    if not subnets:
        raise RuntimeError('当前区域没有可用默认子网，请先在 AWS VPC 中创建子网')
    subnet = sorted(subnets, key=lambda item: item.get('AvailableIpAddressCount', -1), reverse=True)[0]
    vpc_id = subnet.get('VpcId')
    if not vpc_id:
        raise RuntimeError('AWS 子网没有返回 VPC ID')
    group_name = 'gcpmx-open-all'
    groups = client.describe_security_groups(
        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}, {'Name': 'group-name', 'Values': [group_name]}]
    ).get('SecurityGroups', [])
    if groups:
        group_id = groups[0]['GroupId']
    else:
        created = client.create_security_group(
            GroupName=group_name,
            Description='GCPMX managed EC2 access group (all inbound IPv4)',
            VpcId=vpc_id,
        )
        group_id = created['GroupId']
    for ranges in (
        {'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'GCPMX all IPv4 ports'}]},
        {'Ipv6Ranges': [{'CidrIpv6': '::/0', 'Description': 'GCPMX all IPv6 ports'}]},
    ):
        try:
            client.authorize_security_group_ingress(
                GroupId=group_id,
                IpPermissions=[{'IpProtocol': '-1', **ranges}],
            )
        except Exception as exc:
            if 'InvalidPermission.Duplicate' not in str(exc):
                raise
    return subnet, group_id

def aws_password_user_data(client, image_id, password, existing=''):
    """Build first-boot password login setup without an EC2 key pair."""
    if not password:
        return existing.strip(), '未设置密码'
    if len(password) > 128 or any(ch in password for ch in '\r\n'):
        raise HTTPException(400, '密码长度不能超过 128 个字符，且不能包含换行')
    try:
        image = client.describe_images(ImageIds=[image_id]).get('Images', [{}])[0]
    except Exception:
        image = {}
    name = (image.get('Name') or '').lower()
    platform = (image.get('PlatformDetails') or image.get('Platform') or '').lower()
    if 'windows' in name or 'windows' in platform:
        escaped = password.replace('`', '``').replace('"', '`"')
        script = f'<powershell>net user Administrator "{escaped}" /active:yes</powershell>'
        return ((existing.strip() + '\n' + script).strip() if existing.strip() else script), 'Administrator'
    extra = existing.strip()
    if extra and extra.startswith(('#cloud-config', '#include', 'Content-Type:')):
        raise HTTPException(400, '填写密码时，开机脚本请使用普通 Shell 脚本，不要再填写完整 cloud-config 文档')
    script_b64 = base64.b64encode(extra.encode('utf-8')).decode('ascii') if extra else ''
    password_entry = json.dumps(f'root:{password}', ensure_ascii=False)
    commands = [
        "sed -ri 's/^#?\\s*PasswordAuthentication\\s+.*/PasswordAuthentication yes/' /etc/ssh/sshd_config 2>/dev/null || true",
        "sed -ri 's/^#?\\s*PermitRootLogin\\s+.*/PermitRootLogin yes/' /etc/ssh/sshd_config 2>/dev/null || true",
        'systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true',
    ]
    if script_b64:
        commands.append(f"echo {script_b64} | base64 -d | bash")
    command_lines = '\n'.join(f'  - [ bash, -lc, {json.dumps(command)} ]' for command in commands)
    cloud = (
        '#cloud-config\n'
        'disable_root: false\n'
        'ssh_pwauth: true\n'
        'chpasswd:\n'
        '  expire: false\n'
        '  list:\n'
        f'    - {password_entry}\n'
        'runcmd:\n'
        f'{command_lines}\n'
    )
    return cloud, 'root'

def aws_saved_boot_script(saved: str) -> str:
    """Recover the original shell script from old panel-generated cloud-init data.

    Older versions stored the final generated UserData in aws_deployments. A
    replacement must feed the original script back through aws_password_user_data,
    otherwise it is rejected as a nested cloud-config document.
    """
    text = (saved or '').strip()
    if not text:
        return ''
    if text.startswith('#cloud-boothook'):
        return text.split('\n', 1)[1] if '\n' in text else ''
    match = re.search(r'echo\s+([A-Za-z0-9+/=]+)\s+\|\s+base64\s+-d\s+\|\s+bash', text)
    if text.startswith('#cloud-config') and match:
        try:
            return base64.b64decode(match.group(1)).decode('utf-8')
        except Exception:
            return ''
    return text

@app.post('/api/aws/credentials')
async def upload_aws_credentials(request: Request, file: UploadFile = File(...)):
    logged_in(request)
    if not file.filename or not file.filename.lower().endswith('.json'):
        raise HTTPException(400, '只允许上传 JSON 文件')
    raw = await file.read()
    if len(raw) > 1024 * 1024:
        raise HTTPException(413, 'AWS JSON 文件不能超过 1 MB')
    try:
        data = json.loads(raw.decode('utf-8'))
    except Exception:
        raise HTTPException(400, 'JSON 格式无效')
    access = data.get('AccessKeyId') or data.get('access_key_id') if isinstance(data, dict) else None
    secret = data.get('SecretAccessKey') or data.get('secret_access_key') if isinstance(data, dict) else None
    if not access or not secret:
        raise HTTPException(400, 'AWS JSON 必须包含 AccessKeyId 和 SecretAccessKey')
    path = Path(AWS_CREDENTIALS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    log('aws_upload_credentials', result='ok', detail='AWS 凭据已保存')
    return {'ok': True, 'message': 'AWS 凭据已保存'}

@app.get('/api/aws/regions')
def aws_regions(request: Request, account_id: int = 0):
    logged_in(request)
    # 使用面板内置的常用区域列表，避免每次打开创建窗口都等待 DescribeRegions。
    # 创建实例时 AWS 仍会对具体区域做最终校验。
    regions = list(AWS_REGION_LABELS)
    return [{'id': x, 'name': AWS_REGION_LABELS.get(x, x)} for x in sorted(regions)]

@app.get('/api/aws/images')
def aws_images(request: Request, region: str, account_id: int, architecture: str = 'x86_64'):
    logged_in(request)
    if architecture not in {'x86_64', 'arm64'}:
        raise HTTPException(400, '架构只能是 x86_64 或 arm64')
    client = aws_ec2(region, account_id)
    image_patterns = AWS_IMAGE_NAME_PATTERNS
    if architecture == 'arm64':
        image_patterns = [pattern.replace('amd64', 'arm64') for pattern in AWS_IMAGE_NAME_PATTERNS
                          if 'Windows_Server' not in pattern]
    response = client.describe_images(Owners=['amazon', '099720109477', '136693071363'], Filters=[
        {'Name': 'state', 'Values': ['available']},
        {'Name': 'architecture', 'Values': [architecture]},
        {'Name': 'name', 'Values': image_patterns},
    ])
    latest = {}
    for item in response.get('Images', []):
        raw_name = item.get('Name', '')
        name = raw_name.lower()
        if not name:
            continue
        key = None
        if 'ubuntu' in name:
            for version, marker in (('24.04', ('noble-24.04', '24.04')), ('22.04', ('jammy-22.04', '22.04')), ('20.04', ('focal-20.04', '20.04'))):
                if any(x in name for x in marker):
                    key = 'ubuntu-' + version
                    break
        elif 'debian' in name:
            for version in ('13', '12', '11', '10'):
                if f'debian-{version}' in name or f'debian_{version}' in name:
                    key = 'debian-' + version
                    break
        elif 'windows' in name:
            for version in ('2022', '2019'):
                if version in name:
                    key = 'windows-' + version
                    break
        if key not in AWS_IMAGE_LABELS:
            continue
        current = latest.get(key)
        if current is None or item.get('CreationDate', '') > current.get('creation_date', ''):
            os_name, version = AWS_IMAGE_LABELS[key]
            latest[key] = {'id': item.get('ImageId'), 'name': f'{os_name} {version}', 'os': os_name,
                           'version': version, 'description': raw_name, 'creation_date': item.get('CreationDate', '')}
    return [latest[key] for key in AWS_IMAGE_LABELS if key in latest]

@app.get('/api/aws/instance-types')
def aws_instance_types(request: Request, region: str, account_id: int, architecture: str = 'x86_64'):
    logged_in(request)
    # 常用 x86 型号固定返回，避免 DescribeInstanceTypes 在权限不足或网络慢时阻塞创建页。
    # RunInstances 会在真正创建时按区域校验型号是否可用。
    if architecture not in {'x86_64', 'arm64'}:
        raise HTTPException(400, '架构只能是 x86_64 或 arm64')
    return AWS_TYPE_FALLBACK_ARM if architecture == 'arm64' else AWS_TYPE_FALLBACK

def aws_running_vcpu(account_id: int, region: str):
    """返回当前区域 pending/running 实例的 vCPU 用量；无法识别型号时返回 None。"""
    client = aws_ec2(region, account_id)
    paginator = client.get_paginator('describe_instances')
    instances = []
    for page in paginator.paginate(Filters=[{'Name': 'instance-state-name', 'Values': ['pending', 'running']}]):
        for reservation in page.get('Reservations', []):
            instances.extend(reservation.get('Instances', []))
    if not instances:
        return 0, True
    instance_types = sorted({x.get('InstanceType', '') for x in instances if x.get('InstanceType')})
    vcpu_map = {str(x['id']): int(x['vcpu']) for x in AWS_TYPE_FALLBACK + AWS_TYPE_FALLBACK_ARM}
    unknown = set(instance_types) - set(vcpu_map)
    if unknown:
        try:
            type_response = client.describe_instance_types(InstanceTypes=list(unknown))
            for item in type_response.get('InstanceTypes', []):
                value = item.get('VCpuInfo', {}).get('DefaultVCpus')
                if value:
                    vcpu_map[item.get('InstanceType')] = int(value)
        except Exception:
            return None, False
    if any(x not in vcpu_map for x in instance_types):
        return None, False
    return sum(vcpu_map.get(x.get('InstanceType'), 0) for x in instances), True

def aws_instance_runtime_core(account_id: int, region: str):
    """同步刷新账号在指定区域的实例数量，供账号卡片快速显示。"""
    client = aws_ec2(region, account_id)
    total = 0
    running = 0
    states = []
    paginator = client.get_paginator('describe_instances')
    for page in paginator.paginate():
        for reservation in page.get('Reservations', []):
            for item in reservation.get('Instances', []):
                total += 1
                state = item.get('State', {}).get('Name', '')
                if state in {'terminated', 'shutting-down'}:
                    total -= 1
                    continue
                states.append(state)
                if state in {'pending', 'running'}:
                    running += 1
    detail = f'运行中 {running}，实例总数 {total}'
    return {'account_id': account_id, 'region': region, 'running_count': running,
            'instance_count': total, 'detail': detail}

def aws_instance_runtime_all_regions(account_id: int):
    """汇总账号在已支持区域中的实例数量，识别未由本面板创建的实例。"""
    total = 0
    running = 0
    checked = []
    errors = []
    regions_with_instances = []
    # 并发读取，避免某个无权限/不可用区域阻塞整个账号的卡片更新。
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(AWS_REGION_LABELS)))) as pool:
        futures = {pool.submit(aws_instance_runtime_core, account_id, candidate): candidate
                   for candidate in AWS_REGION_LABELS}
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                errors.append(f'{candidate}: {exc}')
                continue
            checked.append(candidate)
            total += result['instance_count']
            running += result['running_count']
            if result['instance_count'] > 0:
                regions_with_instances.append(candidate)
    if not checked:
        detail = 'EC2 读取失败：请确认账号拥有 ec2:DescribeInstances 权限'
        with closing(db_connect()) as c:
            c.execute('''INSERT INTO aws_account_runtime(account_id,region,running_count,instance_count,detail,checked_at)
                         VALUES(?,?,?,?,?,?)
                         ON CONFLICT(account_id) DO UPDATE SET region=excluded.region,running_count=excluded.running_count,
                         instance_count=excluded.instance_count,detail=excluded.detail,checked_at=excluded.checked_at''',
                      (account_id, 'all', 0, 0, detail, now()))
            c.commit()
        return {'account_id': account_id, 'region': 'all', 'running_count': 0,
                'instance_count': 0, 'detail': detail, 'checked_at': now(), 'checked_regions': []}
    home_region = sorted(regions_with_instances)[0] if regions_with_instances else 'all'
    detail = f'全部区域：运行中 {running}，实例总数 {total}'
    with closing(db_connect()) as c:
        c.execute('''INSERT INTO aws_account_runtime(account_id,region,running_count,instance_count,detail,checked_at)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(account_id) DO UPDATE SET region=excluded.region,running_count=excluded.running_count,
                     instance_count=excluded.instance_count,detail=excluded.detail,checked_at=excluded.checked_at''',
                  (account_id, home_region, running, total, detail, now()))
        c.commit()
    return {'account_id': account_id, 'region': home_region, 'running_count': running,
            'instance_count': total, 'detail': detail, 'checked_at': now(), 'checked_regions': checked}

async def aws_runtime_scan_locked(account_id: int):
    # 全区域 DescribeInstances 很重；同一时间只允许一个扫描，避免配额任务、
    # 页面手动检测和自动任务互相堆积线程及 AWS API 请求。
    async with aws_runtime_scan_lock:
        return await asyncio.to_thread(aws_instance_runtime_all_regions, account_id)

def save_aws_quota_status(account_id: int, region: str, state: str, quota_value=None,
                          usage_value=None, usage_ratio=None, detail: str = ''):
    with closing(db_connect()) as c:
        old = c.execute('SELECT state,quota_value FROM aws_quota_status WHERE account_id=?', (account_id,)).fetchone()
        previous_state = old[0] if old else ''
        previous_quota = old[1] if old else None
        c.execute('''INSERT INTO aws_quota_status(account_id,region,state,quota_name,quota_value,usage_value,usage_ratio,detail,checked_at)
                     VALUES(?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(account_id) DO UPDATE SET region=excluded.region,state=excluded.state,
                     quota_name=excluded.quota_name,quota_value=excluded.quota_value,usage_value=excluded.usage_value,
                     usage_ratio=excluded.usage_ratio,detail=excluded.detail,checked_at=excluded.checked_at''',
                  (account_id, region, state, AWS_QUOTA_NAME, quota_value, usage_value, usage_ratio, detail, now()))
        c.commit()
    quota_changed = previous_quota is not None and quota_value is not None and float(previous_quota) != float(quota_value)
    alert_states = {'warning', 'critical', 'error', 'blocked'}
    notify = quota_changed or (state != previous_state and (state in alert_states or previous_state in alert_states))
    return previous_state, notify, quota_changed

def aws_quota_check_core(account_id: int, region: str, alert_ratio: float):
    with closing(db_connect()) as c:
        row = c.execute('SELECT name,access_key_id FROM aws_accounts WHERE id=?', (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'AWS 账号不存在')
    account_name = row[0]
    quota_client = aws_client('service-quotas', region, account_id)
    response = quota_client.get_service_quota(ServiceCode='ec2', QuotaCode=AWS_QUOTA_CODE)
    quota = response.get('Quota') or {}
    limit = float(quota.get('Value') or 0)
    usage, usage_known = aws_running_vcpu(account_id, region)
    ratio = (float(usage) / limit) if usage_known and limit > 0 else None
    if limit <= 0:
        state = 'critical'
        detail = 'AWS 返回的 EC2 vCPU 配额为 0'
    elif usage_known and ratio is not None and ratio >= 1:
        state = 'critical'
        detail = f'用量 {usage:g} / 配额 {limit:g} vCPU，已达到上限'
    elif usage_known and ratio is not None and ratio >= alert_ratio:
        state = 'warning'
        detail = f'用量 {usage:g} / 配额 {limit:g} vCPU，达到 {ratio:.0%}'
    elif usage_known:
        state = 'ok'
        detail = f'用量 {usage:g} / 配额 {limit:g} vCPU'
    else:
        state = 'ok'
        detail = f'配额上限 {limit:g} vCPU；当前实例型号无法完整读取用量'
    previous_state, notify, quota_changed = save_aws_quota_status(account_id, region, state, limit, usage, ratio, detail)
    return {'ok': True, 'account_id': account_id, 'account_name': account_name, 'region': region,
            'state': state, 'quota_value': limit, 'usage_value': usage, 'usage_ratio': ratio,
            'detail': detail, 'checked_at': now(), 'previous_state': previous_state, 'notify': notify,
            'quota_changed': quota_changed}

def save_aws_quota_error(account_id: int, region: str, exc):
    raw = str(exc)
    lowered = raw.lower()
    blocked = any(key in lowered for key in (
        'blocked', 'suspended', 'not recognized as a valid account',
        'accountverification', 'account verification', 'invalidaccount',
        'account is currently', 'account is on hold'))
    state = 'blocked' if blocked else 'error'
    detail = ('账号封禁或 AWS 账号验证异常：' if blocked else '配额检测失败：') + raw
    previous_state, notify, quota_changed = save_aws_quota_status(account_id, region, state, detail=detail)
    with closing(db_connect()) as c:
        row = c.execute('SELECT name FROM aws_accounts WHERE id=?', (account_id,)).fetchone()
    return {'ok': False, 'account_id': account_id, 'account_name': row[0] if row else str(account_id),
            'region': region, 'state': state, 'detail': detail, 'checked_at': now(),
            'previous_state': previous_state, 'notify': notify, 'quota_changed': quota_changed}

async def notify_aws_quota_result(result):
    if not result.get('notify'):
        return
    if result.get('quota_changed'):
        status = '🔔 配额上限发生变化'
    else:
        status = {'warning': '⚠️ 配额接近上限', 'critical': '🚨 配额已达到上限',
                  'blocked': '⛔ AWS 账号封禁/验证异常', 'error': '❌ 配额检测异常',
                  'ok': '✅ 配额检测恢复正常'}.get(result['state'], '配额状态变化')
    text = (f'GCPMX AWS 配额通知\n账号：{result.get("account_name", result.get("account_id"))}\n'
            f'区域：{result.get("region", "-")}\n状态：{status}\n{result.get("detail", "")}')
    await send_telegram_message(text)

@app.post('/api/aws/accounts/{account_id}/quota-check')
async def aws_account_quota_check(account_id: int, request: Request, region: str = ''):
    logged_in(request)
    cfg = aws_quota_config()
    region = region.strip() or cfg['region']
    try:
        result = await asyncio.to_thread(aws_quota_check_core, account_id, region, cfg['alert_ratio'])
    except Exception as exc:
        result = await asyncio.to_thread(save_aws_quota_error, account_id, region, exc)
    try:
        await aws_runtime_scan_locked(account_id)
    except Exception as exc:
        log('aws_instance_runtime', result='failed', detail=f'账号={account_id}, region={region}, {exc}')
    await notify_aws_quota_result(result)
    log('aws_quota_check', detail=f'账号={account_id}, region={region}, state={result["state"]}', result='ok' if result.get('ok') else 'failed')
    return result

@app.post('/api/aws/quota-check-all')
async def aws_quota_check_all(request: Request):
    logged_in(request)
    cfg = aws_quota_config()
    with closing(db_connect()) as c:
        account_ids = [row[0] for row in c.execute('SELECT id FROM aws_accounts ORDER BY id').fetchall()]
    results = []
    for account_id in account_ids:
        try:
            result = await asyncio.to_thread(aws_quota_check_core, account_id, cfg['region'], cfg['alert_ratio'])
        except Exception as exc:
            result = await asyncio.to_thread(save_aws_quota_error, account_id, cfg['region'], exc)
        try:
            await aws_runtime_scan_locked(account_id)
        except Exception as exc:
            log('aws_instance_runtime', result='failed', detail=f'账号={account_id}, region={cfg["region"]}, {exc}')
        await notify_aws_quota_result(result)
        results.append(result)
    return results

@app.get('/api/aws/instances')
def aws_instances(request: Request, region: str = '', account_id: int = 0):
    logged_in(request)
    deployment_config = {}
    if account_id:
        with closing(db_connect()) as c:
            c.row_factory = sqlite3.Row
            for row in c.execute('SELECT * FROM aws_deployments WHERE account_id=?', (account_id,)).fetchall():
                deployment_config[row['instance_id']] = row
    requested_region = region.strip()
    regions = list(AWS_REGION_LABELS) if requested_region == 'all' else [requested_region or aws_cfg()['region']]
    def scan_region(scan_region):
        found = []
        error = ''
        try:
            client = aws_ec2(scan_region, account_id or None)
            paginator = client.get_paginator('describe_instances')
            for page in paginator.paginate():
                for reservation in page.get('Reservations', []):
                    for item in reservation.get('Instances', []):
                        tags = {t['Key']: t['Value'] for t in item.get('Tags', [])}
                        state = item.get('State', {}).get('Name', '')
                        if state in {'terminated', 'shutting-down'}:
                            continue
                        deployment = deployment_config.get(item.get('InstanceId'))
                        location = aws_region_info(scan_region)
                        type_info = next((x for x in AWS_TYPE_FALLBACK + AWS_TYPE_FALLBACK_ARM if x['id'] == item.get('InstanceType')), {})
                        found.append({'id': item.get('InstanceId'), 'name': tags.get('Name', ''),
                                     'state': state,
                                     'type': item.get('InstanceType', ''), 'az': item.get('Placement', {}).get('AvailabilityZone', ''),
                                     'private_ip': item.get('PrivateIpAddress', ''), 'public_ip': item.get('PublicIpAddress', ''),
                                     'region': scan_region, 'account_id': account_id,
                                     'region_name': location['region_name'], 'country_name': location['country_name'],
                                     'country_flag': location['country_flag'], 'country_code': location['country_code'],
                                     'gfw_enabled': bool(deployment['gfw_enabled']) if deployment and 'gfw_enabled' in deployment.keys() else False,
                                     'gfw_check_once': bool(deployment['gfw_check_once']) if deployment and 'gfw_check_once' in deployment.keys() else False,
                                     'port': int(deployment['port']) if deployment and deployment['port'] else 22,
                                     'vcpu': type_info.get('vcpu'), 'memory_mib': type_info.get('memory_mib'),
                                     'bandwidth': type_info.get('bandwidth', '按实例型号/网络适配器')})
        except Exception as exc:
            error = f'{scan_region}: {exc}'
            log('aws_instances_scan', result='failed', detail=f'account={account_id}, {error}')
        return found, error
    if requested_region == 'all':
        rows = []
        errors = []
        with ThreadPoolExecutor(max_workers=min(12, max(1, len(regions)))) as pool:
            futures = [pool.submit(scan_region, candidate) for candidate in regions]
            for future in as_completed(futures):
                found, error = future.result()
                rows.extend(found)
                if error:
                    errors.append(error)
    else:
        rows, error = scan_region(regions[0])
        errors = [error] if error else []
    # DescribeInstances 失败时保留面板部署记录，避免账号异常导致实例卡片“消失”。
    # 云端详情不可读的实例会以账号异常状态展示，账号恢复后刷新即可补齐真实 IP/状态。
    if (errors or not rows) and deployment_config:
        known = {item.get('id') for item in rows}
        detail = '；'.join(errors[:3])
        for instance_id, deployment in deployment_config.items():
            if instance_id in known:
                continue
            scan_region_name = deployment['region'] or (regions[0] if regions else aws_cfg()['region'])
            if requested_region and requested_region != 'all' and scan_region_name != requested_region:
                continue
            location = aws_region_info(scan_region_name)
            rows.append({'id': instance_id, 'name': deployment['instance_name'] or '未命名实例',
                         'state': 'account_error', 'type': deployment['instance_type'] or '-',
                         'az': '', 'private_ip': '', 'public_ip': '', 'region': scan_region_name,
                         'account_id': account_id, 'region_name': location['region_name'],
                         'country_name': location['country_name'], 'country_flag': location['country_flag'],
                         'country_code': location['country_code'], 'gfw_enabled': bool(deployment['gfw_enabled']),
                         'gfw_check_once': bool(deployment['gfw_check_once']),
                         'port': int(deployment['port'] or 22), 'account_error': True,
                         'error_detail': detail})
    if request.query_params.get('include_errors') == '1':
        return {'items': rows, 'errors': errors, 'checked_regions': regions}
    return rows

def aws_instance_bandwidth_region(account_id: int, region: str, instance_ids: list[str]):
    """Read the latest one-minute EC2 NetworkIn/NetworkOut rates.

    CloudWatch is intentionally queried separately from DescribeInstances so a
    missing cloudwatch permission or an empty metric never blocks the EC2 page.
    """
    client = aws_client('cloudwatch', region, account_id)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=6)
    result = {}
    for instance_id in instance_ids:
        values = {'instance_id': instance_id, 'region': region, 'state': 'ok',
                  'in_bps': None, 'out_bps': None, 'sampled_at': None}
        try:
            for metric_name, key in (('NetworkIn', 'in_bps'), ('NetworkOut', 'out_bps')):
                response = client.get_metric_statistics(
                    Namespace='AWS/EC2', MetricName=metric_name,
                    Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                    StartTime=start, EndTime=end, Period=60, Statistics=['Sum'])
                datapoints = response.get('Datapoints') or []
                if datapoints:
                    latest = max(datapoints, key=lambda item: item.get('Timestamp', ''))
                    seconds = max(1, int(latest.get('Period') or 60))
                    values[key] = max(0.0, float(latest.get('Sum') or 0)) / seconds
                    values['sampled_at'] = latest.get('Timestamp') or values['sampled_at']
            if values['in_bps'] is None and values['out_bps'] is None:
                values['state'] = 'empty'
        except Exception:
            # The frontend should remain clean when CloudWatch is unavailable.
            values['state'] = 'unavailable'
        result[instance_id] = values
    return result

def aws_instance_bandwidth_all(account_id: int, instances: list[dict]):
    grouped = {}
    for item in instances:
        instance_id = str(item.get('id') or item.get('instance_id') or '').strip()
        region = str(item.get('region') or '').strip()
        if instance_id and region:
            grouped.setdefault(region, []).append(instance_id)
    if not grouped:
        return []
    merged = {}
    with ThreadPoolExecutor(max_workers=min(8, len(grouped))) as pool:
        futures = {pool.submit(aws_instance_bandwidth_region, account_id, region, ids): region
                   for region, ids in grouped.items()}
        for future in as_completed(futures):
            try:
                merged.update(future.result())
            except Exception:
                # Do not expose per-region CloudWatch failures in the UI.
                continue
    return [merged.get(str(item.get('id') or item.get('instance_id')), {
        'instance_id': str(item.get('id') or item.get('instance_id') or ''),
        'region': item.get('region') or '', 'state': 'unavailable',
        'in_bps': None, 'out_bps': None, 'sampled_at': None,
    }) for item in instances if item.get('id') or item.get('instance_id')]

def aws_live_bandwidth_sample(account_id: int, item: dict):
    """Take a one-second sample inside a panel-managed instance over SSH."""
    instance_id = str(item.get('id') or item.get('instance_id') or '').strip()
    ip = str(item.get('public_ip') or '').strip()
    if not instance_id or not ip:
        return {'instance_id': instance_id, 'region': item.get('region') or '', 'state': 'unavailable',
                'in_bps': None, 'out_bps': None, 'sampled_at': now()}
    with closing(db_connect()) as c:
        row = c.execute('SELECT password,image_id,probe_token FROM aws_deployments WHERE instance_id=? AND account_id=?',
                        (instance_id, account_id)).fetchone()
    if not row or not (row[0] or '').strip():
        return {'instance_id': instance_id, 'region': item.get('region') or '', 'state': 'unavailable',
                'in_bps': None, 'out_bps': None, 'sampled_at': now()}
    probe_token = (row[2] or '').strip() if len(row) > 2 else ''
    if probe_token:
        try:
            import httpx
            response = httpx.get(f'http://{ip}:9090/v1/bandwidth', headers={'X-Probe-Token': probe_token}, timeout=3.0)
            response.raise_for_status()
            data = response.json()
            return {'instance_id': instance_id, 'region': item.get('region') or '', 'state': data.get('state', 'unavailable'),
                    'in_bps': data.get('in_bps'), 'out_bps': data.get('out_bps'), 'sampled_at': data.get('sampled_at')}
        except Exception:
            pass
    try:
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        windows = 'windows' in (row[1] or '').lower()
        username = 'Administrator' if windows else 'root'
        ssh.connect(ip, port=22, username=username, password=row[0], timeout=4,
                    banner_timeout=4, auth_timeout=4, look_for_keys=False, allow_agent=False)
        if windows:
            command = "powershell -NoProfile -Command \"$a=Get-NetAdapterStatistics | Measure-Object -Property ReceivedBytes -Sum; $b=Get-NetAdapterStatistics | Measure-Object -Property SentBytes -Sum; Write-Output ($a.Sum.ToString()+' '+$b.Sum.ToString()); Start-Sleep -Seconds 1; $a=Get-NetAdapterStatistics | Measure-Object -Property ReceivedBytes -Sum; $b=Get-NetAdapterStatistics | Measure-Object -Property SentBytes -Sum; Write-Output ($a.Sum.ToString()+' '+$b.Sum.ToString())\""
        else:
            command = "awk '/:/{gsub(\":\", \" \" ); rx+=$2;tx+=$10} END{print rx,tx}' /proc/net/dev; sleep 1; awk '/:/{gsub(\":\", \" \" ); rx+=$2;tx+=$10} END{print rx,tx}' /proc/net/dev"
        _, stdout, _ = ssh.exec_command(command, timeout=5)
        lines = [line.strip() for line in stdout.read().decode('utf-8', 'ignore').splitlines() if line.strip()]
        ssh.close()
        first = [float(x) for x in lines[0].split()[:2]]
        second = [float(x) for x in lines[-1].split()[:2]]
        return {'instance_id': instance_id, 'region': item.get('region') or '', 'state': 'live',
                'in_bps': max(0.0, second[0] - first[0]), 'out_bps': max(0.0, second[1] - first[1]),
                'sampled_at': now()}
    except Exception:
        return {'instance_id': instance_id, 'region': item.get('region') or '', 'state': 'unavailable',
                'in_bps': None, 'out_bps': None, 'sampled_at': now()}

def aws_live_bandwidth_all(account_id: int, instances: list[dict]):
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(instances)))) as pool:
        return list(pool.map(lambda item: aws_live_bandwidth_sample(account_id, item), instances[:50]))

@app.post('/api/aws/instances/live-bandwidth')
async def aws_instances_live_bandwidth(request: Request):
    logged_in(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, '请求数据无效')
    account_id = int(data.get('account_id') or 0) if isinstance(data, dict) else 0
    instances = data.get('instances') if isinstance(data, dict) else []
    if not account_id or not isinstance(instances, list):
        return []
    return await asyncio.to_thread(aws_live_bandwidth_all, account_id, instances)

@app.post('/api/aws/instances/bandwidth')
async def aws_instances_bandwidth(request: Request):
    logged_in(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, '请求数据无效')
    account_id = int(data.get('account_id') or 0) if isinstance(data, dict) else 0
    instances = data.get('instances') if isinstance(data, dict) else []
    if not account_id or not isinstance(instances, list):
        return []
    return await asyncio.to_thread(aws_instance_bandwidth_all, account_id, instances[:50])

def aws_instance_action_core(instance_id: str, action: str, region: str = '', account_id: int = 0):
    if action not in {'start', 'stop', 'reboot', 'terminate'}:
        raise HTTPException(400, 'AWS action 必须是 start、stop、reboot 或 terminate')
    client = aws_ec2(region or None, account_id or None)
    method = {'start': 'start_instances', 'stop': 'stop_instances', 'reboot': 'reboot_instances',
              'terminate': 'terminate_instances'}[action]
    response = getattr(client, method)(InstanceIds=[instance_id])
    if action == 'terminate':
        with closing(db_connect()) as c:
            c.execute('DELETE FROM aws_deployments WHERE instance_id=? AND (?=0 OR account_id=?)',
                      (instance_id, account_id, account_id))
            c.commit()
    log(f'aws_{action}', instance=f'aws:{instance_id}', detail=f'region={region or aws_cfg()["region"]}')
    return {'ok': True, 'action': action, 'instance_id': instance_id,
            'response': response.get('StartingInstances') or response.get('StoppingInstances') or
                       response.get('RebootingInstances') or response.get('TerminatingInstances') or []}

async def aws_terminate_background(instance_id: str, region: str, account_id: int):
    try:
        await asyncio.to_thread(aws_instance_action_core, instance_id, 'terminate', region, account_id)
        await notify_telegram_event('EC2 实例已删除', 实例=instance_id, 区域=region)
    except Exception as exc:
        log('aws_terminate', instance=f'aws:{instance_id}', result='failed', detail=str(exc))
    finally:
        aws_terminating_instances.discard(instance_id)

@app.post('/api/aws/instances/{instance_id}/{action}')
async def aws_instance_action(instance_id: str, action: str, request: Request, region: str = '', account_id: int = 0):
    logged_in(request)
    # 该通配路由先于专用 reinstall 路由注册；在这里显式转发，避免把重装误当成普通 AWS action。
    if action == 'reinstall':
        try:
            result = await asyncio.to_thread(aws_reinstall_instance_core, instance_id, region, account_id)
        except Exception as exc:
            await notify_telegram_event('EC2 重装失败', 实例=instance_id, 详情=str(getattr(exc, 'detail', exc)))
            raise
        await notify_telegram_event('EC2 正在重装', 旧实例=instance_id, 新实例=result['instance_id'], 区域=result['region'])
        return result
    if action == 'terminate':
        if instance_id in aws_terminating_instances:
            return {'ok': True, 'status': 'deleting', 'instance_id': instance_id, 'message': '实例删除任务已在执行'}
        aws_terminating_instances.add(instance_id)
        asyncio.create_task(aws_terminate_background(instance_id, region, account_id))
        log('aws_terminate', instance=f'aws:{instance_id}', result='queued', detail='已转入后台删除任务')
        return {'ok': True, 'status': 'deleting', 'instance_id': instance_id, 'message': '实例删除任务已提交'}
    result = await asyncio.to_thread(aws_instance_action_core, instance_id, action, region, account_id)
    if action in {'start', 'stop', 'reboot'}:
        await notify_telegram_event(f'EC2 实例已{("开机" if action == "start" else "关机" if action == "stop" else "重启")}', 实例=instance_id, 区域=region)
    return result

def aws_reinstall_instance_core(instance_id: str, region: str = '', account_id: int = 0):
    """Create a replacement from the panel deployment settings, then terminate the old instance."""
    with closing(db_connect()) as c:
        c.row_factory = sqlite3.Row
        row = c.execute('SELECT * FROM aws_deployments WHERE instance_id=? AND (?=0 OR account_id=?)',
                        (instance_id, account_id, account_id)).fetchone()
    if not row:
        raise HTTPException(400, '该实例不是由面板创建，缺少镜像和登录密码，无法自动重装；请在面板新建实例')
    if not (row['password'] or '').strip():
        raise HTTPException(400, '该实例没有保存登录密码，无法自动重装；请在面板新建实例')
    target_region = row['region'] or region or aws_cfg()['region']
    req = AWSCreateRequest(
        account_id=int(row['account_id']), region=target_region,
        image_id=row['image_id'], instance_type=row['instance_type'], count=1,
        architecture=row['architecture'] if 'architecture' in row.keys() else 'x86_64',
        disk_gb=int(row['disk_gb'] or 0) if 'disk_gb' in row.keys() else 0,
        no_public_ip=bool(row['no_public_ip']) if 'no_public_ip' in row.keys() else False,
        spot=bool(row['spot']) if 'spot' in row.keys() else False,
        ipv6=bool(row['ipv6']) if 'ipv6' in row.keys() else False,
        static_ip=bool(row['static_ip']) if 'static_ip' in row.keys() else False,
        ip_cidr=row['ip_cidr'] or '' if 'ip_cidr' in row.keys() else '',
        password=row['password'] or '', user_data=aws_saved_boot_script(row['user_data'] or ''),
        name=row['instance_name'] or '', port=int(row['port'] or 22),
        auto_enabled=bool(row['auto_enabled']),
        gfw_enabled=bool(row['gfw_enabled']) if 'gfw_enabled' in row.keys() else False,
        gfw_probe_hosts=row['gfw_probe_hosts'] or '' if 'gfw_probe_hosts' in row.keys() else '',
        gfw_check_once=bool(row['gfw_check_once']) if 'gfw_check_once' in row.keys() else False,
    )
    created = aws_create_instance_core(req)
    new_id = created['instance_id']
    try:
        client = aws_ec2(target_region, int(row['account_id']))
        client.terminate_instances(InstanceIds=[instance_id])
    except Exception as exc:
        log('aws_reinstall', instance=f'aws:{instance_id}', result='failed', detail=f'新实例 {new_id} 已创建，但旧实例终止失败：{exc}')
        raise HTTPException(502, f'新实例 {new_id} 已创建，但旧实例终止失败：{exc}')
    with closing(db_connect()) as c:
        c.execute('DELETE FROM aws_deployments WHERE instance_id=? AND account_id=?', (instance_id, int(row['account_id'])))
        c.commit()
    log('aws_reinstall', instance=f'aws:{instance_id}', detail=f'新实例={new_id}, region={target_region}')
    return {'ok': True, 'old_instance_id': instance_id, 'instance_id': new_id, 'region': target_region,
            'message': '重装任务已提交，新实例正在启动，公网 IP 稍后刷新'}

@app.post('/api/aws/instances/{instance_id}/reinstall')
async def aws_reinstall_instance(instance_id: str, request: Request, region: str = '', account_id: int = 0):
    logged_in(request)
    try:
        result = await asyncio.to_thread(aws_reinstall_instance_core, instance_id, region, account_id)
    except Exception as exc:
        await notify_telegram_event('EC2 重装失败', 实例=instance_id, 详情=str(getattr(exc, 'detail', exc)))
        raise
    await notify_telegram_event('EC2 正在重装', 旧实例=instance_id, 新实例=result['instance_id'], 区域=result['region'])
    return result

@app.patch('/api/aws/instances/{instance_id}')
def aws_update_deployment(instance_id: str, req: AWSDeploymentUpdateRequest, request: Request, account_id: int = 0):
    logged_in(request)
    with closing(db_connect()) as c:
        row = c.execute('SELECT account_id FROM aws_deployments WHERE instance_id=? AND (?=0 OR account_id=?)',
                        (instance_id, account_id, account_id)).fetchone()
        if not row:
            raise HTTPException(404, '面板中没有找到该实例记录')
        c.execute('''UPDATE aws_deployments
                     SET user_data=?, instance_name=?, port=?, auto_enabled=?, gfw_enabled=?, gfw_probe_hosts=?, backup_group=?, failures=0
                     WHERE instance_id=? AND account_id=?''',
                  (req.user_data, req.instance_name.strip(), req.port, int(req.auto_enabled),
                   int(req.gfw_enabled), req.gfw_probe_hosts.strip(), req.backup_group.strip(), instance_id, row[0]))
        c.commit()
    log('aws_edit_deployment', instance=f'aws:{instance_id}', detail='开机脚本和检测配置已更新')
    return {'ok': True, 'message': '实例配置已保存；下次重装或同组替补时将使用新的开机脚本'}

@app.get('/api/aws/instances/{instance_id}/config')
def aws_get_deployment_config(instance_id: str, request: Request, account_id: int = 0):
    logged_in(request)
    with closing(db_connect()) as c:
        c.row_factory = sqlite3.Row
        row = c.execute('SELECT instance_name,user_data,port,auto_enabled,gfw_enabled,gfw_probe_hosts,backup_group,group_name FROM aws_deployments WHERE instance_id=? AND (?=0 OR account_id=?)',
                        (instance_id, account_id, account_id)).fetchone()
    if not row:
        raise HTTPException(404, '面板中没有找到该实例记录')
    return dict(row)

def aws_instance_record(client, instance_id):
    response = client.describe_instances(InstanceIds=[instance_id])
    reservations = response.get('Reservations', [])
    instances = reservations[0].get('Instances', []) if reservations else []
    if not instances:
        raise HTTPException(404, 'AWS 实例不存在或无权限访问')
    item = instances[0]
    interface = (item.get('NetworkInterfaces') or [{}])[0]
    association = interface.get('Association') or {}
    return item, interface, association

@app.post('/api/aws/instances/{instance_id}/rotate-ip')
async def aws_rotate_ip(instance_id: str, request: Request, region: str = '', port: int = 443, account_id: int = 0):
    logged_in(request)
    return await aws_rotate_ip_core(instance_id, region, port, account_id)

def aws_rotate_ip_provider(instance_id: str, region: str = '', account_id: int = 0):
    client = aws_ec2(region or None, account_id or None)
    item, interface, association = aws_instance_record(client, instance_id)
    old_ip = item.get('PublicIpAddress') or association.get('PublicIp') or ''
    old_allocation = association.get('AllocationId')
    old_association = association.get('AssociationId')
    new_ip = ''
    if old_allocation:
        # Elastic IP：申请新地址并关联到原网卡，成功后再释放旧地址。
        allocated = client.allocate_address(Domain='vpc')
        new_allocation = allocated['AllocationId']
        try:
            association_args = {'AllocationId': new_allocation, 'NetworkInterfaceId': interface['NetworkInterfaceId'], 'AllowReassociation': True}
            if interface.get('PrivateIpAddress'):
                association_args['PrivateIpAddress'] = interface['PrivateIpAddress']
            client.associate_address(**association_args)
            new_ip = allocated['PublicIp']
            if old_association:
                client.disassociate_address(AssociationId=old_association)
            client.release_address(AllocationId=old_allocation)
        except Exception:
            client.release_address(AllocationId=new_allocation)
            raise
    else:
        # AWS 自动公网 IP：停止再启动实例，AWS 通常会重新分配公网地址。
        state = item.get('State', {}).get('Name')
        if state != 'stopped':
            client.stop_instances(InstanceIds=[instance_id])
            client.get_waiter('instance_stopped').wait(InstanceIds=[instance_id])
        client.start_instances(InstanceIds=[instance_id])
        client.get_waiter('instance_running').wait(InstanceIds=[instance_id])
        refreshed, _, _ = aws_instance_record(client, instance_id)
        new_ip = refreshed.get('PublicIpAddress') or ''
    return old_ip, new_ip

async def aws_rotate_ip_core(instance_id: str, region: str = '', port: int = 443, account_id: int = 0,
                             gfw_enabled: bool = False):
    old_ip, new_ip = await asyncio.to_thread(aws_rotate_ip_provider, instance_id, region, account_id)
    if old_ip:
        blacklist(old_ip, 'AWS 手动换 IP 前的旧 IP')
    if not new_ip:
        log('aws_rotate_ip', instance=f'aws:{instance_id}', old_ip=old_ip, result='failed', detail='AWS 未返回新的公网 IP')
        raise HTTPException(500, 'AWS 未返回新的公网 IP，请确认子网自动分配公网 IPv4 已启用')
    # 仅当已配置探针并明确判定为 blocked 时，才把新 IP 加入黑名单。
    # 单纯 TCP 失败可能是实例刚启动、服务尚未监听、端口填错或安全组未生效，
    # 不能据此判断 IP 被墙，否则会误杀正常 IP。
    probe_cfg = aws_probe_config()
    if gfw_enabled and probe_cfg['configured']:
        gfw_result = await aws_gfw_probe(new_ip, port)
        if gfw_result['state'] == 'blocked':
            blacklist(new_ip, f'AWS 新 IP 经探针确认疑似被墙：{gfw_result["detail"]}')
            log('aws_rotate_ip', instance=f'aws:{instance_id}', old_ip=old_ip, new_ip=new_ip, result='failed', detail=gfw_result['detail'])
            raise HTTPException(502, f'AWS 新 IP {new_ip} 经探针确认被墙，已加入黑名单')
        if gfw_result['state'] == 'unknown':
            log('aws_rotate_ip', instance=f'aws:{instance_id}', old_ip=old_ip, new_ip=new_ip, result='unknown', detail=gfw_result['detail'])
            raise HTTPException(502, f'AWS 新 IP {new_ip} 暂时无法确认，未加入黑名单：{gfw_result["detail"]}')
        reachable = True
    else:
        reachable = await tcp_probe(new_ip, port, 5)
        if not reachable:
            log('aws_rotate_ip', instance=f'aws:{instance_id}', old_ip=old_ip, new_ip=new_ip, result='unknown', detail=f'tcp/{port} 未响应，未加入黑名单')
            raise HTTPException(502, f'AWS 新 IP {new_ip} 的 TCP/{port} 暂未响应，未加入黑名单，将在下次检测重试')
    log('aws_rotate_ip', instance=f'aws:{instance_id}', old_ip=old_ip, new_ip=new_ip, detail='AWS IP 已更换并通过 TCP 探测')
    await notify_telegram_event('AWS IP 已更换', 实例=instance_id, 旧IP=old_ip, 新IP=new_ip, 区域=region)
    return {'ok': True, 'old_ip': old_ip, 'new_ip': new_ip, 'reachable': True}

def aws_create_instance_core(req: AWSCreateRequest, register=True):
    if not req.region or not req.image_id or not req.instance_type:
        raise HTTPException(400, '账号、区域、镜像和实例类型不能为空')
    if not req.password.strip():
        raise HTTPException(400, '必须填写登录密码；当前面板不使用 EC2 密钥对登录')
    if req.architecture not in {'x86_64', 'arm64'}:
        raise HTTPException(400, '架构只能是 x86_64 或 arm64')
    if req.static_ip and req.count != 1:
        raise HTTPException(400, '批量创建时不能同时绑定静态 IP')
    if req.no_public_ip and req.static_ip:
        raise HTTPException(400, '禁用公网 IP 与静态 IP 不能同时启用')
    client = aws_ec2(req.region, req.account_id)
    subnet, security_group_id = aws_default_subnet_and_open_sg(client)
    params = {'ImageId': req.image_id, 'InstanceType': req.instance_type, 'MinCount': req.count, 'MaxCount': req.count}
    network_interface = {
        'DeviceIndex': 0,
        'SubnetId': subnet['SubnetId'],
        'Groups': [security_group_id],
        'AssociatePublicIpAddress': not req.no_public_ip,
    }
    if req.ipv6:
        network_interface['Ipv6AddressCount'] = 1
    params['NetworkInterfaces'] = [network_interface]
    if req.disk_gb:
        params['BlockDeviceMappings'] = [{'DeviceName': '/dev/sda1', 'Ebs': {'VolumeSize': req.disk_gb,
                                  'VolumeType': 'gp3', 'DeleteOnTermination': True}}]
    user_data, login_username = aws_password_user_data(client, req.image_id, req.password, req.user_data)
    if user_data and not user_data.startswith(('#cloud-', '#include', 'Content-Type:', '<powershell>')):
        user_data = '#cloud-boothook\n' + user_data
    if user_data:
        params['UserData'] = user_data
    if req.spot:
        params['InstanceMarketOptions'] = {'MarketType': 'spot'}
    if req.name:
        params['TagSpecifications'] = [{'ResourceType': 'instance', 'Tags': [{'Key': 'Name', 'Value': req.name.strip()}]}]
    try:
        response = client.run_instances(**params)
        instances = response.get('Instances') or []
        if not instances:
            raise RuntimeError('AWS 未返回创建的实例')
        instance_ids = [item['InstanceId'] for item in instances]
        instance_id = instance_ids[0]
        static_ip = ''
        if req.static_ip:
            allocated = client.allocate_address(Domain='vpc')
            client.associate_address(AllocationId=allocated['AllocationId'], InstanceId=instance_id)
            static_ip = allocated.get('PublicIp', '')
        with closing(db_connect()) as c:
            row = c.execute('SELECT group_name FROM aws_accounts WHERE id=?', (req.account_id,)).fetchone()
            group_name = (row[0] if row else '') or 'default'
            if register:
                for created_id in instance_ids:
                    c.execute('''INSERT INTO aws_deployments(group_name,account_id,instance_id,region,image_id,instance_type,password,user_data,instance_name,port,auto_enabled,failures,created_at,gfw_enabled,gfw_probe_hosts,gfw_check_once,architecture,disk_gb,no_public_ip,spot,ipv6,static_ip,ip_cidr,backup_group,backup_max_instances)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                              (group_name, req.account_id, created_id, req.region, req.image_id, req.instance_type,
                               req.password, req.user_data.strip(), req.name.strip(), req.port, 1 if req.auto_enabled else 0, 0, now(),
                               1 if req.gfw_enabled else 0, req.gfw_probe_hosts.strip(), 1 if req.gfw_check_once else 0,
                               req.architecture, req.disk_gb, 1 if req.no_public_ip else 0,
                               1 if req.spot else 0, 1 if req.ipv6 else 0, 1 if req.static_ip else 0, req.ip_cidr.strip(),
                               req.backup_group.strip(), req.backup_max_instances))
                c.commit()
        log('aws_create_instance', instance=f'aws:{instance_id}', detail=f'region={req.region}, type={req.instance_type}, count={len(instance_ids)}, group={group_name}, spot={req.spot}, gfw={req.gfw_enabled}')
        return {'ok': True, 'instance_id': instance_id, 'instance_ids': instance_ids, 'static_ip': static_ip,
                'ip_cidr': req.ip_cidr, 'group_name': group_name, 'gfw_enabled': req.gfw_enabled,
                'security_group_id': security_group_id, 'login_username': login_username}
    except HTTPException:
        raise
    except Exception as exc:
        log('aws_create_instance', result='failed', detail=str(exc))
        raise HTTPException(500, str(exc))

@app.post('/api/aws/instances/create')
def aws_create_instance(req: AWSCreateRequest, request: Request):
    logged_in(request)
    return aws_create_instance_core(req)

async def aws_replace_deployment(row, reason):
    with closing(db_connect()) as c:
        backup_group = (row['backup_group'] or row['group_name'] or '').strip() if 'backup_group' in row.keys() else (row['group_name'] or '').strip()
        # 严格同组：未分组账号不能替补任何分组；备用账号组为空时也不允许跨组替补。
        if not backup_group or backup_group == 'default':
            raise RuntimeError('该实例未配置有效备用账号组，未执行跨组替补')
        max_instances = int(row['backup_max_instances'] or 0) if 'backup_max_instances' in row.keys() else 0
        accounts = c.execute("""SELECT a.id FROM aws_accounts a
                               WHERE a.id<>? AND trim(COALESCE(a.group_name,''))=?
                               ORDER BY a.id""", (row['account_id'], backup_group)).fetchall()
        if max_instances:
            accounts = [a for a in accounts if c.execute('SELECT COUNT(*) FROM aws_deployments WHERE account_id=?', (a[0],)).fetchone()[0] < max_instances]
    if not accounts:
        raise RuntimeError(f'分组 {row["group_name"]} 没有可替补的其他 AWS 账号')
    last_error = reason
    for (account_id,) in accounts:
        req = AWSCreateRequest(account_id=account_id, region=row['region'], image_id=row['image_id'], instance_type=row['instance_type'],
                               password=row['password'] or '', user_data=aws_saved_boot_script(row['user_data'] or ''), name=row['instance_name'] or '',
                               port=row['port'], auto_enabled=bool(row['auto_enabled']),
                               gfw_enabled=bool(row['gfw_enabled']) if 'gfw_enabled' in row.keys() else False,
                               gfw_probe_hosts=(row['gfw_probe_hosts'] or '') if 'gfw_probe_hosts' in row.keys() else '',
                               gfw_check_once=bool(row['gfw_check_once']) if 'gfw_check_once' in row.keys() else False,
                               backup_group=(row['backup_group'] or '') if 'backup_group' in row.keys() else '',
                               backup_max_instances=int(row['backup_max_instances'] or 0) if 'backup_max_instances' in row.keys() else 0,
                               architecture=row['architecture'] if 'architecture' in row.keys() else 'x86_64',
                               disk_gb=int(row['disk_gb'] or 0) if 'disk_gb' in row.keys() else 0,
                               no_public_ip=bool(row['no_public_ip']) if 'no_public_ip' in row.keys() else False,
                               spot=bool(row['spot']) if 'spot' in row.keys() else False,
                               ipv6=bool(row['ipv6']) if 'ipv6' in row.keys() else False,
                               static_ip=bool(row['static_ip']) if 'static_ip' in row.keys() else False,
                               ip_cidr=row['ip_cidr'] or '' if 'ip_cidr' in row.keys() else '')
        try:
            created = await asyncio.to_thread(aws_create_instance_core, req, False)
            with closing(db_connect()) as c:
                c.execute('UPDATE aws_deployments SET account_id=?,instance_id=?,failures=0 WHERE id=?', (account_id, created['instance_id'], row['id']))
                c.commit()
            log('aws_account_failover', instance=f'aws:{created["instance_id"]}', result='ok', detail=f'分组={row["group_name"]}, 原因={reason}, 账号={account_id}')
            await notify_telegram_event('AWS 账号已自动替补成', 分组=row['group_name'], 原账号=row['account_id'], 新账号=account_id, 新实例=created['instance_id'], 原因=reason)
            return created
        except Exception as exc:
            last_error = str(exc)
            log('aws_account_failover', result='failed', detail=f'分组={row["group_name"]}, 账号={account_id}, {last_error}')
    await notify_telegram_event('AWS 账号替补失败', 分组=row['group_name'], 原账号=row['account_id'], 原因=last_error)
    raise RuntimeError(f'分组 {row["group_name"]} 的替补账号全部失败：{last_error}')

def is_aws_account_failure(exc):
    """只有 AWS 账号/API 鉴权或账号状态异常，才允许触发同组账号替补。"""
    code = ''
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        code = str(response.get('Error', {}).get('Code', ''))
    text = f'{code} {exc}'.lower()
    return any(x in text for x in (
        'invalidclienttokenid', 'invalidaccesskeyid', 'signaturedoesnotmatch',
        'expiredtoken', 'accessdenied', 'unauthorizedoperation', 'authfailure',
        'accountdisabled', 'invalidclienttoken', 'account is currently blocked',
        'not recognized as a valid account', 'accountblocked', 'account verification',
        'accountverification', 'suspended', 'on hold', 'invalid account',
        'account is currently', 'not a valid account'
    ))

async def aws_auto_monitor():
    """每 2 分钟检测启用自动监控的实例；失败先换 IP，账号异常则切换同组账号重建。"""
    await asyncio.sleep(10)
    while True:
        try:
            with closing(db_connect()) as c:
                c.row_factory = sqlite3.Row
                rows = c.execute('SELECT * FROM aws_deployments WHERE auto_enabled=1 OR gfw_enabled=1 ORDER BY id').fetchall()
            for row in rows:
                try:
                    client = await asyncio.to_thread(aws_ec2, row['region'], row['account_id'])
                    item, _, _ = await asyncio.to_thread(aws_instance_record, client, row['instance_id'])
                    current_ip = item.get('PublicIpAddress') or ''
                    if not current_ip:
                        with closing(db_connect()) as c:
                            c.execute('UPDATE aws_deployments SET failures=0 WHERE id=?', (row['id'],)); c.commit()
                        log('aws_auto_check', instance=f'aws:{row["instance_id"]}', result='skipped', detail='实例没有公网 IP，跳过 GFW/端口检测')
                        continue
                    if row['gfw_enabled'] and aws_probe_config()['configured']:
                        gfw_result = await aws_gfw_probe(current_ip, row['port'])
                        if gfw_result['state'] == 'unknown':
                            with closing(db_connect()) as c:
                                c.execute('UPDATE aws_deployments SET failures=0 WHERE id=?', (row['id'],)); c.commit()
                            log('aws_gfw_check', instance=f'aws:{row["instance_id"]}', result='unknown', detail=gfw_result['detail'])
                            continue
                        reachable = gfw_result['state'] == 'ok'
                        detection_detail = gfw_result['detail']
                    else:
                        reachable = bool(current_ip and await tcp_probe(current_ip, row['port'], 5))
                        detection_detail = f'tcp/{row["port"]}'
                    failures = 0 if reachable else int(row['failures']) + 1
                    with closing(db_connect()) as c:
                        c.execute('UPDATE aws_deployments SET failures=? WHERE id=?', (failures, row['id']))
                        c.commit()
                    log('aws_auto_check', instance=f'aws:{row["instance_id"]}', old_ip=current_ip, result='ok' if reachable else 'failed', detail=f'分组={row["group_name"]}, 检测={detection_detail}, failure={failures}')
                    if not reachable and failures >= 2:
                        try:
                            await aws_rotate_ip_core(row['instance_id'], row['region'], row['port'], row['account_id'], bool(row['gfw_enabled']))
                            with closing(db_connect()) as c:
                                c.execute('UPDATE aws_deployments SET failures=0 WHERE id=?', (row['id'],)); c.commit()
                        except Exception as exc:
                            # IP 检测失败本身不替补账号；换 IP 失败后留在当前账号重试。
                            if isinstance(exc, HTTPException) and exc.status_code in (500, 502):
                                log('aws_auto_rotate', instance=f'aws:{row["instance_id"]}', result='failed', detail=f'当前账号换 IP 失败，等待下次重试：{exc.detail}')
                            elif is_aws_account_failure(exc):
                                await aws_replace_deployment(row, f'AWS 账号/API 异常：{exc}')
                            else:
                                log('aws_auto_rotate', instance=f'aws:{row["instance_id"]}', result='failed', detail=f'当前账号换 IP 异常，暂不替补：{exc}')
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if is_aws_account_failure(exc):
                        await aws_replace_deployment(row, f'AWS 账号故障/封禁：{exc}')
                    else:
                        log('aws_auto_check', instance=f'aws:{row["instance_id"]}', result='failed', detail=f'检测异常，暂不替补：{exc}')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log('aws_auto_monitor', result='failed', detail=str(exc))
        await asyncio.sleep(120)

async def aws_quota_monitor():
    """按面板设置定期检查每个 AWS 账号的 EC2 vCPU 配额，并在状态变化时通知 Telegram。"""
    await asyncio.sleep(20)
    while True:
        cfg = aws_quota_config()
        try:
            with closing(db_connect()) as c:
                account_ids = [row[0] for row in c.execute('SELECT id FROM aws_accounts ORDER BY id').fetchall()]
            for account_id in account_ids:
                if cfg['enabled']:
                    try:
                        result = await asyncio.to_thread(aws_quota_check_core, account_id, cfg['region'], cfg['alert_ratio'])
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        result = await asyncio.to_thread(save_aws_quota_error, account_id, cfg['region'], exc)
                    try:
                        await aws_runtime_scan_locked(account_id)
                    except Exception as exc:
                        log('aws_instance_runtime', result='failed', detail=f'账号={account_id}, region={cfg["region"]}, {exc}')
                    await notify_aws_quota_result(result)
                    log('aws_quota_auto_check', result='ok' if result.get('ok') else 'failed',
                        detail=f'账号={account_id}, region={cfg["region"]}, state={result["state"]}')
                else:
                    try:
                        await aws_runtime_scan_locked(account_id)
                    except Exception as exc:
                        log('aws_instance_runtime', result='failed', detail=f'账号={account_id}, region={cfg["region"]}, {exc}')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log('aws_quota_monitor', result='failed', detail=str(exc))
        await asyncio.sleep(cfg['interval_seconds'])

@app.get('/api/health')
def health(request: Request): logged_in(request); return {'ok': True, 'time': now()}
@app.post('/api/probe')
async def probe(req: ProbeRequest, request: Request):
    logged_in(request)
    ok = await tcp_probe(req.ip, req.port, req.timeout); log('probe', old_ip=req.ip, result='ok' if ok else 'failed', detail=f'tcp/{req.port}'); return {'ip': req.ip, 'port': req.port, 'reachable': ok}
@app.get('/api/blacklist')
def get_blacklist(request: Request):
    logged_in(request)
    with closing(db_connect()) as c:
        c.row_factory = sqlite3.Row; return [dict(x) for x in c.execute('SELECT * FROM ip_blacklist ORDER BY created_at DESC')]
@app.get('/api/logs')
def get_logs(request: Request, limit: int = 100):
    logged_in(request)
    with closing(db_connect()) as c:
        c.row_factory = sqlite3.Row; return [dict(x) for x in c.execute('SELECT * FROM operation_log ORDER BY id DESC LIMIT ?', (min(limit,500),))]
def gcp_compute():
    if os.getenv('GCP_MOCK','1') == '1': return None
    if Path(CREDENTIALS_PATH).is_file():
        os.environ.setdefault('GOOGLE_APPLICATION_CREDENTIALS', str(CREDENTIALS_PATH))
    from google.cloud import compute_v1; return compute_v1

def monitor_failure_count(instance: str, value=None):
    key = f'failure_count:{instance}'
    with closing(db_connect()) as c:
        if value is None:
            row = c.execute('SELECT value FROM panel_config WHERE key=?', (key,)).fetchone()
            return int(row[0]) if row else 0
        c.execute('INSERT INTO panel_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
        c.commit()

async def auto_monitor():
    """每个配置实例按面板设置定时检测；连续失败后调用同一套换 IP 逻辑。"""
    await asyncio.sleep(5)
    while True:
        cfg = panel_config()
        interval = max(60, min(int(cfg.get('interval_seconds', 120)), 3600))
        try:
            if cfg.get('auto_enabled') and cfg['project'] and cfg['zone'] and cfg['instance'] and gcp_compute() is not None:
                obj = get_instance(cfg['project'], cfg['zone'], cfg['instance'])
                _, _, current_ip = instance_ip_and_access(obj)
                if current_ip:
                    reachable = await tcp_probe(current_ip, cfg['port'], 5)
                    count = 0 if reachable else monitor_failure_count(cfg['instance']) + 1
                    monitor_failure_count(cfg['instance'], count)
                    log('auto_check', cfg['instance'], old_ip=current_ip, result='ok' if reachable else 'failed', detail=f'tcp/{cfg["port"]}, failure={count}')
                    if not reachable and count >= int(cfg.get('failures_required', 2)):
                        await rotate_ip_core(RotateRequest(project=cfg['project'], zone=cfg['zone'], instance=cfg['instance'], port=cfg['port']))
                        monitor_failure_count(cfg['instance'], 0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log('auto_check', cfg.get('instance'), result='failed', detail=f'自动检测异常：{exc}')
        await asyncio.sleep(interval)

def wait_operation(operation):
    operation.result()
    if getattr(operation, 'error_code', None):
        raise RuntimeError(getattr(operation, 'error_message', 'GCP operation failed'))

def get_instance(project, zone, instance):
    return gcp_compute().InstancesClient().get(project=project, zone=zone, instance=instance)

def instance_ip_and_access(instance_obj):
    for nic in instance_obj.network_interfaces:
        if nic.access_configs:
            access = nic.access_configs[0]
            return nic.name or 'nic0', access.name or 'External NAT', access.nat_ip or ''
    return 'nic0', 'External NAT', ''

@app.get('/api/instance/{project}/{zone}/{instance}')
def instance_info(project, zone, instance, request: Request):
    logged_in(request)
    if gcp_compute() is None:
        return {'project': project, 'zone': zone, 'instance': instance, 'status': 'MOCK', 'ip': ''}
    obj = get_instance(project, zone, instance)
    nic, access, ip = instance_ip_and_access(obj)
    return {'project': project, 'zone': zone, 'instance': instance, 'status': obj.status, 'ip': ip, 'network_interface': nic, 'access_config': access}
@app.post('/api/instances/{project}/{zone}/{instance}/{action}')
def instance_action(project, zone, instance, action, request: Request):
    logged_in(request)
    if action not in {'start','stop','reset'}: raise HTTPException(400, 'action 必须是 start、stop 或 reset')
    if gcp_compute() is None: log(action, instance=instance, detail='mock mode'); return {'ok':True,'mode':'mock','action':action}
    client = getattr(gcp_compute(), 'InstancesClient')(); op = getattr(client, action)(project=project, zone=zone, instance=instance); log(action, instance=instance); return {'ok':True,'operation':op.name}

@app.post('/api/credentials')
async def upload_credentials(request: Request, file: UploadFile = File(...)):
    logged_in(request)
    if not file.filename or not file.filename.lower().endswith('.json'):
        raise HTTPException(400, '只允许上传 JSON 文件')
    raw = await file.read()
    if len(raw) > 1024 * 1024:
        raise HTTPException(413, 'JSON 文件不能超过 1 MB')
    try:
        data = json.loads(raw.decode('utf-8'))
    except Exception:
        raise HTTPException(400, 'JSON 格式无效')
    if not isinstance(data, dict) or data.get('type') != 'service_account':
        raise HTTPException(400, '这不是 Google Cloud service account JSON')
    path = Path(CREDENTIALS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(path)
    log('upload_credentials', result='ok', detail='service account JSON 已保存')
    return {'ok': True, 'message': 'GCP 凭据已保存', 'path': str(path)}
@app.post('/api/rotate-ip')
async def rotate_ip(req: RotateRequest, request: Request):
    logged_in(request)
    return await rotate_ip_core(req)

async def rotate_ip_core(req: RotateRequest):
    if os.getenv('GCP_MOCK','1') == '1':
        new_ip=f'203.0.113.{int(time.time())%240+1}'; log('rotate_ip', req.instance, new_ip=new_ip, detail='mock mode'); return {'ok':True,'mode':'mock','new_ip':new_ip,'attempts':1}
    try:
        obj = get_instance(req.project, req.zone, req.instance)
        nic, access_name, old_ip = instance_ip_and_access(obj)
        if old_ip:
            blacklist(old_ip, '自动换 IP 前的旧 IP')
            client = gcp_compute().InstancesClient()
            op = client.delete_access_config(project=req.project, zone=req.zone, instance=req.instance, access_config=access_name, network_interface=nic)
            wait_operation(op)
        last_reason = '没有获得新 IP'
        for attempt in range(1, req.max_attempts + 1):
            client = gcp_compute().InstancesClient()
            op = client.add_access_config(project=req.project, zone=req.zone, instance=req.instance, network_interface=nic,
                                          access_config_resource=gcp_compute().AccessConfig(name=access_name, type='ONE_TO_ONE_NAT'))
            wait_operation(op)
            new_obj = get_instance(req.project, req.zone, req.instance)
            _, _, new_ip = instance_ip_and_access(new_obj)
            if not new_ip:
                last_reason = 'GCP 未返回新的公网 IP'
            else:
                with closing(db_connect()) as c:
                    old_blacklisted = c.execute('SELECT 1 FROM ip_blacklist WHERE ip=?', (new_ip,)).fetchone()
                reachable = bool(not old_blacklisted and await tcp_probe(new_ip, req.port, 5))
                if reachable:
                    log('rotate_ip', req.instance, old_ip=old_ip, new_ip=new_ip, detail=f'GCP API + TCP probe, attempt={attempt}')
                    await notify_telegram_event('GCP IP 已更换', 实例=req.instance, 旧IP=old_ip, 新IP=new_ip, 区域=req.zone)
                    return {'ok': True, 'old_ip': old_ip, 'new_ip': new_ip, 'attempts': attempt, 'reachable': True}
                # TCP 不通不能证明 IP 被墙：实例启动阶段、服务未监听、端口填写错误
                # 或安全组未生效都会得到同样结果。保留候选 IP，记录为待确认，避免误杀正常 IP。
                last_reason = f'新 IP {new_ip} 黑名单或 tcp/{req.port} 暂未响应'
                log('rotate_ip', req.instance, old_ip=old_ip, new_ip=new_ip, result='unknown', detail=last_reason)
            # Remove the failed candidate before asking GCP for the next ephemeral IP.
            op = client.delete_access_config(project=req.project, zone=req.zone, instance=req.instance,
                                             access_config=access_name, network_interface=nic)
            wait_operation(op)
        raise RuntimeError(f'尝试 {req.max_attempts} 次仍未得到合格 IP：{last_reason}')
    except Exception as exc:
        log('rotate_ip', req.instance, result='failed', detail=str(exc))
        raise HTTPException(500, str(exc))
