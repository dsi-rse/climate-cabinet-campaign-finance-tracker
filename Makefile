.PHONY: build run-container run-interactive run-notebooks \
        run-standardize-pipeline run-normalize-pipeline \
        run-standardize-normalize-pipeline clean

# Define constants
mkfile_path := $(abspath $(firstword $(MAKEFILE_LIST)))
current_dir := $(notdir $(patsubst %/,%,$(dir $(mkfile_path))))
current_abs_path := $(subst Makefile,,$(mkfile_path))

# Project constants
project_image_name := campaign-finance-tracker
project_container_name := campaign-finance-tracker-container
project_dir := $(current_abs_path)

# Build Docker image. The host uid is passed in so files the container writes
# into the mounted repository belong to the host user.
build:
	docker build --build-arg USER_UID=$(shell id -u) -t $(project_image_name) -f Dockerfile $(current_abs_path)

# Helper for running containers
run-container:
	docker run --rm -v $(current_abs_path):/project -t $(project_image_name) $(cmd)

run-interactive: build
	docker run -it --rm -v $(current_abs_path):/project -t $(project_image_name) /bin/bash

run-notebooks: build
	docker run --rm -v $(current_abs_path):/project -p 8888:8888 -t $(project_image_name) \
	jupyter lab --port=8888 --ip=0.0.0.0 --IdentityProvider.token='' --no-browser

run-collect-pipeline: build
	$(MAKE) run-container cmd="cft collect"

run-standardize-pipeline: build
	$(MAKE) run-container cmd="cft standardize"

run-normalize-pipeline: build
	$(MAKE) run-container cmd="cft normalize --chunk-size 2000"

run-clean-pipeline: build
	$(MAKE) run-container cmd="cft clean --chunk-size 2000"

run-link-pipeline: build
	$(MAKE) run-container cmd="cft link --model-path $(DATA_DIR)/link_model.json"
	
run-classify-pipeline: build
	$(MAKE) run-container cmd="cft classify"


# Clean up Docker resources
clean:
	docker system prune -f

