#!/usr/bin/python3

import sys, os, tempfile, shutil
import jinja2

from utils import *
from store import *
from OpenSSL import crypto

RSA_KEY_SIZE = 2048 # TODO make configurable

_schema_dirs = [ os.path.dirname(__file__), '/usr/share/filoco' ]
for _loc in _schema_dirs:
    _fn = os.path.join(_loc, 'schema.sql')
    if os.path.exists(_fn):
        SCHEMA_FILE = os.path.realpath(_fn)
        break
else:
    raise RuntimeError("Unable to find 'schema.sql' in %r" % _schema_dirs)

def main(dir, *, synctree=False, name:'n'=None):
    if dir: os.chdir(dir or '.')
    try: store, sub = Store.find()
    except StoreNotFound: pass
    else: err('Directory %s already in a Filoco store (%s).' % (os.getcwd(), store.root_path))
    try:
        if os.path.exists('.filoco.tmp'): shutil.rmtree('.filoco.tmp')
        os.mkdir('.filoco.tmp')
        spurt('.filoco.tmp/version', "1")
        spurt('.filoco.tmp/type', "fs")
        db = SqliteWrapper('.filoco.tmp/meta.sqlite', wal=True)
        sync_mode = 'synctree' if synctree else 'serial'
        spurt('.filoco.tmp/sync_mode', sync_mode)
        tpl = jinja2.Template(slurp(SCHEMA_FILE), line_statement_prefix='#')
        schema = tpl.render(sync_mode=sync_mode)
        db.execute(schema)

        key = crypto.PKey()
        key.generate_key(crypto.TYPE_RSA, RSA_KEY_SIZE)
        priv_key = crypto.dump_privatekey(crypto.FILETYPE_PEM, key)
        pub_key = crypto.dump_publickey(crypto.FILETYPE_PEM, key)

        cert = crypto.X509()
        cert.get_subject().CN = name or 'unnamed'
        cert.set_serial_number(1000)
        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(10*365*24*60*60)
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(key)
        cert.sign(key, 'sha256')

        store_id = cert.digest('sha256').decode('ascii').replace(':','').lower()
        spurt('.filoco.tmp/store_id', store_id)

        db.insert('stores', idx=0, id=store_id)

        spurt('.filoco.tmp/store_cert', crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode('ascii'))
        with open('.filoco.tmp/store_key', 'wb') as file:
            os.chmod('.filoco.tmp/store_key', 0o600)
            file.write(priv_key)

        os.rename('.filoco.tmp', '.filoco')
        
    except:
        try: shutil.rmtree('.filoco.tmp')
        except OSError: pass
        raise



if __name__ == '__main__':
    run(main)
