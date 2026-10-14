"""Run with: python -m app.worker (backend on PYTHONPATH)."""
import logging
import os
from pathlib import Path
import time
from .service import Service


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage',choices=['decode','clap'])
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    root=Path(__file__).resolve().parents[2]
    service=Service(os.getenv('MUSICBOX_DATA_DIR',str(root/'.data')),os.getenv('DATABASE_URL'))
    logging.info('Musicbox durable audio worker started')
    while True:
        try:
            service.process_jobs(max_jobs=1,stage_filter=args.stage)
        except KeyboardInterrupt:
            return
        except Exception:
            logging.exception('Worker iteration failed; retrying')
        time.sleep(.5)


if __name__=='__main__':
    main()
