"""Launch the pinned vendor API with the documented no-planner CPU profile."""
import argparse
import os
from pathlib import Path
import runpy
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-root', type=Path, required=True)
    args, server_args = parser.parse_known_args()
    engine_root = args.engine_root.resolve()
    if not (engine_root / 'acestep' / 'api_server.py').is_file():
        parser.error('The selected directory does not contain the ACE-Step API.')
    if os.getenv('ACESTEP_TMPDIR'):
        temporary_root = Path(os.environ['ACESTEP_TMPDIR']).resolve()
        temporary_root.mkdir(parents=True, exist_ok=True)
        for name in ('TMPDIR', 'TEMP', 'TMP'):
            os.environ[name] = str(temporary_root)
        # Vendor imports may cache gettempdir() before its lifespan changes the
        # environment. Configure it first so reference-path validation agrees.
        tempfile.tempdir = None
    sys.path.insert(0, str(engine_root))
    from acestep import model_downloader
    if os.getenv('ACESTEP_INIT_LLM', '').strip().lower() in ('false', '0', 'no'):
        # The pinned vendor readiness check otherwise requires the unused 1.7B
        # planner and downloads its whole bundle even for DiT-only inference.
        # Only that optional component is removed; core model checks remain.
        model_downloader.MAIN_MODEL_COMPONENTS = [
            name for name in model_downloader.MAIN_MODEL_COMPONENTS
            if name != 'acestep-5Hz-lm-1.7B'
        ]
    sys.argv = ['acestep.api_server', *server_args]
    runpy.run_module('acestep.api_server', run_name='__main__')


if __name__ == '__main__':
    main()
