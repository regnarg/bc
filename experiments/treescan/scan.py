#!/usr/bin/python3

import sys, os, random
import multiprocessing as mp
from subprocess import check_call
from time import time

def read_dataset(name):
    files = []
    with open(name, 'rb') as fd:
        for line in fd:
            ino,fn = line.strip().split(b' ', 1)
            ino = int(ino)
            files.append((ino,fn))
    return files

def st(fn):
    try: os.lstat(fn[1])
    except IOError: pass

def st_multi(fns):
    for rec in fns: st(rec)

def reset_cache():
    check_call(['sudo', 'sysctl', '-wq', 'vm.drop_caches=3'])

for dataset in 'tdata.dirs.ino', 'tdata.files.ino', 'tdata.all.ino':
    files = read_dataset(dataset)
    for sort in 'find', 'ino', 'shuffle':
        sorted_files = list(files)
        if sort == 'find': pass
        elif sort == 'ino': sorted_files.sort()
        elif sort == 'shuffle': random.shuffle(sorted_files)
        else: raise ValueError

        for K in 1,2,4,8,16,32,64,128:
            p = mp.Pool(K)
            for mode in 'blocks', 'interleaved':
                reset_cache()
                start = time()
                if mode == 'blocks':
                    pass
                    p.map(st, sorted_files)
                elif mode == 'interleaved':
                    pass
                    parts = [ sorted_files[i::K] for i in range(K) ]
                    p.map(st_multi, parts)
                else: raise ValueError
                end = time()
                took = end-start
                print(dataset,sort,K,mode,took)
            p.close()
            p.join()

