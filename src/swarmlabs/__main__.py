"""Allow `python -m swarmlabs` to invoke the CLI."""
from swarmlabs.cli import main
import sys

sys.exit(main())
