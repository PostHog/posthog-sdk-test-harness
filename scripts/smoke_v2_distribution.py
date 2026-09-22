"""Exercise an installed v2 distribution outside the checkout with controlled hosts."""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

DRIVER = """
import asyncio
import json
import sys
from importlib.metadata import distribution
from pathlib import Path
import posthog_test_harness
from posthog_test_harness.v2.bundle import specification_inputs
from posthog_test_harness.v2.contracts import Contracts
from tests.v2_ai_host import AIHost
from tests.v2_flush_host import serve

async def main():
    installed = distribution('posthog-sdk-test-harness')
    assert not json.loads(installed.read_text('direct_url.json') or '{}').get('dir_info', {}).get('editable', False)
    outputs = []
    with specification_inputs() as (specs, bundle):
        contracts = Contracts()
        for defect, expected_exit in [(None, 0), ('wrong_route', 1)]:
            async with serve(contracts, host_type=AIHost, defect=defect) as (host, url):
                name = defect or 'healthy'
                process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'posthog_test_harness.v2.cli',
                    'run', '--migration-suite', '--adapter-url', url, '--profile', host.profile['id'],
                    '--report', name + '.json', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
                Path(name + '.log').write_bytes(stdout + stderr)
                assert process.returncode == expected_exit, (process.returncode, stdout, stderr)
                report = json.loads(Path(name + '.json').read_text())
                assert not report['errors']
                executed = [r for r in report['results'] if r['result']['executed']]
                assert len(executed) == 5
                if defect is None:
                    assert all(r['result']['status'] == 'passed' for r in executed)
                else:
                    assert any(r['result']['status'] == 'failed_assertion' for r in executed)
                diagnostics = json.loads(Path(name + '.json.diagnostics.json').read_text())
                assert diagnostics['distribution']['bundle_sha256'] == bundle['bundle_sha256']
                outputs.append({'defect': defect, 'exit': process.returncode, 'executed': len(executed)})
    print(json.dumps({'purpose': 'controlled distribution smoke, not SDK conformance',
        'installed_module': posthog_test_harness.__file__,
        'bundle_sha256': bundle['bundle_sha256'], 'runs': outputs}, indent=2))

asyncio.run(main())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path, help="Python from a clean installed-wheel environment")
    parser.add_argument("--out", required=True, type=Path, help="Fresh directory outside the source checkout")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.out.resolve()
    if output.is_relative_to(repo):
        parser.error("Smoke directory must be outside the source checkout")
    output.mkdir(parents=True, exist_ok=False)
    (output / "tests").mkdir()
    (output / "tests/__init__.py").write_text("")
    for name in ("v2_ai_host.py", "v2_flush_host.py"):
        shutil.copy2(repo / "tests" / name, output / "tests" / name)
    (output / "smoke.py").write_text(DRIVER)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    result = subprocess.run(
        [str(args.python.absolute()), "smoke.py"],
        cwd=output,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=150,
    )
    (output / "smoke.log").write_bytes(result.stdout)
    print(result.stdout.decode(), end="")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
