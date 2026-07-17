#!/usr/bin/env python3
"""Entry point: python run.py [--host H] [--port P] [--debug]

Defaults can also be set via environment variables (used by Docker):
    TVCHARTS_HOST (default 127.0.0.1), TVCHARTS_PORT (default 8050)
"""

import argparse
import os

from tvcharts.app import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tvcharts — Python charting app")
    parser.add_argument("--host", default=os.environ.get("TVCHARTS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("TVCHARTS_PORT", "8050")))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    main(host=args.host, port=args.port, debug=args.debug)
