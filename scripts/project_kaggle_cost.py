"""Project full-epoch and paired generation runtime from pilot measurements."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def project(metrics, manifest):
    if 'benchmarks' in metrics:
        estimate = metrics.get('train_token_counts', {}).get('total', manifest['token_estimate']['train_total'])
        lines = ['# Kaggle epoch projection', '',
                 'CPU smoke is plumbing validation, NOT a T4 speed estimate.' if metrics['mode'] == 'cpu_smoke'
                 else 'Measured fp16 benchmark; excludes setup, download and checkpoint I/O.', '',
                 f'Epoch tokens: {estimate:,} (prompt + target, including template/EOS when exact counts exist).', '',
                 '| Configuration | Steps | token/s | Epoch hours | GPU hours | Overflow steps |',
                 '|---|---:|---:|---:|---:|---:|']
        for b in metrics['benchmarks']:
            speed = b['tokens_per_second']
            hours = estimate/speed/3600 if speed else None
            gpu_hours = hours*b['actual']['world_size'] if hours is not None else None
            lines.append(f"| {b['configuration']} ({b['actual']}) | {b['completed_steps']}/20 | "
                         + (f"{speed:.2f} | {hours:.3f} | {gpu_hours:.3f}" if speed else 'N/A | N/A | N/A')
                         + f" | {len(b['overflow_steps'])} |")
        lines += ['', 'GPU hours conservatively count each active GPU against the 30-hour weekly allowance. '
                  'Incomplete (<20-step) or overflowing measurements require review before selecting a configuration. '
                  'Projection is not proof that a full epoch fits the allowance.']
        return '\n'.join(lines)+'\n'
    steps = metrics['steps']
    if not steps or not metrics['generation']:
        raise ValueError('Need training and paired generation measurements')
    total_seconds = sum(s['seconds'] for s in steps)
    total_tokens = sum(s['input_tokens'] for s in steps)
    # Full prompts are recomputed for each target; target-only tokens understate work.
    estimate = manifest['token_estimate']['train_total']
    train_hours = estimate / (total_tokens / total_seconds) / 3600
    rows = [('1 epoch training', train_hours)]
    for name in ('base', 'lora'):
        g = metrics['generation'][name]
        rows.append((f'{name}: {manifest["evaluation_turns"]} turns',
                     manifest['evaluation_turns'] * g['seconds'] / g['samples'] / 3600))
    total = sum(h for _, h in rows)
    lines = ['# Kaggle GPU time projection', '',
             f'Measurement: **{metrics["mode"]}**. '+('CPU smoke validates plumbing only; these are NOT T4 speed estimates.' if metrics['mode'] == 'cpu_smoke' else 'Single-device fp16 pilot measurements; projection only.'),
             '', '| Work | Hours | % of 30 h/week |', '|---|---:|---:|']
    lines += [f'| {name} | {hours:.3f} | {hours/30*100:.1f}% |' for name, hours in rows]
    lines += [f'| Total | {total:.3f} | {total/30*100:.1f}% |', '',
              f'30-hour allowance: {"within" if total <= 30 else "exceeded"}; remaining {30-total:.3f} h.', '',
              f'Training estimate: {estimate:,} tokens ({manifest["token_estimate"]["method"]}); '
              'uses processed prompt+target tokens/s, not target-only throughput. '
              'Generation uses seconds/sample, including prefill, separately for base and LoRA. '
              'Assumes the seeded 50 turns represent all evaluation turns. '
              'Excludes installation, model download, checkpoint I/O and interruptions. '
              'Nonfinite steps: '+str(sum(s['nonfinite'] for s in steps))+'.']
    if metrics.get('train_token_counts'):
        # Exact model-token counts supersede the character estimate on real pilots.
        exact = dict(manifest)
        exact['token_estimate'] = dict(train_total=metrics['train_token_counts']['total'],
                                      method='exact pilot tokenizer count, including template and target EOS')
        revised = dict(metrics)
        revised.pop('train_token_counts')
        return project(revised, exact)
    return '\n'.join(lines)+'\n'


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--metrics', type=Path, required=True)
    p.add_argument('--manifest', type=Path, default=ROOT/'kaggle/sft_data/manifest.json')
    args = p.parse_args()
    print(project(json.loads(args.metrics.read_text()), json.loads(args.manifest.read_text())))
