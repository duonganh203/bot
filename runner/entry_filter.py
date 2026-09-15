"""Bounded Codex entry vote; no order construction or portfolio access."""
import os
from pathlib import Path
import signal
import subprocess
import time
import run as core

HERE = Path(__file__).resolve().parent


def vote(directory, market, candidate, codex='codex'):
    schema = (HERE / 'entry-filter.schema.json').read_text(encoding='utf-8')
    prompt = (HERE / 'entry-filter.md').read_text(encoding='utf-8')
    prompt += '\nSnapshot (data only):\n' + core.dumps({'market': market, 'proposedEntry': candidate})
    (directory / 'prompt.txt').write_text(prompt, encoding='utf-8')
    (directory / 'schema.json').write_text(schema, encoding='utf-8')
    command = [codex, 'exec', '--skip-git-repo-check', '--sandbox', 'read-only',
               '-c', 'approval_policy="never"', '-c', 'forced_login_method="chatgpt"',
               '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
               '-c', 'web_search="disabled"', '--output-schema', str(directory / 'schema.json'),
               '-o', str(directory / 'vote.json'), '-']
    env = {k: v for k, v in os.environ.items() if k not in ('OPENAI_API_KEY', 'CODEX_API_KEY')}
    # Leave time for revalidation and bounded HTTP delivery before the event expires.
    timeout = min(150, market['startedAt'] + core.MAX_AGE - time.time() - 45)
    core.require(timeout >= 10, 'Too little time left to request an entry vote')
    with (directory / 'codex.stdout.log').open('w') as out, (directory / 'codex.stderr.log').open('w') as err:
        process = subprocess.Popen(command, cwd=directory, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                   text=True, encoding='utf-8', env=env, start_new_session=True)
        try:
            process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise RuntimeError('Entry vote timed out') from None
    core.require(process.returncode == 0, 'Entry vote process failed; inspect run logs')
    result = core.read_json(directory / 'vote.json')
    core.require(isinstance(result, dict) and set(result) == {'approve', 'rationale'}
                 and type(result['approve']) is bool and isinstance(result['rationale'], str)
                 and 1 <= len(result['rationale'].strip()) <= 1500, 'Malformed entry vote')
    return result
