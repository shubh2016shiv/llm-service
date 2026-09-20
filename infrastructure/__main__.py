"""Allow the control plane to run as `python -m infrastructure`."""

from infrastructure.local_stack.cli import main

raise SystemExit(main())
