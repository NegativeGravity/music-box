import io
import wave
from app.demo import synth


def test_demo_synth_exact_duration_and_nonempty_audio():
    with wave.open(io.BytesIO(synth(1,'Instrumental',seconds=3))) as audio:
        assert audio.getnframes()==66150
        assert audio.getframerate()==22050
        assert len(audio.readframes(66150))==132300
