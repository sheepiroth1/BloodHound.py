import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.join(ROOT, "deps"))
sys.path.insert(0, ROOT)

import bloodhound

bloodhound.main()