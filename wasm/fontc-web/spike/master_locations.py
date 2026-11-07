#!/usr/bin/env python3
"""Print the master locations the studio's pipeline hands the transforms
for a source (pipeline._master_locations), as JSON — `null` when it finds
none. The SPAC oracle gives the same list to both injectors.

Usage: master_locations.py SOURCE
"""

import json
import sys

from avar2_studio import pipeline

print(json.dumps(pipeline._master_locations(sys.argv[1])))
