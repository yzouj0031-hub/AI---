"""假 OpenAI 端点：验证 failover / 解析 / 降级。
/v1        正常返回（故意裹 markdown 代码块，模拟真实模型习惯）
/bad       永远 500  → 测重试+切备用
/slow      永远超时  → 测 abort
"""
import json, re, time
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_POST(self):
        n=int(self.headers.get('Content-Length',0))
        body=json.loads(self.rfile.read(n) or '{}')
        p=self.path
        if p.startswith('/slow'): time.sleep(30); return
        if p.startswith('/bad'):
            b=b'{"error":"boom"}'
            self.send_response(500)
            self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*')
            self.send_header('Content-Length',str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        user=body['messages'][-1]['content']
        sysmsg=body['messages'][0]['content']
        if '测试助手' in sysmsg:
            out='就绪'
        elif sysmsg.startswith('[watch]'):
            d=json.loads(user)
            others=[x for x in d.get('alive',[]) if x!=d.get('you')]
            out='```json\n'+json.dumps({"watch":(0 if 0 in others else (others[0] if others else None))})+'\n```'
        elif 'verdict' in sysmsg:
            out='```json\n'+json.dumps({"verdict":"contradict",
                "line":"不对，t=12.5s 我在电力室看见你了，你说你在导航室？",
                "delta":0.8},ensure_ascii=False)+'\n```'
        else:
            d=json.loads(user)
            others=[x for x in d.get('alive',[]) if x!=d.get('you')]
            sus={o:(0.7 if '紫' in o else 0.2) for o in others}
            out='```json\n'+json.dumps({
                "line":"我整段都在"+(d['memory'][0]['room'] if d.get('memory') else '食堂')+"，没见过紫。他去哪了？",
                "suspect":sus},ensure_ascii=False)+'\n```'
        r={"choices":[{"message":{"role":"assistant","content":out}}]}
        b=json.dumps(r).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Content-Length',str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Headers','*')
        self.send_header('Access-Control-Allow-Methods','*')
        self.end_headers()

HTTPServer(('127.0.0.1',8791),H).serve_forever()
