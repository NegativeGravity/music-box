"""Verify the pinned CLAP model, then enable durable background upgrades."""
import os
from pathlib import Path
import sys
import time
import numpy as np

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
os.environ.setdefault('HF_HUB_DISABLE_XET','1')
from app.audio import clap_embedding, MODEL_REVISION
from app.service import Service

service=Service(os.getenv('MUSICBOX_DATA_DIR',str(root/'.data')),os.getenv('DATABASE_URL'))
start=time.time()
signal=(.15*np.sin(np.arange(480000)*2*np.pi*440/48000)).astype(np.float32)
vector=clap_embedding(signal,str(service.root/'model-cache'))
print('CLAP verified:',len(vector),'dimensions, revision',MODEL_REVISION,'seconds',round(time.time()-start,2))
(service.root/'clap.enabled').write_text(MODEL_REVISION,encoding='utf-8')
service.enqueue_clap()
print('Background CLAP jobs queued. Playback remains available during upgrades.')
