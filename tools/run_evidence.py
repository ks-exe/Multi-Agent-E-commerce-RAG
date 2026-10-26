"""Run the original commands and retain complete, timestamped execution evidence."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {name: ['tests/test_agent.py', name]
            for name in ('task2', 'task3', 'task4', 'task5', 'task6', 'all')}
COMMANDS.update(live=['src/agent_orchestrator.py', 'test'],
                infrastructure=['infrastructure/verify_deployment.py'],
                deploy=['src/agent_orchestrator.py', 'deploy'],
                parallel=['tests/test_live_parallel_retrieval.py'])
EXPECTED_SCORES = {'task2': 40, 'task3': 20, 'task4': 15, 'task5': 25, 'task6': 20, 'all': 120}


def run(name):
    destination = ROOT / 'docs' / 'submission' / 'logs'
    destination.mkdir(parents=True, exist_ok=True)
    arguments = ['-X', 'utf8', '-u', *COMMANDS[name]]
    started = datetime.now(timezone.utc)
    print('\n$ python ' + ' '.join(arguments), flush=True)
    process = subprocess.Popen([sys.executable, *arguments], cwd=ROOT,
                               env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace')
    lines = []
    for line in process.stdout:
        lines.append(line)
        print(line, end='', flush=True)
    code = process.wait()
    transcript = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', ''.join(lines))
    log = destination / (name + '.txt')
    log.write_text(transcript, encoding='utf-8', newline='\n')
    score = re.search(r'Score:\s*(\d+)/(\d+) pts', transcript)
    passed = code == 0
    if name in EXPECTED_SCORES:
        expected = str(EXPECTED_SCORES[name])
        passed = passed and score is not None and score[1] == score[2] == expected
    if name == 'live':
        passed = passed and len(re.findall(r'^Trace ID: 1-[0-9a-f-]+$', transcript, re.M)) == 5
        passed = passed and len(re.findall(r'^Response: \S', transcript, re.M)) == 5
    metadata = {'command': 'python ' + ' '.join(arguments),
                'python_version': sys.version.split()[0],
                'started_utc': started.isoformat(),
                'finished_utc': datetime.now(timezone.utc).isoformat(),
                'exit_code': code, 'passed': passed,
                'transcript_sha256': hashlib.sha256(log.read_bytes()).hexdigest()}
    if score:
        metadata['score'] = score[1] + '/' + score[2]
    (destination / (name + '.json')).write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(f'Captured {name}: exit={code}, verified={passed}', flush=True)
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', nargs='+', choices=list(COMMANDS))
    args = parser.parse_args()
    passed = True
    for case in args.cases:
        passed = run(case) and passed
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
