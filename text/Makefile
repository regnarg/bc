all: thesis.pdf

%.tex: %.md
	pandoc -t latex $< -o $@ --chapters

# LaTeX must be run multiple times to get references right
thesis.pdf: thesis.tex $(wildcard *.tex) $(subst .md,.tex,$(wildcard *.md)) bibliography.bib
	pdflatex --interaction=batchmode $<
	bibtex thesis
	pdflatex --interaction=batchmode $<
	pdflatex --interaction=batchmode $<

clean:
	rm -f *.log *.dvi *.aux *.toc *.lof *.lot *.out *.bbl *.blg
	rm -f thesis.pdf
