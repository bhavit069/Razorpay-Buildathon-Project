# `make` is not installed on the dev box; run.py is the canonical entry point.
# These targets exist so the commands in ARCHITECTURE.md work where make does.
.PHONY: data data300k validate moat sweep metrics clean
data data300k validate moat sweep metrics clean:
	python run.py $@
