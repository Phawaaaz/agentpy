"""Entry point: python pipeline.py "<task description>"

Runs the autonomous multi-stage pipeline (see pipeline/ and
interfaces/pipeline_cli.py) instead of the interactive CLI (main.py).
"""

from interfaces.pipeline_cli import main

if __name__ == "__main__":
    main()
