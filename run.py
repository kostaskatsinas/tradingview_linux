#!/usr/bin/env python3
"""Entry point: python run.py [--host H] [--port P] [--debug]"""

import argparse

from tvcharts.app import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tvcharts — Python charting app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    main(host=args.host, port=args.port, debug=args.debug)
