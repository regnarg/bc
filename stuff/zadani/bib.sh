#!/bin/bash

bibtex2html -nokeys -o - -s csplainnat/csplainnat.bst  -nodoc -q zadani.bib    | pandoc -f html -t plain

