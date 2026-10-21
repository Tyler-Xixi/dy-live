import os,subprocess,sys,tempfile,time
from pathlib import Path
arch=sys.argv[1]
exe=Path('dist')/arch/'DY直播间自助工具.app/Contents/MacOS/DYLiveAssistantMac'
assert exe.is_file()
with tempfile.TemporaryDirectory(prefix='mac-empty-user-') as directory:
    env=dict(os.environ,HOME=directory)
    env.pop('SSL_CERT_FILE',None)
    report=Path(directory)/'self-check.json'
    subprocess.run([str(exe.resolve()),'--mac-self-check',str(report)],env=env,check=True,timeout=45)
    import json
    assert json.loads(report.read_text())['https_health']==200
    with open(Path(directory)/'out.log','wb') as log:
        process=subprocess.Popen([str(exe.resolve())],env=env,stdout=log,stderr=log)
        try:
            time.sleep(5)
            assert process.poll() is None,(Path(directory)/'out.log').read_text(errors='replace')
        finally:
            if process.poll() is None:process.terminate()
            process.wait(timeout=20)
print('Bundled Mac app stays running with empty HOME; no license or account copied; no task started')
