"""Convert native YuE2 plans into instrumental plans without changing melody."""
import re
from .vendor.yue2_abc import parse_abc as parse_score, TOKEN


def instrumental_score(text):
    before = parse_score(text)
    if not before.voices['Vocal'].notes:
        if not before.voices['Ins'].notes:
            raise ValueError('The instrumental plan has no melody')
        return text
    lines = text.splitlines()
    vocal_lines = [i for i, voice in before.music_lines.items() if voice == 'Vocal']
    ins_lines = [i for i, voice in before.music_lines.items() if voice == 'Ins']
    for vocal, instrumental in zip(vocal_lines, ins_lines, strict=True):
        melody = lines[vocal]
        lines[instrumental] = re.sub(r'"[^"\n]*"', '', melody)
        def rest(match):
            if match.group('note'):
                return 'z' + match.group('duration')
            return match.group(0)
        lines[vocal] = TOKEN.sub(rest, melody)
    result = '\n'.join(lines) + '\n'
    after = parse_score(result)
    if (after.voices['Vocal'].notes or after.voices['Ins'].notes != before.voices['Vocal'].notes
            or after.voices['Vocal'].chords != before.voices['Vocal'].chords):
        raise ValueError('Instrumental conversion changed the planned melody')
    return result
