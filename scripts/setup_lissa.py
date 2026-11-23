"""Fetch the exact upstream dependency without copying it into this source repo."""
import subprocess
from pathlib import Path

REPOSITORY = 'https://github.com/ardoco/lissa.git'
REVISION = 'b2fad29ec2cd83af2d2dc0de665869469143650a'


def setup(root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    destination = root / 'vendor' / 'lissa'
    if destination.exists():
        if not (destination / '.git').exists():
            raise SystemExit('vendor/lissa exists and is not a Git checkout; leave it unchanged.')
        status = subprocess.run(['git', '-C', str(destination), 'status', '--porcelain'],
                                check=True, capture_output=True, text=True)
        if status.stdout.strip():
            raise SystemExit('vendor/lissa has local changes; leave it unchanged.')
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', '--no-checkout', REPOSITORY, str(destination)], check=True)
    subprocess.run(['git', '-C', str(destination), 'checkout', '--detach', REVISION], check=True)
    print('LiSSA ready at ' + str(destination))
    print('Build with Maven: mvn -f vendor/lissa/pom.xml -DskipTests package')


if __name__ == '__main__':
    setup()
