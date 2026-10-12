#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯会议课程自动播放器：零第三方 Python 依赖，本机运行。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import webbrowser
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Timer
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
DBFILE = ROOT / 'data' / 'training.sqlite3'
UPLOADS = ROOT / 'data' / 'uploads'
WEB = ROOT / 'web'
PORT_DEFAULT = 8765
MAX_JSON_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 30 * 1024**3
VIDEO_EXTS = {'.mp4', '.m4v', '.webm', '.mov', '.mkv'}
MIME = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska'}


@contextmanager
def database():
    con = sqlite3.connect(DBFILE, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=20000')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    DBFILE.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                duration REAL,
                source TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                slot TEXT NOT NULL CHECK(slot IN ('AM','PM')),
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                UNIQUE(day,slot)
            );
            CREATE TABLE IF NOT EXISTS playlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                position INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_playlist_session ON playlist(session_id,position);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS player_status (
                id INTEGER PRIMARY KEY CHECK(id=1),
                ts REAL NOT NULL,
                payload TEXT NOT NULL
            );
        ''')


def event(kind: str, detail: str):
    try:
        with database() as db:
            db.execute('INSERT INTO events(ts,kind,detail) VALUES (?,?,?)', (dt.datetime.now().isoformat(timespec='seconds'), kind[:60], detail[:600]))
            db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)')
    except Exception:
        logging.exception('Failed to record event')


def probe_duration(path: Path) -> float | None:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    try:
        proc = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                               '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                              capture_output=True, text=True, timeout=12, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        val = float(proc.stdout.strip())
        return round(val, 3) if 0 < val < 86400 * 7 else None
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def valid_day(v):
    if not isinstance(v, str):
        raise ValueError('日期格式应为 YYYY-MM-DD')
    try:
        d = dt.date.fromisoformat(v)
    except ValueError:
        raise ValueError('日期格式应为 YYYY-MM-DD')
    if d.isoformat() != v:
        raise ValueError('日期格式应为 YYYY-MM-DD')
    return v


def valid_clock(v):
    if not isinstance(v, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', v):
        raise ValueError('时间格式应为 HH:MM（24小时）')
    return v


def validate_span(start, end):
    start, end = valid_clock(start), valid_clock(end)
    if end <= start:
        raise ValueError('结束时间必须晚于开始时间（每场不能跨日）')
    return start, end


def get_plan():
    with database() as db:
        rows = db.execute('SELECT id,day,slot,start_time,end_time FROM sessions ORDER BY day,start_time,slot').fetchall()
        sessions = [dict(r) for r in rows]
        for s in sessions:
            s['videos'] = [dict(r) for r in db.execute('''
                SELECT v.id,v.title,v.duration,v.source,p.position
                FROM playlist p JOIN videos v ON v.id=p.video_id
                WHERE p.session_id=? ORDER BY p.position,p.id''', (s['id'],))]
    return {'server_now_ms': round(time.time() * 1000), 'sessions': sessions}


def get_library():
    with database() as db:
        return {'videos': [dict(r) for r in db.execute('''
            SELECT id,title,duration,source,path,created_at FROM videos ORDER BY id DESC''')]}


def create_days(obj):
    start_day = dt.date.fromisoformat(valid_day(obj.get('start_day')))
    count = int(obj.get('days', 4))
    if not 1 <= count <= 30:
        raise ValueError('最多一次生成30天')
    am = validate_span(obj.get('am_start','08:30'), obj.get('am_end','12:30'))
    pm = validate_span(obj.get('pm_start','13:30'), obj.get('pm_end','17:30'))
    if am[1] > pm[0]:
        raise ValueError('上午和下午课程时间不能重叠')
    created = 0
    with database() as db:
        for i in range(count):
            day = (start_day + dt.timedelta(days=i)).isoformat()
            for slot, span in (('AM',am),('PM',pm)):
                result = db.execute('INSERT OR IGNORE INTO sessions(day,slot,start_time,end_time) VALUES (?,?,?,?)',
                                    (day,slot,span[0],span[1]))
                created += result.rowcount
    event('plan_changed', f'创建 {created} 个场次，从 {start_day} 开始')
    return {'created':created,'skipped':count*2-created}


def parse_json(handler):
    length = int(handler.headers.get('Content-Length','0'))
    if length <= 0 or length > MAX_JSON_BYTES:
        raise ValueError('JSON请求内容过大或为空')
    try:
        obj = json.loads(handler.rfile.read(length))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('JSON格式错误')
    if not isinstance(obj, dict):
        raise ValueError('请求对象必须为JSON对象')
    return obj


def safe_file_name(name):
    name = Path(name.replace('\\','/')).name.strip().rstrip('. ')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    if not name or len(name) > 200 or Path(name).suffix.lower() not in VIDEO_EXTS:
        raise ValueError('请选择MP4、M4V、WebM、MOV或MKV视频文件')
    return name


def index_video(db, path:Path, source:str):
    resolved = str(path.resolve())
    existing = db.execute('SELECT id FROM videos WHERE path=?',(resolved,)).fetchone()
    if existing:
        return int(existing['id']), False
    duration = probe_duration(path)
    cur = db.execute('INSERT INTO videos(title,path,duration,source) VALUES (?,?,?,?)', (path.stem,resolved,duration,source))
    return cur.lastrowid, True


def import_folder(obj):
    folder = obj.get('folder','')
    if not isinstance(folder,str) or not folder.strip():
        raise ValueError('请输入本机视频文件夹的绝对路径')
    root = Path(folder.strip().strip('"')).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('找不到文件夹，请输入运行服务的电脑上的完整路径')
    recursive = bool(obj.get('recursive',True))
    paths = (p for p in root.rglob('*')) if recursive else root.iterdir()
    added, skipped = [], 0
    with database() as db:
        for p in paths:
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
                continue
            if len(added)+skipped >= 3000:
                raise ValueError('单次最多导入3000个视频')
            video_id, new = index_video(db,p,'本地引用')
            if new: added.append(video_id)
            else: skipped += 1
    event('library_changed', f'导入本机视频：新增{len(added)}，已有{skipped}')
    return {'added':len(added),'skipped':skipped}


def auto_assign():
    """按 Day01/AM、Day01/PM ... 目录自动匹配连续4天的场次。"""
    with database() as db:
        dates = [r['day'] for r in db.execute('SELECT DISTINCT day FROM sessions ORDER BY day')]
        if not dates:
            raise ValueError('请先生成4天的课程计划')
        sessions = {(r['day'],r['slot']):r['id'] for r in db.execute('SELECT * FROM sessions')}
        videos = db.execute('SELECT id,path FROM videos ORDER BY path').fetchall()
        buckets = {}
        for v in videos:
            text = str(v['path']).replace('\\','/')
            dirs = text.split('/')[:-1]
            for j,dir_name in enumerate(dirs):
                match = re.fullmatch(r'(?:day|d)[ _-]*0*([1-9]|[12]\d|30)',dir_name,re.I)
                if not match:
                    continue
                idx = int(match.group(1))-1
                if idx >= len(dates):
                    break
                suffix = dirs[j+1] if j+1 < len(dirs) else ''
                slot = suffix.upper()
                if slot in ('AM','PM'):
                    key = (dates[idx],slot)
                    buckets.setdefault(key,[]).append(dict(v))
                break
        total = 0
        for key,vs in buckets.items():
            session_id = sessions.get(key)
            if session_id is None: continue
            vs.sort(key=lambda x: natural_key(Path(x['path']).name))
            db.execute('DELETE FROM playlist WHERE session_id=?',(session_id,))
            db.executemany('INSERT INTO playlist(session_id,video_id,position) VALUES (?,?,?)',
                           ((session_id,v['id'],i) for i,v in enumerate(vs)))
            total += len(vs)
    event('plan_changed', f'按目录自动分配视频{total}个，涉及{len(buckets)}场')
    return {'assigned':total,'sessions':len(buckets)}


def natural_key(value):
    return [int(n) if n.isdecimal() else n.lower() for n in re.split(r'(\d+)',value)]


class Handler(BaseHTTPRequestHandler):
    server_version = 'TrainingPlayer/1.0'

    def log_message(self, fmt,*args):
        if self.path.startswith('/api/heartbeat') or self.path.startswith('/api/plan'):
            return
        logging.info('%s %s', self.address_string(), fmt % args)

    def send_data(self,data,status=200):
        body = json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self,msg,status=400):
        self.send_data({'error':str(msg)},status)

    def do_GET(self):
        try:
            self._get()
        except (ValueError,sqlite3.Error,OSError) as e:
            logging.exception('GET %s',self.path)
            self.send_error_json(str(e),400)

    def _get(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == '/':
            self.send_response(302)
            self.send_header('Location','/admin')
            self.end_headers()
        elif path == '/api/health':
            self.send_data({'ok':True,'local':True,'time':dt.datetime.now().isoformat()})
        elif path == '/api/plan':
            self.send_data(get_plan())
        elif path == '/api/library':
            self.send_data(get_library())
        elif path == '/api/status':
            with database() as db:
                row=db.execute('SELECT ts,payload FROM player_status WHERE id=1').fetchone()
                events=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 60')]
            hb={'last_seen':row['ts'],'payload':json.loads(row['payload'])} if row else None
            self.send_data({'heartbeat':hb,'events':events,'server_now_ms':round(time.time()*1000)})
        elif re.fullmatch(r'/media/\d+',path):
            self.send_media(int(path.rsplit('/',1)[1]))
        elif path in ('/admin','/player') or path.startswith('/static/'):
            item = 'admin.html' if path == '/admin' else 'player.html' if path == '/player' else path[len('/static/'):]
            target = (WEB/item).resolve()
            if not target.is_relative_to(WEB.resolve()) or not target.is_file():
                self.send_error(404); return
            content=target.read_bytes()
            ctype=mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if target.suffix in ('.html','.css','.js'): ctype += '; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def send_media(self,video_id):
        with database() as db:
            v=db.execute('SELECT path FROM videos WHERE id=?',(video_id,)).fetchone()
        if not v:
            self.send_error(404,'未找到视频'); return
        path=Path(v['path'])
        try:
            size=path.stat().st_size
            if not path.is_file(): raise FileNotFoundError()
        except OSError:
            self.send_error(404,'视频文件丢失'); return
        start,end=0,size-1
        partial=False
        header=self.headers.get('Range')
        if header:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',header.strip())
            if not match or (not match.group(1) and not match.group(2)):
                self.unsat_range(size); return
            a,b=match.groups()
            if a:
                start=int(a)
                if b: end=min(int(b),size-1)
            else:
                suffix=int(b)
                start=max(size-suffix,0)
            if start>=size or start>end or size==0:
                self.unsat_range(size); return
            partial=True
        length=end-start+1
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type',MIME.get(path.suffix.lower(),'application/octet-stream'))
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Content-Length',str(length))
        if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.send_header('Cache-Control','private,max-age=3600')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        try:
            with path.open('rb') as f:
                f.seek(start)
                remaining=length
                while remaining>0:
                    block=f.read(min(1024*512,remaining))
                    if not block:break
                    self.wfile.write(block)
                    remaining-=len(block)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def unsat_range(self,size):
        self.send_response(416)
        self.send_header('Content-Range',f'bytes */{size}')
        self.send_header('Content-Length','0')
        self.end_headers()

    def do_POST(self):
        try:
            self._post()
        except (ValueError,sqlite3.Error,OSError,OverflowError) as e:
            logging.warning('POST %s error: %s',self.path,e)
            self.send_error_json(str(e),400)

    def _post(self):
        p=urlsplit(self.path)
        path=p.path
        if path == '/api/upload':
            q=parse_qs(p.query)
            filename=safe_file_name(q.get('filename',[''])[0])
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=MAX_UPLOAD_BYTES:
                raise ValueError('上传文件大小无效或超过30GB')
            saved=UPLOADS/(uuid.uuid4().hex[:12]+'_'+filename)
            try:
                with saved.open('wb') as out:
                    remaining=size
                    while remaining:
                        chunk=self.rfile.read(min(1024*1024,remaining))
                        if not chunk: raise ValueError('上传中断')
                        out.write(chunk)
                        remaining-=len(chunk)
                with database() as db:
                    id,_=index_video(db,saved,'上传副本')
            except Exception:
                saved.unlink(missing_ok=True)
                raise
            event('library_changed',f'上传视频：{filename}')
            self.send_data({'id':id,'name':filename}); return
        obj=parse_json(self)
        if path=='/api/generate':
            self.send_data(create_days(obj))
        elif path=='/api/import':
            self.send_data(import_folder(obj))
        elif path=='/api/auto-assign':
            self.send_data(auto_assign())
        elif path=='/api/sessions':
            day=valid_day(obj.get('day'))
            slot=obj.get('slot')
            if slot not in ('AM','PM'): raise ValueError('请选择上午或下午')
            start,end=validate_span(obj.get('start_time'),obj.get('end_time'))
            with database() as db:
                db.execute('INSERT INTO sessions(day,slot,start_time,end_time) VALUES (?,?,?,?)',(day,slot,start,end))
            event('plan_changed',f'添加场次 {day} {slot}')
            self.send_data({'ok':True})
        elif path=='/api/event':
            kind=str(obj.get('kind','player'))[:60]
            detail=str(obj.get('detail',''))[:500]
            event(kind,detail)
            self.send_data({'ok':True})
        elif path=='/api/heartbeat':
            payload=json.dumps(obj,ensure_ascii=False)
            if len(payload)>4000: raise ValueError('心跳内容过长')
            with database() as db:
                db.execute('INSERT INTO player_status(id,ts,payload) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET ts=excluded.ts,payload=excluded.payload', (time.time(),payload))
            self.send_data({'ok':True})
        elif re.fullmatch(r'/api/videos/\d+/duration',path):
            video_id=int(path.split('/')[3])
            duration=float(obj.get('duration',0))
            if not 0.1<=duration<=7*86400: raise ValueError('视频时长无效')
            with database() as db:
                result=db.execute('UPDATE videos SET duration=? WHERE id=?',(round(duration,3),video_id))
                if result.rowcount==0: raise ValueError('视频不存在')
            self.send_data({'ok':True})
        else:
            self.send_error(404)

    def do_PUT(self):
        try:
            obj=parse_json(self)
            path=urlsplit(self.path).path
            if re.fullmatch(r'/api/sessions/\d+/items',path):
                session_id=int(path.split('/')[3]); ids=obj.get('video_ids')
                if not isinstance(ids,list) or len(ids)>1000 or any(type(n)!=int or n<1 for n in ids):
                    raise ValueError('请传入视频ID列表（最多1000个）')
                with database() as db:
                    if not db.execute('SELECT 1 FROM sessions WHERE id=?',(session_id,)).fetchone():
                        raise ValueError('场次不存在')
                    if ids:
                        known={r['id'] for r in db.execute(f'SELECT id FROM videos WHERE id IN ({",".join("?"*len(set(ids)))})',list(set(ids)))}
                        if not set(ids).issubset(known):raise ValueError('包含不存在的视频')
                    db.execute('DELETE FROM playlist WHERE session_id=?',(session_id,))
                    db.executemany('INSERT INTO playlist(session_id,video_id,position) VALUES (?,?,?)',
                                   ((session_id,n,i) for i,n in enumerate(ids)))
                event('plan_changed',f'场次{session_id}列表更新：{len(ids)}个视频')
                self.send_data({'ok':True})
            elif re.fullmatch(r'/api/sessions/\d+',path):
                session_id=int(path.rsplit('/',1)[1])
                start,end=validate_span(obj.get('start_time'),obj.get('end_time'))
                day=valid_day(obj.get('day'))
                slot=obj.get('slot')
                if slot not in ('AM','PM'):raise ValueError('场次只能为AM或PM')
                with database() as db:
                    result=db.execute('UPDATE sessions SET day=?,slot=?,start_time=?,end_time=? WHERE id=?',
                                      (day,slot,start,end,session_id))
                    if not result.rowcount:raise ValueError('场次不存在')
                event('plan_changed',f'修改场次{session_id}')
                self.send_data({'ok':True})
            elif re.fullmatch(r'/api/videos/\d+',path):
                video_id=int(path.rsplit('/',1)[1])
                title=str(obj.get('title','')).strip()[:200]
                if not title:raise ValueError('名称不能为空')
                duration=obj.get('duration')
                if duration not in (None,''):
                    duration=float(duration)
                    if not 0.1<=duration<=7*86400: raise ValueError('时长必须大于0')
                else:duration=None
                with database() as db:
                    db.execute('UPDATE videos SET title=?,duration=? WHERE id=?',(title,duration,video_id))
                event('library_changed',f'修改视频{video_id}')
                self.send_data({'ok':True})
            else:self.send_error(404)
        except (ValueError,sqlite3.Error,OSError) as e:
            self.send_error_json(str(e),400)

    def do_DELETE(self):
        try:
            path=urlsplit(self.path).path
            if re.fullmatch(r'/api/sessions/\d+',path):
                item_id=int(path.rsplit('/',1)[1])
                with database() as db:db.execute('DELETE FROM sessions WHERE id=?',(item_id,))
                event('plan_changed',f'删除场次{item_id}')
                self.send_data({'ok':True})
            elif re.fullmatch(r'/api/videos/\d+',path):
                item_id=int(path.rsplit('/',1)[1])
                with database() as db:db.execute('DELETE FROM videos WHERE id=?',(item_id,))
                event('library_changed',f'移除视频{item_id}（不删除磁盘文件）')
                self.send_data({'ok':True})
            else:self.send_error(404)
        except (ValueError,sqlite3.Error,OSError) as e:
            self.send_error_json(str(e),400)


def main():
    parser=argparse.ArgumentParser(description='腾讯会议本地课程自动播放服务')
    parser.add_argument('--port',type=int,default=PORT_DEFAULT)
    parser.add_argument('--host',default='127.0.0.1',help='默认仅本机访问；不建议开放公网')
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if args.host not in ('127.0.0.1','localhost','::1'):
        print('警告：本系统不含登录验证，不允许在不可信网络或公网开放！')
    logging.basicConfig(level=logging.INFO,format='[%(asctime)s] %(message)s')
    init_db()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    server.daemon_threads=True
    print('\n'+'='*56)
    print('  腾讯会议课程自动播放 Web 服务已经启动')
    print(f'  管理后台：http://127.0.0.1:{args.port}/admin')
    print(f'  播放页面：http://127.0.0.1:{args.port}/player')
    print('  关闭服务：在本窗口按 Ctrl+C')
    print('='*56+'\n')
    if not args.no_browser:
        Timer(1,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}/admin')).start()
    try:server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:print('\n服务已停止')
    finally:server.server_close()


if __name__=='__main__':main()
