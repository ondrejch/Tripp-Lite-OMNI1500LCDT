VENV := .venv
PYTHON := $(VENV)/bin/python
PYINSTALLER := $(VENV)/bin/pyinstaller
SPEC := ups_monitor_pyqt5.spec
TARGET := dist/ups_monitor_pyqt5
SOURCES := ups_monitor_pyqt5.py ups_device.py $(SPEC)

.PHONY: all build deps run clean

all: build

build: $(TARGET)

$(TARGET): $(SOURCES)
	@test -x $(PYINSTALLER) || $(MAKE) deps
	$(PYINSTALLER) --noconfirm $(SPEC)

deps:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install pyinstaller pyqt5 pyqtgraph

run: $(TARGET)
	./$(TARGET)

clean:
	rm -rf build dist __pycache__
