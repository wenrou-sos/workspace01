#!/usr/bin/env python3
"""KTV 门店排队模拟系统 —— 方案管理服务
仅用 Python 标准库：http.server 提供静态页面 + REST API，sqlite3 持久化方案。
用法: python3 ktv_server.py [端口]   默认 8000，然后浏览器打开 http://localhost:8000/
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


def connect():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS scenarios(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL,
        params     TEXT NOT NULL,
        results    TEXT NOT NULL)""")
    return con


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
            rows = con.execute(
                'SELECT id,name,created_at,params,results FROM scenarios ORDER BY id').fetchall()
            con.close()
            return self._json([{'id': r[0], 'name': r[1], 'created_at': r[2],
                                'params': json.loads(r[3]), 'results': json.loads(r[4])}
                               for r in rows])
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
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        con = connect()
        cur = con.execute(
            'INSERT INTO scenarios(name,created_at,params,results) VALUES(?,?,?,?)',
            (name, now,
             json.dumps(data.get('params') or {}, ensure_ascii=False),
             json.dumps(data.get('results') or {}, ensure_ascii=False)))
        con.commit()
        sid = cur.lastrowid
        con.close()
        self._json({'id': sid, 'name': name, 'created_at': now})

    def do_DELETE(self):
        p = urlparse(self.path).path
        if p.startswith('/api/scenarios/'):
            try:
                sid = int(p.rsplit('/', 1)[1])
            except ValueError:
                return self._json({'error': 'bad id'}, 400)
            con = connect()
            con.execute('DELETE FROM scenarios WHERE id=?', (sid,))
            con.commit()
            con.close()
            return self._json({'ok': True})
        return self._json({'error': 'not found'}, 404)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    os.chdir(ROOT)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    connect().close()
    print(f'▶ KTV 模拟系统已启动: http://localhost:{port}/')
    print(f'  方案存储(SQLite): {DB}')
    try:
        HTTPServer(('0.0.0.0', port), Handler).serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')
