"""Install a built wheel in a clean venv and run all mocked examples."""
import argparse,json,os,subprocess,sys,venv
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument("--wheel",required=True);parser.add_argument("--name",default="");args=parser.parse_args()
wheel=Path(args.wheel).resolve()
folder=root/"artifacts"/("install-python-"+str(sys.version_info.major)+str(sys.version_info.minor)+args.name)
if folder.exists(): raise SystemExit("Install test directory exists; use a fresh artifacts directory")
venv.EnvBuilder(with_pip=True).create(folder)
python=folder/("Scripts/python.exe" if os.name=="nt" else "bin/python")
def run(*args,**kwargs): subprocess.run([str(python),*map(str,args)],check=True,**kwargs)
run("-m","pip","install",wheel)
run("-c","import importlib.metadata as m; import cloudbyte_tracer as c; assert c.__version__ == '0.1.0'; assert all('extra ==' in r or 'extra == ' in r for r in (m.requires('cloudbyte-tracer') or [])); print('Wheel core import: zero required dependencies')",cwd=folder)
env={k:v for k,v in os.environ.items() if not k.startswith("CLOUDBYTE_")};env["CLOUDBYTE_DISABLED"]="true"
run(root/"examples/python/manual.py",env=env,cwd=folder)
run("-m","pip","install",str(wheel)+"[all]","openai","anthropic","langgraph","httpx")
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
spans=[]
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        spans.extend(body["spans"])
        self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
        self.wfile.write(json.dumps({"accepted":len(body["spans"]),"rejected":[]}).encode())
    def log_message(self,*args): pass
server=ThreadingHTTPServer(("127.0.0.1",0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
env["CLOUDBYTE_DISABLED"]="false";env["CLOUDBYTE_AGENT_KEY"]="cbagt_"+"T"*32;env["CLOUDBYTE_BASE_URL"]="http://127.0.0.1:"+str(server.server_port)
try:
    for example in ["openai_chat.py","anthropic_chat.py","langgraph_agent.py"]:
        spans.clear();run(root/"examples/python"/example,env=env,cwd=folder)
        llms=[s for s in spans if s["kind"]=="llm" and s.get("ended_at")]
        assert llms and llms[0].get("model")=="test-model", example
        assert llms[0].get("usage",{}).get("input_tokens")==7, example
        by_id={s["span_id"]:s for s in spans}
        assert llms[0]["parent_span_id"] in by_id
        if example=="langgraph_agent.py": assert by_id[llms[0]["parent_span_id"]]["name"]=="respond"
finally:
    server.shutdown();server.server_close();thread.join()
print("Clean wheel install and four offline examples passed")
