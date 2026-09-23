"""Controlled subprocess stand-in. Never connects to a provider."""

import json
import sys
import time

request = json.load(sys.stdin)
mode = sys.argv[1]
if mode == "failure":
    sys.stderr.write("secret-fixture")
    sys.exit(3)
if mode == "timeout":
    time.sleep(10)
elif mode == "overflow":
    sys.stdout.write("x" * 300000)
elif mode == "malformed":
    print("secret-fixture")
else:
    print(
        json.dumps(
            {
                "answers": {
                    "action": {"choice": next(iter(request["questions"]["action"]["criteria"]))}
                },
                "usage": {"totalTokens": 7},
                "response": {"modelId": "fixture/jev"},
            }
        )
    )
