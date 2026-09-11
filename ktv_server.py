#!/usr/bin/env python3
"""KTV 门店排队模拟系统 —— 方案与版本管理服务
仅用 Python 标准库：http.server 提供静态页面 + REST API，sqlite3 持久化方案与历史版本。
用法: python3 ktv_server.py [端口]   默认 8000，然后浏览器打开 http://localhost:8000/

数据模型:
  scenarios  —— 方案（同名方案只占一行）
  versions   —— 每次保存生成一个版本（version 从 1 递增），保存参数与模拟结果
旧版单表 scenarios(id,name,created_at,params,results) 在首次启动时自动迁移为版本结构。
"""
import json
import os
import sqlite3
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'ktv_scenarios.db')
PAGE = 'ktv-sim.html'


def _create_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS scenarios(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS versions(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        scenario_id INTEGER NOT NULL,
        version     INTEGER NOT NULL,
        created_at  TEXT NOT NULL,
        params      TEXT NOT NULL,
        results     TEXT NOT NULL,
        UNIQUE(scenario_id, version))""")
    con.execute('CREATE INDEX IF NOT EXISTS idx_versions_scn ON versions(scenario_id)')


def _migrate_legacy(con):
    """旧库：scenarios 表单行存快照 → 按同名归并为方案 + 多个历史版本。"""
    cols = [r[1] for r in con.execute('PRAGMA table_info(scenarios)').fetchall()]
    if not cols or 'params' not in cols:
        return False
    con.execute('ALTER TABLE scenarios RENAME TO scenarios_old')
    _create_schema(con)
    rows = con.execute(
        'SELECT name,created_at,params,results FROM scenarios_old ORDER BY id').fetchall()
    ver_seq = {}
    for name, created_at, params, results in rows:
        cur = con.execute('SELECT id FROM scenarios WHERE name=?', (name,)).fetchone()
        if cur:
            sid = cur[0]
        else:
            con.execute('INSERT INTO scenarios(name,created_at) VALUES(?,?)',
                        (name, created_at))
            sid = con.execute('SELECT id FROM scenarios WHERE name=?', (name,)).fetchone()[0]
        ver_seq[sid] = ver_seq.get(sid, 0) + 1
        con.execute(
            'INSERT INTO versions(scenario_id,version,created_at,params,results)'
            ' VALUES(?,?,?,?,?)', (sid, ver_seq[sid], created_at, params, results))
    con.execute('DROP TABLE scenarios_old')
    return True


def connect():
    con = sqlite3.connect(DB)
    _create_schema(con)
    if _migrate_legacy(con):
        con.commit()
    return con


def _version_row(r):
    return {'id': r[0], 'version': r[1], 'created_at': r[2],
            'params': json.loads(r[3]), 'results': json.loads(r[4])}


class Handler(SimpleHTTPRequestHandler):
    # 允许 file:// 方式打开的页面也能访问 API
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == '/':
            self.path = '/' + PAGE
            return super().do_GET()
        if p == '/api/scenarios':
            con = connect()
            out = []
            for sid, name, created_at in con.execute(
                    'SELECT id,name,created_at FROM scenarios ORDER BY id').fetchall():
                vers = [_version_row(r) for r in con.execute(
                    'SELECT id,version,created_at,params,results FROM versions '
                    'WHERE scenario_id=? ORDER BY version', (sid,)).fetchall()]
                out.append({'id': sid, 'name': name, 'created_at': created_at,
                            'versions': vers})
            con.close()
            return self._json(out)
        return super().do_GET()

    def do_POST(self):
        p = urlparse(self.path).path
        if p != '/api/scenarios':
            return self._json({'error': 'not found'}, 404)
        try:
            n = int(self.headers.get('Content-Length') or 0)
            data = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._json({'error': 'bad json'}, 400)
        name = str(data.get('name') or '未命名方案').strip()[:60]
        params = json.dumps(data.get('params') or {}, ensure_ascii=False)
        results = json.dumps(data.get('results') or {}, ensure_ascii=False)
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        con = connect()
        try:
            row = con.execute('SELECT id FROM scenarios WHERE name=?', (name,)).fetchone()
            if row:
                sid = row[0]
                version = con.execute(
                    'SELECT COALESCE(MAX(version),0)+1 FROM versions WHERE scenario_id=?',
                    (sid,)).fetchone()[0]
            else:
                cur = con.execute(
                    'INSERT INTO scenarios(name,created_at) VALUES(?,?)', (name, now))
                sid = cur.lastrowid
                version = 1
            cur = con.execute(
                'INSERT INTO versions(scenario_id,version,created_at,params,results)'
                ' VALUES(?,?,?,?,?)', (sid, version, now, params, results))
            con.commit()
        except sqlite3.Error as e:
            con.close()
            return self._json({'error': str(e)}, 500)
        con.close()
        self._json({'id': sid, 'name': name, 'version': version,
                    'version_id': cur.lastrowid, 'created_at': now})

    def do_DELETE(self):
        p = urlparse(self.path).path
        parts = [x for x in p.split('/') if x]
        # /api/scenarios/<sid>                       删除整个方案（含全部历史版本）
        # /api/scenarios/<sid>/versions/<vid>        仅删除某一个版本
        if len(parts) >= 3 and parts[0] == 'api' and parts[1] == 'scenarios':
            try:
                sid = int(parts[2])
            except ValueError:
                return self._json({'error': 'bad id'}, 400)
            con = connect()
            try:
                if len(parts) == 3:
                    vids = [r[0] for r in con.execute(
                        'SELECT id FROM versions WHERE scenario_id=?', (sid,)).fetchall()]
                    con.execute('DELETE FROM versions WHERE scenario_id=?', (sid,))
                    con.execute('DELETE FROM scenarios WHERE id=?', (sid,))
                    con.commit()
                    con.close()
                    return self._json({'ok': True, 'removed_versions': vids})
                if len(parts) == 5 and parts[3] == 'versions':
                    try:
                        vid = int(parts[4])
                    except ValueError:
                        return self._json({'error': 'bad version id'}, 400)
                    owns = con.execute(
                        'SELECT 1 FROM versions WHERE id=? AND scenario_id=?',
                        (vid, sid)).fetchone()
                    if not owns:
                        con.close()
                        return self._json({'error': 'version not found'}, 404)
                    total = con.execute(
                        'SELECT COUNT(*) FROM versions WHERE scenario_id=?',
                        (sid,)).fetchone()[0]
                    if total <= 1:
                        con.close()
                        return self._json(
                            {'error': '每个方案至少保留一个版本；如不需要请删除整个方案'}, 409)
                    con.execute('DELETE FROM versions WHERE id=?', (vid,))
                    con.commit()
                    con.close()
                    return self._json({'ok': True})
            except sqlite3.Error as e:
                con.close()
                return self._json({'error': str(e)}, 500)
        return self._json({'error': 'not found'}, 404)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    os.chdir(ROOT)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    connect().close()
    print(f'▶ KTV 模拟系统已启动: http://localhost:{port}/')
    print(f'  方案与版本存储(SQLite): {DB}')
    try:
        HTTPServer(('0.0.0.0', port), Handler).serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')
