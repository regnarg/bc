all: binxor.so check_helper

check_helper: check_helper.cpp
	g++ -Wall -lsqlite3 -std=gnu++11 -o$@ $<

binxor.so: binxor.c
	gcc -Wall -g -fPIC -shared $< -o $@
