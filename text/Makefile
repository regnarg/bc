all: thesis.pdf

img/%.pdf: img/%.asy
	cd img && asy --tex=pdftex ../$<

%.tex: %.md
	pandoc -F pandoc-crossref -t latex $< -o - --chapters | ./uniproc.pl >$@

# LaTeX must be run multiple times to get references right
thesis.pdf: thesis.tex $(wildcard *.tex) $(subst .md,.tex,$(wildcard *.md)) \
			bibliography.bib $(subst .asy,.pdf,$(wildcard img/*.asy))
	pdflatex --interaction=nonstopmode $<
	bibtex thesis
	pdflatex --interaction=nonstopmode $<
	pdflatex --interaction=nonstopmode $<

clean:
	rm -f *.log *.dvi *.aux *.toc *.lof *.lot *.out *.bbl *.blg
	rm -f thesis.pdf
