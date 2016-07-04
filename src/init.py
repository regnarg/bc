#!/usr/bin/python3
"""
Usage: init.py [DIR]

Create a new empty Filoco store in a given directory or the current working directory.
"""

import sys, os, tempfile, shutil

from utils import *
from store import *

_schema_dirs = [ os.path.dirname(__file__), '/usr/share/filoco' ]
for _loc in _schema_dirs:
    _fn = os.path.join(_loc, 'schema.sql')
    if os.path.exists(_fn):
        SCHEMA_FILE = os.path.realpath(_fn)
        break
else:
    raise RuntimeError("Unable to find 'schema.sql' in %r" % _schema_dirs)

def main(dir = '.'):
    if dir: os.chdir(dir or '.')
    try: store = Store.find()
    except StoreNotFound: pass
    else: err('Directory %s already in a Filoco store (%s).' % (os.getcwd(), store.root_path))
    try:
        if os.path.exists('.filoco.tmp'): shutil.rmtree('.filoco.tmp')
        os.mkdir('.filoco.tmp')
        spurt('.filoco.tmp/version', "1")
        spurt('.filoco.tmp/type', "fs")
        db = SqliteWrapper('.filoco.tmp/meta.sqlite', wal=True)
        db.execute(slurp(SCHEMA_FILE))
        os.rename('.filoco.tmp', '.filoco')
    except:
        try: shutil.rmtree('.filoco.tmp')
        except OSError: pass
        raise



if __name__ == '__main__':
    main(**docopt_attr(__doc__))
