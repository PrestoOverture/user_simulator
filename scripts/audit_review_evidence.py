"""Independent arithmetic, raw turn grouping and frozen-review completeness checks."""
import csv
from difflib import SequenceMatcher
import json
from pathlib import Path
import random
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usersim.csds import load


def main():
    evidence = Path('outputs/review_evidence')
    raw = {r['DialogueID']: r for r in load()}
    superseded = json.loads((evidence/'superseded_generations_and_requests.json').read_text())
    old_texts = {(d['dataset'], g['dialogue_id'], g['turn']): g['text'] for d in superseded for g in d['generations']}
    review = json.loads((evidence/'keyword_samples.json').read_text())
    pool = []
    checked = 0
    for directory in ('outputs', 'outputs/extended'):
        root = Path(directory)
        with (root/'side_by_side.csv').open(newline='') as stream:
            pairs = list(csv.DictReader(stream))
        with (root/'metrics_by_turn.csv').open(newline='') as stream:
            metrics = {(int(r['dialogue_id']), int(r['turn']), r['source']): r for r in csv.DictReader(stream)}
        previous = {}
        for pair in pairs:
            identifier, turn = int(pair['dialogue_id']), int(pair['turn'])
            runs = []
            for msg in raw[identifier]['Dialogue']:
                if not runs or runs[-1][0] != msg['speaker']:
                    runs.append((msg['speaker'], [msg['utterance']]))
                else:
                    runs[-1][1].append(msg['utterance'])
            human_messages = [messages for speaker, messages in runs if speaker == 'Q'][turn-1]
            assert pair['human'] == '\n'.join(human_messages), 'raw grouping mismatch'
            for source in ('human', 'simulator'):
                text = pair[source]
                recorded = metrics[identifier, turn, source]
                compact = re.sub(r'\s+', '', text)
                history = previous.setdefault((identifier, source), [])
                count = len(re.findall(r'[A-Za-z0-9\u4e00-\u9fff]', text))
                assert int(recorded['char_count']) == count
                assert int(recorded['short']) == (count <= 5)
                assert int(recorded['repeated']) == any(SequenceMatcher(None, compact, old).ratio() > .6 for old in history)
                message_count = len(human_messages) if source == 'human' else sum(bool(line.strip()) for line in text.splitlines())
                assert int(recorded['message_count']) == message_count
                if turn == 1:
                    identifier_present = bool(re.search(r'\[(?:订单编号|数字)\]', compact)) or (source == 'simulator' and bool(re.search(r'\d{6,}', text)))
                    assert int(recorded['first_identifier']) == identifier_present
                    assert int(recorded['first_char_count']) == count
                else:
                    assert recorded['first_identifier'] == recorded['first_char_count'] == ''
                history.append(compact)
                checked += 1
                before_text = old_texts.get((directory, identifier, turn), text) if source == 'simulator' else text
                s = re.sub(r'\s+', '', before_text)
                old_flags = {m: int(any(k in s for k in review['keywords'][m])) for m in ('politeness','escalation','refusal','urgency','correction')}
                old_flags['correction'] |= int(bool(re.search(review['keywords']['correction_regex'], s)))
                pool.append(dict(dataset=directory, dialogue_id=identifier, turn=turn, source=source, text=before_text, **old_flags))
    for metric in ('politeness','escalation','refusal','urgency','correction'):
        for source in ('human','simulator'):
            for hit, limit in ((1,20),(0,50)):
                candidates = [r for r in pool if r['source']==source and r[metric]==hit]
                expected = random.Random(f"{review['seed']}:{metric}:{source}:{hit}").sample(candidates, min(limit,len(candidates)))
                actual = [r for r in review['samples'] if r['metric']==metric and r['source']==source and r['hit']==hit]
                key = lambda r:(r['dataset'],r['dialogue_id'],r['turn'],r['source'],r['text'])
                assert list(map(key,actual)) == list(map(key,expected)), 'review sample not reproducible'
                assert all(type(r['behavior_present']) is bool and type(r['reasonable']) is bool and r['note'] for r in actual)
    summaries = json.loads((evidence/'summary_review.json').read_text())
    selected = {identifier for directory in ('outputs','outputs/extended') for identifier in json.loads((Path(directory)/'sampled_ids.json').read_text())['ids']}
    assert len(summaries)==len(selected)==520 and {r['dialogue_id'] for r in summaries}==selected
    assert all(r['original']==raw[r['dialogue_id']]['UserSumm'] and r['review_note'] for r in summaries)
    current_summaries = {r['dialogue_id']: r for directory in ('outputs','outputs/extended')
                         for r in json.loads((Path(directory)/'summary_audit.json').read_text())}
    assert all(r['after']==current_summaries[r['dialogue_id']]['goal'] for r in summaries)
    assert {r['dialogue_id'] for r in summaries if r['changed']} == {1538,3278,9150}
    assert sum(len(d['generations']) for d in superseded)==37
    before = json.loads((evidence/'before_metrics.json').read_text())
    new_requests = list(map(json.loads, (evidence/'rerun/request_usage.jsonl').read_text().splitlines()))
    assert len(new_requests)==37 and all(r['status']=='ok' and r['usage_known'] for r in new_requests)
    for field in ('input_tokens','output_tokens','request_count'):
        baseline_total = sum(json.loads(before[d]['token_usage.json'])[field] for d in before)
        current_total = sum(json.loads((Path(d)/'token_usage.json').read_text())[field] for d in before)
        superseded_total = sum((1 if field=='request_count' else r[field]) for d in superseded for r in d['requests'])
        rerun_total = sum((1 if field=='request_count' else r[field]) for r in new_requests)
        assert baseline_total + rerun_total == current_total + superseded_total, 'cumulative cost mismatch'
    print(f'PASS: {checked} metric rows independently checked; 520 raw summaries; {len(review["samples"])} reproducible, annotated keyword samples')


if __name__ == '__main__':
    main()
