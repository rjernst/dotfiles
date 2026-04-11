BINARIES  = ta
BUILD_DIR ?= $(HOME)/bin

HASH := $(shell find . -type f \( -name 'go.mod' -o \( -name '*.go' ! -name '*_test.go' \) \) ! -path '*/testdata/*' | sort | xargs shasum | shasum | cut -d' ' -f1 | cut -c1-16)
DATE := $(shell date +%Y-%m-%d)

.PHONY: all clean list $(BINARIES)

all: $(BINARIES)

$(BINARIES):
	go build \
		-ldflags "-X dotfiles/tools/$@/cmd.BuildHash=$(HASH) -X dotfiles/tools/$@/cmd.BuildDate=$(DATE)" \
		-o $(BUILD_DIR)/$@ ./$@

list:
	@echo $(BINARIES)

clean:
	$(foreach bin,$(BINARIES),rm -f $(BUILD_DIR)/$(bin);)
