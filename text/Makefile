all: thesis.pdf abstract-en.pdf abstract-cz.pdf
export TEXINPUTS=./:

img/%.pdf: img/%.asy
	cd img && asy --tex=pdftex ../$<

%.tex: %.md
	pandoc -F pandoc-crossref -t latex $< -o - --chapters | ./uniproc.pl >$@

# LaTeX must be run multiple times to get references right
thesis.pdf: thesis.tex $(wildcard *.tex) $(subst .md,.tex,$(wildcard *.md)) \
			bibliography.bib $(subst .asy,.pdf,$(wildcard img/*.asy)) \
			thesis.xmpdata
	pdflatex --interaction=nonstopmode $< || true
	#bibtex thesis
	biber thesis || true
	pdflatex --interaction=nonstopmode $< || true
	pdflatex --interaction=nonstopmode $<

abstract-en.pdf: abstract-en.tex
	pdflatex --interaction=nonstopmode $<
abstract-cz.pdf: abstract-cz.tex
	pdflatex --interaction=nonstopmode $<

clean:
	rm -f *.log *.dvi *.aux *.toc *.lof *.lot *.out *.bbl *.blg *.xmpi
	rm -f thesis.pdf
