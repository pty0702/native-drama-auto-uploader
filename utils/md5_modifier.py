import os
import random


def modify_md5(filepath):
    with open(filepath, "ab") as f:
        f.write(os.urandom(16) + b"\x00" * random.randint(1, 10))
