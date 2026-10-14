"""Native-inference boundary fixture: synthesize audio, never load an ML model."""
import json
from pathlib import Path
import sys
import time
import wave
import math
import struct
import os
import psutil

config_path = Path(sys.argv[1])
folder = config_path.parent
config = json.loads(config_path.read_text())
temporary_identity = folder / 'runner-identity.tmp'
temporary_identity.write_text(json.dumps({'pid': os.getpid(), 'started_at': psutil.Process().create_time(), 'attempt_id': config['attempt_id']}))
temporary_identity.replace(folder / 'runner-identity.json')
while True:
    try:
        if json.loads((folder / 'start.ready').read_text())['attempt_id'] == config['attempt_id']:
            break
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(.02)
time.sleep(.15)
request = config['request']
with wave.open(str(folder / 'audio.wav'), 'wb') as audio:
    audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
    samples = b''.join(struct.pack('<hh', value, value) for value in
                     (int(math.sin(i * 2 * math.pi * (200 + request['seed']) / 48000) * 8000) for i in range(96000)))
    audio.writeframes(samples)
result = {'status': 'completed', 'audio': 'audio.wav', 'score': None,
          'attempt_id': config['attempt_id'],
          'seed': request['seed'], 'elapsed_seconds': .2, 'duration_capped': False}
temporary = folder / 'runner-result.tmp'
temporary.write_text(json.dumps(result))
temporary.replace(folder / 'runner-result.json')
