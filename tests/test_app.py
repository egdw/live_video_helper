"""无需第三方Python包的基础集成测试。"""
import datetime
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


class TrainingSystemTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_db,cls.old_upload = app.DBFILE,app.UPLOADS
        app.DBFILE = Path(cls.temp.name)/'db.sqlite3'
        app.UPLOADS = Path(cls.temp.name)/'uploads'
        app.init_db()
        cls.server = ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=5)
        app.DBFILE,app.UPLOADS = cls.old_db,cls.old_upload
        cls.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=12)
        hdr=headers or {}
        if isinstance(body,dict):
            body=json.dumps(body,ensure_ascii=False).encode('utf-8')
            hdr['Content-Type']='application/json'
        conn.request(method,path,body=body,headers=hdr)
        res=conn.getresponse()
        data=res.read()
        result=res.status,dict(res.getheaders()),data
        conn.close();return result

    def test_00_health_and_frontend(self):
        status,_,body=self.request('GET','/api/health')
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['ok'])
        for url in ('/admin','/player','/static/admin.js','/static/player.js'):
            status,_,body=self.request('GET',url)
            self.assertEqual(status,200)
            self.assertGreater(len(body),500)
        status,_,_=self.request('GET','/static/../app.py')
        self.assertIn(status,(400,404))

    def test_10_generate_and_validation(self):
        data={'start_day':'2026-10-13','days':4,'am_start':'08:30','am_end':'12:30','pm_start':'13:30','pm_end':'17:30'}
        status,_,body=self.request('POST','/api/generate',data)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['created'],8)
        status,_,body=self.request('POST','/api/generate',data)
        self.assertEqual(json.loads(body)['skipped'],8)
        status,_,body=self.request('POST','/api/generate',{**data,'am_end':'07:00'})
        self.assertEqual(status,400)
        self.assertIn('结束时间',body.decode())

    def test_20_import_autosort_playlist_and_media_range(self):
        folder=Path(self.temp.name)/'MyVideos'
        morning=folder/'Day01'/'AM'
        morning.mkdir(parents=True)
        (morning/'02_中.mp4').write_bytes(b'0123456789abcdefghij')
        (morning/'01_前.mp4').write_bytes(b'XYZ'*10)
        status,_,body=self.request('POST','/api/import',{'folder':str(folder),'recursive':True})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['added'],2)
        status,_,body=self.request('POST','/api/auto-assign',{})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['assigned'],2)
        status,_,body=self.request('GET','/api/plan')
        plan=json.loads(body)['sessions']
        first=plan[0]
        self.assertEqual([v['title'] for v in first['videos']],['01_前','02_中'])
        vid=first['videos'][0]['id']
        status,headers,body=self.request('GET',f'/media/{vid}',headers={'Range':'bytes=1-5'})
        self.assertEqual(status,206)
        self.assertEqual(body,b'YZXYZ')
        self.assertTrue(headers['Content-Range'].startswith('bytes 1-5/'))
        status,headers,body=self.request('GET',f'/media/{vid}',headers={'Range':'bytes=999-'})
        self.assertEqual(status,416)
        status,_,_=self.request('POST',f'/api/videos/{vid}/duration',{'duration':1200.5})
        self.assertEqual(status,200)
        session_id=first['id']
        status,_,_=self.request('PUT',f'/api/sessions/{session_id}/items',{'video_ids':[vid,vid]})
        self.assertEqual(status,200)
        status,_,body=self.request('GET','/api/plan')
        self.assertEqual(len(json.loads(body)['sessions'][0]['videos']),2)
        status,_,_=self.request('PUT',f'/api/sessions/{session_id}/items',{'video_ids':[999999]})
        self.assertEqual(status,400)

    def test_30_upload_heartbeat_and_status(self):
        payload=b'x'*8192
        status,_,body=self.request('POST','/api/upload?filename=unit_test.mp4',payload,{'Content-Type':'application/octet-stream'})
        self.assertEqual(status,200)
        new_id=json.loads(body)['id']
        status,_,body=self.request('GET',f'/media/{new_id}',headers={'Range':'bytes=-8'})
        self.assertEqual(status,206)
        self.assertEqual(body,b'x'*8)
        status,_,_=self.request('POST','/api/heartbeat',{'mode':'paused','session':None,'video':None})
        self.assertEqual(status,200)
        status,_,body=self.request('GET','/api/status')
        self.assertEqual(json.loads(body)['heartbeat']['payload']['mode'],'paused')

if __name__=='__main__':unittest.main()
