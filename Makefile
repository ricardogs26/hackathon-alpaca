# Release workflow driven by the single version in optionwright/__init__.py.
#   make version   -> print it
#   make test      -> pytest
#   make lint      -> ruff
#   make release   -> build + push + bump k8s manifest + apply + rollout
# Bump __version__ first; never edit the image tag in k8s/ by hand.

VERSION  := $(shell python3 -c "import re;print(re.search(r'__version__\s*=\s*\"([^\"]+)\"',open('optionwright/__init__.py').read()).group(1))")
IMAGE    := registry.richardx.dev/optionwright
MANIFEST := k8s/02-deployment.yaml
NS       := hackathon
PY       := .venv/bin/python

.PHONY: version test lint build push bump deploy release

version:
	@echo $(VERSION)

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check optionwright tests

build:
	docker build -t $(IMAGE):$(VERSION) .

push:
	docker push $(IMAGE):$(VERSION)

bump:
	sed -i 's#$(IMAGE):[0-9][0-9.]*#$(IMAGE):$(VERSION)#' $(MANIFEST)
	@grep -n "image: $(IMAGE):$(VERSION)" $(MANIFEST)

deploy:
	kubectl apply -f $(MANIFEST) -n $(NS)
	kubectl rollout status deployment/optionwright -n $(NS) --timeout=120s

release: lint test build push bump deploy
	@echo "released $(VERSION) — now: git commit -am 'release: $(VERSION)' && git tag v$(VERSION)"
